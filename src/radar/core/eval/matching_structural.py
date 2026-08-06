"""Suíte `matching_structural` — célula A/B do boost estrutural (gold vs grafo).

Espelho da suíte `matching` (mesmo golden, mesmos evaluators, mesma tarefa) com
uma única diferença: o funil roda com `structural_boost=True` (boost de
vizinhança `similar_a` do kg_spike, `src/radar/core/kg/spike/match_boost.py`).

A decisão (grafo agrega ao match?) NÃO sai de um gate — sai da comparação entre
a rodada `matching` (baseline) e esta rodada nos mesmos `eval_results/*.json`:
mrr, recall@10, false_positives@8, unjudged@8 e hardneg. Por isso
`classification="diagnostic"`, `criteria=()` (postura de spike_kg/provenance).

Prerequisitos = os da `matching` + `kg_spike.edges` populado (mesmo DATABASE_URL).
"""
from __future__ import annotations

import os
from typing import Any

from radar.core.eval import matching
from radar.core.eval.harness import Suite, get_input
from radar.core.eval.matching import (  # noqa: F401 — reuso do golden/evaluators
    AS_OF,
    GOLDEN,
    HARDNEG,
    SUITE_K,
    TOP_K,
    _expected_case_ids,
    _expected_cases,
    eval_false_positives,
    eval_hardneg,
    eval_mrr,
    eval_recall10,
    eval_unjudged,
    load_data,
)


def task(*, item: Any, **_) -> dict:
    from radar.core.services import match_v3

    inp = get_input(item)

    if inp.get("case_kind") == "hardneg":
        v = match_v3.stage1_verdict(inp["edital"], inp["profile"])
        if v is None:
            return {"error": f"edital {inp['edital']} não encontrado em entities"}
        return {"stage1": v}

    matches = match_v3.find_matching_opportunities(
        inp["profile"], kinds=frozenset({"edital"}), as_of=AS_OF,
        top_k=inp.get("top_k", TOP_K), min_affinity=0.0, use_hyde=False,
        structural_boost=True,
    )
    return {
        "ranked": [matching._file_key(m.entity_id) for m in matches],
        "matches": [{
            "file_key": matching._file_key(m.entity_id), "name": m.name[:60],
            "affinity": round(m.affinity, 3), "score": round(m.score, 3),
        } for m in matches],
    }


def _prereqs() -> str | None:
    base = matching._prereqs()
    if base:
        return base
    if not os.getenv("DATABASE_URL"):
        return "requer DATABASE_URL (kg_spike.edges para o boost)"
    return None


SUITE = Suite(
    name="matching_structural",
    description="Match v3 + boost estrutural similar_a (kg_spike) vs mesmo golden do matching — célula A/B.",
    load_data=load_data,
    task=task,
    evaluators=[eval_mrr, eval_recall10, eval_false_positives, eval_unjudged, eval_hardneg],
    prereqs=_prereqs,
    classification="diagnostic",
    version="1",
    criteria=(),
    metric_directions={
        "mean_false_positives_at_8": "lower_is_better",
        "mean_unjudged_at_8": "lower_is_better",
    },
    dataset_paths=[GOLDEN, HARDNEG],
    expected_cases=_expected_cases,
    expected_case_ids=_expected_case_ids,
    manifest_env=["EMBEDDING_MODEL", "EMBEDDING_DIMENSIONS", "MATCH_V3_MIN_AFFINITY"],
    manifest_config={
        "as_of": AS_OF.isoformat(),
        "ranking_top_k": TOP_K,
        "judgment_window": SUITE_K,
        "use_hyde": False,
        "structural_boost": True,
        "boost_edge": "similar_a",
        "alpha_source": "MATCH_STRUCTURAL_ALPHA",
    },
)
