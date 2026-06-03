"""Suíte de avaliação do RAG (retriever de chunks de edital).

Porta `scripts/eval_rag.py` para o harness unificado: `task` chama
`retrieve_chunks` (mede latência); os `evaluators` reaproveitam
`core.rag_eval` (Recall/Hit@K + reciprocal rank + faithfulness via juiz LLM).
Golden em `eval_data/golden/<source>.json` (gerado por scripts/generate_golden.py).
"""
from __future__ import annotations

import json
import os
import time
from typing import Any

from config import ROOT
from core.eval.harness import Evaluation, Suite, get_input

GOLDEN_DIR = ROOT / "eval_data" / "golden"
SOURCE = os.getenv("EVAL_RAG_SOURCE", "finep")


def load_data() -> list[dict]:
    path = GOLDEN_DIR / f"{SOURCE}.json"
    if not path.exists():
        return []
    from core.retriever import (
        DEFAULT_FTS_WEIGHT,
        DEFAULT_MAX_PER_SOURCE,
        DEFAULT_TOP_K,
    )
    golden = json.loads(path.read_text(encoding="utf-8"))
    items = []
    for q in golden.get("queries", []):
        items.append({
            "input": {
                "edital_id": str(q["edital_id"]),
                "query": q["query"],
                "k": DEFAULT_TOP_K,
                "fts_weight": DEFAULT_FTS_WEIGHT,
                "max_per_source": DEFAULT_MAX_PER_SOURCE,
            },
            "expected_output": q.get("expected", []),
            "metadata": {"case_id": q["id"], "edital_id": str(q["edital_id"])},
        })
    return items


def task(*, item: Any, **_) -> dict:
    inp = get_input(item)
    from core.retriever import retrieve_chunks
    t0 = time.perf_counter()
    retrieved = retrieve_chunks(
        db=None,
        edital_id=inp["edital_id"],
        query=inp["query"],
        k=inp["k"],
        fts_weight=inp["fts_weight"],
        max_per_source=inp["max_per_source"],
    )
    return {
        "retrieved": retrieved,
        "latency_ms": round((time.perf_counter() - t0) * 1000.0, 2),
        "query": inp["query"],
    }


def eval_retrieval(*, output, expected_output, **_) -> list[Evaluation]:
    """Recall/Hit@K + reciprocal rank + null_result + latência."""
    if not isinstance(output, dict) or "error" in output:
        return [{"name": "reciprocal_rank", "value": 0.0,
                 "comment": (output or {}).get("error", "output inválido")}]
    from core.rag_eval import DEFAULT_KS, evaluate_query

    res = evaluate_query(output.get("retrieved", []), expected_output or [], ks=DEFAULT_KS)
    evals: list[Evaluation] = [
        {"name": f"hit_at_{k}", "value": v} for k, v in res["hit_at_k"].items()
    ]
    evals.append({"name": "reciprocal_rank", "value": round(res["reciprocal_rank"], 4)})
    evals.append({"name": "null_result", "value": res["null_result"]})
    evals.append({"name": "latency_ms", "value": output.get("latency_ms")})
    return evals


def eval_faithfulness(*, output, **_) -> Evaluation | None:
    """Context relevance via juiz LLM (0-5). Pulável com EVAL_NO_FAITHFULNESS=1."""
    if os.getenv("EVAL_NO_FAITHFULNESS"):
        return None
    if not isinstance(output, dict) or "error" in output:
        return None
    from core.rag_eval import judge_faithfulness
    return {"name": "faithfulness",
            "value": judge_faithfulness(output.get("query", ""), output.get("retrieved", []))}


def _prereqs() -> str | None:
    if not (os.getenv("SUPABASE_URL") and os.getenv("SUPABASE_SERVICE_KEY")):
        return "requer SUPABASE_URL+SERVICE_KEY (retrieval em pgvector)"
    if not os.getenv("OPENAI_API_KEY"):
        return "requer OPENAI_API_KEY (embeddings do retrieval + faithfulness)"
    if not (GOLDEN_DIR / f"{SOURCE}.json").exists():
        return f"golden {SOURCE}.json ausente — rode scripts/generate_golden.py"
    return None


SUITE = Suite(
    name="rag",
    description="Recall/Hit@K + reciprocal rank + faithfulness do retriever de chunks.",
    load_data=load_data,
    task=task,
    evaluators=[eval_retrieval, eval_faithfulness],
    prereqs=_prereqs,
)
