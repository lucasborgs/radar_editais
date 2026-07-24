"""Espinha do harness de avaliação: `Suite` + `run_suite`.

`run_suite` roda uma suíte com a MESMA definição em duas intenções:
  • run  → diagnóstico local, publicável explicitamente no Langfuse;
  • gate → decisão completa baseada em critérios versionados.

O contrato dos `evaluators` e do `task` espelha o do Langfuse 4.x, então não há
código de cola divergente entre os dois caminhos.
"""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import logging
import os
import platform
import statistics
import subprocess
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, TypedDict

from radar.core.config import ROOT

logger = logging.getLogger(__name__)

EVAL_RESULTS_DIR = ROOT / "eval_results"
MANIFEST_SCHEMA_VERSION = "1"
RunIntent = Literal["run", "gate"]

_SAFE_ENV_DEFAULTS = (
    "LLM_BACKEND",
    "OPENAI_MODEL",
    "OPENAI_MODEL_PRO",
    "EMBEDDING_MODEL",
    "ANTHROPIC_MODEL_AGENT",
    "GEMINI_MODEL",
    "OLLAMA_MODEL",
    "RERANK_BACKEND",
)
_EFFECTIVE_DEFAULTS = {
    "LLM_BACKEND": "openai",
    "OPENAI_MODEL": "gpt-4o-mini",
    "OPENAI_MODEL_PRO": "gpt-4o-mini",
    "EMBEDDING_MODEL": "text-embedding-3-small",
    "ANTHROPIC_MODEL_AGENT": "claude-sonnet-4-6",
    "GEMINI_MODEL": "gemini-2.5-flash",
    "OLLAMA_MODEL": "llama3.2",
    "RERANK_BACKEND": "off",
    "MATCH_V3_MIN_AFFINITY": "0.52",
    "EMBEDDING_DIMENSIONS": "1536",
}
_MODEL_ENV_NAMES = {
    "OPENAI_MODEL", "OPENAI_MODEL_PRO", "EMBEDDING_MODEL",
    "ANTHROPIC_MODEL_AGENT", "GEMINI_MODEL", "OLLAMA_MODEL",
}
_SECRET_ENV_MARKERS = ("KEY", "SECRET", "TOKEN", "PASSWORD", "DATABASE", "DSN", "URL")


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
SuiteClassification = Literal["gate", "candidate", "diagnostic", "experimental"]
MetricDirection = Literal["higher_is_better", "lower_is_better", "expected"]
CriterionOperator = Literal["gte", "lte", "eq"]


@dataclass(frozen=True)
class Criterion:
    """Critério versionado que transforma uma métrica agregada em decisão."""

    metric: str
    operator: CriterionOperator
    threshold: float | int | bool
    description: str = ""

    @property
    def direction(self) -> MetricDirection:
        if self.operator == "gte":
            return "higher_is_better"
        if self.operator == "lte":
            return "lower_is_better"
        return "expected"

    def evaluate(self, aggregate: dict[str, Any]) -> dict[str, Any]:
        observed = aggregate.get(self.metric)
        if observed is None:
            return {
                "metric": self.metric,
                "operator": self.operator,
                "threshold": self.threshold,
                "observed": None,
                "passed": None,
                "description": self.description,
            }
        if self.operator == "gte":
            passed = observed >= self.threshold
        elif self.operator == "lte":
            passed = observed <= self.threshold
        else:
            passed = observed == self.threshold
        return {
            "metric": self.metric,
            "operator": self.operator,
            "threshold": self.threshold,
            "observed": observed,
            "passed": bool(passed),
            "description": self.description,
        }


