"""
Radar Data Trust 00 — Suíte shadow de classificação de relevância.

Compara a saída dos classificadores shadow com os 14 goldens de RT00-T02.
Sem alteração de staging, ledger, cache, gold, API ou frontend.

Uso:
    python -m radar.core.eval run relevance_shadow
"""
from __future__ import annotations

import json
import logging
import os
import re

from radar.core.config import ROOT
from radar.core.eval.harness import Evaluation, Suite, get_input
from radar.core.eval.relevance_goldens import (
    GOLDEN_DIR,
    SILVER_DIR,
    RelevanceGoldenLoader,
)

logger = logging.getLogger(__name__)

# =============================================================================
# Input assembly — mapeia cada source_ref para o material de entrada
# =============================================================================

_TRIAGE_GOLDEN = ROOT / "data" / "evaluation" / "golden" / "triage.json"

# Map dataset kind → silver catalog file + key path
_SILVER_CATALOGS: dict[str, tuple[str, str]] = {
    "investor": ("investidores.json", "investidores"),
    "program": ("programas.json", "programas"),
}


def _load_legacy_triage_cases() -> dict[str, dict]:
    """Carrega triage.json como mapa case_id → entry."""
    if not _TRIAGE_GOLDEN.exists():
        return {}
    raw = json.loads(_TRIAGE_GOLDEN.read_text(encoding="utf-8"))
    return {t["case_id"]: t for t in raw if "case_id" in t}


def _load_silver_record(kind: str, record_id: str) -> dict | None:
    """Carrega um registro do catálogo silver pelo kind + record_id."""
    catalog_info = _SILVER_CATALOGS.get(kind)
    if not catalog_info:
        return None
    fname, key = catalog_info
    path = SILVER_DIR / fname
    if not path.exists():
        return None
    raw = json.loads(path.read_text(encoding="utf-8"))
    items = raw.get(key, raw) if isinstance(raw, dict) else raw
    for item in items:
        if isinstance(item, dict):
            iid = item.get("id") or item.get("case_id")
            if iid and str(iid) == record_id:
                return item
    return None


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", str(s)).strip()


def _assemble_input(item: dict) -> dict:
    """Monta o input, expected_output e metadata para um item do golden.

    Returns:
      {"input": {...}, "expected_output": {...}, "metadata": {...}}
    """
    loader = RelevanceGoldenLoader()
    loader.load_all()

    kind = item["kind"]
    case_id = item["case_id"]
    source_ref = item.get("source_ref", "")
    source_record_id = item.get("source_record_id", "")
    verdict = item.get("verdict", {})

    metadata = {
        "case_id": case_id,
        "kind": kind,
        "source_ref": source_ref,
        "source_record_id": source_record_id,
        "as_of": item.get("as_of", ""),
    }

    material = ""
    source_quality = "unknown"

    if source_ref.startswith("src:"):
        # Look up actor_sources.json
        src_id = source_ref
        for s in loader._actor_sources:
            if s.get("source_id") == src_id:
                material = s.get("quote", "")
                source_quality = "official_page"
                break
        if not material:
            logger.warning("source_id %s not found in actor_sources", src_id)
    elif source_ref == "legacy_triage_case":
        triage_map = _load_legacy_triage_cases()
        entry = triage_map.get(source_record_id)
        if entry:
            parts = [entry.get("title", ""), entry.get("snippet", ""),
                     entry.get("content", "")]
            material = "\n\n".join(p for p in parts if p)
            source_quality = "legacy_triage"
        else:
            logger.warning("case_id %s not found in triage.json", source_record_id)
    elif source_ref == "curated_record":
        record = _load_silver_record(kind, source_record_id)
        if record:
            material = json.dumps(record, ensure_ascii=False, indent=2)
            source_quality = "curated_record"
        else:
            logger.warning("record %s not found in silver catalog", source_record_id)

    metadata["source_quality"] = source_quality

    expected_output = {
        "decision": verdict.get("decision", ""),
        "reason_codes": verdict.get("reason_codes", []),
        "exclusion_codes": verdict.get("exclusion_codes", []),
    }

    return {
        "input": {"content": _norm(material)},
        "expected_output": expected_output,
        "metadata": metadata,
    }


