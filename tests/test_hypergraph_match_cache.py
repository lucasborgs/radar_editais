"""Caches do match (hardening PR6.3 / F12) — puros, sem rede/disco.

Cobre: cache in-process dos embeddings dos nós-empresa (por hash do texto),
memo de módulo do snapshot do ecossistema (graphs, eco, emb) com TTL, e o
reuso do embedding quando o corpus não muda após a expiração do TTL.
"""
from __future__ import annotations

import numpy as np
import pytest

import core.services.hypergraph_match as hm

COMPANY = [
    {"name": "VisãoAgro", "type": "Tema", "description": "visão computacional no agro"},
]
GRAPHS = {
    "finep__1": {
        "nodes": [
            {"name": "Chamada Agro", "type": "Edital", "description": "edital agro"},
            {"name": "Agricultura de precisão", "type": "Tema", "description": ""},
        ],
        "edges": [],
    },
}


@pytest.fixture(autouse=True)
def _reset_caches():
    hm._COMPANY_EMB_CACHE.clear()
    hm._eco_memo = None
    yield
    hm._COMPANY_EMB_CACHE.clear()
    hm._eco_memo = None


def _fake_embed_factory(calls: list):
    def _fake(texts):
        calls.append(list(texts))
        return [[1.0, 0.0] for _ in texts]
    return _fake


def test_company_embeddings_cached_by_text_hash(monkeypatch):
    calls: list = []
    monkeypatch.setattr(hm, "embed_texts", _fake_embed_factory(calls))

    e1 = hm._embed_company_nodes(COMPANY)
    e2 = hm._embed_company_nodes(COMPANY)

    assert len(calls) == 1  # segunda chamada veio do cache
    assert e1 is e2
    assert isinstance(e1, np.ndarray)

    # Texto diferente → hash diferente → re-embeda.
    hm._embed_company_nodes([{"name": "Outra", "type": "Tema", "description": "x"}])
    assert len(calls) == 2


def test_company_cache_is_fifo_capped(monkeypatch):
    calls: list = []
    monkeypatch.setattr(hm, "embed_texts", _fake_embed_factory(calls))
    monkeypatch.setattr(hm, "_COMPANY_EMB_CACHE_MAX", 2)

    for i in range(3):
        hm._embed_company_nodes([{"name": f"n{i}", "type": "Tema", "description": ""}])
    assert len(hm._COMPANY_EMB_CACHE) == 2  # nunca passa do cap


def test_ecosystem_snapshot_memoized_within_ttl(monkeypatch):
    loads: list = []
    embeds: list = []
    monkeypatch.setattr(
        "core.kg.kg_store.load_all_hypergraphs",
        lambda: loads.append(1) or GRAPHS,
    )
    monkeypatch.setattr(
        hm, "_embed_ecosystem_texts",
        lambda texts, h: embeds.append(1) or np.ones((len(texts), 2), dtype=np.float32),
    )

    g1, eco1, emb1 = hm._ecosystem_snapshot()
    g2, eco2, emb2 = hm._ecosystem_snapshot()

    assert len(loads) == 1 and len(embeds) == 1  # 2ª chamada 100% do memo
    assert g1 is g2 and eco1 is eco2 and emb1 is emb2
    assert len(eco1) == 2


def test_ecosystem_snapshot_reuses_embeddings_after_ttl_when_corpus_unchanged(monkeypatch):
    loads: list = []
    embeds: list = []
    monkeypatch.setattr(
        "core.kg.kg_store.load_all_hypergraphs",
        lambda: loads.append(1) or GRAPHS,
    )
    monkeypatch.setattr(
        hm, "_embed_ecosystem_texts",
        lambda texts, h: embeds.append(1) or np.ones((len(texts), 2), dtype=np.float32),
    )

    _, _, emb1 = hm._ecosystem_snapshot()
    # Expira o TTL na mão: recua o timestamp do memo.
    ts, h, graphs, eco, emb = hm._eco_memo
    hm._eco_memo = (ts - hm._ECO_MEMO_TTL - 1, h, graphs, eco, emb)

    _, _, emb2 = hm._ecosystem_snapshot()

    assert len(loads) == 2   # recarregou a montagem (detecta mudança de corpus)
    assert len(embeds) == 1  # corpus idêntico → embeddings reusados
    assert emb2 is emb1


def test_empty_ecosystem_not_memoized(monkeypatch):
    loads: list = []
    monkeypatch.setattr(
        "core.kg.kg_store.load_all_hypergraphs",
        lambda: loads.append(1) or {},
    )

    _, eco, _ = hm._ecosystem_snapshot()
    _, eco2, _ = hm._ecosystem_snapshot()

    assert eco == [] and eco2 == []
    assert len(loads) == 2  # vazio não congela: tenta de novo no próximo request
    assert hm._eco_memo is None


def test_eco_embeddings_for_uses_memo_by_identity(monkeypatch):
    monkeypatch.setattr(
        "core.kg.kg_store.load_all_hypergraphs", lambda: GRAPHS,
    )
    sentinel = np.full((2, 2), 7.0, dtype=np.float32)
    monkeypatch.setattr(hm, "_embed_ecosystem_texts", lambda texts, h: sentinel)

    _, eco, emb = hm._ecosystem_snapshot()
    assert hm._eco_embeddings_for(eco) is emb  # mesmo objeto → memo

    # Lista equivalente mas NÃO idêntica → cai no caminho embed_ecosystem.
    fallback = np.zeros((2, 2), dtype=np.float32)
    monkeypatch.setattr(hm, "embed_ecosystem", lambda e: fallback)
    assert hm._eco_embeddings_for(list(eco)) is fallback
