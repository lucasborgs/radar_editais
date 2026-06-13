"""Espinha do harness de avaliação: `Suite` + `run_suite`.

`run_suite` roda uma suíte com a MESMA definição em dois modos:
  • Langfuse configurado → `langfuse.run_experiment` (Experiments + scores).
  • Sem Langfuse        → loop local idêntico, grava `eval_results/<ts>_<suite>.json`.

O contrato dos `evaluators` e do `task` espelha o do Langfuse 4.x, então não há
código de cola divergente entre os dois caminhos.
"""
from __future__ import annotations

import json
import logging
import os
import statistics
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TypedDict

from config import ROOT

logger = logging.getLogger(__name__)

EVAL_RESULTS_DIR = ROOT / "eval_results"


class Evaluation(TypedDict, total=False):
    """Um score nomeado de um output. Compatível com a Evaluation do Langfuse."""
    name: str
    value: float | int | bool | None
    comment: str


# Um caso: {"input": ..., "expected_output": ..., "metadata": {...}}.
Item = dict[str, Any]
# task(*, item, **kwargs) -> output
TaskFn = Callable[..., Any]
# evaluator(*, input, output, expected_output, metadata, **kwargs) -> Evaluation | list[Evaluation] | None
EvaluatorFn = Callable[..., "Evaluation | list[Evaluation] | None"]
# run_evaluator(item_results) -> Evaluation | list[Evaluation] | None
RunEvaluatorFn = Callable[[list[dict]], "Evaluation | list[Evaluation] | None"]


@dataclass
class Suite:
    """Definição declarativa de uma avaliação de pipeline."""
    name: str
    description: str
    load_data: Callable[[], list[Item]]
    task: TaskFn
    evaluators: Sequence[EvaluatorFn]
    run_evaluators: Sequence[RunEvaluatorFn] = field(default_factory=tuple)
    # Retorna um motivo-para-pular (str) quando faltam pré-requisitos (DB, creds,
    # artefatos), ou None quando pode rodar. Evita falha obscura no meio da rodada.
    prereqs: Callable[[], str | None] | None = None


def get_input(item: Any) -> Any:
    """Lê o `input` de um item (dict local ou DatasetItem do Langfuse)."""
    if isinstance(item, dict):
        return item.get("input")
    return getattr(item, "input", None)


def _langfuse_configured() -> bool:
    return bool(os.getenv("LANGFUSE_PUBLIC_KEY") and os.getenv("LANGFUSE_SECRET_KEY"))


def _coerce_evals(raw: Any) -> list[Evaluation]:
    if raw is None:
        return []
    return list(raw) if isinstance(raw, list) else [raw]


# ---------------------------------------------------------------------------
# Fallback local (sem Langfuse) — replica a semântica do run_experiment.
# ---------------------------------------------------------------------------

def _run_local(suite: Suite, items: list[Item]) -> tuple[list[dict], list[Evaluation]]:
    item_results: list[dict] = []
    for item in items:
        inp = get_input(item)
        expected = item.get("expected_output") if isinstance(item, dict) else None
        meta = (item.get("metadata") if isinstance(item, dict) else None) or {}
        try:
            output = suite.task(item=item)
        except Exception as e:  # isola falha de um caso (espelha o Langfuse)
            logger.error("task falhou (%s): %s", meta.get("case_id", "?"), e)
            output = {"error": str(e)}

        evals: list[Evaluation] = []
        for ev in suite.evaluators:
            try:
                r = ev(input=inp, output=output, expected_output=expected, metadata=meta)
            except Exception as e:
                r = {"name": getattr(ev, "__name__", "evaluator"),
                     "value": None, "comment": f"erro: {e}"}
            evals.extend(_coerce_evals(r))

        item_results.append({
            "input": inp, "output": output, "expected_output": expected,
            "metadata": meta, "evaluations": evals,
        })

    run_evals: list[Evaluation] = []
    for rev in suite.run_evaluators:
        try:
            run_evals.extend(_coerce_evals(rev(item_results)))
        except Exception as e:
            logger.error("run_evaluator falhou: %s", e)
    return item_results, run_evals


def _aggregate(item_results: list[dict]) -> dict[str, float]:
    """Média por nome de score entre os itens (valores numéricos/bool)."""
    buckets: dict[str, list[float]] = defaultdict(list)
    for ir in item_results:
        for ev in ir.get("evaluations", []):
            v = ev.get("value")
            if isinstance(v, bool):
                buckets[ev["name"]].append(1.0 if v else 0.0)
            elif isinstance(v, (int, float)):
                buckets[ev["name"]].append(float(v))
    return {f"mean_{k}": round(statistics.mean(vs), 4) for k, vs in buckets.items() if vs}


# ---------------------------------------------------------------------------
# Caminho Langfuse
# ---------------------------------------------------------------------------

def _to_lf_evals(raw: Any, LFEval: type) -> Any:  # noqa: N803 — alias da classe Langfuse Evaluation
    """Converte Evaluation dict(s) → instâncias Langfuse Evaluation (tem .name)."""
    if raw is None:
        return None
    evals = raw if isinstance(raw, list) else [raw]
    result = [
        LFEval(name=e["name"], value=e.get("value"), comment=e.get("comment"))
        for e in evals if e and e.get("name")
    ]
    return result if len(result) > 1 else (result[0] if result else None)


