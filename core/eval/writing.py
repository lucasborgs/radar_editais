"""Suíte de avaliação do agente de escrita (fim-a-fim).

Porta `scripts/eval_agent_writing.py` para o harness unificado. O `task` roda
uma WritingSession real (agente + tools) até salvar a seção e computa os
artefatos de julgamento (grounding por-claim, erros factuais, coerência) — que
dependem do DB/escopo da sessão. Os `evaluators` são finos: expõem cada métrica
como score nomeado. Métricas operacionais (turnos até salvar, nº de tool calls,
latência) viram scores também — instrumentação do harness de agente no mesmo lugar.

Pré-requisitos (toca DB + LLM): SUPABASE_*, OPENAI/ANTHROPIC e EVAL_WORKSPACE_ID
(workspace onde as sessões de eval são criadas).

F0 (2026-07-12): eval_cases_v2.json = golden §4 com 4 famílias.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Any

from config import ROOT
from core.eval.harness import Evaluation, Suite, get_input

logger = logging.getLogger(__name__)

FIXTURE = ROOT / "tests" / "fixtures" / "eval_cases.json"
FIXTURE_V2 = ROOT / "tests" / "fixtures" / "eval_cases_v2.json"


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
    from core.db import get_supabase_service
    from core.kg.temporal import render_temporal_block
    from core.llm.agent_tools.critic_agent import _build_proposal_context
    from core.retrieval.retriever import format_chunks_for_prompt, retrieve_chunks
    from core.services.writing_session import WritingSession
    from core.writing_eval import (
        extract_edital_claims,
        judge_factual_errors,
        judge_internal_coherence,
        score_grounding,
    )

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
        "draft": draft,
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


# ---------------------------------------------------------------------------
# Evaluadores do Golden §4 (F0)
# ---------------------------------------------------------------------------


def _trigram_overlap(a: str, b: str) -> float:
    """Fração de trigramas de `b` contidos em `a` (0..1)."""
    if len(a) < 3 or len(b) < 3:
        return 0.0
    tri_a = {a[i:i+3] for i in range(len(a) - 2)}
    tri_b = {b[i:i+3] for i in range(len(b) - 2)}
    if not tri_b:
        return 0.0
    return len(tri_a & tri_b) / len(tri_b)


def eval_section_hallucination(*, output, metadata=None, **_) -> Evaluation | None:
    """Flag se o conteúdo do rascunho contém seções que NÃO estão no outline
    do edital — medido por overlap de trigramas com os títulos de seção
    conhecidos. 0 = sem alucinação, 1+ = seções suspeitas."""
    if not isinstance(output, dict):
        return None
    section = output.get("section", "")
    content = output.get("draft", "")
    if not content:
        return None
    n_extra = 0
    lines = [ln.strip() for ln in content.split("\n") if ln.strip()]
    for line in lines:
        if line.startswith("#") and len(line) > 5:
            overlap = _trigram_overlap(section, line)
            if overlap < 0.3:
                n_extra += 1
    return {"name": "section_hallucination", "value": n_extra,
            "comment": f"{n_extra} heading(s) fora do outline"}


def eval_user_edit_preserved(*, output, metadata=None, **_) -> Evaluation | None:
    """Família 3: verifica se a edição do usuário (edit_intent) está
    presente no rascunho final. None se não for caso de user edit."""
    if not isinstance(output, dict) or not metadata:
        return None
    edit_intent = metadata.get("edit_intent", "")
    if not edit_intent:
        return None
    draft = output.get("draft", "")
    preserved = edit_intent.lower() in draft.lower()
    return {"name": "user_edit_preserved", "value": preserved,
            "comment": "preservou" if preserved else "perdeu edição do usuário"}


def eval_misfit_honesty(*, output, metadata=None, **_) -> Evaluation | None:
    """Família 2: o agente RECUSOU educadamente (true) ou tentou fabricar
    alinhamento (false). None se não for caso misfit."""
    if not isinstance(output, dict) or not metadata:
        return None
    familia = metadata.get("familia")
    if familia != 2:
        return None
    draft = output.get("draft", "")
    recusa = bool(re.search(
        r"(não atende|incompatível|não é adequado|não se alinha|não se enquadra"
        r"|lamentamos informar|não podemos|fora do escopo|impossível|não é possível"
        r"|recusamos)", draft.lower()
    ))
    return {"name": "misfit_honesty", "value": recusa,
            "comment": "recusou" if recusa else "aceitou (provável alucinação)"}


def eval_tools0_sections(*, output, **_) -> Evaluation | None:
    """Flag se alguma seção foi gerada sem tool calls (possível bypass do
    agente). 0 = todas usaram tools, >0 = seções suspect."""
    if not isinstance(output, dict):
        return None
    # O output atual não expõe tools-por-seção no task().
    # F0: valor sentinela — em fases posteriores será populado.
    return {"name": "tools0_sections", "value": None,
            "comment": "não disponível na task atual (F0)"}


# ---------------------------------------------------------------------------
# load_data para eval_cases_v2 (golden §4)
# ---------------------------------------------------------------------------

_N_RUNS = 3


def load_data_v2() -> list[dict]:
    if not FIXTURE_V2.exists():
        return []
    data = json.loads(FIXTURE_V2.read_text(encoding="utf-8"))
    profiles = data.get("profiles", {})
    items = []
    for case in data.get("cases", []):
        raw = profiles.get(case["profile"])
        if raw is None:
            continue
        # Decisão B2 (F0): edital_ids_extra no metadata é planejamento —
        # a task() atual cria WritingSession só com edital_id primário.
        # F1 vai wirear o batch multi-edital; até lá, baseline mede só singleton.
        familia = case.get("familia")
        item = {
            "input": {
                "profile": raw,
                "edital_id": case["edital_id"],
                "instruction": case["instruction"],
                "section": case.get("section"),
                "max_turns": case.get("max_turns", 4),
            },
            "expected_output": None,
            "metadata": {
                "case_id": case["id"],
                "edital_id": case["edital_id"],
                "familia": familia,
                "edit_intent": case.get("edit_intent", ""),
                "edital_ids_extra": case.get("edital_ids_extra", []),
            },
        }
        # N-runs: replica cada caso N vezes para estabilidade métrica
        for _ in range(_N_RUNS):
            items.append(item)
    return items


def _prereqs_v2() -> str | None:
    for var in ("SUPABASE_URL", "SUPABASE_SERVICE_KEY"):
        if not os.getenv(var):
            return f"requer {var} (sessões + retrieval)"
    if not (os.getenv("OPENAI_API_KEY") or os.getenv("ANTHROPIC_API_KEY")):
        return "requer OPENAI_API_KEY ou ANTHROPIC_API_KEY (agente + juízes)"
    if not os.getenv("EVAL_WORKSPACE_ID"):
        return "requer EVAL_WORKSPACE_ID (workspace de eval para as sessões)"
    if not FIXTURE_V2.exists():
        return "fixture tests/fixtures/eval_cases_v2.json ausente"
    return None


SUITE_WRITING_V2 = Suite(
    name="writing_v2",
    description="Golden §4: 4 famílias (bem-casados, misfit, user edit, batch E2E) + N-runs.",
    load_data=load_data_v2,
    task=task,
    evaluators=[
        eval_save, eval_grounding, eval_n_claims, eval_factual_errors,
        eval_coherence, eval_tool_calls,
        eval_section_hallucination, eval_user_edit_preserved,
        eval_misfit_honesty, eval_tools0_sections,
    ],
    prereqs=_prereqs_v2,
)


# ---------------------------------------------------------------------------
# Suíte original (backward compat)
# ---------------------------------------------------------------------------

SUITE = Suite(
    name="writing",
    description="Grounding + erros factuais + coerência + métricas operacionais do agente de escrita.",
    load_data=load_data,
    task=task,
    evaluators=[eval_save, eval_grounding, eval_n_claims, eval_factual_errors,
                eval_coherence, eval_tool_calls],
    prereqs=_prereqs,
)
