"""Catálogo público: raiz, stats, listagem/detalhe de editais e registry de
slash commands. Sem auth — vitrine do produto."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from core.kg import hypergraph_catalog

router = APIRouter(tags=["catalog"])


@router.get("/", include_in_schema=False)
def root():
    return {"message": "Radar Editais API v2", "docs": "/docs"}


@router.get("/stats", summary="Estatísticas do catálogo de editais")
def get_stats():
    return hypergraph_catalog.get_stats()


@router.get("/editais", summary="Lista editais com filtros opcionais")
def list_editais(
    status: str | None = Query(None, description="ABERTA | ENCERRADA | Desconhecido"),
    tema: str | None = Query(None, description="Filtro por tema (substring)"),
    limit: int = Query(200, ge=1, le=500),
):
    return hypergraph_catalog.list_editais(status=status, tema=tema, limit=limit)


@router.get("/editais/{edital_id}", summary="Card completo de um edital")
def get_edital(edital_id: str):
    edital = hypergraph_catalog.get_edital(edital_id)
    if edital is None:
        raise HTTPException(status_code=404, detail=f"Edital '{edital_id}' não encontrado")
    return edital


@router.get("/commands", summary="Lista slash commands disponíveis e LLM tiers (Fase 4 #24/#25)")
def list_commands():
    """Registry de slash commands + model tiers para o frontend popular UI.

    Cada comando mapeia para um endpoint existente; o frontend renderiza
    autocomplete quando o usuário digita /xxx no chat. Não exige auth (apenas
    catálogo público de capacidades).
    """
    from core.llm_router import list_tiers
    from core.skills import available_skills
    return {
        "commands": [
            {
                "name": "draft",
                "endpoint": "POST /writing/start",
                "description": "Inicia sessão de escrita de proposta",
                "args": ["edital_id", "library_item_ids?"],
            },
            {
                "name": "review",
                "endpoint": "POST /writing/{session_id}/checklist/auto-review",
                "description": "Roda 3 passes de revisão (compliance + qualidade + completude)",
                "args": [],
            },
            {
                "name": "reflect",
                "endpoint": "POST /me/reflect",
                "description": "Síntese de outcomes anteriores em insights",
                "args": [],
            },
        ],
        "model_tiers": list_tiers(),
        "skills": available_skills(),
    }
