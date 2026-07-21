"""Catálogo público: raiz, stats e listagem/detalhe de oportunidades."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from core.kg import entity_catalog

router = APIRouter(tags=["catalog"])


@router.get("/", include_in_schema=False)
def root():
    return {"message": "Radar Editais API v2", "docs": "/docs"}


@router.get("/stats", summary="Estatísticas do catálogo de editais")
def get_stats():
    return entity_catalog.get_stats()


@router.get("/opportunities", summary="Lista todas as oportunidades (editais + programas + investidores)")
def list_opportunities(
    tipo: str | None = Query(None, description="Filtro por tipo: edital | programa | investidor"),
    limit: int = Query(200, ge=1, le=500),
):
    return entity_catalog.list_opportunities(tipo=tipo, limit=limit)


@router.get("/editais", summary="Lista editais com filtros opcionais")
def list_editais(
    status: str | None = Query(None, description="ABERTA | ENCERRADA | Desconhecido"),
    tema: str | None = Query(None, description="Filtro por tema (substring)"),
    limit: int = Query(200, ge=1, le=500),
):
    return entity_catalog.list_editais(status=status, tema=tema, limit=limit)


@router.get("/editais/{edital_id}", summary="Card completo de um edital")
def get_edital(edital_id: str):
    edital = entity_catalog.get_edital(edital_id)
    if edital is None:
        raise HTTPException(status_code=404, detail=f"Edital '{edital_id}' não encontrado")
    return edital


@router.get("/oportunidades/{opp_id:path}", summary="Ficha completa de uma oportunidade (edital | programa | investimento)")
def get_opportunity(opp_id: str):
    """Ficha unificada (D1/PR8): resolve edital OU curado (programa/investimento).
    `:path` porque ids curados podem conter ':' (ex.: `investidor:indicator capital`)."""
    opp = entity_catalog.get_opportunity(opp_id)
    if opp is None:
        raise HTTPException(status_code=404, detail=f"Oportunidade '{opp_id}' não encontrada")
    return opp
