"""Endpoint de planejamento de proposta (FASE 1 + FASE 4 da spec workspace-multi-mode).

Gera novo plano a partir do Intake, retorna o plano existente de uma sessão
de escrita para re-visualização e ajustes, ou ajusta um plano existente.
"""
from __future__ import annotations

import json
import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from radar.core.infra.auth import CurrentUserId, DbClient
from radar.core.kg.planning_node import generate_plan
from radar.core.llm.llm_client import make_client
from radar.core.services.content_library import get_workspace_id
from radar.core.services.writing_session import get_session_document

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
def planning_get(session_id: str, user_id: CurrentUserId, db: DbClient):
    """Carrega o plano de uma WritingSession pela MESMA fonte de verdade do
    endpoint do documento (`get_session_document`), validando que a sessão
    pertence ao workspace do usuário autenticado.

    Usado pela planning page quando re-aberta do workspace para ajustes.
    """
    workspace_id = get_workspace_id(db, user_id)
    doc = get_session_document(db, session_id, workspace_id)
    plan = doc.get("plan") if doc else None
    if not isinstance(plan, dict) or not plan:
        raise HTTPException(
            status_code=404,
            detail="Nenhum plano encontrado nesta sessão. "
                   "Gere um plano primeiro via /planning/generate ou pelo chat /plan.",
        )

    # Inclui edital_id para o front contextualizar
    plan = dict(plan)
    plan["_edital_id"] = doc.get("edital_id")
    return plan


class AdjustPlanningRequest(BaseModel):
    instruction: str = Field(max_length=4_000)


def _adjust_plan_with_llm(existing_plan: dict, instruction: str) -> dict:
    """Ajusta o plano existente via LLM (1-shot)."""
    client = make_client()
    try:
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": (
                    "Você ajusta um plano de proposta de fomento. "
                    "Recebe o plano atual em JSON e uma instrução de ajuste. "
                    "Retorne APENAS o JSON do plano ajustado, com a mesma estrutura."
                )},
                {"role": "user", "content": (
                    f"PLANO ATUAL:\n{json.dumps(existing_plan, ensure_ascii=False, indent=2)}\n\n"
                    f"INSTRUÇÃO: {instruction}"
                )},
            ],
            temperature=0.3,
            response_format={"type": "json_object"},
        )
        raw = resp.choices[0].message.content
        if raw:
            return json.loads(raw)
        raise ValueError("LLM retornou resposta vazia.")
    except Exception as e:
        logger.error("adjust_plan LLM erro: %s", e)
        raise HTTPException(status_code=502, detail=f"Erro ao ajustar plano: {e}") from e


@router.post(
    "/planning/{session_id}/adjust",
    summary="Ajusta o plano existente de uma sessão com instrução em linguagem natural",
)
def planning_adjust(
    session_id: str,
    req: AdjustPlanningRequest,
    user_id: CurrentUserId,
    db: DbClient,
):
    """Carrega o plano de uma WritingSession, aplica ajuste via LLM e persiste."""
    if not req.instruction.strip():
        raise HTTPException(status_code=422, detail="Instrução vazia.")

    workspace_id = get_workspace_id(db, user_id)

    # Carrega o plano existente (valida posse do workspace — RLS já defende,
    # o check explícito cobre também o DEMO_MODE onde o RLS é bypassed).
    try:
        row = (
            db.table("writing_sessions")
            .select("section_drafts, edital_id, workspace_id")
            .eq("id", session_id)
            .maybe_single()
            .execute()
        )
        if not row or not row.data:
            raise HTTPException(status_code=404, detail="Sessão não encontrada.")
        if row.data.get("workspace_id") != workspace_id:
            raise HTTPException(status_code=404, detail="Sessão não encontrada.")

        drafts = row.data.get("section_drafts") or {}
        plan = drafts.get("__plan__")
        if not plan:
            raise HTTPException(
                status_code=404,
                detail="Nenhum plano encontrado nesta sessão. Gere um plano primeiro.",
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.error("planning_adjust load erro: %s", e)
        raise HTTPException(status_code=500, detail=str(e)) from e

    # Ajusta via LLM
    adjusted = _adjust_plan_with_llm(plan, req.instruction)

    # Persiste o plano ajustado
    try:
        drafts["__plan__"] = adjusted
        db.table("writing_sessions").update({
            "section_drafts": drafts,
        }).eq("id", session_id).execute()
    except Exception as e:
        logger.error("planning_adjust save erro: %s", e)
        raise HTTPException(status_code=500, detail=f"Erro ao salvar plano ajustado: {e}") from e

    # Inclui edital_id para o front contextualizar
    if isinstance(adjusted, dict):
        adjusted["_edital_id"] = row.data.get("edital_id")

    return adjusted
