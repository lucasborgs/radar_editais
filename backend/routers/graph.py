"""Knowledge graph público: visualização + chat exploratório (sem auth —
vitrine do Dashboard)."""

from __future__ import annotations

import os

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from backend.common import kg_service
from backend.rate_limit import limiter

router = APIRouter(tags=["graph"])


class KGExploreRequest(BaseModel):
    message: str
    history: list[dict] = []
    edital_ids: list[str] = []
    node_id: str | None = None
    node_type: str | None = None


@router.get("/graph", summary="Nós + arestas do knowledge graph (público)")
def get_graph():
    """Grafo Obsidian-style do catálogo: editais, temas e públicos-alvo.

    Público — alimenta a visualização do Dashboard sem exigir login.
    """
    return kg_service.get_graph()


@router.post("/kg-explore", summary="Chat exploratório sobre o catálogo (público, sem perfil)")
@limiter.limit("3/minute")
def kg_explore(request: Request, req: KGExploreRequest):
    """Conversa stateless com o knowledge graph — sem perfil, sem sessão.

    Vitrine do produto: o visitante explora o landscape de fomento antes de
    se cadastrar. O histórico é mantido pelo cliente e reenviado a cada turno.

    Sprint 3 do Cenário B: quando AGENT_EXPLORE_DEFAULT_ENABLED=true, roda o
    agente Anthropic com 4 tools (list_editais, get_edital, find_analogues,
    get_graph_neighbors). Como o endpoint é público (sem workspace), o
    rollout é controlado por env var em vez de coluna em workspaces.
    Default OFF — segue o pipeline determinístico (catálogo inteiro no prompt).
    """
    if not req.message.strip():
        raise HTTPException(status_code=422, detail="Mensagem vazia.")
    agent_enabled = os.getenv("AGENT_EXPLORE_DEFAULT_ENABLED", "false").lower() == "true"
    answer = kg_service.explore(
        req.message, req.history, req.edital_ids, req.node_id, req.node_type,
        agent_enabled=agent_enabled,
    )
    return {"answer": answer}
