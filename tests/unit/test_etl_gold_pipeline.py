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

import core.kg.gold as gold
import core.kg.source_docs as source_docs
import core.tasks as tasks
import pipeline.adapters.base as adapters_base
import pipeline.extractors as extractors

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
                        lambda s, n, d: built.append((s, n)) or [{"section_path": [], "text": "t"}])

    n = tasks._build_all_silver()

    assert n == 2
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
    from core import config
    monkeypatch.setattr(config, "OBSIDIAN_VAULT_DIR", tmp_path / "vault")
    ingest_calls: list = []
    _stub_etl(monkeypatch, ingest_calls)

    asyncio.run(tasks._run_daily_etl(0))

    assert len(ingest_calls) == 1, "ingest_all deve ser chamado uma vez"


def test_run_daily_etl_skips_gold_without_database_url(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    from core import config
    monkeypatch.setattr(config, "OBSIDIAN_VAULT_DIR", tmp_path / "vault")
    ingest_calls: list = []
    _stub_etl(monkeypatch, ingest_calls)

    asyncio.run(tasks._run_daily_etl(0))

    assert ingest_calls == [], "sem DATABASE_URL, a ingestão gold é pulada"
