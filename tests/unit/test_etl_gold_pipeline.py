"""Pipeline diário v3 (PR-C): scrapers → bronze → silver → gold (ingest_all).

Trava o contrato SEM LLM/DB reais:
  • `_build_all_silver` enumera do BRONZE (não de hipergrado/gold) e materializa
    silver por edital;
  • `_run_daily_etl` chama `gold.ingest_all` quando há OPENAI_API_KEY+DATABASE_URL,
    e PULA a ingestão gold sem DATABASE_URL (sem derrubar a run).
"""
from __future__ import annotations

import asyncio

import pytest

import radar.core.kg.gold as gold
import radar.core.kg.source_docs as source_docs
import radar.core.tasks as tasks
import radar.pipeline.adapters.base as adapters_base
import radar.pipeline.extractors as extractors

pytestmark = pytest.mark.unit


class _FakeAdapter:
    def to_documents(self, native):
        return [{"doc_name": native, "units": ["texto"]}]


def test_build_all_silver_enumerates_from_bronze(monkeypatch):
    monkeypatch.setattr(gold, "iter_bronze_editais",
                        lambda: iter([("finep", "1"), ("web", "abc")]))
    monkeypatch.setattr(source_docs, "load", lambda eid: None)
    monkeypatch.setattr(adapters_base, "get_adapter", lambda source: _FakeAdapter())
    built = []
    monkeypatch.setattr(tasks, "build_or_load_structured_doc",
                        lambda s, n, d, **_kw: built.append((s, n)) or [{"section_path": [], "text": "t"}])

    result = tasks._build_all_silver()

    assert result["silver_built"] == 2
    assert result["changed_ids"] == ["finep:1", "web:abc"]
    assert built == [("finep", "1"), ("web", "abc")]


def _stub_etl(monkeypatch, ingest_calls):
    """Mocka scrapers vazios + todas as etapas pós-scraping menos o gate de gold."""
    monkeypatch.setattr(tasks, "send_alert", lambda *a, **k: True)
    monkeypatch.setattr(extractors, "SCRAPER_REGISTRY", {}, raising=False)
    monkeypatch.setattr(tasks, "_build_all_silver", lambda: 0)
    monkeypatch.setattr(gold, "ingest_all",
                        lambda *a, **k: ingest_calls.append((a, k)) or {"edital": 0})
    monkeypatch.setattr(source_docs, "persist_all_current", lambda: 0)
    # Obsidian escreve em disco: neutraliza o export e o vault.
    import scripts.export_to_obsidian as exporter
    monkeypatch.setattr(exporter, "run", lambda *a, **k: None)


