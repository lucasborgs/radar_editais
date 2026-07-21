"""Suíte de avaliação do Structurer (camada silver).

Mede se o structurer classifica corretamente os blocos de uma página de edital.
Duas métricas:
  • kind_recall     — fração dos kinds esperados presente no output (multiset).
  • heading_recall  — fração dos headings esperados encontrados no output
                      (match parcial de texto, case-insensitive).

O structurer é não-determinístico (LLM): não exigimos ordem exata nem texto
idêntico — verificamos distribuição de kinds e presença dos headings.
"""
from __future__ import annotations

import json
import os
from typing import Any

from radar.core.config import ROOT
from radar.core.eval.harness import Evaluation, Suite, get_input

GOLDEN = ROOT / "eval_data" / "golden" / "structurer.json"


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
    from radar.core.ingestion.structurer import _make_client, structure_page
    client, model = _make_client()
    blocks = structure_page(client, model, "Edital.pdf", 1, inp["page_text"], [])
    return {"blocks": blocks}


def eval_kind_recall(*, output, expected_output, **_) -> Evaluation:
    """Fração dos kinds esperados coberta pelo output (multiset match)."""
    from collections import Counter
    if not isinstance(output, dict) or "error" in output:
        return {"name": "kind_recall", "value": 0.0, "comment": "output inválido"}
    pred_kinds = Counter(b.get("kind") for b in output.get("blocks", []))
    exp_kinds = Counter(b.get("kind") for b in expected_output.get("blocks", []))
    if not exp_kinds:
        return {"name": "kind_recall", "value": 1.0}
    matched = sum(min(pred_kinds.get(k, 0), v) for k, v in exp_kinds.items())
    total = sum(exp_kinds.values())
    return {
        "name": "kind_recall",
        "value": round(matched / total, 3),
        "comment": f"{matched}/{total} kinds cobertos",
    }


def eval_heading_recall(*, output, expected_output, **_) -> Evaluation | None:
    """Fração dos headings esperados encontrados no output (match parcial)."""
    if not isinstance(output, dict) or "error" in output:
        return None
    exp_headings = [
        b.get("text", "").lower()
        for b in expected_output.get("blocks", [])
        if b.get("kind") == "heading" and b.get("text")
    ]
    if not exp_headings:
        return None
    pred_texts = [b.get("text", "").lower() for b in output.get("blocks", [])]
    hits = sum(
        any(exp[:30] in pred for pred in pred_texts)
        for exp in exp_headings
    )
    return {
        "name": "heading_recall",
        "value": round(hits / len(exp_headings), 3),
        "comment": f"{hits}/{len(exp_headings)} headings encontrados",
    }


def _prereqs() -> str | None:
    if not os.getenv("OPENAI_API_KEY"):
        return "requer OPENAI_API_KEY"
    if not GOLDEN.exists():
        return f"golden ausente: {GOLDEN}"
    return None


SUITE = Suite(
    name="structurer",
    description="Classificação de blocos (kind) e presença de headings pelo structurer vs golden.",
    load_data=load_data,
    task=task,
    evaluators=[eval_kind_recall, eval_heading_recall],
    prereqs=_prereqs,
)