@dataclass
class Suite:
    """Definição declarativa de uma avaliação de radar.pipeline."""
    name: str
    description: str
    load_data: Callable[[], list[Item]]
    task: TaskFn
    evaluators: Sequence[EvaluatorFn]
    run_evaluators: Sequence[RunEvaluatorFn] = field(default_factory=tuple)
    # Retorna um motivo-para-pular (str) quando faltam pré-requisitos (DB, creds,
    # artefatos), ou None quando pode rodar. Evita falha obscura no meio da rodada.
    prereqs: Callable[[], str | None] | None = None
    classification: SuiteClassification = "diagnostic"
    version: str = "1"
    criteria: Sequence[Criterion] = field(default_factory=tuple)
    metric_directions: dict[str, MetricDirection] = field(default_factory=dict)
    dataset_paths: Sequence[Path] = field(default_factory=tuple)
    expected_cases: int | Callable[[], int] | None = None
    expected_case_ids: Sequence[str] | Callable[[], Sequence[str]] = field(default_factory=tuple)
    manifest_env: Sequence[str] = field(default_factory=tuple)
    manifest_config: dict[str, Any] = field(default_factory=dict)


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


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _git(*args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", *args], cwd=ROOT, check=True, capture_output=True, text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return result.stdout.strip()


def _git_context() -> dict[str, Any]:
    # Apenas mudanças rastreadas entram no snapshot. Artefatos locais não
    # rastreados não alteram a identidade de uma rodada reproduzível.
    diff = _git("diff", "HEAD", "--")
    return {
        "commit": _git("rev-parse", "HEAD"),
        "branch": _git("branch", "--show-current"),
        "dirty": bool(diff),
        "tracked_diff_sha256": _sha256_bytes((diff or "").encode()),
    }


def _runtime_context() -> dict[str, Any]:
    packages: dict[str, str] = {}
    for package in ("radar-editais", "langfuse"):
        try:
            packages[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            continue
    return {
        "python": platform.python_version(),
        "implementation": platform.python_implementation(),
        "platform": platform.platform(),
        "packages": packages,
    }


def _safe_environment(suite: Suite) -> tuple[dict[str, str], dict[str, Any]]:
    names = dict.fromkeys((*_SAFE_ENV_DEFAULTS, *suite.manifest_env))
    all_names = (*names, *suite.manifest_config)
    forbidden = [
        name for name in all_names
        if any(marker in name.upper() for marker in _SECRET_ENV_MARKERS)
    ]
    if forbidden:
        raise ValueError(f"manifest_env contém variável sensível: {', '.join(forbidden)}")
    values = {
        name: os.getenv(name, _EFFECTIVE_DEFAULTS.get(name, ""))
        for name in names
    }
    values = {name: value for name, value in values.items() if value}
    models = {name: value for name, value in values.items() if name in _MODEL_ENV_NAMES}
    config = {name: value for name, value in values.items() if name not in _MODEL_ENV_NAMES}
    config.update(suite.manifest_config)
    return models, config


def _dataset_manifest(
    suite: Suite,
    items: list[Item],
    expected: int | None,
    expected_ids: Sequence[str],
) -> dict[str, Any]:
    files = []
    for path in suite.dataset_paths:
        absolute = path if path.is_absolute() else ROOT / path
        try:
            display_path = absolute.relative_to(ROOT)
        except ValueError:
            display_path = absolute
        entry: dict[str, Any] = {"path": str(display_path)}
        if absolute.is_file():
            content = absolute.read_bytes()
            entry.update({"sha256": _sha256_bytes(content), "bytes": len(content)})
        else:
            entry["missing"] = True
        files.append(entry)
    case_ids = [
        str((item.get("metadata") or {}).get("case_id"))
        for item in items
        if isinstance(item, dict) and (item.get("metadata") or {}).get("case_id") is not None
    ]
    return {
        "files": files,
        "expected_cases": expected,
        "loaded_cases": len(items),
        "case_ids": case_ids,
        "omitted_case_ids": sorted(set(expected_ids) - set(case_ids)),
    }


def _expected_cases(suite: Suite) -> int | None:
    if callable(suite.expected_cases):
        return suite.expected_cases()
    return suite.expected_cases


def _expected_case_ids(suite: Suite) -> Sequence[str]:
    if callable(suite.expected_case_ids):
        return suite.expected_case_ids()
    return suite.expected_case_ids


def _base_manifest(
    suite: Suite,
    *,
    intent: RunIntent,
    items: list[Item],
    expected: int | None,
    expected_ids: Sequence[str],
    started_at: datetime,
) -> dict[str, Any]:
    directions = dict(suite.metric_directions)
    directions.update({criterion.metric: criterion.direction for criterion in suite.criteria})
    models, config = _safe_environment(suite)
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "intent": intent,
        "suite": {
            "name": suite.name,
            "version": suite.version,
            "classification": suite.classification,
            "description": suite.description,
        },
        "git": _git_context(),
        "dataset": _dataset_manifest(suite, items, expected, expected_ids),
        "criteria": [criterion.__dict__ for criterion in suite.criteria],
        "metric_directions": directions,
        "runtime": _runtime_context(),
        "models": models,
        "config": config,
        "execution": {"started_at": started_at.isoformat()},
    }


# ---------------------------------------------------------------------------
# Fallback local (sem Langfuse) — replica a semântica do run_experiment.
# ---------------------------------------------------------------------------

def _eval_max_workers() -> int:
    """Concorrência do loop de casos (env EVAL_MAX_WORKERS, default 4 — modesto
    para não estourar rate limit do gpt-4o-mini)."""
    raw = os.getenv("EVAL_MAX_WORKERS", "4")
    try:
        return max(1, int(raw))
    except ValueError:
        logger.warning("EVAL_MAX_WORKERS inválida (%r) — usando 4", raw)
        return 4


def _run_one_item(
    suite: Suite, item: Item,
) -> tuple[dict, list[dict[str, Any]]]:
    """Roda task + evaluators para UM item. Isolamento de falha: qualquer exceção
    (task ou evaluator) vira erro registrado, nunca propaga (espelha o Langfuse)."""
    errors: list[dict[str, Any]] = []
    inp = get_input(item)
    expected = item.get("expected_output") if isinstance(item, dict) else None
    meta = (item.get("metadata") if isinstance(item, dict) else None) or {}
    try:
        output = suite.task(item=item)
    except Exception as e:
        logger.error("task falhou (%s): %s", meta.get("case_id", "?"), e)
        output = {"error": str(e)}
        errors.append({"stage": "task", "case_id": meta.get("case_id"), "error": str(e)})

    evals: list[Evaluation] = []
    for ev in suite.evaluators:
        try:
            r = ev(input=inp, output=output, expected_output=expected, metadata=meta)
        except Exception as e:
            r = {"name": getattr(ev, "__name__", "evaluator"),
                 "value": None, "comment": f"erro: {e}"}
            errors.append({
                "stage": "evaluator",
                "case_id": meta.get("case_id"),
                "evaluator": getattr(ev, "__name__", "evaluator"),
                "error": str(e),
            })
        evals.extend(_coerce_evals(r))

    return {
        "input": inp, "output": output, "expected_output": expected,
        "metadata": meta, "evaluations": evals,
    }, errors


def _run_local(
    suite: Suite, items: list[Item],
) -> tuple[list[dict], list[Evaluation], list[dict[str, Any]]]:
    import concurrent.futures

    item_results: list[dict | None] = [None] * len(items)
    errors: list[dict[str, Any]] = []
    max_workers = min(_eval_max_workers(), len(items)) or 1

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(_run_one_item, suite, item): i for i, item in enumerate(items)}
        for fut in concurrent.futures.as_completed(futures):
            idx = futures[fut]
            item_results[idx], item_errors = fut.result()
            errors.extend(item_errors)

    run_evals: list[Evaluation] = []
    for rev in suite.run_evaluators:
        try:
            run_evals.extend(_coerce_evals(rev(item_results)))
        except Exception as e:
            logger.error("run_evaluator falhou: %s", e)
            errors.append({
                "stage": "run_evaluator",
                "evaluator": getattr(rev, "__name__", "run_evaluator"),
                "error": str(e),
            })
    return item_results, run_evals, errors


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


