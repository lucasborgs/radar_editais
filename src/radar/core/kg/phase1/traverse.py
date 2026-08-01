"""core/kg/phase1/traverse.py — BFS multi-salto + dedução de caminho.

Reuso VALIDADO da spike (`src/radar/core/kg/spike/traverse.py`, sem nenhuma
dependência da spike): travessia em processo sobre as arestas da projeção
(barata no tamanho do grafo, ~2.8k nós). Funções PURAS sobre listas de arestas —
testáveis sem DB. Direção não importa para vizinhança/comunidade (postura de
`entity_catalog._bfs_edges`); a serialização (futura, no Explorar) preserva a
direção real.
"""
from __future__ import annotations

from collections import defaultdict, deque
from typing import Any


def _adjacency_refs(edges: list[dict[str, Any]], min_weight: float = 0.0) -> dict[str, list[dict[str, Any]]]:
    """{node_id: [arestas incidentes]} — não-direcionado. Preserva o dict ORIGINAL
    (direção, weight, properties). Arestas com weight abaixo de `min_weight` não
    participam da travessia (ex.: hub `setor:multissetorial`, que recebe
    weight=0.1 no build — opção 4 da SPEC do spike)."""
    adj: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for e in edges:
        if (e.get("weight") or 1.0) < min_weight:
            continue
        adj[e["source_id"]].append(e)
        adj[e["target_id"]].append(e)
    return adj


def bfs_edges(
    edges: list[dict[str, Any]], seed: str, depth: int = 1, *,
    min_weight: float = 0.0,
) -> list[dict[str, Any]]:
    """Arestas alcançáveis de `seed` até `depth` saltos (não-direcionado,
    cycle-safe). Retorna as arestas ORIGINAIS (preserva direção/weight)."""
    depth = max(1, int(depth))
    adj = _adjacency_refs(edges, min_weight=min_weight)
    visited: set[str] = {seed}
    frontier = {seed}
    seen: set[tuple[str, str, str]] = set()
    collected: list[dict[str, Any]] = []
    for _ in range(depth):
        nxt: set[str] = set()
        for node in frontier:
            for e in adj.get(node, []):
                pair = (e["source_id"], e["target_id"], e["type"])
                if pair in seen or (e["target_id"], e["source_id"], e["type"]) in seen:
                    continue
                seen.add(pair)
                collected.append(e)
                other = e["target_id"] if e["source_id"] == node else e["source_id"]
                if other not in visited:
                    nxt.add(other)
        visited |= nxt
        frontier = nxt
        if not frontier:
            break
    return collected


def find_paths(
    edges: list[dict[str, Any]],
    start: str,
    goal: str,
    *,
    max_depth: int = 4,
    limit: int = 5,
    min_weight: float = 0.0,
) -> list[list[tuple[str, str, str]]]:
    """Caminhos de `start` a `goal` (BFS por largura, nó não pode repetir).
    Cada caminho é uma lista de passos (source, tipo, target) na direção
    PERCORRIDA — contíguos para leitura (dedução de caminho)."""
    adj = _adjacency_refs(edges, min_weight=min_weight)
    if start not in adj or goal not in adj:
        return []

    found: list[list[tuple[str, str, str]]] = []
    queue: deque[tuple[str, list[tuple[str, str, str]], frozenset]] = deque(
        [(start, [], frozenset({start}))]
    )
    while queue and len(found) < limit:
        node, path, visited = queue.popleft()
        if len(path) >= max_depth:
            continue
        for e in adj.get(node, []):
            other = e["target_id"] if e["source_id"] == node else e["source_id"]
            step = (node, e["type"], other)
            if other == goal:
                found.append([*path, step])
                if len(found) >= limit:
                    break
                continue
            if other in visited:
                continue
            queue.append((other, [*path, step], visited | {other}))
    return found


def reachable_within(
    edges: list[dict[str, Any]], seed: str, max_depth: int = 2, *,
    min_weight: float = 0.0,
) -> set[str]:
    """Nós alcançáveis até `max_depth` saltos (subgrafo da vizinhança)."""
    depth = max(1, int(max_depth))
    adj = _adjacency_refs(edges, min_weight=min_weight)
    visited: set[str] = {seed}
    frontier = {seed}
    for _ in range(depth):
        nxt: set[str] = set()
        for node in frontier:
            for e in adj.get(node, []):
                other = e["target_id"] if e["source_id"] == node else e["source_id"]
                if other not in visited:
                    visited.add(other)
                    nxt.add(other)
        frontier = nxt
        if not frontier:
            break
    return visited


def filter_predicate(edges: list[dict[str, Any]], predicate: str) -> list[dict[str, Any]]:
    """Filtra arestas por tipo. `predicate` exato."""
    return [e for e in edges if e["type"] == predicate]
