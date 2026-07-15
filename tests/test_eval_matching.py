from __future__ import annotations

from core.eval import matching


def _output(*ids: str) -> dict:
    return {
        "matches": [
            {"file_key": file_key, "affinity": 0.9}
            for file_key in ids
        ],
    }


def test_false_positive_requires_explicit_human_judgment():
    expected = {
        "relevant": ["relevant"],
        "neutral": ["neutral"],
        "confirmed_irrelevant": ["irrelevant"],
    }
    output = _output("relevant", "neutral", "irrelevant", "unknown")

    false_positives = matching.eval_false_positives(
        output=output, expected_output=expected,
    )
    unjudged = matching.eval_unjudged(output=output, expected_output=expected)

    assert false_positives["value"] == 1
    assert "irrelevant" in false_positives["comment"]
    assert unjudged["value"] == 1
    assert "unknown" in unjudged["comment"]


def test_matching_contract_accepts_zero_false_positives_only():
    criteria = {criterion.metric: criterion for criterion in matching.SUITE.criteria}
    assert criteria["mean_false_positives_at_8"].threshold == 0
    assert criteria["mean_false_positives_at_8"].operator == "eq"
    assert criteria["mean_unjudged_at_8"].threshold == 0
    assert matching.SUITE.classification == "candidate"
