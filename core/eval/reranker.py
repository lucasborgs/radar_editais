"""Suíte de avaliação do Reranker (RAG pipeline).

Mede a qualidade do ranking produzido pelo reranker contra o golden.
Duas métricas:
  • top1_accuracy  — o chunk mais relevante foi rankado em primeiro?
  • ndcg_3         — qualidade do top-3 (NDCG@3 normalizado).

Só cobre o backend ativo (env RERANK_BACKEND). Se reranker retornar None
(backend off ou dep ausente), todos os casos falham com score 0 e o prereq
reporta o problema.
"""
from __future__ import annotations

import json
import math
import os
from typing import Any

from config import ROOT
from core.eval.harness import Evaluation, Suite, get_input, llm_available

GOLDEN = ROOT / "eval_data" / "golden" / "reranker.json"


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
    from core.reranker import rerank_scores
    scores = rerank_scores(inp["query"], inp["chunks"])
    if scores is None:
        return {"error": "reranker indisponível — verifique RERANK_BACKEND"}
    ranking = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
    return {"ranking": ranking, "scores": [round(s, 4) for s in scores]}


def eval_top1(*, output, expected_output, **_) -> Evaluation:
    """O chunk mais relevante foi rankado em primeiro?"""
    if not isinstance(output, dict) or "error" in output:
        return {"name": "top1_accuracy", "value": 0.0,
                "comment": (output or {}).get("error", "output inválido")}
    pred_top = output.get("ranking", [-1])[0]
    exp_top = expected_output["ranking"][0]
    return {
        "name": "top1_accuracy",
        "value": pred_top == exp_top,
        "comment": f"previsto={pred_top} esperado={exp_top}",
    }


def eval_ndcg3(*, output, expected_output, **_) -> Evaluation:
    """NDCG@3: qualidade do top-3 do ranking previsto."""
    if not isinstance(output, dict) or "error" in output:
        return {"name": "ndcg_3", "value": 0.0}
    exp_ranking = expected_output["ranking"]
    n = len(exp_ranking)
    relevance = {idx: n - pos for pos, idx in enumerate(exp_ranking)}
    pred_ranking = output.get("ranking", [])
    k = min(3, n)

    dcg = sum(
        relevance.get(pred_ranking[i], 0) / math.log2(i + 2)
        for i in range(min(k, len(pred_ranking)))
    )
    ideal = sorted(relevance.values(), reverse=True)[:k]
    idcg = sum(v / math.log2(i + 2) for i, v in enumerate(ideal))
    ndcg = round(dcg / idcg, 3) if idcg > 0 else 0.0
    return {"name": "ndcg_3", "value": ndcg}


def _prereqs() -> str | None:
    backend = os.getenv("RERANK_BACKEND", "cross-encoder").lower()
    if backend in ("off", "none", "disabled", ""):
        return "RERANK_BACKEND=off — reranker desligado"
    if backend == "llm" and not llm_available():
        return "RERANK_BACKEND=llm requer LLM (OPENAI/ANTHROPIC key, LLM_BACKEND=ollama/gemini ou *_BASE_URL)"
    if not GOLDEN.exists():
        return f"golden ausente: {GOLDEN}"
    return None


SUITE = Suite(
    name="reranker",
    description="Qualidade do ranking de chunks RAG: top1_accuracy + NDCG@3 vs golden.",
    load_data=load_data,
    task=task,
    evaluators=[eval_top1, eval_ndcg3],
    prereqs=_prereqs,
)
