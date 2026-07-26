"""Testes da instrumentação de source_runs no ETL diário (RT03-T03).

Cobre:
  - sucesso com itens
  - resultado vazio e ambíguo
  - exceção PipelineError tipada
  - exceção genérica não classificável
  - falha ao abrir/finalizar telemetria
  - mesmo batch_id para os quatro canais
  - mapeamento web → web_curated
  - preservação de pipeline_errors, alertas e resultado do pipeline
"""
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

import radar.core.tasks as tasks
import radar.pipeline.extractors as extractors
from radar.core.infra.pipeline_errors import TimeoutError
from radar.core.services import source_runs

pytestmark = pytest.mark.unit


class _FakeScraper:
    def __init__(self, items=None, exc=None):
        self._items = items
        self._exc = exc

    def extract(self):
        if self._exc:
            raise self._exc
        return list(self._items or [])


def _fake_registry(**scraper_kwargs: dict) -> dict:
    """Constrói SCRAPER_REGISTRY fake com as 4 fontes."""
    return {
        key: {
            "source": key,
            "display_name": key.upper(),
            "cls": _FakeScraper,
            "kwargs": scraper_kwargs.get(key, {}),
        }
        for key in ("finep", "fapesp", "fapesc", "web")
    }


def _stub_post_scraping(monkeypatch):
    """Mocka etapas pós-scraping para isolar a instrumentação."""
    monkeypatch.setattr(tasks, "send_alert", lambda *a, **k: None)
    monkeypatch.setattr(tasks, "_build_all_silver", lambda: 0)
    import radar.core.kg.gold as gold
    import radar.core.kg.source_docs as source_docs
    monkeypatch.setattr(gold, "ingest_all", lambda *a, **k: {"edital": 0})
    monkeypatch.setattr(source_docs, "persist_all_current", lambda: 0)
    import scripts.export_to_obsidian as exporter
    monkeypatch.setattr(exporter, "run", lambda *a, **k: None)


# ══════════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════════


def _setup_etl(
    monkeypatch,
    registry: dict,
    start_retval: str = "mock-run-id",
    finish_retval: bool = True,
    db_available: bool = True,
):
    """Configura mocks e retorna (mock_start_run, mock_finish_run, mock_db)."""
    mock_db = MagicMock() if db_available else None
    monkeypatch.setattr(tasks, "get_supabase_service", lambda: mock_db)

    mock_start = MagicMock(return_value=start_retval)
    mock_finish = MagicMock(return_value=finish_retval)
    monkeypatch.setattr(source_runs, "start_run", mock_start)
    monkeypatch.setattr(source_runs, "finish_run", mock_finish)

    monkeypatch.setattr(extractors, "SCRAPER_REGISTRY", registry, raising=False)
    _stub_post_scraping(monkeypatch)
    return mock_start, mock_finish, mock_db


# ══════════════════════════════════════════════════════════════════════════
# Sucesso com itens
# ══════════════════════════════════════════════════════════════════════════


class TestSuccessWithItems:

    REGISTRY = _fake_registry(
        finep={"items": [{"id": 1}]},
        fapesp={"items": [{"id": 2}, {"id": 3}]},
        fapesc={"items": [{"id": 4}]},
        web={"items": [{"id": 5}, {"id": 6}, {"id": 7}]},
    )

    def test_finish_run_called_for_each_source(self, monkeypatch):
        mock_start, mock_finish, _ = _setup_etl(monkeypatch, self.REGISTRY)
        asyncio.run(tasks._run_daily_etl(0))

        assert mock_start.call_count == 4
        assert mock_finish.call_count == 4

    def test_all_succeeded_status(self, monkeypatch):
        mock_start, mock_finish, _ = _setup_etl(monkeypatch, self.REGISTRY)
        asyncio.run(tasks._run_daily_etl(0))

        for c in mock_finish.call_args_list:
            assert c.kwargs["status"] == "succeeded"

    def test_records_observed_matches_item_count(self, monkeypatch):
        mock_start, mock_finish, _ = _setup_etl(monkeypatch, self.REGISTRY)
        asyncio.run(tasks._run_daily_etl(0))

        # A ordem segue a iteração do dict: finep=1, fapesp=2, fapesc=1, web=3
        expected = [1, 2, 1, 3]
        for c, exp in zip(mock_finish.call_args_list, expected, strict=True):
            assert c.kwargs["records_observed"] == exp

    def test_error_count_zero_on_success(self, monkeypatch):
        mock_start, mock_finish, _ = _setup_etl(monkeypatch, self.REGISTRY)
        asyncio.run(tasks._run_daily_etl(0))

        for c in mock_finish.call_args_list:
            assert c.kwargs["error_count"] == 0


