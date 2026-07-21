"""Rúbrica e métricas do eval de matching (Front 2) — core/matching_eval.

Testes puros: parser da rúbrica e métricas (precisão@K, expected_in_top,
agregação). Sem rede.
"""
from __future__ import annotations

import pytest

from core.eval.metrics_matching import (
    RubricVerdict,
    _parse_rubric,
    aggregate_matching_runs,
    expected_in_top,
    precision_at_k,
)

pytestmark = pytest.mark.unit

# =============================================================================
# _parse_rubric
# =============================================================================

def test_parse_rubric_ok():
    fit, elig, rationale = _parse_rubric('{"fit_tematico": 2, "elegibilidade": 1, "rationale": "ok"}')
    assert (fit, elig, rationale) == (2, 1, "ok")


def test_parse_rubric_clamps_out_of_range():
    fit, elig, _ = _parse_rubric('{"fit_tematico": 9, "elegibilidade": -3}')
    assert fit == 2
    assert elig == 0


def test_parse_rubric_garbage():
    assert _parse_rubric("nope") == (0, 0, "")


def test_parse_rubric_code_fence():
    fit, elig, _ = _parse_rubric('```json\n{"fit_tematico": 1, "elegibilidade": 2}\n```')
    assert (fit, elig) == (1, 2)


# =============================================================================
# RubricVerdict.is_hit
# =============================================================================

def test_is_hit_requires_all_three():
    assert RubricVerdict(2, 2, True).is_hit() is True
    assert RubricVerdict(0, 2, True).is_hit() is False   # fit 0
    assert RubricVerdict(2, 0, True).is_hit() is False   # inelegível
    assert RubricVerdict(2, 2, False).is_hit() is False  # expirado


# =============================================================================
# precision_at_k
# =============================================================================

def test_precision_at_k():
    verdicts = [
        RubricVerdict(2, 2, True),    # hit
        RubricVerdict(1, 1, True),    # hit
        RubricVerdict(0, 2, True),    # miss (fit 0)
        RubricVerdict(2, 2, False),   # miss (expirado)
    ]
    assert precision_at_k(verdicts, 2) == 1.0
    assert precision_at_k(verdicts, 4) == 0.5
    assert precision_at_k(verdicts, 0) == 0.0
    assert precision_at_k([], 3) == 0.0


# =============================================================================
# expected_in_top
# =============================================================================

def test_expected_in_top():
    ids = ["finep:612", "finep:782", "finep:734"]
    assert expected_in_top(ids, "finep:612", 3) is True
    assert expected_in_top(ids, "finep:734", 2) is False  # fora do top-2
    assert expected_in_top(ids, "finep:999", 3) is False


# =============================================================================
# aggregate_matching_runs
# =============================================================================

def test_aggregate():
    per = [
        {"precision_at_3": 1.0, "precision_at_5": 0.8, "expected_hit": True,
         "n_expired_in_topk": 0, "n_ineligible_in_topk": 0},
        {"precision_at_3": 0.33, "precision_at_5": 0.4, "expected_hit": False,
         "n_expired_in_topk": 1, "n_ineligible_in_topk": 2},
    ]
    agg = aggregate_matching_runs(per)
    assert agg["n_profiles"] == 2
    assert abs(agg["mean_precision_at_3"] - (1.0 + 0.33) / 2) < 1e-9
    assert agg["expected_hit_rate"] == 0.5
    assert agg["total_expired_in_topk"] == 1
    assert agg["total_ineligible_in_topk"] == 2


def test_aggregate_empty():
    assert aggregate_matching_runs([]) == {}
