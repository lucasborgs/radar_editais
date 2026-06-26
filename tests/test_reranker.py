"""Reranker do RAG de escrita (Front 4) — core/reranker + integração no retriever.

Sem dep pesada (sentence-transformers/torch) nem DB: testamos o contrato de
degradação graciosa (backend off/ausente → None) e a reordenação do pool no
`_apply_rerank` com um reranker stubbado.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import core.reranker as reranker  # noqa: E402
from core.retrieval.retriever import _apply_rerank  # noqa: E402

# =============================================================================
# rerank_scores — contrato de degradação graciosa
# =============================================================================

def test_rerank_off_returns_none(monkeypatch):
    monkeypatch.setenv("RERANK_BACKEND", "off")
    assert reranker.rerank_scores("q", ["a", "b"], backend="off") is None


def test_rerank_empty_inputs_return_none():
    assert reranker.rerank_scores("", ["a"]) is None
    assert reranker.rerank_scores("q", []) is None


def test_rerank_unknown_backend_returns_none():
    assert reranker.rerank_scores("q", ["a"], backend="banana") is None


def test_rerank_missing_dependency_degrades(monkeypatch):
    """cross-encoder sem sentence-transformers instalado → None (não levanta)."""
    def _boom():
        raise ImportError("No module named 'sentence_transformers'")
    monkeypatch.setattr(reranker, "_get_cross_encoder", _boom)
    assert reranker.rerank_scores("q", ["a", "b"], backend="cross-encoder") is None


def test_sigmoid_monotonic():
    assert reranker._sigmoid(-10) < reranker._sigmoid(0) < reranker._sigmoid(10)
    assert 0.0 < reranker._sigmoid(0) < 1.0


# =============================================================================
# _apply_rerank — reordenação do pool no retriever
# =============================================================================

def _by_id(rows: list[tuple[str, str, str]]) -> dict[str, dict]:
    # rows: (id, edital_id, text)
    return {r[0]: {"id": r[0], "edital_id": r[1], "text": r[2]} for r in rows}


def test_apply_rerank_reorders_by_relevance(monkeypatch):
    by_id = _by_id([
        ("c1", "finep:1", "irrelevante"),
        ("c2", "finep:1", "muito relevante"),
    ])
    # RRF tinha c1 na frente; reranker dá c2 (relevante) score alto.
    ranked = [("c1", 0.9), ("c2", 0.8)]
    monkeypatch.setattr(
        "core.reranker.rerank_scores",
        lambda q, texts, backend=None: [0.1, 0.9],
    )
    out = _apply_rerank(ranked, by_id, "query", 20)
    assert [cid for cid, _ in out] == ["c2", "c1"]


def test_apply_rerank_none_keeps_rrf_order(monkeypatch):
    by_id = _by_id([("c1", "finep:1", "x"), ("c2", "finep:1", "y")])
    ranked = [("c1", 0.9), ("c2", 0.8)]
    monkeypatch.setattr("core.reranker.rerank_scores", lambda q, texts, backend=None: None)
    out = _apply_rerank(ranked, by_id, "query", 20)
    assert out == ranked


def test_apply_rerank_tail_preserved(monkeypatch):
    by_id = _by_id([(f"c{i}", "finep:1", f"t{i}") for i in range(5)])
    ranked = [(f"c{i}", 1.0 - i * 0.1) for i in range(5)]
    # Pool de 2 → reordena; cauda (c2,c3,c4) mantém ordem RRF após o pool.
    monkeypatch.setattr(
        "core.reranker.rerank_scores",
        lambda q, texts, backend=None: [0.2, 0.9],  # inverte c0,c1
    )
    out = _apply_rerank(ranked, by_id, "query", 2)
    assert [cid for cid, _ in out] == ["c1", "c0", "c2", "c3", "c4"]
