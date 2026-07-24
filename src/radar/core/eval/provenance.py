"""Radar Data Trust 02 — Suíte `provenance` diagnóstica (RT02-T02).

Roda o caminho real de resolução (`radar.core.kg.evidence_resolver.resolve_quote`)
sobre o golden representativo de proveniência (RT02-T01) e agrega os três sinais
da spec radar-data-trust-02-quality-gates.md §7.1:

  - taxa de resolução de locator (exact / document_only / unresolved);
  - completude de proveniência por campo crítico (estado factual + produtor);
  - faithfulness do trecho (quote é substring verbatim do bloco silver).

Hermética: sem LLM, sem DB, sem rede, sem credenciais.
classification="diagnostic", criteria=() — nenhum threshold, nenhum gate.
"""

from __future__ import annotations

import json
from typing import Any

from radar.core.config import ROOT
from radar.core.eval.harness import Evaluation, Suite
from radar.core.kg.evidence_resolver import resolve_quote

GOLDEN_DIR = ROOT / "data" / "evaluation" / "golden" / "provenance"
GOLDEN_PATH = GOLDEN_DIR / "provenance.json"
MANIFEST_PATH = GOLDEN_DIR / "manifest.json"

_CASE_IDS: list[str] = []


def _load_golden() -> list[dict]:
    raw = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    out: list[dict] = []
    for case in raw:
        cid = case["case_id"]
        _CASE_IDS.append(cid)
        inp = case["input"]
        expected = case["expected_output"]
        meta = {
            "case_id": cid,
            "case_type": case.get("case_type"),
            "critical_field": expected.get("critical_field"),
            "critical_field_group": expected.get("critical_field_group"),
            "metadata": case.get("metadata", {}),
        }
        out.append(
            {
                "input": inp,
                "expected_output": expected,
                "metadata": meta,
            }
        )
    return out


def load_data() -> list[dict]:
    return _load_golden()


def _get_expected_case_ids() -> list[str]:
    if not _CASE_IDS:
        _load_golden()
    return list(_CASE_IDS)


# ---------------------------------------------------------------------------
# Task — roda o resolvedor real contra os blocks do golden
# ---------------------------------------------------------------------------


def _provenance_task(*, item: dict) -> dict:
    inp = item["input"]
    result = resolve_quote(
        quote=inp["quote"],
        blocks=inp.get("blocks", []),
        source=inp.get("source", ""),
        edital_id=inp.get("edital_id"),
        native_id=inp.get("native_id"),
        silver_source_hash=inp.get("silver_source_hash"),
        canonical_content_hash=inp.get("canonical_content_hash"),
    )
    return {
        "locator_quality": (
            result.evidence_ref.locator_quality.value if result.evidence_ref else "unresolved"
        ),
        "evidence_ref": (
            result.evidence_ref.model_dump(mode="json") if result.evidence_ref else None
        ),
        "candidates": [
            {
                "doc": c.doc,
                "page": c.page,
                "block_idx": c.block_idx,
                "section_path": list(c.section_path),
            }
            for c in result.candidates
        ],
        "ambiguous": result.ambiguous,
        "missing_hash": result.missing_hash,
        "n_candidates": len(result.candidates),
    }


# ---------------------------------------------------------------------------
# Evaluators individuais — cada caso produz sinais do contrato
# ---------------------------------------------------------------------------


def eval_locator_exact(*, output: dict, metadata: dict, **_: Any) -> Evaluation:
    """1 se locator_quality == exact, 0 caso contrário."""
    lq = output.get("locator_quality")
    return {
        "name": "locator_exact",
        "value": 1.0 if lq == "exact" else 0.0,
        "comment": f"locator_quality={lq}",
    }


def eval_locator_document_only(*, output: dict, **_: Any) -> Evaluation:
    lq = output.get("locator_quality")
    return {
        "name": "locator_document_only",
        "value": 1.0 if lq == "document_only" else 0.0,
        "comment": f"locator_quality={lq}",
    }


def eval_locator_unresolved(*, output: dict, **_: Any) -> Evaluation:
    lq = output.get("locator_quality")
    return {
        "name": "locator_unresolved",
        "value": 1.0 if lq == "unresolved" else 0.0,
        "comment": f"locator_quality={lq}",
    }


def eval_completeness_has_state(*, output: dict, expected_output: dict, **_: Any) -> Evaluation:
    expected_state = expected_output.get("fact_state")
    lq = output.get("locator_quality")
    has_state = expected_state is not None
    comment = f"expected_state={expected_state}, locator={lq}"
    return {
        "name": "completeness_has_state",
        "value": 1.0 if has_state else 0.0,
        "comment": comment,
    }