# ══════════════════════════════════════════════════════════════════════════
# Resultado vazio
# ══════════════════════════════════════════════════════════════════════════


class TestEmptyResult:

    REGISTRY = _fake_registry(
        finep={"items": []},
        fapesp={"items": []},
        fapesc={"items": []},
        web={"items": []},
    )

    def test_records_observed_zero(self, monkeypatch):
        mock_start, mock_finish, _ = _setup_etl(monkeypatch, self.REGISTRY)
        asyncio.run(tasks._run_daily_etl(0))

        for c in mock_finish.call_args_list:
            assert c.kwargs["records_observed"] == 0

    def test_status_succeeded_not_healthy_inference(self, monkeypatch):
        """Lista vazia é sucesso técnico (scraper rodou), não falha.
        O leitor de saúde futuro deve tratar records_observed=0 sem
        proof de completude como ambíguo — a instrumentação não infere
        `healthy` nem `partial`."""
        mock_start, mock_finish, _ = _setup_etl(monkeypatch, self.REGISTRY)
        asyncio.run(tasks._run_daily_etl(0))

        for c in mock_finish.call_args_list:
            assert c.kwargs["status"] == "succeeded"
            assert c.kwargs.get("reason_code") is None


# ══════════════════════════════════════════════════════════════════════════
# Exceção PipelineError tipada
# ══════════════════════════════════════════════════════════════════════════


class TestPipelineError:

    REGISTRY = _fake_registry(
        finep={"exc": TimeoutError("FINEP timeout")},
        fapesp={"items": [{"id": 1}]},
        fapesc={"items": [{"id": 2}]},
        web={"items": [{"id": 3}]},
    )

    def test_failed_status(self, monkeypatch):
        mock_start, mock_finish, _ = _setup_etl(monkeypatch, self.REGISTRY)
        asyncio.run(tasks._run_daily_etl(0))

        # Primeira chamada (finep) = failed, demais = succeeded
        assert mock_finish.call_args_list[0].kwargs["status"] == "failed"
        for c in mock_finish.call_args_list[1:]:
            assert c.kwargs["status"] == "succeeded"

    def test_reason_code_from_category(self, monkeypatch):
        mock_start, mock_finish, _ = _setup_etl(monkeypatch, self.REGISTRY)
        asyncio.run(tasks._run_daily_etl(0))

        assert mock_finish.call_args_list[0].kwargs["reason_code"] == "timeout"

    def test_error_count_one(self, monkeypatch):
        mock_start, mock_finish, _ = _setup_etl(monkeypatch, self.REGISTRY)
        asyncio.run(tasks._run_daily_etl(0))

        assert mock_finish.call_args_list[0].kwargs["error_count"] == 1

    def test_records_observed_zero_on_failure(self, monkeypatch):
        mock_start, mock_finish, _ = _setup_etl(monkeypatch, self.REGISTRY)
        asyncio.run(tasks._run_daily_etl(0))

        assert mock_finish.call_args_list[0].kwargs["records_observed"] == 0


# ══════════════════════════════════════════════════════════════════════════
# Exceção genérica não classificável
# ══════════════════════════════════════════════════════════════════════════


class TestGenericException:

    REGISTRY = _fake_registry(
        finep={"exc": ValueError("algo inesperado")},
        fapesp={"items": [{"id": 1}]},
        fapesc={"items": [{"id": 2}]},
        web={"items": [{"id": 3}]},
    )

    def test_failed_status(self, monkeypatch):
        mock_start, mock_finish, _ = _setup_etl(monkeypatch, self.REGISTRY)
        asyncio.run(tasks._run_daily_etl(0))

        assert mock_finish.call_args_list[0].kwargs["status"] == "failed"

    def test_reason_code_unknown(self, monkeypatch):
        """Exceção sem classificação → reason_code = unknown."""
        mock_start, mock_finish, _ = _setup_etl(monkeypatch, self.REGISTRY)
        asyncio.run(tasks._run_daily_etl(0))

        assert mock_finish.call_args_list[0].kwargs["reason_code"] == "unknown"


