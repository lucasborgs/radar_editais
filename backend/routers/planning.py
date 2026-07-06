"""Endpoint de planejamento de proposta (FASE 1 + FASE 4 da spec workspace-multi-mode).

Gera novo plano a partir do Intake, ou retorna o plano existente de uma sessão
de escrita para re-visualização e ajustes.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from core.auth import DbClient
from core.kg.planning_node import generate_plan

logger = logging.getLogger(__name__)

router = APIRouter(tags=["planning"])


class PlanningRequest(BaseModel):
    question: str = Field(max_length=4_000)
    analysis: str = Field(max_length=16_000)
    edital_id: str | None = None
    company_nodes: list[dict] | None = None


@router.post(
    "/planning/generate",
    summary="Gera plano estruturado de proposta a partir do contexto do Intake",
)
def planning_generate(req: PlanningRequest):
    if not req.question.strip():
        raise HTTPException(status_code=422, detail="Pergunta vazia.")
    if not req.analysis.strip():
        raise HTTPException(status_code=422, detail="Análise vazia.")

    plan = generate_plan(
        question=req.question,
        analysis=req.analysis,
        edital_id=req.edital_id,
        company_nodes=req.company_nodes,
    )

    if "error" in plan:
        raise HTTPException(status_code=502, detail=plan["error"])

    return plan


@router.get(
    "/planning/{session_id}",
    summary="Retorna o plano existente de uma sessão de escrita",
)
def planning_get(session_id: str, db: DbClient):
    """Carrega o plano (`section_drafts.__plan__`) de uma WritingSession.

    Usado pela planning page quando re-aberta do workspace para ajustes.
    """
    try:
        row = (
            db.table("writing_sessions")
            .select("section_drafts, edital_id")
            .eq("id", session_id)
            .maybe_single()
            .execute()
        )
        if not row or not row.data:
            raise HTTPException(status_code=404, detail="Sessão não encontrada.")

        drafts = row.data.get("section_drafts") or {}
        plan = drafts.get("__plan__")

        if not plan:
            raise HTTPException(
                status_code=404,
                detail="Nenhum plano encontrado nesta sessão. "
                       "Gere um plano primeiro via /planning/generate ou pelo chat /plan.",
            )

        # Inclui edital_id para o front contextualizar
        if isinstance(plan, dict):
            plan["_edital_id"] = row.data.get("edital_id")

        return plan
    except HTTPException:
        raise
    except Exception as e:
        logger.error("planning_get erro: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