def _refuse_hostile_environment() -> str | None:
    """Fail-closed antes de qualquer task/load_data/prereqs.

    Recusa:
      - ENVIRONMENT=production ou staging;
      - ENVIRONMENT ausente/desconhecido/local/test com DATABASE_URL ou
        SUPABASE_URL apontando para alvo remoto.

    Mensagens sanitizadas: nunca incluem DSN, chave ou URL completa.
    Publicação opt-in no Langfuse a partir de ambiente local permanece
    possível (não configura um "alvo remoto" — Langfuse é telemetria de
    eval, não banco de dados do produto).
    """
    from radar.core.environment import Environment, _host, _is_local_host, resolve_environment

    env = resolve_environment()
    if env is Environment.PRODUCTION:
        return "ambiente de produção não permitido para avaliação"
    if env is Environment.STAGING:
        return "ambiente de staging não permitido para avaliação"

    if env in (Environment.LOCAL, Environment.TEST, Environment.UNKNOWN):
        import os

        remote = []
        for var in ("DATABASE_URL", "SUPABASE_URL"):
            host = _host(os.getenv(var))
            if host and not _is_local_host(host):
                remote.append(var)
        if remote:
            return (
                f"ambiente {env.value} com alvo remoto em "
                f"{' e '.join(remote)} recusado"
            )

    return None


