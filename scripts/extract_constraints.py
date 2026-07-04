#!/usr/bin/env python3
"""Produz `constraints[]` de elegibilidade dura (PR5) sobre o corpus migrado.

Passe LLM de build (irmão de `canonicalize_concepts.py`): para cada
Oportunidade(edital) com `requisitos_texto`/`exclusoes_texto`, chama o produtor
(`core/kg/constraints_producer.py`) e grava as `constraints[]` estruturadas
in-place. O corpus foi migrado MECANICAMENTE (PR2) — os requisitos já estão
foldados num nó só, prontos para estruturar.

FAÇA BACKUP de data/knowledge_graph/ antes (padrão da casa).

Uso:
    python -m scripts.extract_constraints --dry     # só relata o que produziria
    python -m scripts.extract_constraints           # aplica in-place
    python -m scripts.extract_constraints --overwrite  # re-produz mesmo os já preenchidos
"""
from __future__ import annotations

import argparse
import json
import logging

from dotenv import load_dotenv

load_dotenv()

from config import KNOWLEDGE_GRAPH_DIR  # noqa: E402
from core.kg.constraints_producer import extract_constraints  # noqa: E402

logger = logging.getLogger(__name__)

HYPERGRAPHS_DIR = KNOWLEDGE_GRAPH_DIR / "hypergraphs"


def _editais(graph: dict) -> list[dict]:
    return [
        n for n in graph.get("nodes", [])
        if n.get("type") == "Oportunidade" and n.get("kind") == "edital"
    ]


def run(*, dry: bool = False, overwrite: bool = False) -> dict:
    files = sorted(HYPERGRAPHS_DIR.glob("*__*.json"))  # só editais (1 arquivo=1 edital)
    n_editais = com_texto = com_constraints = total_constraints = 0
    samples: list[str] = []

    for path in files:
        try:
            graph = json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            logger.warning("extract_constraints: JSON inválido %s: %s", path.name, e)
            continue

        changed = False
        for node in _editais(graph):
            n_editais += 1
            reqs = node.get("requisitos_texto") or []
            excs = node.get("exclusoes_texto") or []
            if not reqs and not excs:
                continue
            com_texto += 1
            if node.get("constraints") and not overwrite:
                continue
            cons = extract_constraints(reqs, excs)
            if not cons:
                continue
            com_constraints += 1
            total_constraints += len(cons)
            if len(samples) < 12:
                samples.append(f"{path.stem}: {json.dumps(cons, ensure_ascii=False)}")
            if not dry:
                node["constraints"] = cons
                changed = True

        if changed and not dry:
            path.write_text(json.dumps(graph, ensure_ascii=False, indent=2), encoding="utf-8")

    return {
        "editais": n_editais,
        "com_texto_residual": com_texto,
        "com_constraints": com_constraints,
        "total_constraints": total_constraints,
        "samples": samples,
    }


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description="Produtor de constraints de elegibilidade (PR5)")
    ap.add_argument("--dry", action="store_true", help="só relata, não escreve")
    ap.add_argument("--overwrite", action="store_true", help="re-produz nós já com constraints")
    args = ap.parse_args()

    stats = run(dry=args.dry, overwrite=args.overwrite)
    mode = "DRY-RUN" if args.dry else "APLICADO"
    print(f"\n[{mode}] produtor de constraints de elegibilidade")
    print(f"  editais: {stats['editais']}")
    print(f"  com texto residual (req/excl): {stats['com_texto_residual']}")
    print(f"  com constraints produzidas: {stats['com_constraints']}")
    print(f"  total de constraints: {stats['total_constraints']}")
    if stats["samples"]:
        print("  amostras:")
        for s in stats["samples"]:
            print(f"    • {s}")


if __name__ == "__main__":
    main()
