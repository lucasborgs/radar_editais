"""Suíte de avaliação da EXTRAÇÃO (Fase 2.4).

Mede a extração-LLM contra o golden CORRIGIDO por humano. Três métricas:
  • presence_accuracy — acerto de presença/abstenção (`is_present` previsto ==
    esperado) por campo DECISÃO. É a métrica-chave: pega `absent` errado (a raiz
    do crédito-grátis) nos dois sentidos.
  • value_correctness — onde ambos têm valor, o valor bate? (estrito; `themes`
    cru é naturalmente ruidoso — vira sinal, não erro.)
  • evidence_faithfulness — para campos `stated`, a `evidence` é SUBSTRING real
    da fonte? Pega evidência fabricada.

Golden: `eval_data/golden/extraction.json` (corrigido por revisão humana). O
gate nunca usa rascunhos gerados pela própria LLM como referência.
"""
from __future__ import annotations

import json
import os
from typing import Any

from core.config import ROOT
from core.eval.harness import Criterion, Evaluation, Suite, get_input
from domain.edital_extraction import DECISION_FIELDS

GOLDEN = ROOT / "eval_data" / "golden" / "extraction.json"
def _norm(s: str) -> str:
    return " ".join((s or "").lower().split())


def _present(field: dict | None) -> bool:
    return bool(field) and field.get("state") != "absent" and field.get("value") is not None


def load_data() -> list[dict]:
    if not GOLDEN.exists():
        return []
    from core.ingestion.edital_extractor import raw_by_native_id
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))

    raw_idx: dict[str, dict] = {}
    items = []
    for g in golden:
        src = g["source"]
        if src not in raw_idx:
            try:
                raw_idx[src] = raw_by_native_id(src)
            except Exception:
                raw_idx[src] = {}
        hit = raw_idx[src].get(g["native_id"])
        if not hit:
            continue  # sem o input cru não dá para re-extrair
        items.append({
            "input": {"source": src, "native_id": g["native_id"], "raw": hit["raw"]},
            "expected_output": g["extraction"],
            "metadata": {"case_id": f"{src}:{g['native_id']}", "source": src},
        })
    return items


def task(*, item: Any, **_) -> dict:
    inp = get_input(item)
    from core.ingestion.edital_extractor import extract_edital
    return extract_edital(inp["source"], inp["native_id"], inp["raw"]).model_dump()


def _value_matches(field: str, pred: dict, exp: dict) -> bool:
    pv, ev = pred.get("value"), exp.get("value")
    if field in ("eligible_entities", "themes"):
        return {_norm(x) for x in (pv or [])} == {_norm(x) for x in (ev or [])}
    if field == "mechanism":
        return _norm(str(pv)) == _norm(str(ev))
    if field == "trl_range":
        return (pv or {}) == (ev or {})
    if field == "counterpart":
        return (pv or {}).get("required") == (ev or {}).get("required")
    if field == "requires_ict_partner":
        return pv == ev
    return pv == ev


def eval_presence(*, output, expected_output, **_) -> Evaluation:
    """Acerto de presença/abstenção por campo DECISÃO (a métrica-chave)."""
    if not isinstance(output, dict) or "error" in output:
        return {"name": "presence_accuracy", "value": 0.0, "comment": "extração falhou"}
    hits = sum(
        _present(output.get(f)) == _present(expected_output.get(f))
        for f in DECISION_FIELDS
    )
    return {"name": "presence_accuracy", "value": round(hits / len(DECISION_FIELDS), 3)}


def eval_value(*, output, expected_output, **_) -> Evaluation | None:
    """Correção do value onde ambos (previsto e esperado) têm valor."""
    if not isinstance(output, dict) or "error" in output:
        return None
    both = [f for f in DECISION_FIELDS
            if _present(output.get(f)) and _present(expected_output.get(f))]
    if not both:
        return None
    ok = sum(_value_matches(f, output[f], expected_output[f]) for f in both)
    return {"name": "value_correctness", "value": round(ok / len(both), 3),
            "comment": f"{ok}/{len(both)} campos presentes-em-ambos"}


