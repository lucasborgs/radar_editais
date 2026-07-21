"""
Unit tests pro gate de re-indexação de `radar.core.tasks._index_is_current`.

O gate decide se `chunk_edital_task` pode pular o re-embed: exige (1) hash do
conteúdo batendo com o marcador do chunk 0 e (2) count(*) íntegro. O marcador
é gravado SÓ após o último batch — runs que morrem no meio deixam o índice sem
marcador e o gate força reindexação.

Não toca DB: usa um stub do client supabase (mesmo padrão de chains
table().select().eq().maybe_single().execute()).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from radar.core.tasks import _index_is_current  # noqa: E402

pytestmark = pytest.mark.unit

HASH = "abc123"


class _Result:
    def __init__(self, data=None, count=None):
        self.data = data
        self.count = count


class _FakeTable:
    """Stub da chain supabase-py. select(count='exact') → modo count;
    select('metadata') → modo fetch do marcador."""

    def __init__(self, meta_row: dict | None, row_count: int | None):
        self._meta_row = meta_row
        self._row_count = row_count
        self._counting = False

    def select(self, *_args, **kwargs):
        self._counting = kwargs.get("count") == "exact"
        return self

    def eq(self, *_args):
        return self

    def maybe_single(self):
        return self

    def execute(self):
        if self._counting:
            return _Result(count=self._row_count)
        return _Result(data=self._meta_row)


class _FakeDB:
    def __init__(self, meta_row: dict | None, row_count: int | None):
        self._table = _FakeTable(meta_row, row_count)

    def table(self, _name: str):
        return self._table


class _ExplodingDB:
    def table(self, _name: str):
        raise RuntimeError("conexão caiu")


def test_gate_skip_quando_hash_e_count_batem():
    db = _FakeDB({"metadata": {"content_hash": HASH, "n_chunks": 10}}, row_count=10)
    assert _index_is_current(db, "finep:1", HASH, n_chunks=10) is True


def test_gate_reindexa_indexacao_parcial():
    """Cenário do failure mode original: run antiga morreu no meio dos batches
    — hash presente (legado) mas count < esperado. Gate manda reindexar."""
    db = _FakeDB({"metadata": {"content_hash": HASH}}, row_count=4)
    assert _index_is_current(db, "finep:1", HASH, n_chunks=10) is False


def test_gate_reindexa_quando_conteudo_mudou():
    db = _FakeDB({"metadata": {"content_hash": "outro"}}, row_count=10)
    assert _index_is_current(db, "finep:1", HASH, n_chunks=10) is False


def test_gate_reindexa_sem_marcador():
    """Run que morreu antes do marcador final: chunk 0 existe mas o metadata
    não tem content_hash → reindexa."""
    db = _FakeDB({"metadata": {"contem_data": True}}, row_count=10)
    assert _index_is_current(db, "finep:1", HASH, n_chunks=10) is False


def test_gate_reindexa_nunca_indexado():
    db = _FakeDB(None, row_count=0)
    assert _index_is_current(db, "finep:1", HASH, n_chunks=10) is False


def test_gate_reindexa_metadata_nao_dict():
    db = _FakeDB({"metadata": None}, row_count=10)
    assert _index_is_current(db, "finep:1", HASH, n_chunks=10) is False


def test_gate_legado_completo_nao_reembeda():
    """Índice legado (hash em todos os chunks, sem n_chunks no marcador) mas
    COMPLETO: hash bate e count == esperado → skip, sem re-embed forçado."""
    db = _FakeDB({"metadata": {"content_hash": HASH}}, row_count=7)
    assert _index_is_current(db, "finep:1", HASH, n_chunks=7) is True


def test_gate_erro_de_lookup_reindexa():
    """Na dúvida (DB indisponível), reindexar é o lado seguro."""
    assert _index_is_current(_ExplodingDB(), "finep:1", HASH, n_chunks=10) is False


def test_gate_count_none_tratado_como_zero():
    db = _FakeDB({"metadata": {"content_hash": HASH}}, row_count=None)
    assert _index_is_current(db, "finep:1", HASH, n_chunks=10) is False
