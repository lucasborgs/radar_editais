"""Entrada de escrita fundamentada por caminho selecionado (SCV1-T06)."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from radar.api.rate_limit import limiter
from radar.core.infra.auth import CurrentUserId, DbClient
from radar.core.services.consultant import (
    ConsultantConflictError,
    ConsultantNotFoundError,
    ConsultantValidationError,
)
from radar.core.services.content_library import get_workspace_id
from radar.core.services.grounded_writing import grounded_writing

router = APIRouter(prefix="/writing/grounded", tags=["grounded-writing"])


class GroundedWritingOpenRequest(BaseModel):
    conversation_id: str
    path_id: str
    artifact_type: str = "proposta_tecnica"
    allowed_material_ids: list[str] = Field(default_factory=list)


class GroundedWritingTurnRequest(BaseModel):
    session_id: str
    instruction: str = Field(min_length=1, max_length=16_000)
    section_hint: str | None = Field(default=None, max_length=200)
    idempotency_key: str | None = Field(default=None, max_length=128)


def _handle_error(exc: Exception) -> HTTPException:
    if isinstance(exc, ConsultantNotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, ConsultantConflictError):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, ConsultantValidationError):
        return HTTPException(status_code=422, detail=str(exc))
    return HTTPException(status_code=500, detail="Não foi possível abrir a escrita fundamentada.")


@router.post("/open", summary="Abre escrita a partir de um caminho selecionado")
@limiter.limit("10/minute")
def grounded_writing_open(
    request: Request,
    req: GroundedWritingOpenRequest,
    user_id: CurrentUserId,
    db: DbClient,
):
    workspace_id = get_workspace_id(db, user_id)
    try:
        return grounded_writing.open(
            db,
            workspace_id,
            conversation_id=req.conversation_id,
            path_id=req.path_id,
            artifact_type=req.artifact_type,
            allowed_material_ids=req.allowed_material_ids,
        )
    except Exception as exc:
        raise _handle_error(exc) from exc


@router.post("/turn", summary="Executa um turno de escrita fundamentada")
@limiter.limit("10/minute")
async def grounded_writing_turn(
    request: Request,
    req: GroundedWritingTurnRequest,
    user_id: CurrentUserId,
    db: DbClient,
):
    workspace_id = get_workspace_id(db, user_id)
    try:
        return await asyncio.to_thread(
            grounded_writing.turn,
            db,
            workspace_id,
            req.session_id,
            req.instruction,
            req.section_hint,
            req.idempotency_key,
        )
    except Exception as exc:
        raise _handle_error(exc) from exc


@router.post("/{session_id}/review", summary="Revisa cobertura, qualidade e lacunas")
@limiter.limit("10/minute")
async def grounded_writing_review(
    request: Request,
    session_id: str,
    user_id: CurrentUserId,
    db: DbClient,
):
    workspace_id = get_workspace_id(db, user_id)
    try:
        return await grounded_writing.review(db, workspace_id, session_id)
    except Exception as exc:
        raise _handle_error(exc) from exc
