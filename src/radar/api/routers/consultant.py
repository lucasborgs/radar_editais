from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from radar.api.rate_limit import limiter
from radar.core.infra.auth import CurrentUserId, DbClient
from radar.core.services.consultant import (
    ConsultantConflictError,
    ConsultantNotFoundError,
    ConsultantValidationError,
    consultant_service,
)
from radar.core.services.content_library import get_workspace_id

router = APIRouter(prefix="/consultant", tags=["consultant"])


class ConsultantTurnRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4_000)
    conversation_id: str | None = None
    idempotency_key: str = Field(min_length=1, max_length=128)
    expected_revision: int | None = Field(default=None, ge=0)


class BriefUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=0)
    original_intention: str | None = None
    problem_hypothesis: str | None = None
    affected_users: str | None = None
    solution_hypothesis: str | None = None
    technologies_capabilities: list[str] | None = None
    innovation_objective: str | None = None
    stage_maturity: str | None = None
    location_constraints: str | None = None
    impact_expected: str | None = None
    partnership_needs: str | None = None


class ConfirmProjectRequest(BaseModel):
    expected_revision: int = Field(ge=0)


class SelectPathRequest(BaseModel):
    expected_revision: int = Field(ge=0)
    reason: str = Field(default="", max_length=2_000)


class ReassessPathRequest(BaseModel):
    expected_revision: int = Field(ge=0)
    reason: str = Field(min_length=1, max_length=2_000)


@router.post("/turn", summary="Turno do ConsultantGraph")
@limiter.limit("10/minute")
def consultant_turn(
    request: Request,
    req: ConsultantTurnRequest,
    user_id: CurrentUserId,
    db: DbClient,
):
    workspace_id = get_workspace_id(db, user_id)
    try:
        return consultant_service.turn(
            db, workspace_id, req.message.strip(), req.conversation_id, req.idempotency_key,
            req.expected_revision,
        )
    except ConsultantNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ConsultantConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.patch("/{conversation_id}/brief", summary="Atualiza o brief em revisão")
def update_brief(
    conversation_id: str,
    req: BriefUpdateRequest,
    user_id: CurrentUserId,
    db: DbClient,
):
    workspace_id = get_workspace_id(db, user_id)
    updates = req.model_dump(exclude={"expected_revision"}, exclude_none=True)
    try:
        return consultant_service.update_brief(
            db, workspace_id, conversation_id, req.expected_revision, updates,
        )
    except ConsultantNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ConsultantConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ConsultantValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/{conversation_id}/project/confirm", summary="Confirma o projeto a partir do brief")
def confirm_project(
    conversation_id: str,
    req: ConfirmProjectRequest,
    user_id: CurrentUserId,
    db: DbClient,
):
    workspace_id = get_workspace_id(db, user_id)
    try:
        return consultant_service.confirm_project(
            db, workspace_id, conversation_id, req.expected_revision,
        )
    except ConsultantNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ConsultantConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ConsultantValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/{conversation_id}/paths/{path_id}/select", summary="Registra o caminho escolhido")
def select_path(
    conversation_id: str,
    path_id: str,
    req: SelectPathRequest,
    user_id: CurrentUserId,
    db: DbClient,
):
    workspace_id = get_workspace_id(db, user_id)
    try:
        return consultant_service.select_path(
            db, workspace_id, conversation_id, path_id, req.expected_revision, req.reason,
        )
    except ConsultantNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ConsultantConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ConsultantValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/{conversation_id}/paths/{path_id}/reassess", summary="Pede reavaliação de um caminho")
def reassess_path(
    conversation_id: str,
    path_id: str,
    req: ReassessPathRequest,
    user_id: CurrentUserId,
    db: DbClient,
):
    workspace_id = get_workspace_id(db, user_id)
    try:
        return consultant_service.reassess_path(
            db, workspace_id, conversation_id, path_id, req.expected_revision, req.reason,
        )
    except ConsultantNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ConsultantConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ConsultantValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/{conversation_id}", summary="Lê o estado persistido do consultor")
def consultant_state(conversation_id: str, user_id: CurrentUserId, db: DbClient):
    workspace_id = get_workspace_id(db, user_id)
    try:
        return consultant_service.get(db, workspace_id, conversation_id)
    except ConsultantNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.delete("/{conversation_id}", summary="Exclui uma conversa do consultor")
def delete_consultant_state(conversation_id: str, user_id: CurrentUserId, db: DbClient):
    workspace_id = get_workspace_id(db, user_id)
    if not consultant_service.repository.delete(db, conversation_id, workspace_id):
        raise HTTPException(status_code=404, detail="Conversa do consultor não encontrada.")
    return {"ok": True}
