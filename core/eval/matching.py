"""Suíte de avaliação do matching (topo do funil).

Reposiciona `scripts/eval_matching.py` no harness unificado SEM reescrever o
julgamento: `task` roda o HybridMatch real; os `evaluators` reaproveitam a
rúbrica de `core.matching_eval` (fit temático + elegibilidade via juiz LLM,
vigência determinística) e a checagem `expected_in_top`.
"""
from __future__ import annotations

import json
import os
from functools import lru_cache
from typing import Any

from config import ROOT
from core.eval.harness import Evaluation, Suite, get_input

FIXTURE = ROOT / "tests" / "fixtures" / "eval_matching.json"


@lru_cache(maxsize=1)
def _service():
    from core.services.hybrid_match_service import HybridMatchService
    return HybridMatchService()


def _build_profile(raw: dict):
    from domain.user_profile import CompanyProfile
    allowed = set(CompanyProfile.__dataclass_fields__.keys())
    return CompanyProfile(**{k: v for k, v in raw.items() if k in allowed})


def _edital_summary(svc, edital_id: str) -> str:
    card = svc.get_edital_by_id(edital_id) or {}
    parts = [f"id: {edital_id}", f"título: {card.get('title', '')}"]
    for key, label in (("objective", "objetivo"), ("mechanism", "mecanismo"),
                       ("trl_range", "TRL"), ("deadline", "deadline")):
        if card.get(key):
            parts.append(f"{label}: {card[key]}")
    if card.get("themes"):
        parts.append(f"temas: {', '.join(card.get('themes') or [])}")
    if card.get("eligible_entities"):
        parts.append(f"público-alvo: {', '.join(card.get('eligible_entities') or [])}")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Suíte
# ---------------------------------------------------------------------------

def load_data() -> list[dict]:
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    profiles = data["profiles"]
    items = []
    for case in data["cases"]:
        raw = profiles.get(case["profile"])
        if raw is None:
            continue
        items.append({
            "input": {"profile": raw, "top_k": 5},
            "expected_output": {
                "expected_top": case.get("expected_top", []),
                "expected_top_n": case.get("expected_top_n", 3),
            },
            "metadata": {"case_id": case["id"], "profile_name": case["profile"]},
        })
    return items


def task(*, item: Any, **_) -> dict:
    inp = get_input(item)
    svc = _service()
    profile = _build_profile(inp["profile"])
    matches = svc.match(profile, top_k=inp.get("top_k", 5))

    from core.kg.temporal import temporal_context
    enriched = []
    for m in matches:
        eid = m["id"]
        ctx = temporal_context(eid)
        enriched.append({
            "id": eid,
            "score": m.get("score"),
            "eligible": m.get("eligible", True),
            "vigente": not (ctx.expired if ctx else False),
            "summary": _edital_summary(svc, eid),
        })
    return {
        "result_ids": [m["id"] for m in matches],
        "profile_context": profile.to_context(),
        "matches": enriched,
    }


def eval_rubric_precision(*, output, **_) -> list[Evaluation]:
    """Rúbrica por match (juiz LLM) → precisão@K + violações de vigência/elegibilidade."""
    if not isinstance(output, dict) or "error" in output:
        return [{"name": "precision_at_3", "value": 0.0,
                 "comment": (output or {}).get("error", "output inválido")}]

    from core.matching_eval import RubricVerdict, judge_match_rubric, precision_at_k

    pctx = output.get("profile_context", "")
    verdicts: list[RubricVerdict] = []
    n_expired = n_ineligible = 0
    for m in output.get("matches", []):
        if not m["vigente"]:
            n_expired += 1
        if m["eligible"] is False:
            n_ineligible += 1
        fit, elig, rationale = judge_match_rubric(pctx, m["summary"])
        verdicts.append(RubricVerdict(
            fit_tematico=fit, elegibilidade=elig, vigente=m["vigente"], rationale=rationale,
        ))
    return [
        {"name": "precision_at_3", "value": round(precision_at_k(verdicts, 3), 3)},
        {"name": "precision_at_5", "value": round(precision_at_k(verdicts, 5), 3)},
        {"name": "expired_in_topk", "value": n_expired,
         "comment": "VIGÊNCIA VIOLADA" if n_expired else ""},
        {"name": "ineligible_in_topk", "value": n_ineligible},
    ]


def eval_expected_hit(*, output, expected_output, **_) -> Evaluation | None:
    """Os editais esperados aparecem no top-N? (None quando o caso não declara expected.)"""
    expected = (expected_output or {}).get("expected_top", [])
    if not expected:
        return None
    from core.matching_eval import expected_in_top
    n = (expected_output or {}).get("expected_top_n", 3)
    ids = output.get("result_ids", []) if isinstance(output, dict) else []
    hit = all(expected_in_top(ids, e, n) for e in expected)
    return {"name": "expected_hit", "value": hit, "comment": f"esperados={expected}"}


def _prereqs() -> str | None:
    if not os.getenv("OPENAI_API_KEY"):
        return "requer OPENAI_API_KEY (juiz da rúbrica + Stage 2)"
    from core.kg import kg_store
    if not kg_store.load_index().get("editais"):
        return "índice do KG vazio — rode pipeline/build_knowledge_graph"
    return None


SUITE = Suite(
    name="matching",
    description="Precisão@K do HybridMatch via rúbrica LLM + vigência/elegibilidade.",
    load_data=load_data,
    task=task,
    evaluators=[eval_rubric_precision, eval_expected_hit],
    prereqs=_prereqs,
)
