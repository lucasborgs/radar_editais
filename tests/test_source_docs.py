"""Unit tests para core.kg.source_docs — Documento Canônico durável.

Cobre o contrato sem tocar DB: stub do client supabase (chains
table().select()/upsert().eq().limit().execute()) e monkeypatch de
get_supabase_service + _pg_configured.

Espelha docs/specs/durable-source-docs.md: o durável é a fonte primária; o disco
é só cache/fallback; sem Supabase tudo degrada gracioso (load→None, save→no-op).
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

from core.kg import source_docs  # noqa: E402

DOC = [{"doc_name": "Edital.pdf", "units": ["pagina 1", "pagina 2"]}]


# ---------------------------------------------------------------------------
# Stubs do client supabase
# ---------------------------------------------------------------------------

class _Result:
    def __init__(self, data=None):
        self.data = data


class _SelectChain:
    def __init__(self, rows):
        self._rows = rows

    def select(self, *_a, **_k):
        return self

    def eq(self, *_a, **_k):
        return self

    def limit(self, *_a, **_k):
        return self

    def execute(self):
        return _Result(data=self._rows)


class _UpsertChain:
    """Captura o payload do upsert para asserção."""

    def __init__(self, sink: dict):
        self._sink = sink

    def upsert(self, payload, **kwargs):
        self._sink["payload"] = payload
        self._sink["kwargs"] = kwargs
        return self

    def execute(self):
        return _Result(data=[self._sink.get("payload")])


class _FakeDB:
    def __init__(self, *, rows=None, sink=None):
        self._rows = rows
        self._sink = sink

    def table(self, _name):
        if self._sink is not None:
            return _UpsertChain(self._sink)
        return _SelectChain(self._rows)


@pytest.fixture
def pg_on(monkeypatch):
    monkeypatch.setattr(source_docs, "_pg_configured", lambda: True)


# ---------------------------------------------------------------------------
# canonical_hash
# ---------------------------------------------------------------------------

def test_hash_deterministico_e_ordem_estavel():
    a = [{"doc_name": "A", "units": ["x"]}, {"doc_name": "B", "units": ["y"]}]
    b = list(reversed(a))
    assert source_docs.canonical_hash(a) == source_docs.canonical_hash(b)


def test_hash_muda_com_conteudo():
    a = [{"doc_name": "A", "units": ["x"]}]
    b = [{"doc_name": "A", "units": ["x", "z"]}]
    assert source_docs.canonical_hash(a) != source_docs.canonical_hash(b)


def test_hash_muda_com_autoridade_e_active_documents_filtra_historico():
    active = {"doc_name": "A", "units": ["x"],
              "metadata": {"authority_state": "vigente"}}
    old = {"doc_name": "B", "units": ["y"],
           "metadata": {"authority_state": "superseded"}}
    assert source_docs.canonical_hash([active]) != source_docs.canonical_hash([
        {**active, "metadata": {"authority_state": "superseded"}},
    ])
    assert source_docs.active_documents([active, old]) == [active]


# ---------------------------------------------------------------------------
# load
# ---------------------------------------------------------------------------

def test_load_sem_supabase_retorna_none(monkeypatch):
    monkeypatch.setattr(source_docs, "_pg_configured", lambda: False)
    assert source_docs.load("finep:1") is None


def test_load_hit(monkeypatch, pg_on):
    db = _FakeDB(rows=[{"canonical_doc": DOC}])
    monkeypatch.setattr("core.infra.db.get_supabase_service", lambda: db)
    assert source_docs.load("finep:1") == DOC


def test_load_miss_retorna_none(monkeypatch, pg_on):
    db = _FakeDB(rows=[])
    monkeypatch.setattr("core.infra.db.get_supabase_service", lambda: db)
    assert source_docs.load("finep:1") is None


def test_load_erro_degrada_para_none(monkeypatch, pg_on):
    def _boom():
        raise RuntimeError("conexão caiu")

    monkeypatch.setattr("core.infra.db.get_supabase_service", _boom)
    assert source_docs.load("finep:1") is None


# ---------------------------------------------------------------------------
# save
# ---------------------------------------------------------------------------

def test_save_sem_supabase_noop(monkeypatch):
    monkeypatch.setattr(source_docs, "_pg_configured", lambda: False)
    # não deve tentar abrir client; retorna False (no-op)
    assert source_docs.save("finep:1", "finep", DOC) is False


def test_save_doc_vazio_noop(monkeypatch, pg_on):
    called = {"n": 0}
    monkeypatch.setattr(
        "core.infra.db.get_supabase_service",
        lambda: called.__setitem__("n", called["n"] + 1),
    )
    assert source_docs.save("finep:1", "finep", []) is False
    assert called["n"] == 0


def test_save_upsert_payload(monkeypatch, pg_on):
    sink: dict = {}
    db = _FakeDB(sink=sink)
    monkeypatch.setattr("core.infra.db.get_supabase_service", lambda: db)
    assert source_docs.save("finep:782", "finep", DOC) is True
    assert sink["payload"]["edital_id"] == "finep:782"
    assert sink["payload"]["source"] == "finep"
    assert sink["payload"]["canonical_doc"] == DOC
    assert sink["payload"]["content_hash"] == source_docs.canonical_hash(DOC)
    assert sink["kwargs"]["on_conflict"] == "edital_id"


def test_save_erro_nao_levanta(monkeypatch, pg_on):
    def _boom():
        raise RuntimeError("DB fora")

    monkeypatch.setattr("core.infra.db.get_supabase_service", _boom)
    # não deve propagar (persistir não pode quebrar o chunk path); retorna False
    assert source_docs.save("finep:1", "finep", DOC) is False