# ══════════════════════════════════════════════════════════════════════════
# Telemetria best-effort: falha ao abrir run
# ══════════════════════════════════════════════════════════════════════════


class TestTelemetryFailure:

    REGISTRY = _fake_registry(
        finep={"items": [{"id": 1}]},
        fapesp={"items": [{"id": 2}]},
        fapesc={"items": [{"id": 3}]},
        web={"items": [{"id": 4}]},
    )

    def test_start_run_none_does_not_break_etl(self, monkeypatch):
        """start_run retorna None → ETL continua sem finish_run."""
        mock_start, mock_finish, _ = _setup_etl(
            monkeypatch, self.REGISTRY, start_retval=None,
        )
        asyncio.run(tasks._run_daily_etl(0))

        assert mock_start.call_count == 4
        # finish_run não é chamado porque run_id é None
        assert mock_finish.call_count == 0

    def test_start_run_exception_logged_and_continues(self, monkeypatch):
        """start_run levanta exceção → ETL continua sem finish_run."""
        mock_start = MagicMock(side_effect=RuntimeError("DB unreachable"))
        mock_finish = MagicMock()
        monkeypatch.setattr(source_runs, "start_run", mock_start)
        monkeypatch.setattr(source_runs, "finish_run", mock_finish)

        mock_db = MagicMock()
        monkeypatch.setattr(tasks, "get_supabase_service", lambda: mock_db)
        monkeypatch.setattr(extractors, "SCRAPER_REGISTRY", self.REGISTRY, raising=False)
        _stub_post_scraping(monkeypatch)

        asyncio.run(tasks._run_daily_etl(0))

        # start_run foi chamado 4 vezes, finish_run zero
        assert mock_start.call_count == 4
        assert mock_finish.call_count == 0

    def test_db_unavailable_skips_all_telemetry(self, monkeypatch):
        """get_supabase_service falha → db=None → nenhuma telemetria."""
        mock_start, mock_finish, _ = _setup_etl(
            monkeypatch, self.REGISTRY, db_available=False,
        )
        # get_supabase_service já retorna None via _setup_etl
        asyncio.run(tasks._run_daily_etl(0))

        assert mock_start.call_count == 0
        assert mock_finish.call_count == 0


# ══════════════════════════════════════════════════════════════════════════
# Mesmo batch_id
# ══════════════════════════════════════════════════════════════════════════


class TestSameBatchId:

    REGISTRY = _fake_registry(
        finep={"items": [{"id": 1}]},
        fapesp={"items": [{"id": 2}]},
        fapesc={"items": [{"id": 3}]},
        web={"items": [{"id": 4}]},
    )

    def test_all_channels_share_batch_id(self, monkeypatch):
        captured: list[str] = []

        def _capture_start(db, *, batch_id, source_key, mode):
            captured.append(batch_id)
            return "run-id"

        mock_start = MagicMock(side_effect=_capture_start)
        mock_finish = MagicMock()
        monkeypatch.setattr(source_runs, "start_run", mock_start)
        monkeypatch.setattr(source_runs, "finish_run", mock_finish)

        mock_db = MagicMock()
        monkeypatch.setattr(tasks, "get_supabase_service", lambda: mock_db)
        monkeypatch.setattr(extractors, "SCRAPER_REGISTRY", self.REGISTRY, raising=False)
        _stub_post_scraping(monkeypatch)

        asyncio.run(tasks._run_daily_etl(0))

        assert len(captured) == 4
        # Todos os batch_ids são iguais
        assert all(b == captured[0] for b in captured)


# ══════════════════════════════════════════════════════════════════════════
# Mapeamento web → web_curated
# ══════════════════════════════════════════════════════════════════════════


