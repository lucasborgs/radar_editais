"""scripts/split_concepts.py — granularidade atômica dos Conceitos (KG v2 resíduos PR-D).

Spec docs/specs/kg-v2-residuos.md (PR-D). Orquestra core/kg/split_concepts sobre
os hipergrados em disco.

Fluxo:
    python -m scripts.split_concepts stats [--max-words 5]
    python -m scripts.split_concepts propose [--max-words 5] [--limit N]
    python -m scripts.split_concepts apply [--dry-run]
    python -m scripts.split_concepts report
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime

from dotenv import load_dotenv

load_dotenv()

from config import KNOWLEDGE_GRAPH_DIR
from core.kg import split_concepts as split_mod

_HYPERGRAPHS_DIR = KNOWLEDGE_GRAPH_DIR / "hypergraphs"
_PLANS_DIR = KNOWLEDGE_GRAPH_DIR / "canonicalization"
_SPLIT_PLAN = _PLANS_DIR / "split_plan.json"


def _load_graphs() -> dict[str, dict]:
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
    dst = KNOWLEDGE_GRAPH_DIR.parent / f"knowledge_graph.bak.prd_{ts}"
    shutil.copytree(KNOWLEDGE_GRAPH_DIR, dst)
    return str(dst)


def _save_json(path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _load_json(path) -> dict | None:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def cmd_stats(args) -> int:
    graphs = _load_graphs()
    inv = split_mod.inventory_long_concepts(graphs, max_words=args.max_words)
    total_conceitos = sum(
        1 for _, g in graphs.items()
        for n in g.get("nodes", [])
        if n.get("type") == "Conceito"
    )
    print(f"total de Conceitos no corpus: {total_conceitos}")
    print(f"com ≥{args.max_words} palavras: {len(inv)}")
    if inv:
        longest = sorted(inv.values(), key=lambda x: -x["word_count"])[:10]
        print("\ntop 10 mais longos:")
        for c in longest:
            print(f"  [{c['word_count']:2d}w] {c['name']}  (dim={c['dim']}, fan_in={c['fan_in']})")
    return 0


def cmd_propose(args) -> int:
    graphs = _load_graphs()
    inv = split_mod.inventory_long_concepts(graphs, max_words=args.max_words)
    if args.limit:
        inv = dict(list(inv.items())[:args.limit])
    print(f"decompondo {len(inv)} Conceitos via LLM ({split_mod.SPLIT_MODEL})…")
    plan = split_mod.propose_splits(inv)
    _save_json(_SPLIT_PLAN, plan)
    n_split = sum(1 for v in plan.values() if len(v) >= 2)
    n_total_new = sum(len(v) for v in plan.values())
    print(f"plano salvo em {_SPLIT_PLAN}")
    print(f"  {n_split} conceitos decompostos → {n_total_new} termos atômicos")
    return 0


def cmd_apply(args) -> int:
    graphs = _load_graphs()
    plan = _load_json(_SPLIT_PLAN)
    if not plan:
        print("sem split_plan.json — rode propose antes", file=sys.stderr)
        return 1

    if args.dry_run:
        print("── dry-run ──")
        _, st = split_mod.apply_splits(graphs, plan)
        for k, v in st.items():
            print(f"  {k}: {v}")
        print("nenhum arquivo modificado (--dry-run)")
        return 0

    bak = _backup()
    print(f"backup: {bak}")

    graphs, st = split_mod.apply_splits(graphs, plan)
    print(f"splits aplicados: {st['conceitos_split']} divididos, "
          f"{st['conceitos_criados']} criados, "
          f"{st['arestas_reatadas']} arestas reatadas, "
          f"{st['arestas_removidas']} arestas removidas")

    graphs = split_mod.canonicalize_after_split(graphs)
    print("re-validação contra concept_canon (PR-B) aplicada")

    _write_graphs(graphs)
    print(f"escritos {len(graphs)} hipergrados")
    return 0


def cmd_report(_args) -> int:
    graphs = _load_graphs()
    inv = split_mod.inventory_long_concepts(graphs)
    total = sum(
        1 for _, g in graphs.items()
        for n in g.get("nodes", [])
        if n.get("type") == "Conceito"
    )
    n5w = len(inv)
    n5w_pct = round(100 * n5w / max(total, 1), 1)
    print(f"após splits: {total} Conceitos, {n5w} com ≥5 palavras ({n5w_pct}%)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Granularidade atômica dos Conceitos (KG v2 PR-D).")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("stats")
    p.add_argument("--max-words", type=int, default=5)

    p = sub.add_parser("propose")
    p.add_argument("--max-words", type=int, default=5)
    p.add_argument("--limit", type=int, default=0, help="só os N primeiros (debug)")

    p = sub.add_parser("apply")
    p.add_argument("--dry-run", action="store_true")

    sub.add_parser("report")

    args = ap.parse_args()
    return {
        "stats": cmd_stats,
        "propose": cmd_propose,
        "apply": cmd_apply,
        "report": cmd_report,
    }[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
