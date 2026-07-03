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


@pytest.fixture(autouse=True)
def _isolate_kg(monkeypatch, tmp_path):
    """KG num tmp dir, sem Postgres, caches limpos — testes não tocam dados reais."""
    monkeypatch.setattr(kg_store, "KNOWLEDGE_GRAPH_DIR", tmp_path)
    monkeypatch.setattr(kg_store, "_HYPERGRAPHS_DIR", tmp_path / "hypergraphs")
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


def test_load_all_hypergraphs_roundtrip():
    # Grafos JÁ-v2 passam intactos pelo upgrade-on-read (migrate_to_v2 é no-op).
    graphs = {
        "finep__1": {
            "format_version": 2,
            "source_hash": None,
            "proveniencia": {},
            "nodes": [{"type": "Edital", "id": "ed:edital-1", "name": "Edital 1", "prazo": "31/12/2026"}],
            "edges": [],
        },
        "fapesp__2": {
            "format_version": 2,
            "source_hash": None,
            "proveniencia": {},
            "nodes": [{"type": "Edital", "id": "ed:edital-2", "name": "Edital 2"}],
            "edges": [],
        },
    }
    hg_dir = kg_store._HYPERGRAPHS_DIR
    hg_dir.mkdir(parents=True, exist_ok=True)
    for fk, g in graphs.items():
        (hg_dir / f"{fk}.json").write_text(__import__("json").dumps(g), encoding="utf-8")

    loaded = kg_store.load_all_hypergraphs()
    assert loaded == graphs


def test_load_hypergraph_upgrades_v1_on_read():
    # Um arquivo v1 (sem format_version, arestas ligadas por name) é normalizado
    # para v2 no load: nós ganham id, members viram ids (PR1 KG v2).
    v1 = {
        "source_hash": "h",
        "nodes": [
            {"type": "Edital", "name": "Edital X"},
            {"type": "Tema", "name": "Robótica"},
        ],
        "edges": [{"type": "abrange_tema", "members": ["edital x", "robótica"]}],
    }
    hg_dir = kg_store._HYPERGRAPHS_DIR
    hg_dir.mkdir(parents=True, exist_ok=True)
    (hg_dir / "finep__v1.json").write_text(__import__("json").dumps(v1), encoding="utf-8")

    g = kg_store.load_hypergraph("finep__v1")
    assert g["format_version"] == 2
    assert {n["id"] for n in g["nodes"]} == {"ed:edital-x", "tema:robotica"}
    # a aresta agora referencia ids, não name-strings
    assert g["edges"][0]["members"] == ["ed:edital-x", "tema:robotica"]


def tmp_index_path():
    return kg_store.KNOWLEDGE_GRAPH_DIR / "index.json"
