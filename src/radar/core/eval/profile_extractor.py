"""Suíte de avaliação do ProfileExtractor.

Mede quantos campos do CompanyProfile o extrator acerta contra o golden.
Só verifica campos presentes no `expected_output` de cada caso — o restante
é "don't care" (o texto não os mencionava explicitamente).

Métrica-chave: `field_accuracy` — acerto por campo declarado no golden.
"""
from __future__ import annotations

import json
import os
from typing import Any

from radar.core.config import ROOT
from radar.core.eval.harness import Evaluation, Suite, get_input

GOLDEN = ROOT / "data" / "evaluation" / "golden" / "profile_extractor.json"


def load_data() -> list[dict]:
    if not GOLDEN.exists():
        return []
    cases = json.loads(GOLDEN.read_text(encoding="utf-8"))
    return [
        {
            "input": c["input"],
            "expected_output": c["expected_output"],
            "metadata": {"case_id": c["case_id"]},
        }
        for c in cases
    ]


def task(*, item: Any, **_) -> dict:
    inp = get_input(item)
    from dataclasses import asdict

    from radar.core.ingestion.profile_extractor import ProfileExtractor
    result = ProfileExtractor().extract_from_text(inp["text"])
    return asdict(result.profile)


def eval_field_accuracy(*, output, expected_output, **_) -> Evaluation:
    """Acerto por campo declarado no golden (ignora campos ausentes no expected)."""
    if not isinstance(output, dict):
        return {"name": "field_accuracy", "value": 0.0, "comment": "output inválido"}
    if not expected_output:
        return {"name": "field_accuracy", "value": 1.0, "comment": "sem campos a checar"}

    hits = 0
    for field, exp_val in expected_output.items():
        pred_val = output.get(field)
        if isinstance(exp_val, list):
            match = set(exp_val) == set(pred_val or [])
        else:
            match = pred_val == exp_val
        hits += int(match)

    total = len(expected_output)
    return {
        "name": "field_accuracy",
        "value": round(hits / total, 3),
        "comment": f"{hits}/{total} campos corretos",
    }


def eval_low_confidence_guard(*, output, expected_output, **_) -> Evaluation | None:
    """Penaliza se o extrator retornou campos vazios para campos esperados."""
    if not isinstance(output, dict) or not expected_output:
        return None
    missing = [f for f in expected_output if not output.get(f)]
    if not missing:
        return None
    return {
        "name": "missing_fields",
        "value": len(missing),
        "comment": f"campos esperados mas vazios: {missing}",
    }


def _prereqs() -> str | None:
    if not os.getenv("OPENAI_API_KEY"):
        return "requer OPENAI_API_KEY"
    if not GOLDEN.exists():
        return f"golden ausente: {GOLDEN}"
    return None


SUITE = Suite(
    name="profile_extractor",
    description="Acerto de campos do CompanyProfile extraídos de texto corporativo vs golden.",
    load_data=load_data,
    task=task,
    evaluators=[eval_field_accuracy, eval_low_confidence_guard],
    prereqs=_prereqs,
    classification="diagnostic",
)