def run_suite(
    suite: Suite,
    *,
    intent: RunIntent = "run",
    publish: bool = False,
    push: bool | None = None,
    out_dir: Path = EVAL_RESULTS_DIR,
    limit: int | None = None,
) -> dict:
    """Executa uma suíte como diagnóstico (`run`) ou decisão (`gate`).

    `publish=True` envia ao Langfuse. `push` é um alias temporário para chamadas
    legadas e será removido depois da migração dos consumidores.
    """
    if push is not None:
        publish = push
    started_at = datetime.now(timezone.utc)
    expected = _expected_cases(suite)
    expected_ids = _expected_case_ids(suite)

    hostile = _refuse_hostile_environment()
    if hostile is not None:
        print(f"[block] {suite.name}: {hostile}")
        return _terminal_payload(
            suite, intent=intent, status="error", reason=hostile,
            items=[], expected=expected, expected_ids=expected_ids, started_at=started_at,
            publish=publish,
        )

    if intent == "gate" and limit is not None:
        return _terminal_payload(
            suite, intent=intent, status="error", reason="gate não aceita --limit",
            items=[], expected=expected, expected_ids=expected_ids, started_at=started_at,
            publish=publish,
        )
    if publish and limit is not None:
        return _terminal_payload(
            suite, intent=intent, status="error",
            reason="publicação não aceita rodada limitada",
            items=[], expected=expected, expected_ids=expected_ids, started_at=started_at,
            publish=publish,
        )
    if publish and not _langfuse_configured():
        return _terminal_payload(
            suite, intent=intent, status="error",
            reason="publicação requer LANGFUSE_PUBLIC_KEY e LANGFUSE_SECRET_KEY",
            items=[], expected=expected, expected_ids=expected_ids, started_at=started_at,
            publish=publish,
        )
    if intent == "gate" and suite.classification != "gate":
        return _terminal_payload(
            suite, intent=intent, status="error",
            reason=f"suíte classificada como {suite.classification}; gate não habilitado",
            items=[], expected=expected, expected_ids=expected_ids, started_at=started_at,
            publish=publish,
        )
    if suite.prereqs is not None:
        reason = suite.prereqs()
        if reason:
            print(f"[skip] {suite.name}: {reason}")
            return _terminal_payload(
                suite, intent=intent, status="skipped", reason=reason,
                items=[], expected=expected, expected_ids=expected_ids, started_at=started_at,
                publish=publish,
            )

    items = suite.load_data()
    if limit:
        items = items[:limit]
    if not items:
        print(f"[skip] {suite.name}: nenhum caso carregado")
        return _terminal_payload(
            suite, intent=intent, status="skipped", reason="sem casos",
            items=items, expected=expected, expected_ids=expected_ids, started_at=started_at,
            publish=publish,
        )

    ts = started_at.strftime("%Y%m%d_%H%M%S")
    run_name = f"{suite.name}-{ts}"
    used_langfuse = publish

    if used_langfuse:
        print(f"→ {suite.name}: rodando {len(items)} casos via Langfuse Experiments…")
        result = _run_langfuse(suite, items, run_name)
        item_results, run_evals, url = _normalize_lf_result(result)
        errors = _operational_errors(item_results)
        if url:
            print(f"   Langfuse: {url}")
    else:
        print(f"→ {suite.name}: rodando {len(items)} casos (fallback local)…")
        item_results, run_evals, errors = _run_local(suite, items)
        url = None

    aggregate = _aggregate(item_results)
    for ev in run_evals:
        if isinstance(ev.get("value"), (int, float, bool)):
            aggregate[ev["name"]] = ev["value"]

    criteria_results = [criterion.evaluate(aggregate) for criterion in suite.criteria]
    incomplete = expected is not None and len(item_results) != expected
    if incomplete:
        errors.append({
            "stage": "completeness",
            "error": f"esperados {expected} casos; executados {len(item_results)}",
        })

    if intent == "run":
        status = "error" if errors else "diagnostic"
    elif errors or any(result["passed"] is None for result in criteria_results):
        status = "error"
    elif all(result["passed"] for result in criteria_results):
        status = "passed"
    else:
        status = "failed"

    finished_at = datetime.now(timezone.utc)
    manifest = _base_manifest(
        suite, intent=intent, items=items, expected=expected,
        expected_ids=expected_ids, started_at=started_at,
    )
    manifest["status"] = status
    manifest["execution"].update({
        "finished_at": finished_at.isoformat(),
        "duration_seconds": round((finished_at - started_at).total_seconds(), 3),
        "errors": errors,
    })
    manifest["publication"] = {
        "requested": publish,
        "published": used_langfuse,
        "backend": "langfuse" if used_langfuse else "local",
        "url": url,
    }

    payload = {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "suite": suite.name,
        "run_name": run_name,
        "intent": intent,
        "status": status,
        "backend": "langfuse" if used_langfuse else "local",
        "langfuse_url": url,
        "n_cases": len(item_results),
        "aggregate": aggregate,
        "criteria_results": criteria_results,
        "run_evaluations": run_evals,
        "item_results": item_results,
        "manifest": manifest,
    }

    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{ts}_{suite.name}.json"
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str),
                        encoding="utf-8")

    _print_summary(suite.name, aggregate, out_path)
    payload["out_path"] = str(out_path)
    return payload


