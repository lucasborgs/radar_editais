"""Testes herméticos dos evaluators da suíte extraction (sem LLM).

Valida a lógica de pontuação — presença/abstenção, correção de value, e
faithfulness verbatim — com dumps fabricados de EditalExtraction.
"""
from __future__ import annotations

import pytest

from core.eval import extraction as ex
from domain.edital_extraction import (
    Counterpart,
    EditalExtraction,
    Extracted,
    FieldState,
)

pytestmark = pytest.mark.unit


def _dump(**fields) -> dict:
    return EditalExtraction(source="finep", native_id="1", **fields).model_dump()


def test_presence_accuracy_perfeita():
    gold = _dump(themes=Extracted(value=["x"], state=FieldState.STATED))
    pred = _dump(themes=Extracted(value=["x"], state=FieldState.STATED))
    r = ex.eval_presence(output=pred, expected_output=gold)
    assert r["value"] == 1.0


def test_presence_penaliza_absent_errado():
    # gold diz que themes está presente; pred marcou absent → erra 1 de 6 campos
    gold = _dump(themes=Extracted(value=["x"], state=FieldState.STATED))
    pred = _dump()  # tudo absent
    r = ex.eval_presence(output=pred, expected_output=gold)
    assert r["value"] < 1.0
    assert round(r["value"] * 6) == 5  # 5/6 campos batem (só themes diverge)


def test_value_correctness_listas_e_escalares():
    gold = _dump(
        themes=Extracted(value=["Bio", "energia"], state=FieldState.STATED),
        mechanism=Extracted(value="subvencao", state=FieldState.STATED),
    )
    pred = _dump(
        themes=Extracted(value=["bio", "ENERGIA"], state=FieldState.STATED),  # casa (normalizado)
        mechanism=Extracted(value="reembolsavel", state=FieldState.STATED),    # erra
    )
    r = ex.eval_value(output=pred, expected_output=gold)
    assert r["value"] == 0.5  # 1 de 2 campos presentes-em-ambos


def test_faithfulness_pega_evidencia_fabricada():
    raw = "podem participar empresas brasileiras de todos os portes"
    pred = _dump(
        eligible_entities=Extracted(value=["empresas"], state=FieldState.STATED,
                                    evidence="empresas brasileiras de todos os portes"),  # verbatim
        mechanism=Extracted(value="subvencao", state=FieldState.STATED,
                            evidence="recursos de subvenção econômica"),  # NÃO está no raw
    )
    r = ex.eval_faithfulness(input={"raw": raw}, output=pred)
    assert r["value"] == 0.5  # 1 de 2 evidências é substring real


def test_counterpart_compara_required_ignora_percentual():
    gold = _dump(counterpart=Extracted(value=Counterpart(required=True, percentage=20),
                                       state=FieldState.STATED))
    pred = _dump(counterpart=Extracted(value=Counterpart(required=True, percentage=None),
                                       state=FieldState.STATED))
    r = ex.eval_value(output=pred, expected_output=gold)
    assert r["value"] == 1.0  # required bate, % ignorado no match


def test_prereqs_respeita_backend_gemini(monkeypatch):
    monkeypatch.setenv("LLM_BACKEND", "gemini")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    assert "GEMINI_API_KEY" in ex._prereqs()

    monkeypatch.setenv("GEMINI_API_KEY", "dummy")
    assert ex._prereqs() is None