class TestWebMapping:

    REGISTRY = _fake_registry(
        finep={"items": [{"id": 1}]},
        fapesp={"items": [{"id": 2}]},
        fapesc={"items": [{"id": 3}]},
        web={"items": [{"id": 4}]},
    )

    def test_web_uses_web_curated_source_key(self, monkeypatch):
        calls: list[dict] = []

        def _capture_start(db, *, batch_id, source_key, mode):
            calls.append({"source_key": source_key, "mode": mode})
            return "run-id"

        mock_start = MagicMock(side_effect=_capture_start)
        mock_finish = MagicMock()
        monkeypatch.setattr(source_runs, "start_run", mock_start)
        monkeypatch.setattr(source_runs, "finish_run", mock_finish)

        mock_db = MagicMock()
        monkeypatch.setattr(tasks, "get_supabase_service", lambda: mock_db)
        monkeypatch.setattr(extractors, "SCRAPER_REGISTRY", self.REGISTRY, raising=False)
        _stub_post_scraping(monkeypatch)

        asyncio.run(tasks._run_daily_etl(0))

        assert len(calls) == 4
        assert calls[0] == {"source_key": "finep", "mode": "dedicated"}
        assert calls[1] == {"source_key": "fapesp", "mode": "dedicated"}
        assert calls[2] == {"source_key": "fapesc", "mode": "dedicated"}
        assert calls[3] == {"source_key": "web_curated", "mode": "curated_web"}

    def test_finep_fapesp_fapesc_dedicated_mode(self, monkeypatch):
        calls: list[dict] = []

        def _capture_start(db, *, batch_id, source_key, mode):
            calls.append({"source_key": source_key, "mode": mode})
            return "run-id"

        mock_start = MagicMock(side_effect=_capture_start)
        mock_finish = MagicMock()
        monkeypatch.setattr(source_runs, "start_run", mock_start)
        monkeypatch.setattr(source_runs, "finish_run", mock_finish)

        mock_db = MagicMock()
        monkeypatch.setattr(tasks, "get_supabase_service", lambda: mock_db)
        monkeypatch.setattr(extractors, "SCRAPER_REGISTRY", self.REGISTRY, raising=False)
        _stub_post_scraping(monkeypatch)

        asyncio.run(tasks._run_daily_etl(0))

        dedicated = [c for c in calls if c["mode"] == "dedicated"]
        assert len(dedicated) == 3
        assert {c["source_key"] for c in dedicated} == {"finep", "fapesp", "fapesc"}


# ══════════════════════════════════════════════════════════════════════════
# Preservação — pipeline_errors, step_errors, alertas
# ══════════════════════════════════════════════════════════════════════════


