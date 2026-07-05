"""scripts/resolve_programas.py — resolução de menções de programa (KG v2 resíduos PR-C).

Spec docs/specs/kg-v2-residuos.md (PR-C). Orquestra core/kg/resolve_programas
sobre os hipergrados em disco.

Fluxo:
    python -m scripts.resolve_programas stats                 # métricas dos nós programa
    python -m scripts.resolve_programas propose               # cluster + resolve → proposta
    python -m scripts.resolve_programas sample -n 15          # amostra p/ auditoria
    python -m scripts.resolve_programas apply --dry-run        # simula sem escrever
    python -m scripts.resolve_programas apply                 # reescreve grafos + canon
    python -m scripts.resolve_programas queue                 # lista promovidos_auto
    python -m scripts.resolve_programas report                # antes/depois

`apply` é idempotente: recompila o canon de proposta existente e o aplica.
Backup automático antes de reescrever.
"""
from __future__ import annotations

import argparse
import json
import random
import shutil
import sys
from datetime import datetime, timezone

from dotenv import load_dotenv

load_dotenv()

from config import KNOWLEDGE_GRAPH_DIR
from core.kg import kg_store
from core.kg import resolve_programas as resolv_mod

_HYPERGRAPHS_DIR = KNOWLEDGE_GRAPH_DIR / "hypergraphs"
_PLANS_DIR = KNOWLEDGE_GRAPH_DIR / "canonicalization"
_PROPOSAL = _PLANS_DIR / "programa_resolution_proposal.json"


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
    dst = KNOWLEDGE_GRAPH_DIR.parent / f"knowledge_graph.bak.prc_{ts}"
    shutil.copytree(KNOWLEDGE_GRAPH_DIR, dst)
    return str(dst)


def _save_json(path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def _load_json(path) -> dict | None:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def _print_stats(label: str, s: dict) -> None:
    print(f"{label}:")
    for k, v in s.items():
        print(f"  {k}: {v}")


def cmd_stats(_args) -> int:
    graphs = _load_graphs()
    _print_stats("programas no corpus", resolv_mod.corpus_programa_stats(graphs))
    inv = resolv_mod.inventory_programas(graphs)
    print(f"  names únicos: {len(inv)}")
    # Conta lixo óbvio
    lixo = sum(1 for k in inv if resolv_mod._is_obvious_trash(k))
    print(f"  lixo óbvio ('programa' nu): {lixo}")
    return 0


def cmd_propose(args) -> int:
    graphs = _load_graphs()
    inv = resolv_mod.inventory_programas(graphs)
    print(f"inventário: {len(inv)} names únicos de programa")

    clusters = resolv_mod.cluster_programas(inv)
    n_mergeable = sum(1 for c in clusters if len(c) >= 2)
    print(f"clusterização: {len(clusters)} grupos ({n_mergeable} com ≥2 membros)")

    registry = kg_store.load_programas()
    print(f"registro curado: {len(registry)} programas")

    resolutions = resolv_mod.resolve_clusters(
        clusters, inv, registry,
        model=resolv_mod.PROG_RESOLVE_MODEL,
    )
    n_curado = sum(1 for r in resolutions if r["status"] == "curado")
    n_promovido = sum(1 for r in resolutions if r["status"] == "promovido_auto")
    print(f"resolução: {n_curado} curados, {n_promovido} promovidos_auto, "
          f"{len(resolutions)} total")

    proposal = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model": resolv_mod.PROG_RESOLVE_MODEL,
        "stats": {"curados": n_curado, "promovidos_auto": n_promovido, "total": len(resolutions)},
        "resolutions": resolutions,
        "inventory": {k: {"name": v["name"], "fan_in": v["fan_in"]} for k, v in inv.items()},
    }
    _save_json(_PROPOSAL, proposal)
    print(f"proposta salva em {_PROPOSAL}")
    return 0


