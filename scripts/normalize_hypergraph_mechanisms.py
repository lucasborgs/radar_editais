"""Aplica _normalize_mecanismo_nodes + _normalize_fonte_nodes em todos os
hipergrados existentes.

Uso:
    python -m scripts.normalize_hypergraph_mechanisms

Sem necessidade de LLM — apenas limpeza pós-hoc dos nós Mecanismo e Fonte já
extraídos. Modifica os arquivos in-place (com backup em .bak).
"""

import json
import shutil

from config import KNOWLEDGE_GRAPH_DIR
from core.retrieval.hyper_extractor import (
    _normalize_fonte_nodes,
    _normalize_mecanismo_nodes,
)

HYPERGRAPHS_DIR = KNOWLEDGE_GRAPH_DIR / "hypergraphs"


def main() -> None:
    changed = 0
    for path in sorted(HYPERGRAPHS_DIR.glob("*.json")):
        with open(path) as f:
            data = json.load(f)

        orig_nodes = len(data.get("nodes", []))
        orig_edges = len(data.get("edges", []))

        nodes, edges = _normalize_mecanismo_nodes(
            data.get("nodes", []), data.get("edges", []),
        )
        nodes, edges = _normalize_fonte_nodes(nodes, edges)

        if len(nodes) == orig_nodes and len(edges) == orig_edges:
            continue

        shutil.copy2(path, path.with_suffix(".json.bak"))
        data["nodes"] = nodes
        data["edges"] = edges
        with open(path, "w") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        removed_nodes = orig_nodes - len(nodes)
        removed_edges = orig_edges - len(edges)
        print(
            f"  {path.name}: {orig_nodes}→{len(nodes)} nós"
            f" (-{removed_nodes}), {orig_edges}→{len(edges)} arestas"
            f" (-{removed_edges})"
        )
        changed += 1

    print(f"\n{changed} arquivos alterados.")


if __name__ == "__main__":
    main()
