"""Tier 2 — kg_store como single source das wiki pages (file + postgres).

Garante o contrato que fecha o débito data-plane: em prod (postgres) as wiki pages
vêm do blob `wiki` (kg_artifacts), não de arquivo; e o save faz MERGE (run parcial
não apaga as demais).
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core import kg_store  # noqa: E402


def test_load_wiki_page_postgres_from_blob(monkeypatch):
    monkeypatch.setenv("KG_STORE_BACKEND", "postgres")
    monkeypatch.setattr(kg_store, "_load_wiki_blob_pg",
                        lambda: {"finep:1": {"id": "finep:1", "objective": "x"}})
    assert kg_store.load_wiki_page("finep:1") == {"id": "finep:1", "objective": "x"}


def test_load_wiki_page_missing_returns_none(monkeypatch):
    monkeypatch.setenv("KG_STORE_BACKEND", "postgres")
    monkeypatch.setattr(kg_store, "_load_wiki_blob_pg", lambda: {})  # não está no blob
    # e não há arquivo local p/ esse id fake → None (sem cair em arquivo de outro edital)
    assert kg_store.load_wiki_page("finep:fake999test") is None


def test_save_wiki_pages_merges_not_replaces(monkeypatch):
    captured = {}

    class _Table:
        def upsert(self, row, on_conflict=None):
            captured["row"] = row
            return self

        def execute(self):
            return None

    class _Svc:
        def table(self, _t):
            return _Table()

    monkeypatch.setenv("SUPABASE_URL", "https://x.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "svc")
    monkeypatch.setattr(kg_store, "_load_wiki_blob_pg",
                        lambda: {"finep:1": {"id": "finep:1"}})  # já existe 1 página
    monkeypatch.setattr("core.db.get_supabase_service", lambda: _Svc())

    kg_store.save_wiki_pages({"finep:2": {"id": "finep:2"}})  # salva outra

    assert captured["row"]["key"] == "wiki"
    assert set(captured["row"]["blob"]) == {"finep:1", "finep:2"}  # MERGE preservou a 1


def test_save_wiki_pages_noop_without_supabase(monkeypatch):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_KEY", raising=False)
    # não deve nem tentar carregar/upsert — no-op silencioso (modo file puro)
    monkeypatch.setattr(kg_store, "_load_wiki_blob_pg",
                        lambda: (_ for _ in ()).throw(AssertionError("não deveria ler")))
    kg_store.save_wiki_pages({"finep:1": {"id": "finep:1"}})  # não levanta
