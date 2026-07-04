"""scripts/canonicalize_concepts.py — passe de higiene/canonicalização (KG v2 PR3).

Spec docs/specs/kg-redesign.md (PR3). Orquestra o core/kg/canonicalize sobre os
hipergrados em disco, em etapas separadas PROPOSE (LLM, gera planos auditáveis)
e APPLY (mecânico, reescreve os arquivos). Nada de LLM no apply.

Fluxo:
    python -m scripts.canonicalize_concepts stats               # métricas do corpus
    python -m scripts.canonicalize_concepts propose-validation  # LLM → validation_plan.json
    python -m scripts.canonicalize_concepts apply               # backup + reescreve grafos + canon
    python -m scripts.canonicalize_concepts propose-merges      # embeddings+LLM → merge_plan.json
    python -m scripts.canonicalize_concepts sample-merges -n 30 # amostra p/ conferência manual
    python -m scripts.canonicalize_concepts apply               # (de novo — agora com os merges)
    python -m scripts.canonicalize_concepts propose-macro       # LLM → macro_plan.json (D8)
    python -m scripts.canonicalize_concepts apply-macro         # escreve macro_temas[]
    python -m scripts.canonicalize_concepts report              # antes/depois (fan-in etc.)

`apply` é idempotente: recompila o CANON MAP de TODOS os planos existentes em
data/knowledge_graph/canonicalization/ e o aplica (re-aplicar é no-op). O canon
é persistido via kg_store (`concept_canon`) — o ingest o reaplica em extração
fresca (hyper_extractor).

Guardrails (spec): backup do diretório antes de qualquer reescrita; merges têm
score no plano e DEVEM ser amostrados (sample-merges) antes do apply; descartes
ficam auditáveis no plano/canon, não são deleção cega.
"""
from __future__ import annotations

import argparse
import json
import random
import shutil
import sys
from datetime import datetime

from dotenv import load_dotenv

load_dotenv()  # OPENAI_API_KEY (propose-*) + SUPABASE_* (publicação do canon)

from config import KNOWLEDGE_GRAPH_DIR  # noqa: E402
from core.kg import canonicalize as canon_mod  # noqa: E402
from core.kg.canonicalize import (  # noqa: E402
    CanonStats,
    apply_macro_temas,
    build_canon,
    canonicalize_graph,
    corpus_stats,
    inventory_concepts,
    propose_macro_temas,
    propose_merges,
    propose_validation,
)
from core.kg.schema import macro_temas_vocab, validate_v2_node  # noqa: E402

_HYPERGRAPHS_DIR = KNOWLEDGE_GRAPH_DIR / "hypergraphs"
_PLANS_DIR = KNOWLEDGE_GRAPH_DIR / "canonicalization"

_BASELINE = _PLANS_DIR / "baseline_stats.json"
_VALIDATION_PLAN = _PLANS_DIR / "validation_plan.json"
_MERGE_PLAN = _PLANS_DIR / "merge_plan.json"
_MACRO_PLAN = _PLANS_DIR / "macro_plan.json"


def _load_graphs() -> dict[str, dict]:
    """Hipergrados do DISCO (o script normaliza os artefatos locais; publicação
    p/ prod é via kg_store no build, como no migrador)."""
    graphs: dict[str, dict] = {}
    for p in sorted(_HYPERGRAPHS_DIR.glob("*.json")):
        graphs[p.stem] = json.loads(p.read_text(encoding="utf-8"))
    if not graphs:
        print(f"nenhum hipergrado em {_HYPERGRAPHS_DIR}", file=sys.stderr)
        raise SystemExit(1)
    return graphs


def _write_graphs(graphs: dict[str, dict]) -> None:
    for fk, g in graphs.items():
        (_HYPERGRAPHS_DIR / f"{fk}.json").write_text(
            json.dumps(g, indent=2, ensure_ascii=False), encoding="utf-8"
        )


def _backup() -> str:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    dst = KNOWLEDGE_GRAPH_DIR.parent / f"knowledge_graph.bak.pr3_{ts}"
    shutil.copytree(KNOWLEDGE_GRAPH_DIR, dst)
    return str(dst)