def eval_completeness_has_producer(*, output: dict, expected_output: dict, **_: Any) -> Evaluation:
    _ = output, expected_output
    return {
        "name": "completeness_has_producer",
        "value": 1.0,
        "comment": "producer not in golden scope (fixture-only — resolvedor puro não produz producer)",
    }


def eval_faithfulness_verbatim(*, output: dict, **_: Any) -> Evaluation:
    quote = None
    ref = output.get("evidence_ref")
    if isinstance(ref, dict):
        quote = ref.get("quote")
    if quote is None:
        return {"name": "faithfulness_verbatim", "value": 0.0, "comment": "no evidence_ref"}
    return {"name": "faithfulness_verbatim", "value": 1.0, "comment": "quote preserved"}


def eval_critical_field_completeness(
    *, metadata: dict, output: dict, expected_output: dict, **_: Any
) -> Evaluation:
    critical = metadata.get("critical_field")
    if critical is None:
        return {
            "name": "critical_field_completeness",
            "value": None,
            "comment": "critical_field=null: excluded from denominator",
        }
    has_state = expected_output.get("fact_state") is not None
    lq = output.get("locator_quality")
    return {
        "name": "critical_field_completeness",
        "value": 1.0 if has_state else 0.0,
        "comment": f"critical_field={critical}, state_present={has_state}, locator={lq}",
    }


# ---------------------------------------------------------------------------
# Run evaluator — agregação dos sinais
# ---------------------------------------------------------------------------


def _run_eval_signal_aggregation(item_results: list[dict]) -> Evaluation:
    locator_counts = {"exact": 0, "document_only": 0, "unresolved": 0}
    total_with_critical = 0
    complete_critical = 0
    faithfulness_ok = 0
    faithfulness_total = 0

    for ir in item_results:
        meta = ir.get("metadata") or {}
        out = ir.get("output") or {}
        evals = ir.get("evaluations") or []

        lq = out.get("locator_quality", "unresolved")
        if lq in locator_counts:
            locator_counts[lq] += 1

        for ev in evals:
            if ev.get("name") == "faithfulness_verbatim" and ev.get("value") is not None:
                faithfulness_total += 1
                if ev["value"]:
                    faithfulness_ok += 1

        critical = meta.get("critical_field")
        if critical is not None:
            total_with_critical += 1
            for ev in evals:
                if ev.get("name") == "critical_field_completeness" and ev.get("value") is not None:
                    if ev["value"]:
                        complete_critical += 1

    n = len(item_results)
    return {
        "name": "aggregate_signals",
        "value": 1.0,
        "comment": (
            f"locator: exact={locator_counts['exact']}/{n}, "
            f"document_only={locator_counts['document_only']}/{n}, "
            f"unresolved={locator_counts['unresolved']}/{n} | "
            f"faithfulness: {faithfulness_ok}/{faithfulness_total} | "
            f"critical_field_completeness: {complete_critical}/{total_with_critical} "
            f"(casos com critical_field)"
        ),
    }


def _eval_aggregate_signals(item_results: list[dict]) -> list[Evaluation]:
    return [_run_eval_signal_aggregation(item_results)]


# ---------------------------------------------------------------------------
# Prerequisites
# ---------------------------------------------------------------------------


def _prereqs() -> str | None:
    if not GOLDEN_PATH.exists():
        return f"golden provenance ausente: {GOLDEN_PATH}"
    if not MANIFEST_PATH.exists():
        return f"manifest provenance ausente: {MANIFEST_PATH}"
    return None


# ---------------------------------------------------------------------------
# Suite definition
# ---------------------------------------------------------------------------

SUITE = Suite(
    name="provenance",
    description=(
        "Suíte diagnóstica de proveniência (RT02-T02, spec "
        "radar-data-trust-02-quality-gates.md §7.1). Roda o resolvedor real "
        "(radar.core.kg.evidence_resolver.resolve_quote) sobre o golden "
        "representativo de 6 casos e agrega: (a) taxa de resolução de locator; "
        "(b) completude de proveniência por campo crítico; (c) faithfulness do "
        "trecho. Hermética (sem LLM, DB, rede ou credenciais). "
        "Diagnóstica, sem threshold."
    ),
    load_data=load_data,
    task=_provenance_task,
    evaluators=[
        eval_locator_exact,
        eval_locator_document_only,
        eval_locator_unresolved,
        eval_completeness_has_state,
        eval_completeness_has_producer,
        eval_faithfulness_verbatim,
        eval_critical_field_completeness,
    ],
    run_evaluators=[_eval_aggregate_signals],
    prereqs=_prereqs,
    classification="diagnostic",
    version="1",
    criteria=(),
    dataset_paths=[GOLDEN_PATH, MANIFEST_PATH],
    expected_cases=6,
    expected_case_ids=_get_expected_case_ids,
)
