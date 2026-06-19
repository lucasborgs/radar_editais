"""Endpoints da staging da torneira de descoberta (Parte C).

`discovered_opportunities` é a fila onde a torneira (cron diário) deixa os
achados como `pending`. Esta é a fila de revisão humana: o usuário PROMOVE o que
vale (a URL vira `web_sources` → o WebScraper a trata como fonte curada → entra
no KG) ou REJEITA (some da fila). Nada entra no RAG sem promoção.

GLOBAL (não workspace-scoped): a torneira é cron de sistema. Auth = qualquer
usuário logado (gate via CurrentUserId). As escritas tocam `web_sources` (RLS
service-role-only), então usamos o cliente service-role — o gate de auth é o
CurrentUserId, não o RLS.

Wiring em backend/api.py:
    from backend.routers.discovered import router as discovered_router
    app.include_router(discovered_router)
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from core.auth import CurrentUserId
from core.db import get_supabase_service

router = APIRouter(prefix="/discovered-opportunities", tags=["discovery"])

# TTL de itens não revisados: pendentes mais antigos que isto não aparecem na
# fila (limpeza física fica para um cron fora de escopo — aqui só filtramos).
TTL_DAYS = 30

_LIST_COLS = (
    "id, url, title, agency, fonte, descricao, prazo_envio, publico_alvo, tema, "
    "opportunity_type, status, created_at, reviewed_at, promoted_web_source_id"
)


@router.get("", summary="Fila de oportunidades descobertas")
def list_discovered(user_id: CurrentUserId, include_reviewed: bool = False):
    """Lista a fila. Default: só `pending` e dentro do TTL de 30 dias.
    `include_reviewed=true` traz também promovidos/rejeitados (sem filtro de TTL),
    mais recentes primeiro."""
    db = get_supabase_service()
    q = db.table("discovered_opportunities").select(_LIST_COLS)
    if not include_reviewed:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=TTL_DAYS)).isoformat()
        q = q.eq("status", "pending").gte("created_at", cutoff)
    res = q.order("created_at", desc=True).execute()
    return {"opportunities": res.data or []}


@router.post("/{opp_id}/promote", status_code=201,
             summary="Promove um achado: a URL vira fonte rastreada (web_sources)")
def promote_discovered(opp_id: str, user_id: CurrentUserId):
    """Insere a URL em `web_sources` (fonte curada) e marca o achado como
    `promoted`. A partir daí o WebScraper a indexa no próximo ciclo. Idempotente
    no `web_sources` (upsert por url): promover algo cuja URL já é fonte apenas
    religa/atualiza o label."""
    db = get_supabase_service()
    res = (db.table("discovered_opportunities").select("*")
             .eq("id", opp_id).maybe_single().execute())
    opp = res.data if res else None
    if opp is None:
        raise HTTPException(status_code=404, detail="Oportunidade não encontrada")
    if opp["status"] != "pending":
        raise HTTPException(status_code=409,
                            detail=f"Já revisada (status={opp['status']})")

    label = (opp.get("title") or opp.get("agency") or "Descoberta")[:200]
    ws_res = (db.table("web_sources")
                .upsert({"url": opp["url"], "label": label, "active": True},
                        on_conflict="url")
                .execute())
    web_source_id = (ws_res.data or [{}])[0].get("id")

    db.table("discovered_opportunities").update({
        "status": "promoted",
        "promoted_web_source_id": web_source_id,
        "reviewed_at": datetime.now(timezone.utc).isoformat(),
    }).eq("id", opp_id).execute()

    return {"promoted": True, "web_source_id": web_source_id, "url": opp["url"]}


class RejectBody(BaseModel):
    reason: str | None = None


@router.post("/{opp_id}/reject", summary="Rejeita um achado (some da fila)")
def reject_discovered(opp_id: str, user_id: CurrentUserId, body: RejectBody | None = None):
    """Marca o achado como `rejected`. O ledger da torneira já impede que a mesma
    URL volte à fila em runs futuras, então não precisa de cache extra aqui."""
    db = get_supabase_service()
    res = (db.table("discovered_opportunities").select("id, status")
             .eq("id", opp_id).maybe_single().execute())
    opp = res.data if res else None
    if opp is None:
        raise HTTPException(status_code=404, detail="Oportunidade não encontrada")
    if opp["status"] != "pending":
        raise HTTPException(status_code=409,
                            detail=f"Já revisada (status={opp['status']})")

    db.table("discovered_opportunities").update({
        "status": "rejected",
        "reject_reason": (body.reason if body else None),
        "reviewed_at": datetime.now(timezone.utc).isoformat(),
    }).eq("id", opp_id).execute()

    return {"rejected": True}