def _classify_one(item: dict) -> dict:
    """Roda o classificador shadow para um item.

    Returns:
      {"verdict": dict} em sucesso.
      {"error": str} em falha operacional.
    """
    from radar.core.ingestion.relevance_classifier import classify

    inp = get_input(item)
    if not inp:
        return {"error": "input vazio"}
    content = inp.get("content", "")
    if not content:
        return {"error": "material de entrada vazio"}

    meta = item.get("metadata", {})
    kind = meta.get("kind", "")
    if not kind:
        return {"error": "kind não especificado no metadata"}

    return classify(kind, content)


# =============================================================================
# Load data — monta os 14 itens a partir dos goldens
# =============================================================================


def load_data() -> list[dict]:
    """Carrega e monta os 14 casos do golden relevance.

    Cada caso contém input, expected_output e metadata para o harness.
    """
    loader = RelevanceGoldenLoader()
    loader.load_all()

    items: list[dict] = []
    for _fk, _items in loader.data.items():
        for golden_item in _items:
            assembled = _assemble_input(golden_item)
            assembled["metadata"].update({
                "kind": golden_item.get("kind", ""),
                "case_id": golden_item.get("case_id", ""),
            })
            items.append(assembled)
    return items


# =============================================================================
# Item-level evaluators
# =============================================================================


def eval_decision_accuracy(*, output, expected_output, **_) -> Evaluation:
    """Veredito bate com o golden? 1.0 se acertou, 0.0 se errou, None se erro."""
    if not isinstance(output, dict) or "error" in output:
        return {"name": "decision_accuracy", "value": None,
                "comment": (output or {}).get("error", "output inválido")}
    pred = (output.get("verdict") or {}).get("decision")
    exp = (expected_output or {}).get("decision")
    if pred is None or exp is None:
        return {"name": "decision_accuracy", "value": None,
                "comment": f"pred={pred} exp={exp}"}
    return {"name": "decision_accuracy", "value": pred == exp,
            "comment": f"pred={pred} exp={exp}"}


def eval_reason_code_coverage(*, output, expected_output, **_) -> Evaluation:
    """Fração dos reason codes do golden que foram detectados.

    reason_code é detectado se aparece em output.reason_codes
    ou output.exclusion_codes. None em erro operacional.
    """
    if not isinstance(output, dict) or "error" in output:
        return {"name": "reason_code_coverage", "value": None,
                "comment": (output or {}).get("error", "output inválido")}
    verdict = output.get("verdict") or {}
    exp = expected_output or {}
    pred_codes = set(verdict.get("reason_codes", []))
    pred_excl = set(verdict.get("exclusion_codes", []))
    pred_all = pred_codes | pred_excl
    exp_codes = set(exp.get("reason_codes", []))
    exp_excl = set(exp.get("exclusion_codes", []))
    exp_all = exp_codes | exp_excl
    if not exp_all:
        return {"name": "reason_code_coverage", "value": 1.0,
                "comment": "sem reason codes esperados"}
    matched = len(pred_all & exp_all)
    coverage = matched / len(exp_all)
    return {"name": "reason_code_coverage", "value": coverage,
            "comment": f"{matched}/{len(exp_all)} reason codes"}


def eval_reason_code_precision(*, output, expected_output, **_) -> Evaluation:
    """Fração dos reason codes emitidos que estão no golden.

    None em erro operacional.
    """
    if not isinstance(output, dict) or "error" in output:
        return {"name": "reason_code_precision", "value": None,
                "comment": (output or {}).get("error", "output inválido")}
    verdict = output.get("verdict") or {}
    exp = expected_output or {}
    pred_codes = set(verdict.get("reason_codes", []))
    pred_excl = set(verdict.get("exclusion_codes", []))
    pred_all = pred_codes | pred_excl
    exp_codes = set(exp.get("reason_codes", []))
    exp_excl = set(exp.get("exclusion_codes", []))
    exp_all = exp_codes | exp_excl
    if not pred_all:
        return {"name": "reason_code_precision", "value": 1.0,
                "comment": "sem reason codes emitidos"}
    matched = len(pred_all & exp_all)
    precision = matched / len(pred_all)
    return {"name": "reason_code_precision", "value": precision,
            "comment": f"{matched}/{len(pred_all)} reason codes corretos"}


