from __future__ import annotations

import json

from config import ROOT
from core.services.explore_routing import (
    Intent,
    RouteContext,
    RouteDecision,
    handoff_target,
    redirect_for,
    route_message,
)


def test_cases_motivadores_seguem_rota_do_golden():
    golden = json.loads(
        (ROOT / "eval_data" / "golden" / "explore.json").read_text(encoding="utf-8")
    )
    assert len(golden["cases"]) == 4
    for case in golden["cases"]:
        target = case["target"]
        decision = route_message(RouteContext(
            message=case["query"], target_type=target["type"], target_id=target["id"],
        ))
        assert decision.intent.value == case["expected_route"], case["id"]
        assert redirect_for(decision) is None, case["id"]


def test_redirect_exige_acao_explicita():
    factual = route_message(RouteContext(message="qual a tese de investimentos da Barn?"))
    assert factual.intent == Intent.ENTITY_FACT
    assert redirect_for(factual) is None

    action = route_message(RouteContext(message="escreva a seção de impacto"))
    assert action.intent == Intent.WRITING_ACTION
    assert "/escrita" in (redirect_for(action) or "")


def test_entidade_nomeada_prevalece_sobre_edital_em_foco():
    decision = route_message(RouteContext(
        message="qual a tese de investimentos da Barn?",
        target_type="edital", target_id="finep:745",
    ))
    assert decision.intent == Intent.ENTITY_FACT
    assert decision.target_type == "investidor"
    assert decision.target_id is None


def test_classificador_limitado_so_e_usado_em_ambiguidade():
    calls = []

    def classifier(context):
        calls.append(context.message)
        return Intent.DISCOVERY

    deterministic = route_message(
        RouteContext(message="quais os itens financiáveis?", target_type="edital"),
        classifier,
    )
    assert deterministic.intent == Intent.EDITAL_FACT_ENUMERATIVE
    assert calls == []

    ambiguous = route_message(RouteContext(message="quero entender o cenário"), classifier)
    assert ambiguous.intent == Intent.DISCOVERY
    assert calls == ["quero entender o cenário"]


def test_handoff_target_table():
    """handoff_target: tabela intent × modo_atual → alvo esperado (função pura)."""
    cases: list[tuple[Intent, str, str | None]] = [
        # WRITING_ACTION → escrita (se não estiver já em escrita)
        (Intent.WRITING_ACTION, "explorer", "escrita"),
        (Intent.WRITING_ACTION, "escrita", None),
        # PLAN_ACTION → escrita (escrita absorve o plano, Task 4)
        (Intent.PLAN_ACTION, "explorer", "escrita"),
        (Intent.PLAN_ACTION, "escrita", None),
        # Intents factuais/de exploração → explorer
        (Intent.EDITAL_FACT, "escrita", "explorer"),
        (Intent.EDITAL_FACT, "explorer", None),
        (Intent.EDITAL_FACT_ENUMERATIVE, "escrita", "explorer"),
        (Intent.EDITAL_FACT_ENUMERATIVE, "explorer", None),
        (Intent.EDITAL_SUMMARY, "escrita", "explorer"),
        (Intent.EDITAL_SUMMARY, "explorer", None),
        (Intent.ENTITY_FACT, "escrita", "explorer"),
        (Intent.ENTITY_FACT, "explorer", None),
        (Intent.DISCOVERY, "escrita", "explorer"),
        (Intent.DISCOVERY, "explorer", None),
        (Intent.MATCH_PROFILE, "escrita", "explorer"),
        (Intent.MATCH_PROFILE, "explorer", None),
        (Intent.CONCEPTUAL, "escrita", "explorer"),
        (Intent.CONCEPTUAL, "explorer", None),
    ]
    for intent, current_mode, expected in cases:
        decision = RouteDecision(intent=intent, target_type=None, target_id=None)
        result = handoff_target(decision, current_mode)
        assert result == expected, (
            f"handoff_target({intent.value}, current_mode='{current_mode}') "
            f"retornou {result!r}, esperava {expected!r}"
        )


def test_handoff_target_nao_forca_por_fallback_ambiguo():
    """handoff_target respeita reason_code=safe_conceptual_fallback: não expulsa."""
    fallback = RouteDecision(
        intent=Intent.CONCEPTUAL,
        target_type=None, target_id=None,
        confidence=0.5, reason_code="safe_conceptual_fallback",
    )
    # Em escrita: não expulsa para explorer
    assert handoff_target(fallback, "escrita") is None
    # Em explorer: fica onde está
    assert handoff_target(fallback, "explorer") is None
