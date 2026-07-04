#!/usr/bin/env python3
"""Backfill de proveniência (PR4) nos hipergrados de edital já migrados.

Os 32 arquivos de edital foram migrados MECANICAMENTE (PR1-3), sem re-extração
— então carregam `proveniencia: {}`. Este passe encana a URL oficial + PDFs +
data de coleta a partir do BRONZE (via adapter da fonte), POR FORA do LLM
(D14/D15): transformação determinística, não re-extrai nada.

Arquivos de CATÁLOGO (programas/investidores/ict — sem `__` no nome) ficam de
fora: cada item tem URL própria no curado, encanada na PR4.1 (rebuild
determinístico dos curados). Ficam listados como pendência (spec §PR4).

Uso:
    python -m scripts.backfill_proveniencia          # aplica in-place
    python -m scripts.backfill_proveniencia --dry    # só relata cobertura
"""
from __future__ import annotations

import argparse
import json
import logging

from config import KNOWLEDGE_GRAPH_DIR
from core.retrieval.hyper_extractor import _provenance

logger = logging.getLogger(__name__)

HYPERGRAPHS_DIR = KNOWLEDGE_GRAPH_DIR / "hypergraphs"


def _has_edital(graph: dict) -> bool:
    return any(
        n.get("type") == "Oportunidade" and n.get("kind") == "edital"
        for n in graph.get("nodes", [])
    )


def backfill(*, dry: bool = False) -> dict:
    """Preenche `proveniencia` nos arquivos de edital. Retorna estatísticas."""
    editais = curados = filled = sem_url = 0
    curados_pendentes: list[str] = []
    for path in sorted(HYPERGRAPHS_DIR.glob("*.json")):
        fk = path.stem
        try:
            graph = json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            logger.warning("backfill: JSON inválido %s: %s", path.name, e)
            continue

        if "__" not in fk:  # catálogo curado — pendência da PR4.1
            if _has_edital(graph) or graph.get("nodes"):
                curados += 1
                curados_pendentes.append(fk)
            continue
        if not _has_edital(graph):
            continue

        editais += 1
        source, _, native = fk.partition("__")
        prov = _provenance(source, native)
        if prov.get("url"):
            filled += 1
        else:
            sem_url += 1
            logger.warning("backfill: sem URL no bronze para %s", fk)
        if prov and not dry:
            graph["proveniencia"] = prov
            path.write_text(
                json.dumps(graph, ensure_ascii=False, indent=2), encoding="utf-8"
            )

    return {
        "editais": editais,
        "com_url": filled,
        "sem_url": sem_url,
        "curados_pendentes": curados_pendentes,
    }


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description="Backfill de proveniência (PR4)")
    ap.add_argument("--dry", action="store_true", help="só relata, não escreve")
    args = ap.parse_args()

    stats = backfill(dry=args.dry)
    mode = "DRY-RUN" if args.dry else "APLICADO"
    print(f"\n[{mode}] proveniência backfill")
    print(f"  editais: {stats['editais']}")
    print(f"  com proveniencia.url: {stats['com_url']}")
    print(f"  sem URL no bronze: {stats['sem_url']}")
    cov = 100.0 * stats["com_url"] / stats["editais"] if stats["editais"] else 0.0
    print(f"  cobertura: {cov:.1f}%")
    if stats["curados_pendentes"]:
        print(
            f"  curados legados sem proveniência de arquivo (PR4.1): "
            f"{', '.join(stats['curados_pendentes'])}"
        )


if __name__ == "__main__":
    main()
