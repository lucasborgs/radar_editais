"""Suíte de avaliação da CLASSIFICAÇÃO de `opportunity_type` (Fase B multi-quadrante).

Mede se o pipeline de Descoberta rotula corretamente o tipo-evento
(edital | desafio | programa) a partir do texto de uma página. Roda o MESMO
`_extract` de produção (`radar.core.ingestion.opportunity_discovery`) sobre trechos golden
rotulados — é a peça da Fase B que é rodável hoje (o MATCH de desafio/programa
fica bloqueado por dados: a torneira web está inerte no launch).

Golden: `data/evaluation/golden/opportunity_type.json` (trechos rotulados à mão).
"""
from __future__ import annotations

import json
import os
from typing import Any

from radar.core.config import ROOT
from radar.core.eval.harness import Evaluation, Suite, get_input

GOLDEN = ROOT / "data" / "evaluation" / "golden" / "opportunity_type.json"


def load_data() -> list[dict]:
    if not GOLDEN.exists():
        return []
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    items = []
    for g in golden:
        items.append({
            "input": {"title": g.get("title", ""), "url": g.get("url", ""),
                      "text": g.get("text", "")},
            "expected_output": {"opportunity_type": g["expected_type"]},
            "metadata": {"case_id": g["case_id"]},
        })
    return items


def task(*, item: Any, **_) -> dict:
    inp = get_input(item)
    from radar.core.ingestion.opportunity_discovery import _extract, _make_client
    from radar.core.web_search import SearchHit

    client, model = _make_client("extract")
    if client is None:
        return {"error": "sem credencial LLM"}
    hit = SearchHit(title=inp["title"], url=inp["url"],
                    snippet=inp["text"][:300], content=inp["text"])
    rec = _extract(hit, inp["text"], agency="", client=client, model=model)
    if rec is None:
        return {"error": "extração retornou None"}
    return {"opportunity_type": rec.get("opportunity_type", "edital")}


def eval_type_accuracy(*, output, expected_output, **_) -> Evaluation:
    """O tipo-evento previsto bate com o esperado?"""
    if not isinstance(output, dict) or "error" in output:
        return {"name": "type_accuracy", "value": 0.0,
                "comment": (output or {}).get("error", "output inválido")}
    pred = output.get("opportunity_type")
    exp = (expected_output or {}).get("opportunity_type")
    return {"name": "type_accuracy", "value": pred == exp,
            "comment": f"previsto={pred} esperado={exp}"}


def _prereqs() -> str | None:
    if not (os.getenv("OPENAI_API_KEY") or os.getenv("GEMINI_API_KEY")):
        return "requer OPENAI_API_KEY ou GEMINI_API_KEY (extrator)"
    if not GOLDEN.exists():
        return f"golden ausente: {GOLDEN}"
    return None


SUITE = Suite(
    name="opportunity_type",
    description="Acurácia da classificação edital/desafio/programa no _extract de produção.",
    load_data=load_data,
    task=task,
    evaluators=[eval_type_accuracy],
    prereqs=_prereqs,
    classification="diagnostic",
)