def _save_json(path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _load_json(path) -> dict | None:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def _validate_members_resolve(graph: dict) -> list[str]:
    ids = {n.get("id") for n in graph.get("nodes", [])}
    return [m for e in graph.get("edges", []) for m in e.get("members", []) if m not in ids]


def _print_stats(label: str, s: dict) -> None:
    print(f"{label}:")
    print(f"  nós por tipo: {s['nos_por_tipo']}  arestas: {s['arestas']}")
    print(f"  Conceito: {s['conceitos_instancias']} instâncias, {s['conceitos_ids_unicos']} ids únicos, "
          f"{s['conceitos_ex_entidade']} ex-Entidade  dims: {s['dims']}")
    print(f"  fan-in médio: {s['fan_in_medio']}  em 1 arquivo só: {s['ids_em_1_arquivo']} ({s['pct_em_1_arquivo']}%)")


def cmd_stats(_args) -> int:
    _print_stats("corpus", corpus_stats(_load_graphs()))
    return 0


def cmd_propose_validation(args) -> int:
    graphs = _load_graphs()
    if not _BASELINE.exists():
        _save_json(_BASELINE, corpus_stats(graphs))
    inv = inventory_concepts(graphs)
    if args.limit:
        inv = dict(list(inv.items())[: args.limit])
    print(f"validando {len(inv)} Conceitos únicos via LLM ({canon_mod.CANON_MODEL})…")
    plan = propose_validation(inv)
    _save_json(_VALIDATION_PLAN, plan)
    verds = {}
    for v in plan.values():
        verds[v["veredicto"]] = verds.get(v["veredicto"], 0) + 1
    cats: dict[str, int] = {}
    for v in plan.values():
        if v["veredicto"] == "descartar":
            c = v.get("categoria", "outro")
            cats[c] = cats.get(c, 0) + 1
    n_dim = sum(1 for v in plan.values() if v["veredicto"] == "manter" and v.get("dim"))
    n_promo = sum(1 for v in plan.values() if v.get("promote"))
    n_fail = sum(1 for v in plan.values() if v.get("fail_open"))
    print(f"vereditos: {verds}")
    print(f"descartes por categoria: {cats}")
    print(f"dims corrigidas: {n_dim}  ex-Entidade promovidos: {n_promo}  fail-open: {n_fail}")
    print(f"plano salvo em {_VALIDATION_PLAN}")
    return 0


def cmd_propose_merges(args) -> int:
    graphs = _load_graphs()
    inv = inventory_concepts(graphs)
    # Merges rodam sobre o corpus PÓS-validação (apply antes de propose-merges).
    # Se o plano de validação existir e ainda não tiver sido aplicado, avisa.
    vplan = _load_json(_VALIDATION_PLAN)
    if vplan:
        pending = [nid for nid, v in vplan.items()
                   if v["veredicto"] != "manter" and nid in inv]
        if pending:
            print(f"⚠ {len(pending)} Conceitos do validation_plan ainda no corpus — "
                  "rode `apply` antes de propose-merges (clustering sobre sobreviventes).",
                  file=sys.stderr)
            return 2
    thr = args.threshold or canon_mod.MERGE_THRESHOLD
    print(f"clustering de {len(inv)} Conceitos (threshold={thr}) + adjudicação LLM…")
    plan = propose_merges(inv, threshold=thr)
    _save_json(_MERGE_PLAN, plan)
    merges = [g for c in plan["clusters"] for g in c["grupos"] if len(g["membros"]) >= 2]
    n_nodes = sum(len(g["membros"]) for g in merges)
    print(f"{len(plan['clusters'])} clusters candidatos → {len(merges)} grupos de merge "
          f"({n_nodes} ids → {len(merges)} canônicos)")
    print(f"plano salvo em {_MERGE_PLAN} — rode sample-merges antes do apply")
    return 0


def cmd_sample_merges(args) -> int:
    plan = _load_json(_MERGE_PLAN)
    if not plan:
        print("sem merge_plan.json — rode propose-merges antes", file=sys.stderr)
        return 1
    merges = []
    for c in plan["clusters"]:
        for g in c["grupos"]:
            if len(g["membros"]) >= 2:
                merges.append((c["score_medio"], g))
    if not merges:
        print("nenhum grupo de merge no plano")
        return 0
    rng = random.Random(args.seed)
    sample = rng.sample(merges, min(args.n, len(merges)))
    print(f"amostra de {len(sample)}/{len(merges)} merges (seed={args.seed}):\n")
    for score, g in sorted(sample, key=lambda x: -x[0]):
        print(f"  [{score:.3f}] → \"{g['nome_canonico']}\" (dim={g.get('dim')})")
        for m in g["membros"]:
            print(f"      • {m}")
    return 0


def cmd_apply(_args) -> int:
    vplan = _load_json(_VALIDATION_PLAN)
    mplan = _load_json(_MERGE_PLAN)
    if not vplan and not mplan:
        print("nenhum plano em canonicalization/ — rode propose-* antes", file=sys.stderr)
        return 1
    canon = build_canon(vplan, mplan)

    graphs = _load_graphs()
    bak = _backup()
    print(f"backup: {bak}")

    st = CanonStats()
    out: dict[str, dict] = {}
    unresolved = 0
    enum_viol = 0
    for fk, g in graphs.items():
        g2 = canonicalize_graph(g, canon, stats=st)
        bad = _validate_members_resolve(g2)
        if bad:
            unresolved += len(bad)
            print(f"  ✗ {fk}: {len(bad)} membros não resolvem (ex.: {bad[:3]})", file=sys.stderr)
        enum_viol += sum(1 for n in g2.get("nodes", []) if validate_v2_node(n))
        out[fk] = g2
    if unresolved:
        print(f"✗ {unresolved} membros não resolvidos — NADA gravado", file=sys.stderr)
        return 2

    _write_graphs(out)
    from core.kg import kg_store
    kg_store.save("concept_canon", canon)
    print(
        f"aplicado a {len(out)} arquivos: descartados={st.discarded} → Ator={st.retyped_ator} "
        f"promovidos={st.promoted} dim corrigida={st.dim_fixed} renomeados={st.renamed} "
        f"dedup intra-arquivo={st.deduped}\n"
        f"  arestas: members dropados={st.dropped_members} arestas degeneradas={st.dropped_edges}"
        + (f"\n  ⚠ {enum_viol} nós com enum fora do schema (§6.4)" if enum_viol else "")
    )
    print("canon publicado via kg_store (`concept_canon`)")
    _print_stats("corpus pós-apply", corpus_stats(out))
    return 0


def cmd_propose_macro(_args) -> int:
    vocab = macro_temas_vocab()
    if not vocab:
        print("WIKI.md sem `macro_temas` no bloco hypergraph_schema (§6.4) — adicione o vocabulário antes.",
              file=sys.stderr)
        return 1
    graphs = _load_graphs()
    print(f"mapeando Oportunidades → macro-temas ({len(vocab)} no vocabulário)…")
    plan = propose_macro_temas(graphs, vocab)
    _save_json(_MACRO_PLAN, plan)
    print(f"{len(plan)} oportunidades com macro_temas — plano salvo em {_MACRO_PLAN}")
    return 0


def cmd_apply_macro(_args) -> int:
    plan = _load_json(_MACRO_PLAN)
    if not plan:
        print("sem macro_plan.json — rode propose-macro antes", file=sys.stderr)
        return 1
    graphs = _load_graphs()
    counts = apply_macro_temas(graphs, plan)
    _write_graphs(graphs)
    print(f"macro_temas escritos em {sum(counts.values())} nós Oportunidade de {len(counts)} arquivos")
    return 0


def cmd_report(_args) -> int:
    base = _load_json(_BASELINE)
    cur = corpus_stats(_load_graphs())
    if base:
        _print_stats("baseline (pré-higiene)", base)
        print()
    _print_stats("atual", cur)
    if base:
        print(f"\nΔ fan-in médio: {base['fan_in_medio']} → {cur['fan_in_medio']}"
              f"  Δ ids únicos: {base['conceitos_ids_unicos']} → {cur['conceitos_ids_unicos']}"
              f"  Δ % 1-arquivo: {base['pct_em_1_arquivo']}% → {cur['pct_em_1_arquivo']}%")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Higiene + canonicalização dos Conceitos (KG v2 PR3).")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("stats")
    p = sub.add_parser("propose-validation")
    p.add_argument("--limit", type=int, default=0, help="só os N primeiros (debug)")
    p = sub.add_parser("propose-merges")
    p.add_argument("--threshold", type=float, default=0.0, help=f"cosseno (default {canon_mod.MERGE_THRESHOLD})")
    p = sub.add_parser("sample-merges")
    p.add_argument("-n", type=int, default=30)
    p.add_argument("--seed", type=int, default=42)
    sub.add_parser("apply")
    sub.add_parser("propose-macro")
    sub.add_parser("apply-macro")
    sub.add_parser("report")
    args = ap.parse_args()

    return {
        "stats": cmd_stats,
        "propose-validation": cmd_propose_validation,
        "propose-merges": cmd_propose_merges,
        "sample-merges": cmd_sample_merges,
        "apply": cmd_apply,
        "propose-macro": cmd_propose_macro,
        "apply-macro": cmd_apply_macro,
        "report": cmd_report,
    }[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
