"""Contrato fechado para respostas estratégicas ancoradas no KG-P1D.

Este módulo não interpreta histórico nem texto livre como evidência. O único
vocabulário factual aceito vem do payload corrente de ``graph_strategy``.
"""
from __future__ import annotations

import json
import logging
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from radar.core.services import temporal_read_model
from radar.domain.data_quality import ValidityState

logger = logging.getLogger(__name__)


class StrategyAction(str, Enum):
    EVALUATE_OPPORTUNITY = "evaluate_opportunity"
    VERIFY_ELIGIBILITY = "verify_eligibility"
    MONITOR_OPPORTUNITY = "monitor_opportunity"
    CONTACT_ICT = "contact_ict"
    CONTACT_AGENCY = "contact_agency"
    ASSESS_PROGRAM = "assess_program"
    ASSESS_INVESTOR = "assess_investor"


class SynthesisSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    kind: str = Field(min_length=1)
    action: StrategyAction
    fact_refs: list[str] = Field(default_factory=list)


class StrategySynthesis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    selections: list[SynthesisSelection] = Field(default_factory=list)


def fact_ref(fact: dict[str, Any]) -> str:
    return "|".join(str(fact.get(key, "")) for key in ("source", "predicate", "target", "origin"))


def _results(payload: dict[str, Any]) -> list[dict[str, Any]]:
    groups = payload.get("results_by_type")
    if not isinstance(groups, dict):
        return []
    return [item for kind in sorted(groups) for item in groups.get(kind, []) if isinstance(item, dict)]


def validate_synthesis(raw: Any, payload: dict[str, Any]) -> StrategySynthesis:
    """Valida IDs, kinds e referências contra a geração corrente."""
    synthesis = StrategySynthesis.model_validate(raw)
    by_id = {str(item.get("id")): item for item in _results(payload)}
    allowed_facts: dict[str, set[str]] = {}
    for item in _results(payload):
        facts = item.get("evidence", {}).get("supporting_facts", [])
        allowed_facts[str(item.get("id"))] = {
            fact_ref(fact) for fact in facts if isinstance(fact, dict)
        }
    allowed_actions = {
        "edital": {StrategyAction.EVALUATE_OPPORTUNITY, StrategyAction.VERIFY_ELIGIBILITY,
                   StrategyAction.MONITOR_OPPORTUNITY},
        "ict": {StrategyAction.CONTACT_ICT},
        "agencia": {StrategyAction.CONTACT_AGENCY},
        "programa": {StrategyAction.ASSESS_PROGRAM},
        "investidor": {StrategyAction.ASSESS_INVESTOR},
    }
    for selection in synthesis.selections:
        item = by_id.get(selection.id)
        if item is None or item.get("kind") != selection.kind:
            raise ValueError("synthesis selection is not in current payload")
        if selection.action not in allowed_actions.get(selection.kind, set()):
            raise ValueError("synthesis action does not match kind")
        refs = set(selection.fact_refs)
        if not refs.issubset(allowed_facts.get(selection.id, set())):
            raise ValueError("synthesis fact reference is not in current payload")
        derived = item.get("evidence", {}).get("derived_steps", [])
        derived_refs = {fact_ref(step) for step in derived if isinstance(step, dict)}
        if refs & derived_refs:
            raise ValueError("derived step cannot be used as supporting fact")
    return synthesis


def _deterministic_synthesis(
    payload: dict[str, Any], temporal: dict[str, Any],
) -> StrategySynthesis:
    selections: list[SynthesisSelection] = []
    for item in _results(payload):
        kind = item.get("kind")
        refs = [
            fact_ref(fact) for fact in item.get("evidence", {}).get("supporting_facts", [])
            if isinstance(fact, dict)
        ]
        if not refs:
            continue
        action = {
            "programa": StrategyAction.ASSESS_PROGRAM,
            "agencia": StrategyAction.CONTACT_AGENCY,
            "ict": StrategyAction.CONTACT_ICT,
            "investidor": StrategyAction.ASSESS_INVESTOR,
        }.get(kind)
        if kind == "edital":
            state = temporal.get(str(item.get("id")))
            action = (StrategyAction.EVALUATE_OPPORTUNITY
                      if state is ValidityState.ACTIVE
                      else StrategyAction.VERIFY_ELIGIBILITY)
        if action is not None:
            selections.append(SynthesisSelection(
                id=str(item["id"]), kind=str(kind), action=action, fact_refs=refs,
            ))
    return StrategySynthesis(selections=selections)


def _native_id_from_graph_id(value: str) -> str:
    return value.split(":", 1)[1] if value.startswith("edital:") else value


def load_edital_temporal_rows(db: Any, ids: list[str]) -> list[dict[str, Any]]:
    """Uma leitura batch mínima dos campos temporais atuais do gold."""
    if db is None or not ids:
        return []
    native_ids = sorted({_native_id_from_graph_id(value) for value in ids})
    response = (db.table("entities")
                .select("native_id,deadline,status,updated_at")
                .eq("kind", "edital")
                .in_("native_id", native_ids)
                .execute())
    return response.data or []


