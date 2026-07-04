"""scripts/migrate_hypergraphs_v2.py — reescreve os hipergrados para o schema v2.

Spec docs/specs/kg-redesign.md. Aplica `core.kg.migrate_v2.migrate_to_v2` a cada
`data/knowledge_graph/hypergraphs/*.json`, em dois passos idempotentes:
  • FORMATO (PR1): `id` por nó, `members` de name→id, `format_version: 2`;
  • TIPOS (PR2): consolida os 12 tipos v1 em Oportunidade/Ator/Conceito, folda
    Fonte/Mecanismo/Requisito/Exclusão em propriedades, remap dos prefixos de id.

Idempotente: arquivos já totalmente-v2 são pulados. Valida, ao final, que TODO
membro de aresta resolve para um id existente.

Uso:
    python -m scripts.migrate_hypergraphs_v2            # reescreve in-place
    python -m scripts.migrate_hypergraphs_v2 --dry-run  # só relatório, não grava

NÃO versionado: os arquivos hypergraphs/ são gitignored (durabilidade via
kg_store/Postgres). Em prod, o blob é republicado pelo build (kg_store.save_
hypergraphs) — este script normaliza os artefatos locais/de dev.
"""
from __future__ import annotations

import argparse
import json
import sys

from config import KNOWLEDGE_GRAPH_DIR
from core.kg.migrate_v2 import MigrationStats, is_types_v2, is_v2, migrate_to_v2
from core.kg.schema import validate_v2_node

_HYPERGRAPHS_DIR = KNOWLEDGE_GRAPH_DIR / "hypergraphs"


def _validate_members_resolve(graph: dict) -> list[str]:
    """Devolve a lista de membros que NÃO resolvem para um id de nó (deve ser
    vazia após a migração)."""
    ids = {n.get("id") for n in graph.get("nodes", [])}
    bad: list[str] = []
    for e in graph.get("edges", []):
        for m in e.get("members", []):
            if m not in ids:
                bad.append(m)
    return bad


def main() -> int:
    ap = argparse.ArgumentParser(description="Migra hipergrados para o formato v2 (PR1).")
    ap.add_argument("--dry-run", action="store_true", help="não grava; só relatório")
    args = ap.parse_args()

    files = sorted(_HYPERGRAPHS_DIR.glob("*.json"))
    if not files:
        print(f"nenhum hipergrado em {_HYPERGRAPHS_DIR}", file=sys.stderr)
        return 1

    total = MigrationStats()
    migrated = skipped = 0
    unresolved_total = 0
    enum_violations = 0

    for path in files:
        graph = json.loads(path.read_text(encoding="utf-8"))
        if is_v2(graph) and is_types_v2(graph):
            skipped += 1
            continue
        st = MigrationStats()
        v2 = migrate_to_v2(graph, stats=st)

        bad = _validate_members_resolve(v2)
        if bad:
            unresolved_total += len(bad)
            print(f"  ✗ {path.name}: {len(bad)} membros NÃO resolvem (ex.: {bad[:3]})",
                  file=sys.stderr)

        # Sanity de enums v2 (§6.4 do WIKI) — loga violações, não bloqueia.
        viols = [v for n in v2.get("nodes", []) if (v := validate_v2_node(n))]
        if viols:
            enum_violations += len(viols)
            print(f"  ⚠ {path.name}: {len(viols)} nós com enum fora do schema (ex.: {viols[:2]})",
                  file=sys.stderr)

        if not args.dry_run:
            path.write_text(
                json.dumps(v2, indent=2, ensure_ascii=False), encoding="utf-8"
            )
        migrated += 1
        for f in ("nodes", "edges_in", "edges_out", "dropped_members",
                  "dropped_edges", "folded_nodes", "reclassified_entidade"):
            setattr(total, f, getattr(total, f) + getattr(st, f))

    mode = "DRY-RUN (nada gravado)" if args.dry_run else "gravado"
    print(
        f"\n{mode}: {migrated} migrados, {skipped} já-v2 pulados de {len(files)} arquivos\n"
        f"  nós={total.nodes}  arestas {total.edges_in}→{total.edges_out}\n"
        f"  members dropados={total.dropped_members}  arestas degeneradas dropadas={total.dropped_edges}\n"
        f"  facetas foldadas (Fonte/Mecanismo/Requisito/Exclusão)={total.folded_nodes}  "
        f"Entidade reclassificadas={total.reclassified_entidade}"
        + (f"\n  ⚠ {enum_violations} nós com enum fora do schema v2 (§6.4)" if enum_violations else "")
    )
    if unresolved_total:
        print(f"  ✗ {unresolved_total} membros não resolvidos — FALHA", file=sys.stderr)
        return 2
    print("  ✓ todo membro resolve para um id existente")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
