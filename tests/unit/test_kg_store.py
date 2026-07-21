"""Testes do loader central do knowledge graph (core/kg_store.py).

Cobre o modo file (default) de forma hermética: redireciona o diretório do KG
para um tmp_path e desliga o caminho Postgres, garantindo que save→load faça
round-trip, que a invalidação por mtime funcione e que chaves desconhecidas /
artefatos ausentes se comportem como especificado. O modo postgres não é
exercitado aqui (exige Supabase); o ponto de troca é uma única função.
"""
from __future__ import annotations

import pytest

from core.kg import kg_store

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _isolate_kg(monkeypatch, tmp_path):
    """KG num tmp dir, sem Postgres, caches limpos — testes não tocam dados reais."""
    monkeypatch.setattr(kg_store, "KNOWLEDGE_GRAPH_DIR", tmp_path)
    monkeypatch.setattr(kg_store, "_pg_configured", lambda: False)
    monkeypatch.setenv("KG_STORE_BACKEND", "file")
    kg_store._file_cache.clear()
    kg_store._pg_cache.clear()
    yield


def test_save_load_roundtrip():
    blob = {"editais": [{"id": "finep-1", "themes": ["x"]}], "reference_date": "2026-06-03"}
    kg_store.save("index", blob)
    assert kg_store.load("index") == blob
    assert kg_store.load_index() == blob


def test_load_missing_returns_default():
    assert kg_store.load("index", default={"editais": []}) == {"editais": []}
    # helper aplica o default seguro automaticamente
    assert kg_store.load_index() == {"editais": []}
    assert kg_store.load_icts() == []


def test_unknown_key_raises():
    with pytest.raises(KeyError):
        kg_store.load("nao_existe")
    with pytest.raises(KeyError):
        kg_store.save("nao_existe", {})


def test_load_icts_unwraps_key():
    kg_store.save("icts", {"icts": [{"id": "ict-1"}, {"id": "ict-2"}]})
    assert kg_store.load_icts() == [{"id": "ict-1"}, {"id": "ict-2"}]


def test_mtime_invalidation(monkeypatch):
    """Reescrever o arquivo (mtime novo) deve invalidar o cache file-mode."""
    kg_store.save("index", {"editais": [{"id": "a"}]})
    assert kg_store.load_index()["editais"][0]["id"] == "a"

    # Reescreve com mtime forçado à frente (evita colisão de mtime em FS de baixa resolução).
    path = tmp_index_path()
    new = {"editais": [{"id": "b"}]}
    path.write_text(__import__("json").dumps(new), encoding="utf-8")
    import os
    future = path.stat().st_mtime + 10
    os.utime(path, (future, future))

    assert kg_store.load_index()["editais"][0]["id"] == "b"


def tmp_index_path():
    return kg_store.KNOWLEDGE_GRAPH_DIR / "index.json"
