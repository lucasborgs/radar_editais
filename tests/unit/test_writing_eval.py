"""Métricas da rúbrica de seção (Front 1.5) — core/writing_eval.

Testes puros: cobrem os parsers de resposta dos juízes (sem rede) e a
agregação. As funções que chamam OpenAI (extract_*, score_*, judge_*) não são
exercitadas aqui — só seus parsers internos e a matemática de agregação.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from core.eval.metrics_writing import (  # noqa: E402
    GroundingResult,
    _parse_claims,
    _parse_coherence,
    _parse_errors,
    _parse_grounded_one,
    _parse_grounding,
    aggregate_writing_runs,
    score_grounding,
)

pytestmark = pytest.mark.unit

# =============================================================================
# parsers
# =============================================================================

def test_parse_claims_ok():
    assert _parse_claims('{"claims": ["prazo é 30/06", "TRL mínimo 5"]}') == [
        "prazo é 30/06", "TRL mínimo 5",
    ]


def test_parse_claims_code_fence():
    raw = '```json\n{"claims": ["a"]}\n```'
    assert _parse_claims(raw) == ["a"]


def test_parse_claims_garbage_returns_empty():
    assert _parse_claims("não é json") == []


def test_parse_grounding_maps_by_index():
    raw = '{"results": [{"i": 0, "grounded": true}, {"i": 2, "grounded": true}]}'
    assert _parse_grounding(raw, 3) == [True, False, True]


def test_parse_grounding_out_of_range_ignored():
    raw = '{"results": [{"i": 9, "grounded": true}]}'
    assert _parse_grounding(raw, 2) == [False, False]


def test_parse_grounding_garbage_all_false():
    assert _parse_grounding("xxx", 2) == [False, False]


def test_parse_grounded_one():
    assert _parse_grounded_one('{"grounded": true}') is True
    assert _parse_grounded_one('{"grounded": false}') is False
    assert _parse_grounded_one("lixo") is False


def test_score_grounding_per_claim_uses_retrieve_fn(monkeypatch):
    """Modo per-claim: recupera evidência por afirmação e julga 1-a-1."""
    calls = []

    def fake_retrieve(query, k):
        calls.append(query)
        # devolve um chunk só para a 2ª claim (a 1ª fica sem evidência)
        return [{"text": "respaldo"}] if "B" in query else []

    monkeypatch.setattr(
        "core.eval.metrics_writing.judge_claim_grounded",
        lambda claim, chunks: bool(chunks),  # grounded sse há evidência
    )
    res = score_grounding(["claim A", "claim B"], retrieve_fn=fake_retrieve)
    assert calls == ["claim A", "claim B"]  # recuperou por claim
    assert res.n_claims == 2
    assert res.n_grounded == 1
    assert res.pct_grounded == 0.5


def test_score_grounding_empty_claims():
    res = score_grounding([], retrieve_fn=lambda q, k: [])
    assert res.n_claims == 0
    assert res.pct_grounded == 0.0


def test_parse_errors_ok():
    assert _parse_errors('{"errors": ["prazo divergente"]}') == ["prazo divergente"]


def test_parse_errors_empty():
    assert _parse_errors('{"errors": []}') == []


def test_parse_coherence_contradiction_forces_incoherent():
    # Mesmo se "coherent": true vier, contradições listadas zeram a coerência.
    res = _parse_coherence('{"coherent": true, "contradictions": ["orçamento difere"]}')
    assert res.coherent is False
    assert res.contradictions == ["orçamento difere"]


def test_parse_coherence_clean():
    res = _parse_coherence('{"coherent": true, "contradictions": []}')
    assert res.coherent is True


def test_parse_coherence_garbage_defaults_coherent():
    res = _parse_coherence("not json")
    assert res.coherent is True


# =============================================================================
# GroundingResult
# =============================================================================

def test_grounding_pct():
    g = GroundingResult(n_claims=4, n_grounded=3)
    assert g.pct_grounded == 0.75


def test_grounding_pct_zero_claims():
    assert GroundingResult(n_claims=0, n_grounded=0).pct_grounded == 0.0


# =============================================================================
# aggregate_writing_runs
# =============================================================================

def test_aggregate_basic():
    per_case = [
        {"n_claims": 4, "n_grounded": 3, "n_factual_errors": 0, "saved": True,
         "turns_to_save": 1, "coherent": True},
        {"n_claims": 2, "n_grounded": 1, "n_factual_errors": 2, "saved": False,
         "turns_to_save": None, "coherent": False},
    ]
    agg = aggregate_writing_runs(per_case)
    assert agg["n_cases"] == 2
    assert agg["total_claims"] == 6
    assert agg["total_grounded"] == 4
    assert abs(agg["pct_grounded"] - 4 / 6) < 1e-9
    assert agg["total_factual_errors"] == 2
    assert agg["mean_factual_errors"] == 1.0
    assert agg["save_rate"] == 0.5
    assert agg["mean_turns_to_save"] == 1.0  # só o caso salvo conta
    assert agg["coherence_rate"] == 0.5


def test_aggregate_empty():
    assert aggregate_writing_runs([]) == {}
