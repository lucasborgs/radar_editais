"""Workspace multi-modo — dispatcher /explorer /escrita + ações one-shot.

Endpoints:
  POST /workspace/{session_id}/mode → troca modo e envia mensagem
  GET  /workspace/{session_id}/mode → modo atual
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from radar.api.common import profile_from_workspace
from radar.api.rate_limit import limiter
from radar.core.infra.auth import CurrentUserId, DbClient
from radar.core.services.content_library import get_workspace_id, list_items
from radar.core.services.workspace_service import (
    VALID_ACTIONS,
    VALID_MODES,
    dispatch,
    mode_welcome,
)

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
    summary="Troca modo e/ou envia mensagem ao workspace",
)
@limiter.limit("20/minute")
def workspace_mode(
    request: Request,
    session_id: str,
    req: ModeSwitchRequest,
    user_id: CurrentUserId,
    db: DbClient,
) -> ModeSwitchResponse:
    """Dispatcher: roteia a mensagem ao agente do modo solicitado.

    Se o modo for diferente do atual, o histórico é filtrado para o novo
    modo e o response inclui uma mensagem de boas-vindas.
    """
    if req.mode not in VALID_MODES and req.mode not in VALID_ACTIONS:
        raise HTTPException(
            status_code=422,
            detail=f"Modo inválido: '{req.mode}'. Use: {', '.join(sorted(VALID_MODES))} ou {', '.join(sorted(VALID_ACTIONS))}",
        )

    workspace_id = get_workspace_id(db, user_id)
    profile = profile_from_workspace(db, workspace_id)

    # Carrega itens da biblioteca para contextualizar o explorer mode
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

    # Se a mensagem estiver vazia (apenas troca de modo), inclui welcome
    # Ações one-shot nunca disparam welcome.
    welcome = None
    if not req.message.strip() and req.mode not in VALID_ACTIONS:
        welcome = mode_welcome(req.mode)

    return ModeSwitchResponse(
        mode=result["mode"],
        response=result["response"],
        welcome=welcome,
        error=None,
    )
