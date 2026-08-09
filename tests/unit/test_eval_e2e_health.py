"""RT02-T04 — sinal E2E `e2e_health` (spec radar-data-trust-02-quality-gates §7.2).

Hermético: sem rede, sem banco real, sem LLM real (mesmos stubs de
`tests/helpers/gold_projection.py`, RT01-T02). Cobre: forma da Suite (1
caso, diagnóstica, sem critério), sinais agregados no caminho feliz,
determinismo entre execuções e o skip honesto de `prereqs` quando falta um
artefato local.
"""
from __future__ import annotations

import pytest

from radar.core.eval import e2e_health
from radar.core.eval.harness import run_suite

pytestmark = pytest.mark.unit


def test_suite_shape_is_diagnostic_no_threshold():
    suite = e2e_health.SUITE
    assert suite.name == "e2e_health"
    assert suite.classification == "diagnostic"
    assert suite.criteria == ()
    assert suite.version == "1"
    assert suite.expected_cases == 1


def test_load_data_is_a_single_minimal_case():
    items = e2e_health.load_data()
    assert len(items) == 1
    case = items[0]
    assert case["metadata"]["case_id"] == e2e_health._CASE_ID
    assert case["expected_output"]["state"] == "stated"
    assert case["expected_output"]["locator_quality"] == "exact"


def test_prereqs_pass_when_fixtures_present():
    assert e2e_health._prereqs() is None


def test_prereqs_reports_missing_pytest(monkeypatch):
    def _missing_pytest():
        raise ImportError("No module named pytest")

    monkeypatch.setattr(e2e_health, "_import_pytest", _missing_pytest)
    reason = e2e_health._prereqs()
    assert reason is not None
    assert "pytest indisponível" in reason
    assert ".[dev]" in reason
    assert e2e_health.sys.executable in reason


def test_prereqs_skip_honestly_when_fixture_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(e2e_health, "FINEP_JSONL", tmp_path / "does-not-exist.jsonl")
    reason = e2e_health._prereqs()
    assert reason is not None
    assert "fixture silver ausente" in reason


class TestHappyPathSignals:
    """O caminho mínimo descoberta(silver)→gold(ingest real)→consumo(leitura
    pública + re-resolução independente) conecta e o fato conhecido
    sobrevive. Roda a Suite real via `run_suite` (mesmo runner de produção),
    isolando só a saída em disco (`tmp_path`)."""

    def _run(self, tmp_path):
        return run_suite(e2e_health.SUITE, intent="run", out_dir=tmp_path)

    def test_run_completes_as_diagnostic_with_one_case(self, tmp_path):
        result = self._run(tmp_path)
        assert result["status"] == "diagnostic"
        assert result["n_cases"] == 1
        assert result["manifest"]["execution"]["errors"] == []

    def test_layers_connect_and_fact_survives(self, tmp_path):
        result = self._run(tmp_path)
        agg = result["aggregate"]
        assert agg["mean_gold_ran"] == 1.0
        assert agg["mean_known_fact_stated"] == 1.0
        assert agg["mean_fact_state_present"] == 1.0
        assert agg["mean_producer_complete"] == 1.0
        assert agg["mean_consumption_present"] == 1.0
        assert agg["mean_quote_survives"] == 1.0
        assert agg["mean_coordinates_match"] == 1.0
        assert agg["mean_layers_connected"] == 1.0
        assert agg["mean_operational_error"] == 0.0
        assert agg["mean_citation_count"] == 1.0

    def test_task_output_carries_known_coordinates(self, tmp_path):
        result = self._run(tmp_path)
        output = result["item_results"][0]["output"]
        assert output["known_fact_state"] == "stated"
        assert output["fact_state_present"] is True
        assert output["producer_complete"] is True
        assert output["citation_quote"] == e2e_health.KNOWN_QUOTE
        assert output["independent_locator_quality"] == "exact"
        assert output["edital_ingested"] == 1

    def test_two_runs_agree_on_aggregate(self, tmp_path):
        """Determinismo (spec §11): sem LLM/rede real, o agregado não varia
        entre execuções."""
        first = self._run(tmp_path / "run1")
        second = self._run(tmp_path / "run2")
        assert first["aggregate"] == second["aggregate"]


class TestDisconnectionIsReportedNotMasked:
    """Se a fatia conhecida não sobreviver (ex.: o path de proveniência
    esperado sumiu), o sinal registra `False`/`None` — nunca uma exceção
    silenciosa nem um valor forjado."""

    def test_missing_known_path_reports_false_not_raise(self, monkeypatch, tmp_path):
        monkeypatch.setattr(e2e_health, "REQUISITO_PATH", "requisitos_texto.999")
        result = run_suite(e2e_health.SUITE, intent="run", out_dir=tmp_path)
        assert result["status"] == "diagnostic"
        agg = result["aggregate"]
        assert agg["mean_known_fact_stated"] == 0.0
        assert agg["mean_consumption_present"] == 0.0
        assert agg["mean_layers_connected"] == 0.0
        assert agg["mean_operational_error"] == 0.0