def cmd_sample(args) -> int:
    proposal = _load_json(_PROPOSAL)
    if not proposal:
        print("sem proposta — rode propose antes", file=sys.stderr)
        return 1
    resolutions = proposal.get("resolutions", [])
    if not resolutions:
        print("nenhuma resolução na proposta")
        return 0
    rng = random.Random(args.seed)
    sample = rng.sample(resolutions, min(args.n, len(resolutions)))
    print(f"amostra de {len(sample)}/{len(resolutions)} resoluções (seed={args.seed}):\n")
    for r in sample:
        status_tag = "✓" if r["status"] == "curado" else "→"
        rid = r.get("registry_id", "") or ""
        rname = r.get("registry_name", "") or ""
        print(f"  {status_tag} [{r['status']:16s}] \"{r['canon_name']}\"")
        print(f"      id: {rid}  nome: {rname}")
        for m in r.get("membros", []):
            print(f"      · {m}")
    return 0


def cmd_apply(args) -> int:
    graphs = _load_graphs()
    proposal = _load_json(_PROPOSAL)
    if not proposal:
        print("sem proposta — rode propose antes", file=sys.stderr)
        return 1

    canon = resolv_mod.build_canon(proposal["resolutions"])
    if args.dry_run:
        print("── dry-run ──")
        print(f"  canon: {len(canon['aliases'])} aliases, "
              f"{len(canon['curados'])} curados, "
              f"{len(canon['promovidos_auto'])} promovidos_auto")
        _, stats = resolv_mod.apply(graphs, canon)
        for k, v in stats.items():
            print(f"  {k}: {v}")
        print("nenhum arquivo modificado (--dry-run)")
        return 0

    bak = _backup()
    print(f"backup: {bak}")

    graphs_mod, stats = resolv_mod.apply(graphs, canon)
    _write_graphs(graphs_mod)
    from core.kg import kg_store
    kg_store.save("programa_canon", canon)

    print(f"aplicado a {len(graphs_mod)} arquivos:")
    for k, v in stats.items():
        print(f"  {k}: {v}")
    print("canon publicado via kg_store (`programa_canon`)")

    n_queue = len(resolv_mod.queue_unresolved(proposal["resolutions"]))
    if n_queue:
        print(f"\n⚠ {n_queue} programas promovidos_auto na fila de curadoria "
              "(rode `queue` para listar)")
    return 0


def cmd_queue(_args) -> int:
    proposal = _load_json(_PROPOSAL)
    if not proposal:
        print("sem proposta — rode propose antes", file=sys.stderr)
        return 1
    fila = resolv_mod.queue_unresolved(proposal["resolutions"])
    if not fila:
        print("nenhum programa promovido_auto — fila vazia")
        return 0
    print(f"fila de curadoria ({len(fila)} programas promovidos_auto):\n")
    for r in fila:
        print(f"  {r['registry_id']:40s} \"{r['canon_name']}\"")
        print(f"  {'':40s} membros: {len(r['membros'])} variações")
    return 0


def cmd_report(_args) -> int:
    graphs = _load_graphs()
    cur = resolv_mod.corpus_programa_stats(graphs)
    _print_stats("programas no corpus (após apply)", cur)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Resolução de menções de programa (KG v2 PR-C).")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("stats")
    sub.add_parser("propose")
    p = sub.add_parser("sample")
    p.add_argument("-n", type=int, default=15)
    p.add_argument("--seed", type=int, default=42)
    p = sub.add_parser("apply")
    p.add_argument("--dry-run", action="store_true", help="simula sem escrever")
    sub.add_parser("queue")
    sub.add_parser("report")

    args = ap.parse_args()
    return {
        "stats": cmd_stats,
        "propose": cmd_propose,
        "sample": cmd_sample,
        "apply": cmd_apply,
        "queue": cmd_queue,
        "report": cmd_report,
    }[args.cmd](args)


if __name__ == "__main__":
    raise SystemExit(main())
