"""Testes do schema tipado de extração v2 (domain/edital_extraction.py).

Hermético (sem LLM): valida o contrato — defaults de abstenção, `is_present`,
as estruturas v2 (counterpart com %, funding_amount, eligibility_constraints),
geração de JSON schema e round-trip.
"""
from __future__ import annotations

import pytest

from radar.domain.edital_extraction import (
    DECISION_FIELDS,
    GATE_FIELDS,
    Counterpart,
    EditalExtraction,
    EligibilityConstraint,
    Extracted,
    FieldState,
    FundingAmount,
    TrlRange,
)

pytestmark = pytest.mark.unit


def test_defaults_sao_absent():
    e = EditalExtraction(source="finep", native_id="612")
    for f in DECISION_FIELDS:
        field = getattr(e, f)
        assert field.state is FieldState.ABSENT
        assert field.is_present is False
    assert e.objective is None
    assert e.key_requirements == []
    assert e.funding_amount is None
    assert e.eligibility_constraints == []


def test_gate_fields_sao_subconjunto_de_decisao():
    # counterpart e requires_ict_partner são DECISÃO mas NÃO gate
    assert set(GATE_FIELDS) < set(DECISION_FIELDS)
    assert "counterpart" not in GATE_FIELDS
    assert "requires_ict_partner" not in GATE_FIELDS


def test_campo_stated_fica_presente():
    e = EditalExtraction(
        source="finep", native_id="612",
        themes=Extracted(value=["bioeconomia"], state=FieldState.STATED,
                         evidence="temáticas: bioeconomia"),
        trl_range=Extracted(value=TrlRange(min=4, max=6), state=FieldState.STATED),
    )
    assert e.themes.is_present and e.themes.value == ["bioeconomia"]
    assert e.trl_range.value.max == 6


def test_counterpart_com_percentual():
    e = EditalExtraction(
        source="finep", native_id="x",
        counterpart=Extracted(value=Counterpart(required=True, percentage=20.0),
                              state=FieldState.STATED, evidence="contrapartida de 20%"),
    )
    assert e.counterpart.value.required is True
    assert e.counterpart.value.percentage == 20.0


def test_funding_e_constraints_sao_contexto():
    e = EditalExtraction(
        source="finep", native_id="x",
        funding_amount=FundingAmount(min=500_000, max=2_000_000),
        eligibility_constraints=[
            EligibilityConstraint(type="region", description="apenas RS/SC/PR",
                                  state=FieldState.STATED, evidence="sediadas no Sul"),
        ],
    )
    assert e.funding_amount.max == 2_000_000
    assert e.eligibility_constraints[0].type == "region"


def test_absent_com_valor_nao_conta_como_presente():
    f: Extracted[str] = Extracted(value="x", state=FieldState.ABSENT)
    assert f.is_present is False


def test_json_schema_gerado():
    schema = EditalExtraction.model_json_schema()
    for k in ("eligible_entities", "counterpart", "funding_amount", "eligibility_constraints"):
        assert k in schema["properties"]


def test_roundtrip():
    e = EditalExtraction(
        source="fapesp", native_id="18067",
        eligible_entities=Extracted(value=["empresas"], state=FieldState.STATED),
        objective="Apoiar pesquisa aplicada.",
        project_duration_months=24,
    )
    again = EditalExtraction.model_validate(e.model_dump())
    assert again.eligible_entities.value == ["empresas"]
    assert again.project_duration_months == 24
