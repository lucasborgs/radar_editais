"""Testes do schema tipado de extração (domain/edital_extraction.py).

Hermético (sem LLM): valida o contrato — defaults de abstenção, `is_present`,
geração de JSON schema (para structured outputs na Fase 3) e round-trip.
"""
from __future__ import annotations

from domain.edital_extraction import (
    DECISION_FIELDS,
    EditalExtraction,
    Extracted,
    FieldState,
    TrlRange,
)


def test_defaults_sao_absent():
    e = EditalExtraction(source="finep", native_id="612")
    # campos DECISÃO nascem ausentes (não meio-crédito cego)
    for f in DECISION_FIELDS:
        field = getattr(e, f)
        assert field.state is FieldState.ABSENT
        assert field.is_present is False
    # contexto nasce vazio/None
    assert e.objective is None
    assert e.key_requirements == []


def test_campo_stated_fica_presente():
    e = EditalExtraction(
        source="finep",
        native_id="612",
        themes=Extracted(value=["bioeconomia"], state=FieldState.STATED,
                         evidence="item 2.1: bioeconomia"),
        trl_range=Extracted(value=TrlRange(min=4, max=6), state=FieldState.STATED),
    )
    assert e.themes.is_present
    assert e.themes.value == ["bioeconomia"]
    assert e.trl_range.value.max == 6


def test_inferred_conta_como_presente_mas_distinto():
    e = EditalExtraction(
        source="web", native_id="x",
        mechanism=Extracted(value="subvencao", state=FieldState.INFERRED),
    )
    assert e.mechanism.is_present  # inferred ainda decide
    assert e.mechanism.state is FieldState.INFERRED


def test_absent_com_valor_nao_conta_como_presente():
    # contrato: state manda; absent nunca é "presente" mesmo com valor residual
    f: Extracted[str] = Extracted(value="x", state=FieldState.ABSENT)
    assert f.is_present is False


def test_json_schema_gerado():
    # a Fase 3 usa isto como structured output — deve gerar sem erro e citar os campos
    schema = EditalExtraction.model_json_schema()
    assert "eligible_entities" in schema["properties"]
    assert "objective" in schema["properties"]


def test_roundtrip():
    e = EditalExtraction(
        source="fapesp", native_id="18067",
        eligible_entities=Extracted(value=["empresas"], state=FieldState.STATED),
        objective="Apoiar pesquisa aplicada.",
    )
    again = EditalExtraction.model_validate(e.model_dump())
    assert again.eligible_entities.value == ["empresas"]
    assert again.objective == "Apoiar pesquisa aplicada."
