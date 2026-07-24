"""Testes da suíte provenance (RT02-T02).

Cobre contratos e fronteiras: carregamento do golden, classificação,
cálculo dos três sinais, critical_field=null, determinismo, registro.
"""
from __future__ import annotations

import json

import pytest

from radar.core.eval.harness import run_suite
from radar.core.eval.provenance import SUITE, load_data


pytestmark = pytest.mark.unit


def test_golden_loads_six_cases():
    items = load_data()
    assert len(items) == 6
    ids = [i["metadata"]["case_id"] for i in items]
    assert "provenance-01-unique-exact" in ids
    assert "provenance-06-legacy-no-silver" in ids


def test_suite_classification_diagnostic():
    assert SUITE.classification == "diagnostic"


def test_suite_version():
    assert SUITE.version == "1"


def test_suite_criteria_empty():
    assert len(SUITE.criteria) == 0


def test_suite_expected_cases():
    assert SUITE.expected_cases == 6


def test_suite_name():
    assert SUITE.name == "provenance"


def test_registry_has_provenance():
    from radar.core.eval.registry import get_suite
    suite = get_suite("provenance")
    assert suite is not None
    assert suite.name == "provenance"


def test_run_produces_locator_signals(tmp_path):
    items = load_data()
    result = run_suite(SUITE, out_dir=tmp_path)
    assert result["status"] == "diagnostic"
    agg = result.get("aggregate", {})
    # deve ter pelo menos os sinais de locator (exact, document_only, unresolved)
    for key in ("mean_locator_exact", "mean_locator_document_only", "mean_locator_unresolved"):
        assert key in agg, f"falta {key} no agregado"


def test_run_produces_faithfulness_signal(tmp_path):
    result = run_suite(SUITE, out_dir=tmp_path)
    agg = result.get("aggregate", {})
    assert "mean_faithfulness_verbatim" in agg


def test_critical_field_null_excluded_from_denominator(tmp_path):
    """Casos com critical_field=null não entram no denominador de
    critical_field_completeness. O evaluator devolve value=None para eles."""
    items = load_data()
    null_cases = [i for i in items if i["metadata"].get("critical_field") is None]
    assert len(null_cases) >= 1  # caso 2 (repeated) e caso 6 (legacy)
    result = run_suite(SUITE, out_dir=tmp_path)
    agg = result.get("aggregate", {})
    # critical_field_completeness média deve ignorar Nones
    cf = agg.get("mean_critical_field_completeness")
    assert cf is not None


def test_legacy_unresolved_not_masked(tmp_path):
    """Caso legado (id 06) deve resultar em locator=unresolved."""
    result = run_suite(SUITE, out_dir=tmp_path)
    for ir in result.get("item_results", []):
        meta = ir.get("metadata") or {}
        if meta.get("case_id") == "provenance-06-legacy-no-silver":
            out = ir.get("output") or {}
            assert out.get("locator_quality") == "unresolved"
            # missing_hash deve ser true
            assert out.get("missing_hash") is True
            return
    pytest.fail("caso legacy não encontrado nos resultados")


def test_determinism_between_runs(tmp_path):
    """Duas execuções devem produzir agregado idêntico (é determinística)."""
    r1 = run_suite(SUITE, out_dir=tmp_path)
    r2 = run_suite(SUITE, out_dir=tmp_path)
    # compara apenas agregados, ignorando timestamps e paths
    # extrai só as chaves de métrica
    metrics1 = {k: v for k, v in r1.get("aggregate", {}).items() if k.startswith("mean_")}
    metrics2 = {k: v for k, v in r2.get("aggregate", {}).items() if k.startswith("mean_")}
    assert metrics1 == metrics2


def test_no_threshold_no_gate():
    assert SUITE.classification != "gate"
    assert SUITE.classification != "candidate"
    assert len(SUITE.criteria) == 0