def _operational_errors(item_results: list[dict]) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []
    for item in item_results:
        metadata = item.get("metadata") or {}
        output = item.get("output")
        if isinstance(output, dict) and output.get("error"):
            errors.append({
                "stage": "task", "case_id": metadata.get("case_id"),
                "error": str(output["error"]),
            })
        for evaluation in item.get("evaluations", []):
            comment = str(evaluation.get("comment") or "")
            if evaluation.get("value") is None and comment.startswith("erro:"):
                errors.append({
                    "stage": "evaluator", "case_id": metadata.get("case_id"),
                    "evaluator": evaluation.get("name"), "error": comment,
                })
    return errors


def _terminal_payload(
    suite: Suite,
    *,
    intent: RunIntent,
    status: str,
    reason: str,
    items: list[Item],
    expected: int | None,
    expected_ids: Sequence[str],
    started_at: datetime,
    publish: bool = False,
) -> dict[str, Any]:
    manifest = _base_manifest(
        suite, intent=intent, items=items, expected=expected,
        expected_ids=expected_ids, started_at=started_at,
    )
    manifest["status"] = status
    manifest["execution"].update({
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "errors": [{"stage": status, "error": reason}],
    })
    manifest["publication"] = {
        "requested": publish,
        "published": False,
        "backend": "local",
        "url": None,
    }
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "suite": suite.name,
        "intent": intent,
        "status": status,
        "skipped": reason if status == "skipped" else None,
        "error": reason if status == "error" else None,
        "manifest": manifest,
    }


def _print_summary(name: str, aggregate: dict, out_path: Path) -> None:
    print(f"\n=== {name.upper()} — AGREGADO ===")
    if not aggregate:
        print("  (sem métricas numéricas)")
    for k, v in sorted(aggregate.items()):
        print(f"  {k:<32} {v:.4f}" if isinstance(v, float) else f"  {k:<32} {v}")
    print(f"\nResultados: {out_path}")