def eval_fn_guard(*, output, expected_output, **_) -> Evaluation:
    """Guarda de falso negativo.

    1.0 se golden=in_scope e pred não é in_scope (FN detectado).
    0.0 se golden=in_scope e pred=in_scope (acertou).
    0.0 se golden não é in_scope.
    None em erro operacional.
    """
    if not isinstance(output, dict) or "error" in output:
        return {"name": "fn_guard", "value": None,
                "comment": (output or {}).get("error", "output inválido")}
    pred = (output.get("verdict") or {}).get("decision")
    exp = (expected_output or {}).get("decision")
    is_fn = (exp == "in_scope") and (pred != "in_scope")
    return {"name": "fn_guard", "value": 1.0 if is_fn else 0.0,
            "comment": "FN — oportunidade/ator relevante perdido" if is_fn else "ok"}


def eval_evidence_grounding(*, output, expected_output, **_) -> Evaluation:
    """O output contém evidence com quotes? 1.0 = sim, 0.0 = vazio, None = erro."""
    if not isinstance(output, dict) or "error" in output:
        return {"name": "evidence_grounding", "value": None,
                "comment": (output or {}).get("error", "output inválido")}
    evidence = (output.get("verdict") or {}).get("evidence", [])
    has_quotes = any(ev.get("quote") for ev in evidence if isinstance(ev, dict))
    return {"name": "evidence_grounding", "value": 1.0 if has_quotes else 0.0}


def eval_operational_error(*, output, **_) -> Evaluation:
    """1 se houve erro operacional, 0 se não."""
    is_error = isinstance(output, dict) and "error" in output
    comment = (output or {}).get("error", "") if is_error else ""
    return {"name": "operational_error", "value": 1 if is_error else 0,
            "comment": comment}


# =============================================================================
# Run evaluators — métricas agregadas por kind
# =============================================================================


def _kind_from_item(item_result: dict) -> str:
    meta = item_result.get("metadata") or {}
    return meta.get("kind", "unknown")


def _verdict_decision(item_result: dict) -> str | None:
    output = item_result.get("output")
    if isinstance(output, dict) and "verdict" in output:
        return output["verdict"].get("decision")
    return None


def _expected_decision(item_result: dict) -> str | None:
    expected = item_result.get("expected_output")
    if isinstance(expected, dict):
        return expected.get("decision")
    return None


def run_eval_metrics_by_kind(item_results: list[dict]) -> list[Evaluation]:
    """Produz métricas agregadas por kind."""
    from collections import defaultdict

    by_kind: dict[str, list[dict]] = defaultdict(list)
    for ir in item_results:
        k = _kind_from_item(ir)
        by_kind[k].append(ir)

    evals: list[Evaluation] = []
    for kind, items in sorted(by_kind.items()):
        accuracy_values = []
        fn_count = 0
        error_count = 0
        coverage_sum = 0.0
        precision_sum = 0.0
        coverage_n = 0
        precision_n = 0

        for ir in items:
            output = ir.get("output") or {}
            expected = ir.get("expected_output") or {}

            # accuracy
            pred = _verdict_decision(ir)
            exp = _expected_decision(ir)
            if pred is not None and exp is not None:
                accuracy_values.append(pred == exp)

            # fn
            if exp == "in_scope" and pred != "in_scope":
                fn_count += 1

            # error
            if isinstance(output, dict) and "error" in output:
                error_count += 1

            # coverage
            pred_codes = set(output.get("verdict", {}).get("reason_codes", [])) if isinstance(output, dict) else set()
            pred_excl = set(output.get("verdict", {}).get("exclusion_codes", [])) if isinstance(output, dict) else set()
            pred_all = pred_codes | pred_excl
            exp_codes = set(expected.get("reason_codes", []))
            exp_excl = set(expected.get("exclusion_codes", []))
            exp_all = exp_codes | exp_excl
            if exp_all and pred_all:
                coverage_sum += len(pred_all & exp_all) / len(exp_all)
                coverage_n += 1
                precision_sum += len(pred_all & exp_all) / len(pred_all)
                precision_n += 1

        if accuracy_values:
            mean_acc = sum(accuracy_values) / len(accuracy_values)
            evals.append({"name": f"{kind}_mean_accuracy", "value": round(mean_acc, 4),
                          "comment": f"{sum(accuracy_values)}/{len(accuracy_values)} correct"})
        if fn_count:
            evals.append({"name": f"{kind}_fn_count", "value": fn_count,
                          "comment": f"{fn_count} FN(s) in {kind}"})
        if error_count:
            evals.append({"name": f"{kind}_error_count", "value": error_count,
                          "comment": f"{error_count} error(s) in {kind}"})
        if coverage_n:
            evals.append({"name": f"{kind}_mean_coverage", "value": round(coverage_sum / coverage_n, 4),
                          "comment": f"mean reason code coverage for {kind}"})
        if precision_n:
            evals.append({"name": f"{kind}_mean_precision", "value": round(precision_sum / precision_n, 4),
                          "comment": f"mean reason code precision for {kind}"})

    return evals


