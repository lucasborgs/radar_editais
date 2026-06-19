"""Endpoints da staging area do deep_research (Item 2, Sprint 3).

`research_findings` é onde os findings do sub-agente de pesquisa chegam
automaticamente (verified=false). Esta fila é o gate humano da Fase B: o usuário
revê e promove para a content_library o que quiser manter.

Wiring em backend/api.py (não feito aqui):
    from backend.routers.research import router as research_router
    app.include_router(research_router)
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException

from core.auth import CurrentUserId, DbClient
from core.services.content_library import create_item, get_workspace_id

router = APIRouter(prefix="/research-findings", tags=["research"])

# TTL de findings não revisados: pendentes mais antigos que isto não aparecem na
# fila (a limpeza física é um cron fora de escopo deste item — só filtramos aqui).
FINDINGS_TTL_DAYS = 30


@router.get("", summary="Fila de research_findings pendentes (Fase B)")
def list_research_findings(
    user_id: CurrentUserId,
    db: DbClient,
    include_promoted: bool = False,
):
    """Lista findings do workspace.

    Default: só pendentes (reviewed_at is null) e não expirados (created_at dentro
    do TTL de 30 dias). `include_promoted=true` traz também os já promovidos
    (sem o filtro de TTL), mais recentes primeiro.
    """
    workspace_id = get_workspace_id(db, user_id)
    q = (
        db.table("research_findings")
        .select("id, question, answer, sources, query, verified, created_at, "
                "reviewed_at, promoted_to_library_id")
        .eq("workspace_id", workspace_id)
    )
    if not include_promoted:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=FINDINGS_TTL_DAYS)).isoformat()
        q = q.is_("reviewed_at", "null").gte("created_at", cutoff)
    result = q.order("created_at", desc=True).execute()
    return {"findings": result.data or []}


@router.post("/{finding_id}/promote", status_code=201,
             summary="Promove um finding para a content_library")
async def promote_research_finding(
    finding_id: str,
    user_id: CurrentUserId,
    db: DbClient,
):
    """Cria um content_item a partir do finding e marca o finding como promovido.

    O item recebe type='other' e a pergunta como título; o corpo é a resposta
    do deep_research com o bloco de fontes anexado. `verified` permanece false no
    finding histórico — o ato de promover é a verificação humana.
    """
    workspace_id = get_workspace_id(db, user_id)

    res = (
        db.table("research_findings")
        .select("*")
        .eq("id", finding_id)
        .eq("workspace_id", workspace_id)
        .maybe_single()
        .execute()
    )
    finding = res.data if res else None
    if finding is None:
        raise HTTPException(status_code=404, detail="Finding não encontrado")
    if finding.get("promoted_to_library_id"):
        raise HTTPException(status_code=409, detail="Finding já promovido")

    # Monta o conteúdo: resposta + fontes (mesma forma que o agente vê).
    body_parts = [finding.get("answer") or ""]
    sources = finding.get("sources") or []
    if sources:
        body_parts.append("\nFontes:")
        for s in sources:
            url = s.get("url", "")
            title = s.get("title") or url
            body_parts.append(f"- {title} — {url}")
    content = "\n".join(p for p in body_parts if p).strip()
    title = (finding.get("question") or "Pesquisa").strip()[:200]

    item = await create_item(
        db,
        workspace_id=workspace_id,
        title=title,
        type_="other",
        content=content,
        tags=["deep_research"],
        source_url=(sources[0].get("url") if sources else None),
    )

    db.table("research_findings").update({
        "promoted_to_library_id": item["id"],
        "reviewed_at": datetime.now(timezone.utc).isoformat(),
    }).eq("id", finding_id).eq("workspace_id", workspace_id).execute()

    return {"promoted": True, "library_id": item["id"], "item": item}
