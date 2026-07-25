"""
Linhagem dos chunks de escrita em `edital_chunks.metadata` (RT01-T09,
docs/specs/radar-data-trust-01-provenance.md §3.4/§6.2).

Hermético: `chunk_edital_task` é exercitada fim-a-fim com stubs de supabase,
embedder, adapter e structurer — nunca toca rede/banco real. Contextualização
é controlada via `radar.core.contextual_retrieval` (nunca chama LLM real).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import radar.core.contextual_retrieval as contextual_retrieval  # noqa: E402
import radar.core.infra.db as db_module  # noqa: E402
import radar.core.tasks as tasks  # noqa: E402
from radar.core.kg import source_docs  # noqa: E402
from radar.core.retrieval.chunker import CHUNKER_VERSION  # noqa: E402

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]

EDITAL_ID = "finep:999"

CANONICAL_DOC = [
    {
        "doc_name": "Edital.pdf",
        "units": ["Texto do edital sobre elegibilidade das empresas participantes."],
        "metadata": {},
    }
]

SILVER_BLOCKS = [
    {
        "doc": "Edital.pdf",
        "page": 1,
        "section_path": ["1. Objeto"],
        "kind": "text",
        "text": "Texto do edital sobre elegibilidade das empresas participantes.",
    }
]


class _FakeAdapter:
    def __init__(self, documents):
        self._documents = documents

    def to_documents(self, _native):
        return self._documents


class _Result:
    def __init__(self, data=None, count=None):
        self.data = data
        self.count = count


class _FakeQuery:
    """Stub mínimo da chain fluente supabase-py (select/insert/delete/update,
    filtrada por .eq()) operando sobre uma store em memória compartilhada
    entre chamadas — permite testar o gate de reindex sem banco real."""

    def __init__(self, store: dict[str, list[dict]], table: str):
        self._store = store
        self._table = table
        self._op: str | None = None
        self._filters: dict = {}
        self._payload = None
        self._count = False
        self._single = False

    def select(self, *_args, **kwargs):
        self._op = "select"
        self._count = kwargs.get("count") == "exact"
        return self

    def insert(self, payload):
        self._op = "insert"
        self._payload = payload
        return self

    def delete(self):
        self._op = "delete"
        return self

    def update(self, payload):
        self._op = "update"
        self._payload = payload
        return self

    def eq(self, field, value):
        self._filters[field] = value
        return self

    def maybe_single(self):
        self._single = True
        return self

    def execute(self):
        rows = self._store.setdefault(self._table, [])

        def matches(r):
            return all(r.get(k) == v for k, v in self._filters.items())

        if self._op == "delete":
            self._store[self._table] = [r for r in rows if not matches(r)]
            return _Result(data=[])
        if self._op == "insert":
            payload = self._payload if isinstance(self._payload, list) else [self._payload]
            for row in payload:
                rows.append(dict(row))
            return _Result(data=payload)
        if self._op == "update":
            matched = [r for r in rows if matches(r)]
            for r in matched:
                r.update(self._payload)
            return _Result(data=matched)
        if self._op == "select":
            matched = [r for r in rows if matches(r)]
            if self._count:
                return _Result(count=len(matched))
            if self._single:
                return _Result(data=matched[0] if matched else None)
            return _Result(data=matched)
        raise AssertionError(f"operação inesperada: {self._op}")


class _FakeDB:
    def __init__(self):
        self._store: dict[str, list[dict]] = {}

    def table(self, name):
        return _FakeQuery(self._store, name)

    def rows(self, table="edital_chunks"):
        return self._store.get(table, [])


def _raise_no_real_db():
    raise RuntimeError("hermetic test: nenhum client supabase real deveria ser criado")


def _wire_common(monkeypatch, db, *, documents=CANONICAL_DOC, blocks=SILVER_BLOCKS):
    # Sem Supabase configurado: source_docs.save/load viram no-op (módulo já
    # trata isso), e o singleton real (lru_cache) fica travado para nunca
    # tentar rede — mark_by_edital's próprio import de get_supabase_service
    # não passa pelo monkeypatch de `tasks`.
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_KEY", raising=False)
    monkeypatch.setattr(db_module, "get_supabase_service", lambda: _raise_no_real_db())
    monkeypatch.setattr(tasks, "get_adapter", lambda _source: _FakeAdapter(documents))
    monkeypatch.setattr(tasks, "build_or_load_structured_doc", lambda *_a, **_k: blocks)
    monkeypatch.setattr(tasks, "embed_texts", lambda texts: [[0.0, 0.0, 0.0] for _ in texts])
    monkeypatch.setattr(tasks, "get_supabase_service", lambda: db)


def _wire_context_off(monkeypatch):
    monkeypatch.setattr(contextual_retrieval, "is_enabled", lambda: False)


def _wire_context_on(monkeypatch, model: str):
    monkeypatch.setattr(contextual_retrieval, "is_enabled", lambda: True)
    monkeypatch.setattr(contextual_retrieval, "effective_model", lambda: model)
    monkeypatch.setattr(
        contextual_retrieval, "contextualize_chunks",
        lambda chunks: [c["text"] for c in chunks],
    )


async def test_rows_carry_canonical_hash_and_chunker_version(monkeypatch):
    db = _FakeDB()
    _wire_common(monkeypatch, db)
    _wire_context_off(monkeypatch)

    await tasks.chunk_edital_task(EDITAL_ID, force=True)

    rows = db.rows()
    assert rows, "esperava ao menos um chunk indexado"
    expected_hash = f"md5:{source_docs.canonical_hash(CANONICAL_DOC)}"
    for row in rows:
        assert row["metadata"]["canonical_content_hash"] == expected_hash
        assert row["metadata"]["chunker_version"] == CHUNKER_VERSION


async def test_context_version_present_only_when_contextualization_enabled(monkeypatch):
    db_off = _FakeDB()
    _wire_common(monkeypatch, db_off)
    _wire_context_off(monkeypatch)
    await tasks.chunk_edital_task(EDITAL_ID, force=True)
    rows_off = db_off.rows()
    assert rows_off
    for row in rows_off:
        assert "context_version" not in row["metadata"]

    db_on = _FakeDB()
    _wire_common(monkeypatch, db_on)
    _wire_context_on(monkeypatch, model="stub-context-model")
    await tasks.chunk_edital_task(EDITAL_ID, force=True)
    rows_on = db_on.rows()
    assert rows_on
    for row in rows_on:
        assert row["metadata"]["context_version"] == {"model": "stub-context-model"}


async def test_text_source_file_page_range_unchanged(monkeypatch):
    db = _FakeDB()
    _wire_common(monkeypatch, db)
    _wire_context_off(monkeypatch)

    await tasks.chunk_edital_task(EDITAL_ID, force=True)

    rows = db.rows()
    assert len(rows) == 1
    row = rows[0]
    assert row["text"] == SILVER_BLOCKS[0]["text"]
    assert row["source_file"] == "Edital.pdf"
    assert row["page_range"] == "p.1"


async def test_reindex_same_content_is_idempotent_and_skips_reembed(monkeypatch):
    db = _FakeDB()
    _wire_common(monkeypatch, db)
    _wire_context_off(monkeypatch)

    await tasks.chunk_edital_task(EDITAL_ID, force=False)
    first_rows = [dict(r) for r in db.rows()]
    assert first_rows

    embed_calls: list[list[str]] = []
    monkeypatch.setattr(
        tasks, "embed_texts",
        lambda texts: embed_calls.append(list(texts)) or [[0.0, 0.0, 0.0] for _ in texts],
    )

    await tasks.chunk_edital_task(EDITAL_ID, force=False)

    assert embed_calls == [], "gate deveria pular o re-embed em conteúdo inalterado"
    assert db.rows() == first_rows


async def test_hash_changes_when_canonical_doc_changes(monkeypatch):
    db_a = _FakeDB()
    _wire_common(monkeypatch, db_a, documents=CANONICAL_DOC, blocks=SILVER_BLOCKS)
    _wire_context_off(monkeypatch)
    await tasks.chunk_edital_task(EDITAL_ID, force=True)
    hash_a = db_a.rows()[0]["metadata"]["canonical_content_hash"]

    other_doc = [
        {
            "doc_name": "Edital.pdf",
            "units": ["Texto DIFERENTE do edital, outra versão do PDF."],
            "metadata": {},
        }
    ]
    other_blocks = [
        {
            "doc": "Edital.pdf",
            "page": 1,
            "section_path": ["1. Objeto"],
            "kind": "text",
            "text": "Texto DIFERENTE do edital, outra versão do PDF.",
        }
    ]
    db_b = _FakeDB()
    _wire_common(monkeypatch, db_b, documents=other_doc, blocks=other_blocks)
    _wire_context_off(monkeypatch)
    await tasks.chunk_edital_task(EDITAL_ID, force=True)
    hash_b = db_b.rows()[0]["metadata"]["canonical_content_hash"]

    assert hash_a != hash_b
