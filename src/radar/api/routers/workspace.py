"""Workspace — ações one-shot (/profile, /review).

A Workspace não é mais multi-modo: mensagens normais de chat vão direto ao
fluxo de escrita (`/writing/turn/stream`). Este endpoint existe apenas para as
ações one-shot disparadas pelo chat (`/profile`, `/review`).

Endpoints:
  POST /workspace/{session_id}/mode → executa uma ação one-shot
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from radar.api.common import profile_from_workspace
from radar.api.rate_limit import limiter
from radar.core.infra.auth import CurrentUserId, DbClient
from radar.core.services.content_library import get_workspace_id, list_items
from radar.core.services.workspace_service import VALID_ACTIONS, dispatch

logger = logging.getLogger(__name__)

router = APIRouter(tags=["workspace"])


class ModeSwitchRequest(BaseModel):
    mode: str = Field(max_length=20)
    message: str = Field(default="", max_length=4_000)


class ModeSwitchResponse(BaseModel):
    mode: str
    response: str
    welcome: str | None = None
    error: str | None = None


@router.post(
    "/workspace/{session_id}/mode",
    summary="Executa uma ação one-shot do workspace (/profile, /review)",
)
@limiter.limit("20/minute")
def workspace_mode(
    request: Request,
    session_id: str,
    req: ModeSwitchRequest,
    user_id: CurrentUserId,
    db: DbClient,
) -> ModeSwitchResponse:
    """Dispatcher de ações one-shot (/profile, /review) da Workspace."""
    if req.mode not in VALID_ACTIONS:
        raise HTTPException(
            status_code=422,
            detail=f"Ação inválida: '{req.mode}'. Use: {', '.join(sorted(VALID_ACTIONS))}",
        )

    workspace_id = get_workspace_id(db, user_id)
    profile = profile_from_workspace(db, workspace_id)

    # Anexos da biblioteca para contextualizar a ação.
    library_items = list_items(db, workspace_id, include_archived=False)

    result = dispatch(
        db=db,
        session_id=session_id,
        workspace_id=workspace_id,
        profile=profile,
        mode=req.mode,
        message=req.message,
        library_items=library_items,
    )

    if result.get("error"):
        raise HTTPException(status_code=502, detail=result["error"])

    return ModeSwitchResponse(
        mode=result["mode"],
        response=result["response"],
        welcome=None,
        error=None,
    )
