"""core/kg/spike/features.py — features de grafo (grau, centralidade, Louvain).

SPEC §8.4. Algorítmico, sem aprendizado: na escala do spike (~550 nós) tudo é
trivial. Comunidades via Louvain do networkx (dep opcional `[spike]`).
"""
from __future__ import annotations

import logging
from typing import Any

from radar.core.kg.spike.graph_store import SCHEMA, connect

logger = logging.getLogger(__name__)


def build_graph() -> Any:
    """Grafo networkx a partir de `kg_spike.edges` (arestas não-direcionadas para
    detecção de comunidade — mesmo critério do BFS de `entity_catalog`)."""
    import networkx as nx

    from radar.core.kg.spike import graph_store

    edges = graph_store.load_edges()
    g = nx.Graph()
    for e in edges:
        g.add_edge(e["source_id"], e["target_id"], type=e["type"], weight=e["weight"])
    return g


def run_communities() -> int:
    """Detecção Louvain → `kg_spike.communities`. Retorna nº de comunidades."""
    import networkx as nx


    g = build_graph()
    if g.number_of_edges() == 0:
        logger.warning("kg_spike: grafo vazio — sem comunidades")
        return 0

    communities = nx.algorithms.community.louvain_communities(g, seed=42)
    rows = [
        (f"com_{idx}", node)
        for idx, members in enumerate(communities)
        for node in members
    ]
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(f"truncate {SCHEMA}.communities")
            cur.executemany(
                f"insert into {SCHEMA}.communities (community_id, node_id) values (%s, %s)",
                rows,
            )
    logger.info("kg_spike: %d comunidades detectadas", len(communities))
    return len(communities)


def node_stats() -> dict[str, dict[str, Any]]:
    """Grau e centralidade por nó (para diagnósticos/tools, não persistido)."""
    import networkx as nx

    g = build_graph()
    if g.number_of_nodes() == 0:
        return {}
    degree = dict(g.degree())
    centrality = nx.degree_centrality(g)
    return {
        node: {"degree": degree[node], "centrality": round(centrality[node], 4)}
        for node in g.nodes()
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print("comunidades:", run_communities())