def test_run_daily_etl_calls_ingest_all(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("DATABASE_URL", "postgresql://x/y")
    from radar.core import config
    monkeypatch.setattr(config, "OBSIDIAN_VAULT_DIR", tmp_path / "vault")
    ingest_calls: list = []
    _stub_etl(monkeypatch, ingest_calls)

    asyncio.run(tasks._run_daily_etl(0))

    assert len(ingest_calls) == 1, "ingest_all deve ser chamado uma vez"


def test_run_daily_etl_skips_gold_without_database_url(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    from radar.core import config
    monkeypatch.setattr(config, "OBSIDIAN_VAULT_DIR", tmp_path / "vault")
    ingest_calls: list = []
    _stub_etl(monkeypatch, ingest_calls)

    asyncio.run(tasks._run_daily_etl(0))

    assert ingest_calls == [], "sem DATABASE_URL, a ingestão gold é pulada"


def test_run_daily_etl_returns_partial_summary_when_step_fails(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    from radar.core import config
    monkeypatch.setattr(config, "OBSIDIAN_VAULT_DIR", tmp_path / "vault")
    _stub_etl(monkeypatch, [])
    monkeypatch.setattr(tasks, "_build_all_silver", lambda: (_ for _ in ()).throw(RuntimeError("silver indisponível")))

    summary = asyncio.run(tasks._run_daily_etl(0))

    assert summary["status"] == "partial"
    assert summary["counters"]["step_errors"] == 1
    assert summary["last_step"] == "obsidian"


def test_run_daily_etl_refreshes_phase1_after_gold_when_enabled(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("DATABASE_URL", "postgresql://x/y")
    monkeypatch.setenv("KG_PHASE1_AUTO_REFRESH_ENABLED", "true")
    from radar.core import config
    monkeypatch.setattr(config, "OBSIDIAN_VAULT_DIR", tmp_path / "vault")
    ingest_calls: list = []
    _stub_etl(monkeypatch, ingest_calls)
    refreshes: list = []
    import radar.core.kg.phase1.lifecycle as lifecycle
    monkeypatch.setattr(
        lifecycle, "refresh_after_gold",
        lambda **k: refreshes.append(k) or {"trigger": k["trigger"], "outcome": "built"},
    )

    summary = asyncio.run(tasks._run_daily_etl(0))

    assert refreshes == [{"trigger": "daily_etl"}]
    assert summary["phase1_refresh"]["outcome"] == "built"
    assert summary["counters"]["kg_phase1_refresh"] == 1
    assert summary["status"] == "succeeded"


def test_run_daily_etl_skips_refresh_when_flag_off(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("DATABASE_URL", "postgresql://x/y")
    monkeypatch.delenv("KG_PHASE1_AUTO_REFRESH_ENABLED", raising=False)
    from radar.core import config
    monkeypatch.setattr(config, "OBSIDIAN_VAULT_DIR", tmp_path / "vault")
    ingest_calls: list = []
    _stub_etl(monkeypatch, ingest_calls)
    from radar.core.kg.phase1 import ingest
    monkeypatch.setattr(
        ingest, "build",
        lambda **k: (_ for _ in ()).throw(AssertionError("flag off → build não roda")),
    )

    summary = asyncio.run(tasks._run_daily_etl(0))

    assert summary["phase1_refresh"] == {"trigger": "daily_etl", "outcome": "disabled"}
    assert summary["counters"]["kg_phase1_refresh"] == 0


def test_run_daily_etl_skips_refresh_when_gold_fails(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("DATABASE_URL", "postgresql://x/y")
    monkeypatch.setenv("KG_PHASE1_AUTO_REFRESH_ENABLED", "true")
    from radar.core import config
    monkeypatch.setattr(config, "OBSIDIAN_VAULT_DIR", tmp_path / "vault")
    _stub_etl(monkeypatch, [])
    monkeypatch.setattr(gold, "ingest_all",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("gold down")))
    refreshes: list = []
    import radar.core.kg.phase1.lifecycle as lifecycle
    monkeypatch.setattr(lifecycle, "refresh_after_gold",
                        lambda **k: refreshes.append(k) or {"outcome": "built"})

    summary = asyncio.run(tasks._run_daily_etl(0))

    assert refreshes == [], "gold falhou → refresh não roda"
    assert summary["status"] == "partial"
    assert summary["counters"]["kg_phase1_refresh"] == 0


def test_run_daily_etl_refresh_failure_is_best_effort(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("DATABASE_URL", "postgresql://x/y")
    monkeypatch.setenv("KG_PHASE1_AUTO_REFRESH_ENABLED", "true")
    from radar.core import config
    monkeypatch.setattr(config, "OBSIDIAN_VAULT_DIR", tmp_path / "vault")
    _stub_etl(monkeypatch, [])
    import radar.core.kg.phase1.lifecycle as lifecycle
    monkeypatch.setattr(lifecycle, "refresh_after_gold",
                        lambda **k: (_ for _ in ()).throw(RuntimeError("boom")))

    summary = asyncio.run(tasks._run_daily_etl(0))

    assert summary["status"] == "succeeded", "falha do refresh não quebra o ETL"
    assert summary["phase1_refresh"] is None
    assert summary["counters"]["kg_phase1_refresh"] == 0


def test_warm_edital_chunks_marks_failed_when_listing_raises(monkeypatch):
    import radar.core.kg.entity_catalog as entity_catalog

    finished = []
    monkeypatch.setattr(tasks, "get_supabase_service", lambda: object())
    monkeypatch.setattr(tasks, "start_cron", lambda *args, **kwargs: "run-1")
    monkeypatch.setattr(tasks, "finish_cron", lambda *args, **kwargs: finished.append(kwargs))
    monkeypatch.setattr(entity_catalog, "list_editais", lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("catalog indisponível")))

    with pytest.raises(RuntimeError, match="catalog indisponível"):
        asyncio.run(tasks.warm_edital_chunks_task.func(timestamp=0))

    assert finished[0]["run_id"] == "run-1"
    assert finished[0]["status"] == "failed"
    assert finished[0]["last_step"] == "list_editais"
    assert isinstance(finished[0]["error"], RuntimeError)
