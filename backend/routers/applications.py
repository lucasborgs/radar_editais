"""Pipeline de applications (Kanban): listagem + transição de status (Fase 3 #23)."""

from __future__ import annotations

import logging
from datetime import date

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from core.auth import CurrentUserId, DbClient
from core.kg import entity_catalog
from core.kg.schema import parse_deadline

logger = logging.getLogger(__name__)

router = APIRouter(tags=["applications"])

_VALID_STATUS_TRANSITIONS = {
    "matched", "brief_gerado", "proposta_iniciada",
    "submetida", "em_analise", "aprovada", "reprovada", "desistiu",
}


class ApplicationStatusUpdate(BaseModel):
    status: str
    feedback_notas: str | None = None


@router.put("/applications/{application_id}/status", summary="Atualiza status de uma application_log (Fase 3 #23)")
async def update_application_status(
    application_id: str,
    payload: ApplicationStatusUpdate,
    user_id: CurrentUserId,
    db: DbClient,
):
    """Atualiza status manualmente (submetida, aprovada, reprovada, desistiu).

    O trigger log_application_event registra a transição em application_events
    automaticamente. RLS garante que o usuário só atualiza applications do
    próprio workspace.

    Quando o novo status é um outcome (submetida/aprovada/reprovada), enfileira
    `reflect_workspace_task` — fecha o loop de reflexão longitudinal, que antes
    só rodava on-demand. A task self-gateia em MIN_OUTCOMES_FOR_REFLECTION,
    então enfileirar a cada outcome é seguro (pula sozinha se ainda há poucos).

    F6 (D3 — congelamento da memória auto-escrita): sob AUTO_MEMORY_WRITE=0
    (default), o auto-defer enfileira a task, mas ela se torna no-op no worker
    (gate em core/tasks.py + reflection_service.reflect_workspace). O defer
    em si não é removido para manter a arquitetura de eventos; o congelamento
    está no executor, não no trigger.
    """
    if payload.status not in _VALID_STATUS_TRANSITIONS:
        raise HTTPException(
            status_code=422,
            detail=f"Status inválido. Use: {', '.join(sorted(_VALID_STATUS_TRANSITIONS))}",
        )
    update: dict = {"status": payload.status}
    if payload.feedback_notas is not None:
        update["feedback_notas"] = payload.feedback_notas

    result = (
        db.table("application_log")
        .update(update)
        .eq("id", application_id)
        .execute()
    )
    if not result.data:
        raise HTTPException(status_code=404, detail="application_log não encontrada")

    row = result.data[0]
    from core.reflection_service import OUTCOME_STATUSES
    workspace_id = row.get("workspace_id")
    if payload.status in OUTCOME_STATUSES and workspace_id:
        try:
            from core.tasks import app as tasks_app
            async with tasks_app.open_async():
                await tasks_app.configure_task("reflect_workspace").defer_async(
                    workspace_id=str(workspace_id)
                )
        except Exception as e:
            # Reflexão é best-effort: nunca derruba o update de status por falha
            # de enfileiramento (worker offline, DB de filas indisponível, etc).
            logger.warning(
                "Falha ao enfileirar reflect_workspace p/ workspace=%s: %s",
                workspace_id, e,
            )
    return row


def _session_progress_pct(session: dict | None) -> int:
    """Calcula % de conclusão de uma proposta a partir da writing_session.

    Total = nº de seções do outline; preenchidas = seções com draft não-vazio.
    Sem sessão ou outline vazio → 0.
    """
    if not session:
        return 0
    outline = session.get("proposal_outline") or []
    total = len(outline)
    if total == 0:
        return 0
    drafts = session.get("section_drafts") or {}
    filled = sum(
        1 for v in drafts.values()
        if isinstance(v, str) and v.strip()
    )
    return round(100 * filled / total)


def _serialize_application(row: dict, card: dict | None, session: dict | None) -> dict:
    """Monta um item do pipeline (Kanban) a partir de uma linha de application_log.

    `card` é a wiki page do edital (ou None se ausente); `session` é a
    writing_session associada (ou None). Resolve título do edital, prazo e
    days_left, além do progresso da proposta.
    """
    deadline = (card or {}).get("deadline") or None
    parsed = parse_deadline(deadline)
    days_left = (parsed - date.today()).days if parsed else None
    return {
        "application_id": row.get("id"),
        "edital_id": row.get("edital_id"),
        "edital_title": (card or {}).get("title") if card else None,
        "status": row.get("status"),
        "match_score": row.get("match_score"),
        "deadline": deadline,
        "days_left": days_left,
        "session_id": row.get("session_id"),
        "progress_pct": _session_progress_pct(session),
        "updated_at": row.get("updated_at"),
    }


def _build_pipeline_items(rows: list[dict], sessions_by_id: dict[str, dict]) -> list[dict]:
    """Serializa as linhas de application_log em itens do pipeline.

    Resolve o card do edital (file-based, cacheado em processo) por linha e
    mapeia a writing_session pré-carregada (batch) por session_id.
    """
    items = []
    for row in rows:
        card = entity_catalog.get_edital(row.get("edital_id"))
        session = sessions_by_id.get(row.get("session_id")) if row.get("session_id") else None
        items.append(_serialize_application(row, card, session))
    return items


@router.get("/applications", summary="Lista applications do workspace (pipeline)")
async def list_applications(user_id: CurrentUserId, db: DbClient):
    """Lista as applications do workspace com metadados do edital para o Kanban.

    Retorna uma lista plana (o frontend agrupa por status nas colunas do
    pipeline), ordenada por updated_at desc. RLS escopa ao workspace do usuário.

    Junta o título/prazo do edital (wiki page) e calcula days_left e o
    progresso da proposta (a partir da writing_session, quando houver). As
    sessions são buscadas em lote (.in_) para evitar N+1.
    """
    result = (
        db.table("application_log")
        .select(
            "id, edital_id, session_id, match_score, status, updated_at"
        )
        .order("updated_at", desc=True)
        .execute()
    )
    rows = result.data or []

    session_ids = [r["session_id"] for r in rows if r.get("session_id")]
    sessions_by_id: dict[str, dict] = {}
    if session_ids:
        sess_result = (
            db.table("writing_sessions")
            .select("id, proposal_outline, section_drafts")
            .in_("id", session_ids)
            .execute()
        )
        sessions_by_id = {s["id"]: s for s in (sess_result.data or [])}

    return _build_pipeline_items(rows, sessions_by_id)
