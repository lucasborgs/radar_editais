#!/usr/bin/env python3
"""Rebuild determinístico dos catálogos curados (KG v2, PR4.1).

Reescreve `hypergraphs/investidores.json` e `hypergraphs/programas.json` a partir
dos JSONs curados, preservando as facetas estruturadas + URL por item e aplicando
o desdobramento D2 (investidor → Ator + Oportunidade(investimento)). SEM LLM.

FAÇA BACKUP de data/knowledge_graph/ antes (padrão da casa).

Uso:
    python -m scripts.rebuild_curadoria --dry   # só relata
    python -m scripts.rebuild_curadoria          # escreve in-place
"""
from __future__ import annotations

import argparse
import logging
from collections import Counter

from dotenv import load_dotenv

load_dotenv()

from core.kg.curadoria_build import build_curated_graphs  # noqa: E402
from core.retrieval.hyper_extractor import HYPERGRAPHS_DIR, _write_hypergraph  # noqa: E402

logger = logging.getLogger(__name__)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description="Rebuild determinístico dos curados (PR4.1)")
    ap.add_argument("--dry", action="store_true", help="só relata, não escreve")
    args = ap.parse_args()

    graphs = build_curated_graphs()
    mode = "DRY-RUN" if args.dry else "APLICADO"
    print(f"\n[{mode}] rebuild determinístico dos catálogos curados")
    for fk, g in graphs.items():
        tc = Counter((n.get("type"), n.get("kind") or n.get("dim")) for n in g["nodes"])
        offers = sum(1 for n in g["nodes"]
                     if n.get("type") == "Oportunidade" and n.get("kind") in ("investimento", "programa"))
        with_url = sum(1 for n in g["nodes"] if n.get("url"))
        print(f"  {fk}: {len(g['nodes'])} nós, {len(g['edges'])} arestas, "
              f"{offers} Oportunidades, {with_url} c/ url")
        for k, c in tc.most_common():
            print(f"      {k}: {c}")
        if not args.dry:
            path = HYPERGRAPHS_DIR / f"{fk}.json"
            _write_hypergraph(path, g)
            print(f"      → {path}")


if __name__ == "__main__":
    main()
