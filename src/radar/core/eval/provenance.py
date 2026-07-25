"""Radar Data Trust 02 — Suíte `provenance` diagnóstica (RT02-T02).

Roda o caminho real de resolução (`radar.core.kg.evidence_resolver.resolve_quote`)
sobre o golden representativo de proveniência (RT02-T01) e agrega os três sinais
da spec radar-data-trust-02-quality-gates.md §7.1:

  - taxa de resolução de locator (exact / document_only / unresolved);
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

def _load_golden() -> list[dict]:
    raw = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    out: list[dict] = []
    for case in raw:
        cid = case["case_id"]
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
    raw = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    return [str(case["case_id"]) for case in raw]


# ---------------------------------------------------------------------------
# Task — roda o resolvedor real contra os blocks do golden
# ---------------------------------------------------------------------------


def _provenance_task(*, item: dict) -> dict:
    inp = item["input"]
    blocks = inp.get("blocks", [])
    result = resolve_quote(
        quote=inp["quote"],
        blocks=blocks,
        source=inp.get("source", ""),
        edital_id=inp.get("edital_id"),
        native_id=inp.get("native_id"),
        silver_source_hash=inp.get("silver_source_hash"),
        canonical_content_hash=inp.get("canonical_content_hash"),
    )
    candidates = [
        {
            "doc": c.doc,
            "page": c.page,
            "block_idx": c.block_idx,
            "section_path": list(c.section_path),
        }
        for c in result.candidates
    ]
    candidate_coordinates = {
        (candidate["doc"], candidate["page"], candidate["block_idx"])
        for candidate in candidates
    }
    candidate_blocks = [
        {
            "doc": block.get("doc"),
            "page": block.get("page"),
            "block_idx": block.get("idx"),
            "text": block.get("text", ""),
        }
        for block in blocks
        if (block.get("doc"), block.get("page"), block.get("idx"))
        in candidate_coordinates
    ]
    return {
        "locator_quality": (
            result.evidence_ref.locator_quality.value if result.evidence_ref else "unresolved"
        ),
        "evidence_ref": (
            result.evidence_ref.model_dump(mode="json") if result.evidence_ref else None
        ),
        "candidates": candidates,
        "candidate_blocks": candidate_blocks,
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


def eval_faithfulness_verbatim(*, output: dict, **_: Any) -> Evaluation:
    if not output.get("candidates"):
        return {
            "name": "faithfulness_verbatim",
            "value": None,
            "comment": "sem candidato resolvido: fora do denominador",
        }

    quote: str | None = None
    ref = output.get("evidence_ref")
    if isinstance(ref, dict):
        quote = ref.get("quote")
    candidate_blocks = output.get("candidate_blocks") or []
    if not quote:
        return {
            "name": "faithfulness_verbatim",
            "value": 0.0,
            "comment": "candidato resolvido sem quote",
        }
    verbatim = any(quote in str(block.get("text", "")) for block in candidate_blocks)
    return {
        "name": "faithfulness_verbatim",
        "value": 1.0 if verbatim else 0.0,
        "comment": "quote encontrada em bloco candidato" if verbatim else "quote ausente dos blocos candidatos",
    }


# ---------------------------------------------------------------------------
# Run evaluator — agregação dos sinais
# ---------------------------------------------------------------------------


def _run_eval_signal_aggregation(item_results: list[dict]) -> Evaluation:
    locator_counts = {"exact": 0, "document_only": 0, "unresolved": 0}
    faithfulness_ok = 0
    faithfulness_total = 0

    for ir in item_results:
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

    n = len(item_results)
    return {
        "name": "aggregate_signals",
        "value": 1.0,
        "comment": (
            f"locator: exact={locator_counts['exact']}/{n}, "
            f"document_only={locator_counts['document_only']}/{n}, "
            f"unresolved={locator_counts['unresolved']}/{n} | "
            f"faithfulness: {faithfulness_ok}/{faithfulness_total} "
            "(somente casos com candidato resolvido)"
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
        "(b) faithfulness do "
        "trecho. Hermética (sem LLM, DB, rede ou credenciais). "
        "Diagnóstica, sem threshold."
    ),
    load_data=load_data,
    task=_provenance_task,
    evaluators=[
        eval_locator_exact,
        eval_locator_document_only,
        eval_locator_unresolved,
        eval_faithfulness_verbatim,
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