def _run_langfuse(suite: Suite, items: list[Item], run_name: str) -> Any:
    from langfuse import Evaluation as LFEval
    from langfuse import Langfuse
    lf = Langfuse()

    def _wrap_ev(ev_fn: EvaluatorFn):
        def wrapper(*, input, output, expected_output, metadata, **kwargs):
            return _to_lf_evals(
                ev_fn(input=input, output=output,
                      expected_output=expected_output, metadata=metadata),
                LFEval,
            )
        return wrapper

    def _wrap_run_ev(run_ev_fn: RunEvaluatorFn):
        def wrapper(*, item_results, **kwargs):
            # Normaliza ExperimentItemResult → dict local para nossos run_evaluators.
            local_results = [
                {
                    "output": getattr(ir, "output", None),
                    "metadata": getattr(ir, "metadata", None) or {},
                    "evaluations": [
                        {"name": getattr(e, "name", None),
                         "value": getattr(e, "value", None),
                         "comment": getattr(e, "comment", None)}
                        for e in (getattr(ir, "evaluations", []) or [])
                    ],
                }
                for ir in (item_results or [])
            ]
            return _to_lf_evals(run_ev_fn(local_results), LFEval)
        return wrapper

    result = lf.run_experiment(
        name=suite.name,
        run_name=run_name,
        description=suite.description,
        data=items,
        task=suite.task,
        evaluators=[_wrap_ev(ev) for ev in suite.evaluators],
        run_evaluators=[_wrap_run_ev(rev) for rev in suite.run_evaluators],
    )
    lf.flush()
    return result


def _normalize_lf_result(result: Any) -> tuple[list[dict], list[Evaluation], str | None]:
    """Converte ExperimentResult → mesmo shape do fallback, para persistir/imprimir."""
    item_results = []
    for ir in getattr(result, "item_results", []) or []:
        evals = []
        for ev in getattr(ir, "evaluations", []) or []:
            evals.append({
                "name": getattr(ev, "name", None),
                "value": getattr(ev, "value", None),
                "comment": getattr(ev, "comment", None),
            })
        item_results.append({
            "output": getattr(ir, "output", None),
            "metadata": getattr(ir, "metadata", None) or {},
            "evaluations": evals,
        })
    run_evals = [
        {"name": getattr(e, "name", None), "value": getattr(e, "value", None),
         "comment": getattr(e, "comment", None)}
        for e in (getattr(result, "run_evaluations", []) or [])
    ]
    url = getattr(result, "dataset_run_url", None)
    return item_results, run_evals, url


# ---------------------------------------------------------------------------
# Orquestração
# ---------------------------------------------------------------------------

def run_suite(
    suite: Suite,
    *,
    push: bool = True,
    out_dir: Path = EVAL_RESULTS_DIR,
    limit: int | None = None,
) -> dict:
    """Roda uma suíte. `push=True` usa Langfuse quando configurado; senão local."""
    if suite.prereqs is not None:
        reason = suite.prereqs()
        if reason:
            print(f"[skip] {suite.name}: {reason}")
            return {"suite": suite.name, "skipped": reason}

    items = suite.load_data()
    if limit:
        items = items[:limit]
    if not items:
        print(f"[skip] {suite.name}: nenhum caso carregado")
        return {"suite": suite.name, "skipped": "sem casos"}

    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    run_name = f"{suite.name}-{ts}"
    used_langfuse = push and _langfuse_configured()

    if used_langfuse:
        print(f"→ {suite.name}: rodando {len(items)} casos via Langfuse Experiments…")
        result = _run_langfuse(suite, items, run_name)
        item_results, run_evals, url = _normalize_lf_result(result)
        if url:
            print(f"   Langfuse: {url}")
    else:
        print(f"→ {suite.name}: rodando {len(items)} casos (fallback local)…")
        item_results, run_evals = _run_local(suite, items)
        url = None

    aggregate = _aggregate(item_results)
    for ev in run_evals:
        if isinstance(ev.get("value"), (int, float, bool)):
            aggregate[ev["name"]] = ev["value"]

    payload = {
        "suite": suite.name,
        "run_name": run_name,
        "backend": "langfuse" if used_langfuse else "local",
        "langfuse_url": url,
        "n_cases": len(item_results),
        "aggregate": aggregate,
        "run_evaluations": run_evals,
        "item_results": item_results,
    }

    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{ts}_{suite.name}.json"
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str),
                        encoding="utf-8")

    _print_summary(suite.name, aggregate, out_path)
    payload["out_path"] = str(out_path)
    return payload


def _print_summary(name: str, aggregate: dict, out_path: Path) -> None:
    print(f"\n=== {name.upper()} — AGREGADO ===")
    if not aggregate:
        print("  (sem métricas numéricas)")
    for k, v in sorted(aggregate.items()):
        print(f"  {k:<32} {v:.4f}" if isinstance(v, float) else f"  {k:<32} {v}")
    print(f"\nResultados: {out_path}")
