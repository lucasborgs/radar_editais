"""Testes da suíte provenance (RT02-T02).

Cobre contratos e fronteiras: carregamento do golden, classificação,
cálculo dos três sinais, critical_field=null, determinismo, registro.
"""

from __future__ import annotations

import pytest

from radar.core.eval.harness import run_suite
from radar.core.eval.provenance import SUITE, _get_expected_case_ids, load_data

pytestmark = pytest.mark.unit


def test_golden_loads_six_cases():
    items = load_data()
    assert len(items) == 6
    ids = [i["metadata"]["case_id"] for i in items]
    assert "provenance-01-unique-exact" in ids
    assert "provenance-06-legacy-no-silver" in ids


def test_expected_case_ids_are_pure_and_unique_across_reloads():
    load_data()
    first = _get_expected_case_ids()
    load_data()
    second = _get_expected_case_ids()
    assert first == second
    assert len(first) == 6
    assert len(set(first)) == 6


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


def test_run_reports_concrete_locator_and_faithfulness_denominators(tmp_path):
    result = run_suite(SUITE, out_dir=tmp_path)
    assert result["status"] == "diagnostic"
    agg = result.get("aggregate", {})
    assert agg["mean_locator_exact"] == 0.3333  # 2/6, arredondado pelo harness
    assert agg["mean_locator_document_only"] == 0.3333  # 2/6
    assert agg["mean_locator_unresolved"] == 0.3333  # 2/6
    assert agg["mean_faithfulness_verbatim"] == 1.0
    assert "mean_completeness_has_state" not in agg
    assert "mean_completeness_has_producer" not in agg
    assert "mean_critical_field_completeness" not in agg

    faithfulness = {
        item["metadata"]["case_id"]: next(
            evaluation["value"]
            for evaluation in item["evaluations"]
            if evaluation["name"] == "faithfulness_verbatim"
        )
        for item in result["item_results"]
    }
    assert sum(value is not None for value in faithfulness.values()) == 4
    assert faithfulness["provenance-05-absent-field"] is None
    assert faithfulness["provenance-06-legacy-no-silver"] is None


def test_critical_field_null_does_not_change_locator_or_faithfulness_scope(tmp_path):
    result = run_suite(SUITE, out_dir=tmp_path)
    null_ids = {
        item["metadata"]["case_id"]
        for item in result["item_results"]
        if item["metadata"].get("critical_field") is None
    }
    assert null_ids == {"provenance-02-repeated-two-pages", "provenance-06-legacy-no-silver"}
    assert "mean_critical_field_completeness" not in result["aggregate"]


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