def resolve_temporal(payload: dict[str, Any], db: Any = None) -> tuple[dict[str, Any], dict[str, int]]:
    editais = [item for item in _results(payload) if item.get("kind") == "edital"]
    ids = [str(item.get("id")) for item in editais]
    try:
        rows = load_edital_temporal_rows(db, ids)
        subjects = temporal_read_model.subjects_from_rows(rows)
        models = temporal_read_model.resolve_temporal_read_models(subjects)
    except Exception as exc:  # fail closed; do not expose exception content
        logger.info("kg_grounded temporal_resolution_failed category=%s", type(exc).__name__)
        models = {}
    by_graph_id = {}
    counts = {"active": 0, "needs_review": 0, "closed": 0}
    for item in editais:
        native_id = _native_id_from_graph_id(str(item.get("id")))
        model = models.get(native_id)
        state = model.validity_state if model else ValidityState.NEEDS_REVIEW
        by_graph_id[str(item.get("id"))] = state
        counts[state.value] = counts.get(state.value, 0) + 1
    return by_graph_id, counts


_ACTION_TEXT = {
    StrategyAction.EVALUATE_OPPORTUNITY: "avaliar oportunidade",
    StrategyAction.VERIFY_ELIGIBILITY: "confirmar elegibilidade e validade",
    StrategyAction.MONITOR_OPPORTUNITY: "monitorar oportunidade",
    StrategyAction.CONTACT_ICT: "avaliar contato com a ICT",
    StrategyAction.CONTACT_AGENCY: "avaliar contato com a agência",
    StrategyAction.ASSESS_PROGRAM: "avaliar o programa",
    StrategyAction.ASSESS_INVESTOR: "avaliar o investidor",
}


def render_grounded_response(
    payload: dict[str, Any], temporal: dict[str, Any],
) -> tuple[str, StrategySynthesis, bool]:
    fallback = False
    try:
        synthesis = validate_synthesis(_deterministic_synthesis(payload, temporal).model_dump(), payload)
    except (ValidationError, ValueError, TypeError):
        fallback = True
        synthesis = StrategySynthesis(selections=[])
    by_id = {str(item.get("id")): item for item in _results(payload)}
    lines = [
        "Recorte estratégico atual do grafo:",
        "A consulta cobre oportunidades, programas, agências, ICTs e investidores; "
        "fatos catalogados e relações derivadas são mantidos separados.",
    ]
    if not synthesis.selections:
        lines.append("Não há resultados explicáveis neste recorte; isso não indica inexistência no mercado.")
        return "\n".join(lines), synthesis, fallback
    for selection in synthesis.selections:
        item = by_id[selection.id]
        state = temporal.get(selection.id)
        if selection.kind == "edital" and state is ValidityState.CLOSED:
            continue
        label = item.get("name") or selection.id
        suffix = " (validade a confirmar)" if state is ValidityState.NEEDS_REVIEW else ""
        lines.append(f"- {label}: {_ACTION_TEXT[selection.action]}{suffix}.")
        shared = item.get("shared_characteristics", [])
        signals = sorted({str(entry.get("value")) for entry in shared if entry.get("value")})
        if signals:
            lines.append(f"  Sinais compartilhados: {', '.join(signals)}.")
    if len(lines) == 2:
        lines.append("Não há oportunidades ativas explicáveis neste recorte; isso não indica inexistência no mercado.")
    return "\n".join(lines), synthesis, fallback


def grounded_response(payload_text: str, *, db: Any = None) -> tuple[str, dict[str, Any]]:
    """Converte somente um payload JSON corrente em resposta e meta sanitizada."""
    try:
        payload = json.loads(payload_text)
        if not isinstance(payload, dict):
            raise ValueError("payload must be object")
    except (json.JSONDecodeError, TypeError, ValueError):
        payload = {"results_by_type": {}, "coverage": {}}
    temporal, temporal_counts = resolve_temporal(payload, db)
    answer, _synthesis, fallback = render_grounded_response(payload, temporal)
    groups = payload.get("results_by_type", {})
    counts = {kind: len(groups.get(kind, [])) for kind in ("edital", "programa", "agencia", "ict", "investidor")}
    meta = {
        "stop_reason": "grounded_response",
        "truncated": bool(payload.get("truncated", False)),
        "called_match": False,
        "called_tools": ["graph_strategy"],
        "graph_strategy_executed": True,
        "intent": "profile_strategy",
        "counts_by_kind": counts,
        "rejected_ids": [],
        "deterministic_fallback": fallback or not bool(payload.get("results_by_type")),
        "temporal_counts": temporal_counts,
    }
    return answer, meta


__all__ = [
    "StrategyAction", "StrategySynthesis", "SynthesisSelection", "fact_ref",
    "grounded_response", "load_edital_temporal_rows", "resolve_temporal",
    "render_grounded_response", "validate_synthesis",
]
