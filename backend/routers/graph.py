"""Knowledge graph público: visualização (sem auth — vitrine do Dashboard)."""

from __future__ import annotations

from fastapi import APIRouter

from backend.common import graph_service

router = APIRouter(tags=["graph"])


@router.get("/graph", summary="Nós + arestas do knowledge graph (público)")
def get_graph():
    """Grafo Obsidian-style do catálogo: editais, temas e públicos-alvo.

    Público — alimenta a visualização do Dashboard sem exigir login.
    """
    return graph_service.get_graph()
