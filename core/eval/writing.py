"""Suíte de avaliação do agente de escrita (fim-a-fim).

Porta `scripts/eval_agent_writing.py` para o harness unificado. O `task` roda
uma WritingSession real (agente + tools) até salvar a seção e computa os
artefatos de julgamento (grounding por-claim, erros factuais, coerência) — que
dependem do DB/escopo da sessão. Os `evaluators` são finos: expõem cada métrica
como score nomeado. Métricas operacionais (turnos até salvar, nº de tool calls,
latência) viram scores também — instrumentação do harness de agente no mesmo lugar.

Pré-requisitos (toca DB + LLM): SUPABASE_*, OPENAI/ANTHROPIC e EVAL_WORKSPACE_ID
(workspace onde as sessões de eval são criadas).
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

from config import ROOT
from core.eval.harness import Evaluation, Suite, get_input

logger = logging.getLogger(__name__)

FIXTURE = ROOT / "tests" / "fixtures" / "eval_cases.json"


def _build_profile(raw: dict):
    from domain.user_profile import CompanyProfile
    allowed = set(CompanyProfile.__dataclass_fields__.keys())
    return CompanyProfile(**{k: v for k, v in raw.items() if k in allowed})


def load_data() -> list[dict]:
    if not FIXTURE.exists():
        return []
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    profiles = data.get("profiles", {})
    items = []
    for case in data.get("cases", []):
        raw = profiles.get(case["profile"])
        if raw is None:
            continue
        items.append({
            "input": {
                "profile": raw,
                "edital_id": case["edital_id"],
                "instruction": case["instruction"],
                "section": case.get("section"),
                "max_turns": case.get("max_turns", 4),
            },
            "expected_output": None,
            "metadata": {"case_id": case["id"], "edital_id": case["edital_id"]},
        })
    return items


def task(*, item: Any, **_) -> dict:
    inp = get_input(item)
    from core.agent_tools.critic_agent import _build_proposal_context
    from core.db import get_supabase_service
    from core.kg.temporal import render_temporal_block
    from core.retriever import format_chunks_for_prompt, retrieve_chunks
    from core.writing_eval import (
        extract_edital_claims,
        judge_factual_errors,
        judge_internal_coherence,
        score_grounding,
    )
    from core.writing_session import WritingSession

    db = get_supabase_service()
    workspace_id = os.environ["EVAL_WORKSPACE_ID"]
    profile = _build_profile(inp["profile"])
    edital_id = inp["edital_id"]
    instruction = inp["instruction"]
    section_hint = inp.get("section")
    max_turns = inp.get("max_turns", 4)

    session = WritingSession(
        db=db, workspace_id=workspace_id, profile=profile, edital_id=edital_id,
    )
    saved_title: str | None = None
    turns_to_save: int | None = None
    n_tool_calls = 0
    t0 = time.monotonic()
    for t in range(1, max_turns + 1):
        before = dict(session._doc_sections)
        msg = instruction if t == 1 else (
            "Finalize e salve o rascunho desta seção com save_draft."
        )
        result = session.turn(msg, section_hint=section_hint)
        n_tool_calls += len(result.get("tool_trace") or [])
        changed = [k for k, v in session._doc_sections.items() if before.get(k) != v]
        if changed:
            saved_title = changed[-1]
            turns_to_save = t
            break
    latency_ms = (time.monotonic() - t0) * 1000.0

    saved = saved_title is not None
    draft = session._doc_sections.get(saved_title, "") if saved else ""

    try:
        chunks = retrieve_chunks(
            db, session._scope_edital_ids, query=(draft[:500] or section_hint or ""), k=5,
        )
        edital_context = format_chunks_for_prompt(chunks, edital_ids=session._scope_edital_ids)
    except Exception as e:
        logger.warning("retrieve_chunks falhou (%s): %s", inp["edital_id"], e)
        edital_context = ""

    claims = extract_edital_claims(draft) if draft else []

    def _retrieve_for_claim(query: str, k: int) -> list[dict]:
        return retrieve_chunks(db, [edital_id], query=query, k=k)

    grounding = score_grounding(claims, retrieve_fn=_retrieve_for_claim, k=5)
    temporal_block = render_temporal_block(edital_id)
    proposal_context = _build_proposal_context(session, saved_title or section_hint or "")
    errors = judge_factual_errors(
        saved_title or section_hint or "", draft, edital_context,
        proposal_context, temporal_block,
    ) if draft else []
    coh = judge_internal_coherence(dict(session._doc_sections)) if saved else None

    return {
        "saved": saved,
        "turns_to_save": turns_to_save,
        "n_tool_calls": n_tool_calls,
        "latency_ms": round(latency_ms, 1),
        "section": saved_title or section_hint,
        "n_claims": grounding.n_claims,
        "n_grounded": grounding.n_grounded,
        "pct_grounded": round(grounding.pct_grounded, 3),
        "n_factual_errors": len(errors),
        "factual_errors": errors,
        "coherent": coh.coherent if coh else None,
        "contradictions": coh.contradictions if coh else [],
        "draft_chars": len(draft),
    }


def _is_out(output) -> bool:
    return isinstance(output, dict) and "error" not in output


def eval_save(*, output, **_) -> Evaluation:
    saved = bool(output.get("saved")) if isinstance(output, dict) else False
    turns = output.get("turns_to_save") if isinstance(output, dict) else None
    return {"name": "saved", "value": saved, "comment": f"turns_to_save={turns}"}


def eval_grounding(*, output, **_) -> Evaluation | None:
    if not _is_out(output):
        return None
    # Sem claims extraídos → grounding INDEFINIDO (não 0%). Seções aspiracionais
    # (ex.: "Objetivo") legitimamente não têm afirmações verificáveis; contá-las
    # como 0.0 afundava a média artificialmente. Excluímos da agregação (None) e
    # expomos n_claims como score próprio (eval_n_claims) p/ flagrar rascunho vazio.
    if not output.get("n_claims"):
        return None
    return {"name": "pct_grounded", "value": output.get("pct_grounded"),
            "comment": f"{output.get('n_grounded')}/{output.get('n_claims')} claims"}


def eval_n_claims(*, output, **_) -> Evaluation | None:
    """Expõe nº de claims extraídos — flagra rascunho vacuous (0 claims) sem
    contaminar o grounding. Score operacional, não barra de qualidade."""
    if not _is_out(output):
        return None
    return {"name": "n_claims", "value": output.get("n_claims")}


def eval_factual_errors(*, output, **_) -> Evaluation | None:
    if not _is_out(output):
        return None
    return {"name": "n_factual_errors", "value": output.get("n_factual_errors")}


def eval_coherence(*, output, **_) -> Evaluation | None:
    if not _is_out(output) or output.get("coherent") is None:
        return None
    return {"name": "coherent", "value": bool(output.get("coherent")),
            "comment": f"{len(output.get('contradictions', []))} contradições"}


def eval_tool_calls(*, output, **_) -> Evaluation | None:
    if not _is_out(output):
        return None
    return {"name": "n_tool_calls", "value": output.get("n_tool_calls")}


def _prereqs() -> str | None:
    for var in ("SUPABASE_URL", "SUPABASE_SERVICE_KEY"):
        if not os.getenv(var):
            return f"requer {var} (sessões + retrieval)"
    if not (os.getenv("OPENAI_API_KEY") or os.getenv("ANTHROPIC_API_KEY")):
        return "requer OPENAI_API_KEY ou ANTHROPIC_API_KEY (agente + juízes)"
    if not os.getenv("EVAL_WORKSPACE_ID"):
        return "requer EVAL_WORKSPACE_ID (workspace de eval para as sessões)"
    if not FIXTURE.exists():
        return "fixture tests/fixtures/eval_cases.json ausente"
    return None


SUITE = Suite(
    name="writing",
    description="Grounding + erros factuais + coerência + métricas operacionais do agente de escrita.",
    load_data=load_data,
    task=task,
    evaluators=[eval_save, eval_grounding, eval_n_claims, eval_factual_errors,
                eval_coherence, eval_tool_calls],
    prereqs=_prereqs,
)
