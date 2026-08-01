"""core/kg/phase1/features.py — features de grafo (Louvain, centralidade).

Algorítmico, sem aprendizado — na escala da projeção (~2.8k nós) tudo é
trivial. Comunidades via Louvain do networkx (dep opcional `[graph]`); sem o
pacote, comunidades são PULADAS (counts=0) e centralidade devolve {} — o build
segue saudável (features são enriquecimento, não contrato). Funções PURAS sobre
listas de arestas (testáveis sem DB).
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def _networkx_available() -> bool:
    try:
        import networkx  # noqa: F401
        return True
    except ImportError:
        return False


def build_graph(edges: list[dict[str, Any]]) -> Any:
    """Grafo networkx NÃO-direcionado a partir das arestas (em memória)."""
    import networkx as nx

    g = nx.Graph()
    for e in edges:
        g.add_edge(e["source_id"], e["target_id"], type=e["type"], weight=e["weight"])
    return g


def detect_communities(
    edges: list[dict[str, Any]], *, seed: int = 42
) -> list[tuple[str, list[str]]]:
    """[(community_id, [node_id, ...])] — Louvain sobre as arestas DADAS.

    Puro: recebe arestas, devolve as comunidades — sem DB. Retorna [] se o grafo
    está vazio ou o networkx não está disponível. Mesmo seed da spike (42) →
    saída determinística para o mesmo conjunto de arestas."""
    if not edges:
        return []
    if not _networkx_available():
        logger.warning("kg_phase1: networkx ausente — comunidades puladas (extra [graph])")
        return []
    import networkx as nx

    g = build_graph(edges)
    if g.number_of_edges() == 0:
        return []
    communities = nx.algorithms.community.louvain_communities(g, seed=seed)
    return [(f"com_{idx}", sorted(members)) for idx, members in enumerate(communities)]


def node_stats(edges: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Grau e centralidade por nó (diagnóstico, NÃO persistido). {} sem networkx
    ou com grafo vazio."""
    if not edges or not _networkx_available():
        return {}
    import networkx as nx

    g = build_graph(edges)
    if g.number_of_nodes() == 0:
        return {}
    degree = dict(g.degree())
    centrality = nx.degree_centrality(g)
    return {
        node: {"degree": degree[node], "centrality": round(centrality[node], 4)}
        for node in g.nodes()
    }


def stored_node_stats(
    generation_id: int | None = None, *, conn: Any = None
) -> dict[str, dict[str, Any]]:
    """Centralidade/grau da GERAÇÃO armazenada (corrente por default).

    Conveniência para leitores: lê as arestas da geração via `store.load_edges`
    e computa em processo. Sem networkx ou sem geração → {} (centralidade é
    "quando disponível")."""
    from radar.core.kg.phase1 import store

    edges = store.load_edges(generation_id=generation_id, conn=conn)
    return node_stats(edges)