def eval_faithfulness(*, input, output, **_) -> Evaluation | None:
    """A evidence dos campos `stated` é substring real da fonte?"""
    if not isinstance(output, dict) or "error" in output:
        return None
    raw = _norm((input or {}).get("raw", ""))
    stated = [output.get(f) for f in DECISION_FIELDS
              if (output.get(f) or {}).get("state") == "stated" and (output.get(f) or {}).get("evidence")]
    if not stated:
        return None
    faithful = sum(_norm(f["evidence"]) in raw for f in stated)
    return {"name": "evidence_faithfulness", "value": round(faithful / len(stated), 3),
            "comment": f"{faithful}/{len(stated)} evidências verbatim"}


# Baseline de não-regressão (Fase 2.4): presence_accuracy ≈ 0.95. As adições de
# schema da Fase B multi-quadrante (opportunity_type + campos de desafio/programa)
# NÃO são DECISION_FIELDS → não devem mover este número. O gate trava se mover.
_PRESENCE_BASELINE = 0.95


def run_presence_regression(item_results: list[dict]) -> Evaluation:
    """Gate de não-regressão: média de presence_accuracy < baseline → regressão.

    Vive no harness (run_evaluator), não em teste paralelo. Emite o booleano e a
    média observada no comentário para diagnóstico.
    """
    vals = [
        ev["value"]
        for ir in item_results
        for ev in ir.get("evaluations", [])
        if ev.get("name") == "presence_accuracy" and isinstance(ev.get("value"), (int, float))
    ]
    if not vals:
        return {"name": "presence_regression", "value": None, "comment": "sem casos"}
    mean = sum(vals) / len(vals)
    regressed = mean < _PRESENCE_BASELINE
    return {"name": "presence_regression", "value": regressed,
            "comment": f"mean={mean:.3f} baseline={_PRESENCE_BASELINE} "
                       f"({'REGREDIU' if regressed else 'ok'})"}


def _prereqs() -> str | None:
    backend = os.getenv("LLM_BACKEND", "openai").lower()
    if backend == "openai" and not os.getenv("OPENAI_API_KEY"):
        return "requer OPENAI_API_KEY (extrator OpenAI)"
    if backend == "gemini" and not (
        os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    ):
        return "requer GEMINI_API_KEY ou GOOGLE_API_KEY (extrator Gemini)"
    if not GOLDEN.exists():
        return "golden ausente — rode scripts/bootstrap_extraction_golden.py"
    return None


def _expected_cases() -> int:
    if not GOLDEN.exists():
        return 0
    return len(json.loads(GOLDEN.read_text(encoding="utf-8")))


def _expected_case_ids() -> list[str]:
    if not GOLDEN.exists():
        return []
    return [
        f"{item['source']}:{item['native_id']}"
        for item in json.loads(GOLDEN.read_text(encoding="utf-8"))
    ]


SUITE = Suite(
    name="extraction",
    description="Presença/abstenção + correção de value + faithfulness da extração vs golden.",
    load_data=load_data,
    task=task,
    evaluators=[eval_presence, eval_value, eval_faithfulness],
    run_evaluators=[run_presence_regression],
    prereqs=_prereqs,
    classification="gate",
    version="1",
    criteria=[
        Criterion(
            "mean_presence_accuracy", "gte", _PRESENCE_BASELINE,
            "Média de presença/abstenção não pode regredir do baseline aceito.",
        ),
    ],
    metric_directions={
        "mean_value_correctness": "higher_is_better",
        "mean_evidence_faithfulness": "higher_is_better",
        "presence_regression": "lower_is_better",
    },
    dataset_paths=[GOLDEN],
    expected_cases=_expected_cases,
    expected_case_ids=_expected_case_ids,
    manifest_env=["LLM_BACKEND", "OPENAI_MODEL_PRO", "GEMINI_MODEL", "OLLAMA_MODEL"],
    manifest_config={
        "decision_fields": list(DECISION_FIELDS),
        "raw_source": "core.ingestion.edital_extractor.raw_by_native_id",
    },
)
