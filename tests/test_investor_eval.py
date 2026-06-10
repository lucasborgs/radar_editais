"""Rúbrica e evaluators do eval multi-quadrante (Fatia 3).

Testes puros, sem rede:
  • core.investor_eval — parser da rúbrica de tese, is_hit, precisão@K.
  • core.eval.investor_match.eval_expected_hit — fundos esperados no top-N.
  • core.eval.opportunity_type.eval_type_accuracy — acerto do tipo-evento.
  • core.eval.extraction.run_presence_regression — gate de não-regressão 0.95.
"""
from __future__ import annotations

from core.investor_eval import ThesisVerdict, _parse_rubric, precision_at_k

# =============================================================================
# _parse_rubric (rúbrica de tese)
# =============================================================================

def test_parse_rubric_ok():
    assert _parse_rubric('{"fit_tese": 2, "fit_estagio": 1, "fit_setor": 0, "rationale": "x"}') == (2, 1, 0, "x")


def test_parse_rubric_clamps():
    tese, est, setor, _ = _parse_rubric('{"fit_tese": 9, "fit_estagio": -1, "fit_setor": 5}')
    assert (tese, est, setor) == (2, 0, 2)


def test_parse_rubric_garbage():
    assert _parse_rubric("nope") == (0, 0, 0, "")


def test_parse_rubric_code_fence():
    tese, est, setor, _ = _parse_rubric('```json\n{"fit_tese": 1, "fit_estagio": 2, "fit_setor": 1}\n```')
    assert (tese, est, setor) == (1, 2, 1)


# =============================================================================
# ThesisVerdict.is_hit + precisão@K
# =============================================================================

def test_is_hit_requires_tese_and_estagio():
    assert ThesisVerdict(2, 2, 0).is_hit() is True        # setor torto não trava
    assert ThesisVerdict(2, 0, 2).is_hit() is False       # estágio incompatível
    assert ThesisVerdict(0, 2, 2).is_hit() is False       # sem relação de tese


def test_precision_at_k():
    verdicts = [ThesisVerdict(2, 2, 2), ThesisVerdict(0, 0, 0), ThesisVerdict(1, 1, 0)]
    assert precision_at_k(verdicts, 3) == 2 / 3
    assert precision_at_k(verdicts, 0) == 0.0
    assert precision_at_k([], 3) == 0.0


# =============================================================================
# eval_expected_hit (investor_match)
# =============================================================================

def test_expected_hit_true_when_all_in_topn():
    from core.eval.investor_match import eval_expected_hit
    out = {"result_ids": ["investidor:a", "investidor:b", "investidor:c"]}
    exp = {"expected_top": ["investidor:a", "investidor:b"], "expected_top_n": 3}
    res = eval_expected_hit(output=out, expected_output=exp)
    assert res["value"] is True


def test_expected_hit_false_when_outside_topn():
    from core.eval.investor_match import eval_expected_hit
    out = {"result_ids": ["investidor:x", "investidor:y", "investidor:a"]}
    exp = {"expected_top": ["investidor:a"], "expected_top_n": 2}
    res = eval_expected_hit(output=out, expected_output=exp)
    assert res["value"] is False


def test_expected_hit_none_without_expected():
    from core.eval.investor_match import eval_expected_hit
    assert eval_expected_hit(output={"result_ids": []}, expected_output={}) is None


# =============================================================================
# eval_type_accuracy (opportunity_type)
# =============================================================================

def test_type_accuracy_match():
    from core.eval.opportunity_type import eval_type_accuracy
    res = eval_type_accuracy(output={"opportunity_type": "desafio"},
                             expected_output={"opportunity_type": "desafio"})
    assert res["value"] is True


def test_type_accuracy_mismatch():
    from core.eval.opportunity_type import eval_type_accuracy
    res = eval_type_accuracy(output={"opportunity_type": "edital"},
                             expected_output={"opportunity_type": "programa"})
    assert res["value"] is False


def test_type_accuracy_error_output():
    from core.eval.opportunity_type import eval_type_accuracy
    res = eval_type_accuracy(output={"error": "boom"}, expected_output={"opportunity_type": "edital"})
    assert res["value"] == 0.0


# =============================================================================
# run_presence_regression (gate de não-regressão da extração)
# =============================================================================

def _item(presence: float) -> dict:
    return {"evaluations": [{"name": "presence_accuracy", "value": presence}]}


def test_regression_gate_ok_above_baseline():
    from core.eval.extraction import run_presence_regression
    res = run_presence_regression([_item(0.97), _item(0.95)])
    assert res["value"] is False  # não regrediu


def test_regression_gate_flags_below_baseline():
    from core.eval.extraction import run_presence_regression
    res = run_presence_regression([_item(0.90), _item(0.92)])
    assert res["value"] is True  # regrediu


def test_regression_gate_no_cases():
    from core.eval.extraction import run_presence_regression
    res = run_presence_regression([])
    assert res["value"] is None
