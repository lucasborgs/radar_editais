"""Suíte de avaliação da TRIAGEM da Descoberta (é oportunidade real?).

Roda o MESMO `_triage` de produção (`core.opportunity_discovery`) sobre
candidatos golden rotulados (DOU + Tavily de 2026-06-10, auditados à mão) e
mede acurácia + guarda de falso negativo. O FN é o erro CARO (oportunidade
perdida, irreversível); o FP é absorvido pelo funil (extração ~US$0,007 +
badge provisorio + verificação humana) — daí as duas métricas separadas.

Mudou prompt/input/modelo da triagem → rode `python -m core.eval triage`
ANTES de mergear (foi assim que o A/B de 2026-06-10 mostrou que content[:1500]
não domina o snippet: corrige truncamento mas se engana com página-lista).

Golden: `eval_data/golden/triage.json` (casos com `review: true` ainda
aguardam palavra final do fundador — rotulagem inicial por auditoria).
"""
from __future__ import annotations

import json
import os
from typing import Any

from config import ROOT
from core.eval.harness import Evaluation, Suite, get_input

GOLDEN = ROOT / "eval_data" / "golden" / "triage.json"


def load_data() -> list[dict]:
    if not GOLDEN.exists():
        return []
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    items = []
    for g in golden:
        items.append({
            "input": {"title": g.get("title", ""), "url": g.get("url", ""),
                      "snippet": g.get("snippet", ""),
                      "content": g.get("content", ""),
                      "agency": g.get("agency", "")},
            "expected_output": {"is_opportunity": bool(g["expected"])},
            "metadata": {"case_id": g["case_id"], "fonte": g.get("fonte", ""),
                         "review": g.get("review", False)},
        })
    return items


def task(*, item: Any, **_) -> dict:
    inp = get_input(item)
    from core.opportunity_discovery import _make_client, _triage
    from core.web_search import SearchHit

    client, model = _make_client("triage")
    if client is None:
        return {"error": "sem credencial LLM"}
    hit = SearchHit(title=inp["title"], url=inp["url"], snippet=inp["snippet"],
                    content=inp["content"], agency=inp.get("agency", ""))
    verdict = _triage(hit, client, model)
    if verdict is None:
        # Contrato novo do _triage (hardening-pre-beta 4.2): None = falha
        # TRANSIENTE (não é rejeição). No eval vira erro de execução — não
        # conta como falso negativo fantasma.
        return {"error": "triagem falhou (transiente)"}
    return {"is_opportunity": verdict["is_opportunity"],
            "agency": verdict["agency"]}


def eval_triage_accuracy(*, output, expected_output, **_) -> Evaluation:
    """Veredito bate com o label? (média = acurácia da suíte)"""
    if not isinstance(output, dict) or "error" in output:
        return {"name": "triage_accuracy", "value": 0.0,
                "comment": (output or {}).get("error", "output inválido")}
    pred = bool(output.get("is_opportunity"))
    exp = bool((expected_output or {}).get("is_opportunity"))
    return {"name": "triage_accuracy", "value": pred == exp,
            "comment": f"previsto={pred} esperado={exp}"}


def eval_fn_guard(*, output, expected_output, **_) -> Evaluation:
    """Guarda do erro CARO: 0.0 só quando perde oportunidade real (FN).
    Média = 1 - taxa de falso negativo. FP não penaliza aqui (funil absorve)."""
    if not isinstance(output, dict) or "error" in output:
        return {"name": "fn_guard", "value": 0.0,
                "comment": (output or {}).get("error", "output inválido")}
    pred = bool(output.get("is_opportunity"))
    exp = bool((expected_output or {}).get("is_opportunity"))
    fn = exp and not pred
    return {"name": "fn_guard", "value": not fn,
            "comment": "FALSO NEGATIVO — oportunidade real rejeitada" if fn else "ok"}


def _prereqs() -> str | None:
    if not (os.getenv("OPENAI_API_KEY") or os.getenv("GEMINI_API_KEY")):
        return "requer OPENAI_API_KEY ou GEMINI_API_KEY (triagem)"
    if not GOLDEN.exists():
        return f"golden ausente: {GOLDEN}"
    return None


SUITE = Suite(
    name="triage",
    description="Triagem da Descoberta: acurácia + guarda de falso negativo no _triage de produção.",
    load_data=load_data,
    task=task,
    evaluators=[eval_triage_accuracy, eval_fn_guard],
    prereqs=_prereqs,
)
