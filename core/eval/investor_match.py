"""Suíte de avaliação do match-por-tese de INVESTIDOR (Q3, kind_class=entidade).

Espelha `core/eval/matching.py`, mas roda `core.services.investor_match.match_investidores`
(sem gate de elegibilidade nem vigência — entidade). Dois evaluators:
  • precision@K via rúbrica de tese (juiz LLM em `core.investor_eval`): cada fundo
    do top-K é julgado por fit de tese/estágio/setor.
  • expected_hit: os fundos esperados aparecem no top-N? (determinístico).

Golden: `tests/fixtures/eval_investor_match.json`.
"""
from __future__ import annotations

import json
import os
from functools import lru_cache
from typing import Any

from config import ROOT
from core.eval.harness import Evaluation, Suite, get_input

FIXTURE = ROOT / "tests" / "fixtures" / "eval_investor_match.json"


@lru_cache(maxsize=1)
def _funds_by_id() -> dict[str, dict]:
    from core.kg import kg_store
    return {i["id"]: i for i in kg_store.load_investidores()}


def _build_profile(raw: dict):
    from domain.user_profile import CompanyProfile
    allowed = set(CompanyProfile.__dataclass_fields__.keys())
    return CompanyProfile(**{k: v for k, v in raw.items() if k in allowed})


def _fund_summary(fund_id: str) -> str:
    f = _funds_by_id().get(fund_id) or {}
    parts = [f"id: {fund_id}", f"nome: {f.get('name', '')}"]
    if f.get("generalista"):
        parts.append("perfil: GENERALISTA (multissetorial)")
    for key, label in (("tese", "tese"), ("lead_follow", "lead/follow")):
        if f.get(key):
            parts.append(f"{label}: {f[key]}")
    for key, label in (("tese_themes", "temas"), ("setores", "setores"),
                       ("estagio_alvo", "estágio alvo")):
        if f.get(key):
            parts.append(f"{label}: {', '.join(f[key])}")
    ticket = f.get("ticket_range")
    if ticket and (ticket.get("min_brl") or ticket.get("max_brl")):
        parts.append(f"ticket (BRL): {ticket.get('min_brl', '?')}–{ticket.get('max_brl', '?')}")
    return "\n".join(parts)


def load_data() -> list[dict]:
    if not FIXTURE.exists():
        return []
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
    from core.services.entity_matcher import EntityMatcher, catalog_investidores
    profile = _build_profile(inp["profile"])
    matches = EntityMatcher(catalog_investidores).match(profile, top_k=inp.get("top_k", 5))
    return {
        "result_ids": [m.get("id") for m in matches],
        "profile_context": profile.to_context(),
        "matches": [{"id": m.get("id"), "score": m.get("score"),
                     "summary": _fund_summary(m.get("id"))} for m in matches],
    }


def eval_thesis_precision(*, output, **_) -> list[Evaluation]:
    """Rúbrica de tese por fundo (juiz LLM) → precisão@K."""
    if not isinstance(output, dict) or "error" in output:
        return [{"name": "precision_at_3", "value": 0.0,
                 "comment": (output or {}).get("error", "output inválido")}]

    from core.investor_eval import ThesisVerdict, judge_thesis_fit, precision_at_k

    pctx = output.get("profile_context", "")
    verdicts: list[ThesisVerdict] = []
    for m in output.get("matches", []):
        tese, est, setor, rationale = judge_thesis_fit(pctx, m["summary"])
        verdicts.append(ThesisVerdict(fit_tese=tese, fit_estagio=est,
                                      fit_setor=setor, rationale=rationale))
    return [
        {"name": "precision_at_3", "value": round(precision_at_k(verdicts, 3), 3)},
        {"name": "precision_at_5", "value": round(precision_at_k(verdicts, 5), 3)},
    ]


def eval_expected_hit(*, output, expected_output, **_) -> Evaluation | None:
    """Os fundos esperados aparecem no top-N? (None quando o caso não declara expected.)"""
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
        return "requer OPENAI_API_KEY (match + juiz da rúbrica)"
    if not _funds_by_id():
        return "investidores.json vazio — diretório de fundos ausente"
    if not FIXTURE.exists():
        return f"golden ausente: {FIXTURE}"
    return None


SUITE = Suite(
    name="investor_match",
    description="Precisão@K do match-por-tese de investidor via rúbrica LLM + expected_hit.",
    load_data=load_data,
    task=task,
    evaluators=[eval_thesis_precision, eval_expected_hit],
    prereqs=_prereqs,
)