class TestPreservation:

    def test_pipeline_errors_still_logged_on_failure(self, monkeypatch):
        """PipelineError ainda persiste via log_pipeline_error."""
        registry = _fake_registry(
            finep={"exc": TimeoutError("timeout")},
            fapesp={"items": [{"id": 1}]},
            fapesc={"items": [{"id": 2}]},
            web={"items": [{"id": 3}]},
        )

        pipeline_spy = MagicMock()
        import radar.core.infra.pipeline_errors as pe
        monkeypatch.setattr(pe, "log_pipeline_error", pipeline_spy)

        mock_db = MagicMock()
        monkeypatch.setattr(tasks, "get_supabase_service", lambda: mock_db)
        mock_start = MagicMock(return_value="run-id")
        mock_finish = MagicMock(return_value=True)
        monkeypatch.setattr(source_runs, "start_run", mock_start)
        monkeypatch.setattr(source_runs, "finish_run", mock_finish)

        monkeypatch.setattr(extractors, "SCRAPER_REGISTRY", registry, raising=False)
        _stub_post_scraping(monkeypatch)

        asyncio.run(tasks._run_daily_etl(0))

        # log_pipeline_error foi chamado ao menos uma vez
        pipeline_spy.assert_called()
        # Verifica que o erro foi persistido com a fonte correta
        found_finep = any(
            kw.get("source") == "finep" or (args and "finep" in str(args))
            for args, kw in pipeline_spy.call_args_list
        )
        assert found_finep, "Deve ter persistido erro da fonte finep"

    def test_step_errors_still_accumulated(self, monkeypatch):
        """step_errors → send_alert ainda dispara com falhas agregadas."""
        registry = _fake_registry(
            finep={"exc": TimeoutError("timeout")},
            fapesp={"items": [{"id": 1}]},
            fapesc={"items": [{"id": 2}]},
            web={"items": [{"id": 3}]},
        )

        alert_calls: list = []

        def _capture_alert(title, body):
            alert_calls.append((title, body))

        mock_db = MagicMock()
        monkeypatch.setattr(tasks, "get_supabase_service", lambda: mock_db)
        monkeypatch.setattr(tasks, "send_alert", _capture_alert)
        monkeypatch.setattr(extractors, "SCRAPER_REGISTRY", registry, raising=False)
        monkeypatch.setattr(tasks, "_build_all_silver", lambda: 0)

        import radar.core.kg.gold as gold
        import radar.core.kg.source_docs as source_docs
        monkeypatch.setattr(gold, "ingest_all", lambda *a, **k: {"edital": 0})
        monkeypatch.setattr(source_docs, "persist_all_current", lambda: 0)
        import scripts.export_to_obsidian as exporter
        monkeypatch.setattr(exporter, "run", lambda *a, **k: None)

        mock_start = MagicMock(return_value="run-id")
        mock_finish = MagicMock(return_value=True)
        monkeypatch.setattr(source_runs, "start_run", mock_start)
        monkeypatch.setattr(source_runs, "finish_run", mock_finish)

        asyncio.run(tasks._run_daily_etl(0))

        # send_alert deve ter sido chamado com falha agregada
        assert len(alert_calls) >= 1
        title, body = alert_calls[0]
        assert "falha" in title.lower() or "Falha" in title
        assert "FINEP" in body or "finep" in body

    def test_telemetry_does_not_affect_pipeline_result(self, monkeypatch):
        """Falha de telemetria não altera total_new nem etapas pós-scraping."""
        registry = _fake_registry(
            finep={"items": [{"id": 1}]},
            fapesp={"items": [{"id": 2}]},
            fapesc={"items": [{"id": 3}]},
            web={"items": [{"id": 4}]},
        )

        silver_calls: list = []
        monkeypatch.setattr(tasks, "_build_all_silver", lambda: (silver_calls.append(1) or 0))

        mock_db = MagicMock()
        monkeypatch.setattr(tasks, "get_supabase_service", lambda: mock_db)
        monkeypatch.setattr(extractors, "SCRAPER_REGISTRY", registry, raising=False)

        mock_start = MagicMock(side_effect=RuntimeError("DB down"))
        mock_finish = MagicMock()
        monkeypatch.setattr(source_runs, "start_run", mock_start)
        monkeypatch.setattr(source_runs, "finish_run", mock_finish)

        import radar.core.kg.gold as gold
        import radar.core.kg.source_docs as source_docs
        monkeypatch.setattr(gold, "ingest_all", lambda *a, **k: {"edital": 0})
        monkeypatch.setattr(source_docs, "persist_all_current", lambda: 0)
        import scripts.export_to_obsidian as exporter
        monkeypatch.setattr(exporter, "run", lambda *a, **k: None)
        monkeypatch.setattr(tasks, "send_alert", lambda *a, **k: None)

        asyncio.run(tasks._run_daily_etl(0))

        # _build_all_silver ainda foi chamada (pós-scraping continua)
        assert len(silver_calls) == 1


# ══════════════════════════════════════════════════════════════════════════
# Invariante: não inferir partial sem evidência explícita
# ══════════════════════════════════════════════════════════════════════════


class TestNoInferredPartial:

    def test_pipeline_error_is_failed_not_partial(self, monkeypatch):
        """PipelineError → status failed, nunca partial."""
        registry = _fake_registry(
            finep={"exc": TimeoutError("timeout")},
            fapesp={"items": [{"id": 1}]},
            fapesc={"items": [{"id": 2}]},
            web={"items": [{"id": 3}]},
        )

        mock_start, mock_finish, _ = _setup_etl(monkeypatch, registry)
        asyncio.run(tasks._run_daily_etl(0))

        assert mock_finish.call_args_list[0].kwargs["status"] == "failed"
        # Nenhuma chamada tem status "partial"
        for c in mock_finish.call_args_list:
            assert c.kwargs["status"] != "partial"
