"""Promote do discovery (admin) → catálogo/match via silver+gold (PR-C v3).

O promovido (edital_link PDF) segue o MESMO caminho silver→gold do ETL diário: o
endpoint enfileira `chunk_edital` (RAG lazy) E `ingest_promoted_edital` (gold),
e a task de ingest constrói o silver e chama `gold.ingest_all`. Sem rede/LLM/DB.
"""
from __future__ import annotations

import asyncio

import backend.routers.discovered as disc
import core.kg.gold as gold
import core.kg.source_docs as source_docs
import core.tasks as tasks


def test_process_edital_pdf_defers_chunk_and_ingest(monkeypatch):
    monkeypatch.setattr(disc, "_download_pdf", lambda url: b"%PDF-fake")
    monkeypatch.setattr(disc, "_extract_pdf_text", lambda b: "texto do edital")
    monkeypatch.setattr(disc, "_save_web_bronze", lambda entry: None)

    deferred: list[tuple[str, dict]] = []

    class _FakeTask:
        def __init__(self, name):
            self.name = name

        def defer(self, **kw):
            deferred.append((self.name, kw))

    monkeypatch.setattr(tasks.app, "configure_task", lambda name: _FakeTask(name))

    out = disc._process_edital_pdf("https://x.gov/edital.pdf", {"title": "Chamada Y"})

    names = {n for n, _ in deferred}
    assert names == {"chunk_edital", "ingest_promoted_edital"}
    # ambos apontam para o mesmo edital web recém-criado
    ids = {kw["edital_id"] for _, kw in deferred}
    assert len(ids) == 1
    assert next(iter(ids)).startswith("web:")
    assert out["url_hash"] == next(iter(ids)).split(":", 1)[1]


def test_ingest_promoted_edital_task_builds_silver_and_ingests(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("DATABASE_URL", "postgresql://x/y")
    monkeypatch.setattr(source_docs, "load", lambda eid: [{"doc_name": "d", "units": ["t"]}])
    monkeypatch.setattr(tasks, "build_or_load_structured_doc",
                        lambda s, n, d: [{"section_path": [], "text": "t"}])
    calls: list = []
    monkeypatch.setattr(gold, "ingest_all", lambda *a, **k: calls.append(k) or {"edital": 1})

    asyncio.run(tasks.ingest_promoted_edital_task.func(edital_id="web:abc123"))

    assert calls == [{"sources": ["edital"]}]


def test_ingest_promoted_edital_task_skips_when_silver_empty(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("DATABASE_URL", "postgresql://x/y")
    monkeypatch.setattr(source_docs, "load", lambda eid: [{"doc_name": "d", "units": ["t"]}])
    monkeypatch.setattr(tasks, "build_or_load_structured_doc", lambda s, n, d: [])
    calls: list = []
    monkeypatch.setattr(gold, "ingest_all", lambda *a, **k: calls.append(k))

    asyncio.run(tasks.ingest_promoted_edital_task.func(edital_id="web:abc123"))

    assert calls == [], "silver vazio → não ingere no gold"
