"""Contrato fechado para respostas estratégicas ancoradas no KG-P1D.

Este módulo não interpreta histórico nem texto livre como evidência. O único
vocabulário factual aceito vem do payload corrente de ``graph_strategy``.
"""
from __future__ import annotations

import json
import logging
import os
import re
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from radar.core.services import temporal_read_model
from radar.domain.data_quality import ValidityState

logger = logging.getLogger(__name__)

STRATEGY_KINDS = ("edital", "programa", "agencia", "ict", "investidor")
STRATEGY_STATUS = frozenset({
    "ok", "insufficient_profile_anchors", "empty", "unavailable", "error", "invalid_request",
})
_CANONICAL_ID = re.compile(r"\b(?:edital|programa|agencia|ict|investidor|setor|tecnologia|uf|empresa):[A-Za-z0-9_.:-]+")


def _payload_ids(value: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        for item in value.values():
            found |= _payload_ids(item)
    elif isinstance(value, list):
        for item in value:
            found |= _payload_ids(item)
    elif isinstance(value, str):
        found |= set(_CANONICAL_ID.findall(value))
    return found


def unknown_cited_ids(answer: str, tool_outputs: list[str]) -> set[str]:
    """Fast-fail mecânico: só identifica IDs citados que não estão nos payloads."""
    payloads: list[dict[str, Any]] = []
    for output in tool_outputs:
        try:
            value = json.loads(output)
        except (TypeError, ValueError):
            continue
        if isinstance(value, dict):
            payloads.append(value)
    available = set().union(*(_payload_ids(payload) for payload in payloads)) if payloads else set()
    cited = set(_CANONICAL_ID.findall(answer or ""))
    return cited - available


def judge_grounding(
    message: str, answer: str, tool_outputs: list[str], *, provider: str, model: str,
) -> dict[str, Any] | None:
    """Juiz fechado: decide aterramento, não estilo nem criatividade."""
    from radar.core.llm.llm_client import make_client

    client = make_client(
        api_key=(os.getenv("OPENAI_API_KEY") if provider == "openai"
                 else os.getenv("ANTHROPIC_API_KEY")),
    )
    response = client.chat.completions.create(
        model=model,
        messages=[
            {
                "role": "system",
                "content": (
                    "Você é um juiz fechado de aterramento factual. Compare pergunta, resposta "
                    "e payloads atuais. Retorne JSON estrito com exatamente: requires_graph "
                    "(boolean), grounded (boolean), unsupported_claims (lista de strings). "
                    "Verifique nomes, IDs, correspondência nome-ID, fatos, relações e temporalidade. "
                    "Conceitos e saudações podem grounded=true sem graph tool. Pergunta factual "
                    "sobre o ecossistema sem payload de graph tool deve grounded=false. "
                    "Não avalie estilo, criatividade ou qualidade da recomendação."
                ),
            },
            {"role": "user", "content": json.dumps({
                "question": message, "answer": answer, "graph_tool_payloads": tool_outputs,
            }, ensure_ascii=False)},
        ],
        temperature=0,
        response_format={"type": "json_object"},
    )
    raw = json.loads(response.choices[0].message.content or "{}")
    if set(raw) != {"requires_graph", "grounded", "unsupported_claims"}:
        return None
    if not isinstance(raw["requires_graph"], bool) or not isinstance(raw["grounded"], bool):
        return None
    if not isinstance(raw["unsupported_claims"], list) or not all(
        isinstance(item, str) for item in raw["unsupported_claims"]
    ):
        return None
    return raw


def temporalize_tool_payload(payload_text: str, db: Any = None) -> str:
    """Remove editais encerrados e marca revisão antes do payload chegar à LLM."""
    try:
        payload = json.loads(payload_text)
    except (TypeError, ValueError):
        return payload_text
    if not isinstance(payload, dict):
        return payload_text
    if isinstance(payload.get("results_by_type"), dict):
        temporal, _counts = resolve_temporal(payload, db)
        editais = payload["results_by_type"].get("edital", [])
    else:
        ids = sorted(item for item in _payload_ids(payload) if item.startswith("edital:"))
        temporal, _counts = resolve_temporal(
            {"results_by_type": {"edital": [{"id": item} for item in ids]}}, db,
        )
        if not temporal:
            return payload_text
        for node in payload.get("nodes", []):
            if isinstance(node, dict) and node.get("id") in temporal:
                state = temporal[node["id"]]
                node["temporal_state"] = state.value
                if state is ValidityState.NEEDS_REVIEW:
                    node["temporal_note"] = "validade a confirmar"
        closed = {item for item, state in temporal.items() if state is ValidityState.CLOSED}
        if isinstance(payload.get("nodes"), list):
            payload["nodes"] = [node for node in payload["nodes"]
                                if not isinstance(node, dict) or node.get("id") not in closed]
        if isinstance(payload.get("edges"), list):
            payload["edges"] = [edge for edge in payload["edges"]
                                if not isinstance(edge, dict)
                                or not ({edge.get("source"), edge.get("target")} & closed)]
        for bucket in (payload.get("members_by_kind") or {}).values():
            if not isinstance(bucket, dict) or not isinstance(bucket.get("ids"), list):
                continue
            pairs = list(zip(bucket["ids"], bucket.get("names", []), strict=False))
            kept_pairs = [(node_id, name) for node_id, name in pairs if node_id not in closed]
            bucket["ids"] = [node_id for node_id, _name in kept_pairs]
            bucket["names"] = [name for _node_id, name in kept_pairs]
            bucket["count"] = len(bucket["ids"])
        for key in ("paths_to_profile", "paths_to_actors"):
            if isinstance(payload.get(key), list):
                payload[key] = [path for path in payload[key]
                                if not any(isinstance(step, dict) and
                                           ({step.get("source"), step.get("target")} & closed)
                                           for step in path)]
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    kept = []
    for item in editais:
        if not isinstance(item, dict):
            continue
        state = temporal.get(str(item.get("id")), ValidityState.NEEDS_REVIEW)
        if state is ValidityState.CLOSED:
            continue
        copy = dict(item)
        copy["temporal_state"] = state.value
        if state is ValidityState.NEEDS_REVIEW:
            copy["temporal_note"] = "validade a confirmar"
        kept.append(copy)
    payload["results_by_type"]["edital"] = kept
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


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
        shared = item.get("shared_characteristics", [])
        derived = item.get("evidence", {}).get("derived_steps", [])
        if not refs and not shared and not derived:
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
    status = payload.get("status")
    status_messages = {
        "insufficient_profile_anchors": "O perfil não produziu âncoras suficientes na taxonomia atual; não foi fabricada afinidade aproximada.",
        "unavailable": "O grafo está indisponível; não foi possível consultar o recorte solicitado.",
        "error": "Falha ao consultar o grafo; não foi possível produzir uma resposta estratégica.",
        "invalid_request": "O recorte solicitado é inválido; nenhum tipo foi consultado.",
    }
    if status in status_messages:
        return status_messages[status], synthesis, fallback
    by_id = {str(item.get("id")): item for item in _results(payload)}
    lines = ["Recorte estratégico atual do grafo:"]
    if not synthesis.selections:
        lines.append(
            "Nenhuma entidade foi encontrada no recorte solicitado."
            if status == "empty" else "Não há resultados explicáveis no payload atual."
        )
        return "\n".join(lines), synthesis, fallback
    closed_count = 0
    review_lines: list[str] = []
    for selection in synthesis.selections:
        item = by_id[selection.id]
        state = temporal.get(selection.id)
        if selection.kind == "edital" and state is ValidityState.CLOSED:
            closed_count += 1
            continue
        label = item.get("name") or selection.id
        lines.extend([f"\n### {label} ({selection.id})", "Afinidade derivada:"])
        shared = item.get("shared_characteristics", [])
        signals = sorted({str(entry.get("value")) for entry in shared if entry.get("value")})
        if signals:
            lines.append(f"- sinais compartilhados: {', '.join(signals)}")
        for step in item.get("evidence", {}).get("derived_steps", []):
            if isinstance(step, dict):
                lines.append(
                    f"- relação derivada: {step.get('source', '')} -[{step.get('predicate', '')}]-> {step.get('target', '')}"
                )
        lines.append("Fatos confirmados:")
        facts = item.get("evidence", {}).get("supporting_facts", [])
        for fact in facts:
            if isinstance(fact, dict):
                lines.append(
                    f"- {fact.get('source', '')} -[{fact.get('predicate', '')}]-> {fact.get('target', '')} "
                    f"(origem: {fact.get('origin', '')})"
                )
        if not facts:
            lines.append("- nenhum fato confirmado no payload atual")
        lines.append("Caminho justificativo:")
        for step in item.get("path", []):
            if isinstance(step, dict):
                lines.append(
                    f"- {step.get('source', '')} -[{step.get('predicate', '')}]-> {step.get('target', '')}"
                )
        action_line = f"Ação recomendada: {_ACTION_TEXT[selection.action]}."
        if state is ValidityState.NEEDS_REVIEW:
            review_lines.append(f"- {label} ({selection.id}): validade a confirmar; {action_line}")
        else:
            lines.append(action_line)
    if review_lines:
        lines.append("\n## Validade a confirmar")
        lines.extend(review_lines)
    if closed_count:
        lines.append(f"\n{closed_count} edital(is) encerrado(s) foram excluído(s) das recomendações.")
    return "\n".join(lines), synthesis, fallback


def grounded_response(payload_text: str, *, db: Any = None) -> tuple[str, dict[str, Any]]:
    """Converte somente um payload JSON corrente em resposta e meta sanitizada."""
    try:
        payload = json.loads(payload_text)
        if not isinstance(payload, dict):
            raise ValueError("payload must be object")
    except (json.JSONDecodeError, TypeError, ValueError):
        payload = {"status": "error", "results_by_type": {}, "coverage": {}}
    if payload.get("status") not in STRATEGY_STATUS:
        payload["status"] = "error"
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
        "status": payload.get("status"),
        "requested_types": payload.get("requested_types", []),
    }
    return answer, meta


__all__ = [
    "StrategyAction", "StrategySynthesis", "SynthesisSelection", "fact_ref",
    "grounded_response", "load_edital_temporal_rows", "resolve_temporal",
    "render_grounded_response", "validate_synthesis",
]
