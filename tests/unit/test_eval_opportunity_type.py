"""Contrato da suíte opportunity_type: erro operacional não é acurácia."""
from __future__ import annotations

import pytest

from radar.core.eval.harness import Suite, run_suite
from radar.core.eval.opportunity_type import eval_type_accuracy, task

pytestmark = pytest.mark.unit


def test_operational_failure_does_not_emit_quality_score():
    output = {"error": "extração retornou None"}

    assert eval_type_accuracy(
        output=output,
        expected_output={"opportunity_type": "edital"},
    ) is None


def test_extraction_failure_stays_operational_without_calling_external_llm(monkeypatch):
    from radar.core.ingestion import opportunity_discovery

    monkeypatch.setattr(
        opportunity_discovery,
        "_make_client",
        lambda _: (object(), "test-model"),
    )
    monkeypatch.setattr(opportunity_discovery, "_extract", lambda *args, **kwargs: None)

    output = task(item={
        "input": {"title": "Teste", "url": "https://example.test", "text": "texto"},
    })

    assert output == {"error": "extração retornou None"}
    assert eval_type_accuracy(
        output=output,
        expected_output={"opportunity_type": "edital"},
    ) is None


def test_invalid_classification_does_not_emit_quality_score():
    assert eval_type_accuracy(
        output={"opportunity_type": None},
        expected_output={"opportunity_type": "edital"},
    ) is None


def test_valid_mismatch_remains_a_quality_score():
    evaluation = eval_type_accuracy(
        output={"opportunity_type": "desafio"},
        expected_output={"opportunity_type": "edital"},
    )

    assert evaluation == {
        "name": "type_accuracy",
        "value": False,
        "comment": "previsto=desafio esperado=edital",
    }


def test_run_with_only_operational_failures_has_no_accuracy_aggregate(tmp_path):
    suite = Suite(
        name="opportunity_type_contract",
        description="teste do contrato de score",
        load_data=lambda: [{
            "input": {},
            "expected_output": {"opportunity_type": "edital"},
            "metadata": {"case_id": "connection-error"},
        }],
        task=lambda **_: {"error": "extração retornou None"},
        evaluators=[eval_type_accuracy],
        expected_cases=1,
    )

    result = run_suite(suite, out_dir=tmp_path)

    assert result["status"] == "error"
    assert result["item_results"][0]["output"] == {
        "error": "extração retornou None",
    }
    assert "mean_type_accuracy" not in result["aggregate"]
    assert result["item_results"][0]["evaluations"] == []
    assert result["manifest"]["execution"]["errors"] == [{
        "stage": "task",
        "case_id": "connection-error",
        "error": "extração retornou None",
    }]