def run_eval_divergences(item_results: list[dict]) -> list[Evaluation]:
    """Registra divergências agrupadas por kind e case_ids de FN/erro."""
    from collections import defaultdict

    divergences: dict[str, list[str]] = defaultdict(list)
    fn_ids: list[str] = []
    error_ids: list[str] = []

    for ir in item_results:
        meta = ir.get("metadata") or {}
        kind = meta.get("kind", "unknown")
        case_id = meta.get("case_id", "?")
        output = ir.get("output") or {}

        pred = _verdict_decision(ir)
        exp = _expected_decision(ir)

        if pred is not None and exp is not None and pred != exp:
            divergences[kind].append(case_id)

        if isinstance(output, dict) and "error" in output:
            error_ids.append(case_id)

        if exp == "in_scope" and pred != "in_scope":
            fn_ids.append(case_id)

    evals: list[Evaluation] = []
    for kind, ids in sorted(divergences.items()):
        evals.append({"name": f"divergence_{kind}", "value": len(ids),
                      "comment": f"case_ids: {', '.join(ids)}"})

    if fn_ids:
        evals.append({"name": "fn_case_ids", "value": len(fn_ids),
                      "comment": f"case_ids: {', '.join(fn_ids)}"})
    if error_ids:
        evals.append({"name": "error_case_ids", "value": len(error_ids),
                      "comment": f"case_ids: {', '.join(error_ids)}"})

    return evals


# =============================================================================
# Prerequisites
# =============================================================================


def _prereqs() -> str | None:
    if not (os.getenv("OPENAI_API_KEY") or os.getenv("GEMINI_API_KEY")):
        return "requer OPENAI_API_KEY ou GEMINI_API_KEY"
    golden_dir = GOLDEN_DIR
    if not golden_dir.exists():
        return f"golden relevance dir ausente: {golden_dir}"
    if not (golden_dir.parent / "triage.json").exists():
        return "golden triage.json ausente"
    return None


def _expected_case_ids() -> list[str]:
    return [
        "triage-tavily-082", "triage-tavily-093", "triage-dou-000",
        "triage-tavily-084", "triage-tavily-118", "triage-tavily-079",
        "triage-tavily-098",
        "investidor:indicator-capital", "investidor:kptl",
        "ict:embrapii:senai-cimatec",
        "programa:pipe-fapesp", "programa:centelha",
        "agencia:finep", "agencia:fapesp",
    ]


# =============================================================================
# Suite definition
# =============================================================================

SUITE = Suite(
    name="relevance_shadow",
    description=(
        "Classificadores shadow de relevância (RT00-T03): compara a saída dos "
        "5 prompts separados com os 14 goldens representativos. Diagnóstica, "
        "sem threshold ou gate bloqueante."
    ),
    load_data=load_data,
    task=_classify_one,
    evaluators=[
        eval_decision_accuracy,
        eval_reason_code_coverage,
        eval_reason_code_precision,
        eval_fn_guard,
        eval_evidence_grounding,
        eval_operational_error,
    ],
    run_evaluators=[
        run_eval_metrics_by_kind,
        run_eval_divergences,
    ],
    prereqs=_prereqs,
    classification="diagnostic",
    version="1",
    dataset_paths=[
        GOLDEN_DIR / "manifest.json",
        GOLDEN_DIR / "opportunities.json",
        GOLDEN_DIR / "investors.json",
        GOLDEN_DIR / "icts.json",
        GOLDEN_DIR / "programs.json",
        GOLDEN_DIR / "agencies.json",
        GOLDEN_DIR / "actor_sources.json",
        _TRIAGE_GOLDEN,
    ],
    expected_cases=14,
    expected_case_ids=_expected_case_ids,
)
