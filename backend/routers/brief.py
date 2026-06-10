"""Opportunity Brief (Fase 3 #21): avaliação GO/NO-GO antes da proposta."""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel

from backend.common import (
    CompanyProfileSchema,
    profile_from_workspace,
    to_py_profile,
)
from backend.rate_limit import limiter
from core.auth import CurrentUserId, DbClient
from core.content_library import get_workspace_id

router = APIRouter(tags=["brief"])


class OpportunityBriefRequest(BaseModel):
    edital_id: str
    profile: CompanyProfileSchema | None = None
    model_tier: str | None = None  # Fase 4 #24


@router.post("/opportunity/brief", summary="Gera Brief de Oportunidade para um edital (decision matrix + GO/NO-GO)")
@limiter.limit("10/minute")
def opportunity_brief(
    request: Request, req: OpportunityBriefRequest, user_id: CurrentUserId, db: DbClient
):
    """Avaliação formal antes de iniciar uma proposta (ADR §3.6, RADAR Gap 6).

    Score determinístico (HybridMatch Stage 1) + narrativa LLM. Persiste
    automaticamente em application_log com status='brief_gerado' (trigger
    em application_events registra o evento).

    Se `profile` não for enviado, lê de workspaces.profile.
    """
    from core.opportunity_brief_service import generate_brief

    workspace_id = get_workspace_id(db, user_id)
    profile = (
        to_py_profile(req.profile) if req.profile
        else profile_from_workspace(db, workspace_id)
    )
    return generate_brief(db, workspace_id, profile, req.edital_id, model_tier=req.model_tier)
