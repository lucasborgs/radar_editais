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
        "failed_codes": verdict.get("failed_codes", []),
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
        return {"name": "reason_code_precision", "value": None,
                "comment": "sem reason codes emitidos; precisão indefinida"}
    matched = len(pred_all & exp_all)
    precision = matched / len(pred_all)
    return {"name": "reason_code_precision", "value": precision,
            "comment": f"{matched}/{len(pred_all)} reason codes corretos"}


def eval_fn_guard(*, output, expected_output, **_) -> Evaluation:
    """Guarda de falso negativo (convenção da suíte triage).

    1.0 = não houve falso negativo (golden in_scope foi mantido OU golden não é in_scope).
    0.0 = golden in_scope foi perdido (pred != in_scope).
    None = erro operacional.
    """
    if not isinstance(output, dict) or "error" in output:
        return {"name": "fn_guard", "value": None,
                "comment": (output or {}).get("error", "output inválido")}
    pred = (output.get("verdict") or {}).get("decision")
    exp = (expected_output or {}).get("decision")
    if exp == "in_scope" and pred != "in_scope":
        return {"name": "fn_guard", "value": 0.0,
                "comment": "FN — golden in_scope perdido"}
    return {"name": "fn_guard", "value": 1.0,
            "comment": "ok"}


def eval_evidence_grounding(*, output, expected_output, input, **_) -> Evaluation:
    """Verifica que todas as quotes estão no material e todo código tem evidence.

    Utiliza input real (material) para verificar substring.
    1.0 = ok, 0.0 = falha, None = erro operacional.
    """
    if not isinstance(output, dict) or "error" in output:
        return {"name": "evidence_grounding", "value": None,
                "comment": (output or {}).get("error", "output inválido")}
    verdict = output.get("verdict")
    if not isinstance(verdict, dict):
        return {"name": "evidence_grounding", "value": 0.0,
                "comment": "sem verdict no output"}
    material = (input or {}).get("content", "")
    if not material:
        return {"name": "evidence_grounding", "value": 0.0,
                "comment": "material de entrada vazio"}

    evidence = verdict.get("evidence", [])
    if not evidence:
        return {"name": "evidence_grounding", "value": 0.0,
                "comment": "nenhuma evidência fornecida"}

    material_norm = re.sub(r"\s+", " ", str(material)).strip()
    all_codes = set(verdict.get("reason_codes", [])) | set(verdict.get("failed_codes", []))
    evidence_codes = set()

    for ev in evidence:
        if not isinstance(ev, dict):
            continue
        ec = ev.get("code", "")
        if not ec:
            continue
        evidence_codes.add(ec)
        eq = ev.get("quote", "")
        if not eq:
            return {"name": "evidence_grounding", "value": 0.0,
                    "comment": f"quote de '{ec}' vazio"}
        q_norm = re.sub(r"\s+", " ", str(eq)).strip()
        if q_norm not in material_norm:
            return {"name": "evidence_grounding", "value": 0.0,
                    "comment": f"quote de '{ec}' não encontrado no material"}

    # Every confirmed code must have evidence
    for code in all_codes:
        if code not in evidence_codes:
            return {"name": "evidence_grounding", "value": 0.0,
                    "comment": f"código '{code}' sem evidência correspondente"}
    # Every evidence code must be in confirmed codes
    for ec in evidence_codes:
        if ec not in all_codes:
            return {"name": "evidence_grounding", "value": 0.0,
                    "comment": f"evidência '{ec}' não referenciada em reason_codes ou failed_codes"}

    return {"name": "evidence_grounding", "value": 1.0,
            "comment": "todas as quotes verificadas no material"}


def eval_failed_code_exact_match(*, output, expected_output, **_) -> Evaluation:
    """failed_codes do pred bate exatamente com o golden?

    1.0 = match exato, 0.0 = divergência, None = erro operacional.
    """
    if not isinstance(output, dict) or "error" in output:
        return {"name": "failed_code_exact_match", "value": None,
                "comment": (output or {}).get("error", "output inválido")}
    pred = set((output.get("verdict") or {}).get("failed_codes", []))
    exp = set((expected_output or {}).get("failed_codes", []))
    match = pred == exp
    return {"name": "failed_code_exact_match", "value": 1.0 if match else 0.0,
            "comment": "failed_codes ok" if match else f"pred={pred} exp={exp}"}


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
        coverage_n = 0
        precision_values: list[float] = []
        fc_exact_match_vals = []

        for ir in items:
            output = ir.get("output") or {}
            expected = ir.get("expected_output") or {}
            is_error = isinstance(output, dict) and "error" in output

            # accuracy (error excluded)
            pred = _verdict_decision(ir)
            exp = _expected_decision(ir)
            if not is_error and pred is not None and exp is not None:
                accuracy_values.append(pred == exp)

            # fn (error não vira FN)
            if not is_error and exp == "in_scope" and pred != "in_scope":
                fn_count += 1

            # error
            if is_error:
                error_count += 1

            # coverage/precision (error excluded)
            if not is_error and isinstance(output, dict):
                pred_codes = set(output.get("verdict", {}).get("reason_codes", []))
                pred_excl = set(output.get("verdict", {}).get("exclusion_codes", []))
                pred_all = pred_codes | pred_excl
                exp_codes = set(expected.get("reason_codes", []))
                exp_excl = set(expected.get("exclusion_codes", []))
                exp_all = exp_codes | exp_excl
                if exp_all:
                    if pred_all:
                        matched = len(pred_all & exp_all)
                        coverage_sum += matched / len(exp_all)
                        precision_values.append(matched / len(pred_all))
                    else:
                        coverage_sum += 0.0  # pred vazio → coverage 0
                    coverage_n += 1

            # failed_code exact match (error excluded)
            if not is_error:
                pred_fc = set((output.get("verdict") or {}).get("failed_codes", []))
                exp_fc = set(expected.get("failed_codes", []))
                fc_exact_match_vals.append(1.0 if pred_fc == exp_fc else 0.0)

        total_items = len(items)
        if accuracy_values:
            mean_acc = sum(accuracy_values) / len(accuracy_values)
            evals.append({"name": f"{kind}_mean_accuracy", "value": round(mean_acc, 4),
                          "comment": f"{sum(accuracy_values)}/{len(accuracy_values)} correct"})
        # Always emit FN count (even zero)
        evals.append({"name": f"{kind}_fn_count", "value": fn_count,
                      "comment": f"{fn_count} FN(s) in {kind}"})
        # Always emit error count (even zero)
        evals.append({"name": f"{kind}_error_count", "value": error_count,
                      "comment": f"{error_count} error(s) in {kind}"})
        if coverage_n:
            evals.append({"name": f"{kind}_mean_coverage", "value": round(coverage_sum / coverage_n, 4),
                          "comment": f"mean reason code coverage for {kind} ({coverage_n}/{total_items} items)"})
        if precision_values:
            evals.append({
                "name": f"{kind}_mean_precision",
                "value": round(sum(precision_values) / len(precision_values), 4),
                "comment": f"mean reason code precision for {kind}",
            })
        if fc_exact_match_vals:
            fc_ok = sum(fc_exact_match_vals)
            evals.append({"name": f"{kind}_fc_exact_match", "value": round(fc_ok / len(fc_exact_match_vals), 4),
                          "comment": f"failed_code exact match {int(fc_ok)}/{len(fc_exact_match_vals)}"})

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
        is_error = isinstance(output, dict) and "error" in output

        pred = _verdict_decision(ir)
        exp = _expected_decision(ir)

        if not is_error and pred is not None and exp is not None and pred != exp:
            divergences[kind].append(case_id)

        if is_error:
            error_ids.append(case_id)

        if not is_error and exp == "in_scope" and pred != "in_scope":
            fn_ids.append(case_id)

    evals: list[Evaluation] = []
    for kind, ids in sorted(divergences.items()):
        evals.append({"name": f"divergence_{kind}", "value": len(ids),
                      "comment": f"case_ids: {', '.join(ids)}"})

    evals.append({"name": "fn_case_ids", "value": len(fn_ids),
                  "comment": f"case_ids: {', '.join(fn_ids)}" if fn_ids else "nenhum FN"})
    evals.append({"name": "error_case_ids", "value": len(error_ids),
                  "comment": f"case_ids: {', '.join(error_ids)}" if error_ids else "nenhum erro"})

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
# T06 — In-scope recall
# =============================================================================


def _is_error_output(output: dict) -> bool:
    return isinstance(output, dict) and "error" in output


def _pred_decision(output: dict) -> str | None:
    if isinstance(output, dict) and "verdict" in output:
        return output["verdict"].get("decision")
    return None


def _expected_decision_from(expected: dict) -> str | None:
    if isinstance(expected, dict):
        return expected.get("decision")
    return None


def _case_id(metadata: dict) -> str:
    return (metadata or {}).get("case_id", "?")


def _kind_from_meta(metadata: dict) -> str:
    return (metadata or {}).get("kind", "unknown")


def run_eval_in_scope_recall(item_results: list[dict]) -> list[Evaluation]:
    """Métricas de recall para in_scope.

    - total_in_scope_goldens: denominador absoluto
    - true_positives: in_scope que permaneceram in_scope
    - semantic_false_negatives: in_scope perdidos sem erro operacional
    - operational_errors_in_scope: in_scope com erro operacional
    - semantic_recall: TP / (TP + FN) — apenas casos avaliáveis
    - end_to_end_recall: TP / total — erro operacional conta como não recuperado
    """
    total_in_scope = 0
    tp = 0
    fn_semantic = 0
    op_errors_in_scope = []

    for ir in item_results:
        output = ir.get("output") or {}
        expected = ir.get("expected_output") or {}
        meta = ir.get("metadata") or {}
        exp = _expected_decision_from(expected)

        if exp != "in_scope":
            continue
        total_in_scope += 1

        if _is_error_output(output):
            op_errors_in_scope.append(_case_id(meta))
            continue

        pred = _pred_decision(output)
        if pred == "in_scope":
            tp += 1
        else:
            fn_semantic += 1

    evals: list[Evaluation] = []
    evals.append({"name": "total_in_scope_goldens", "value": total_in_scope,
                   "comment": f"{total_in_scope} goldens in_scope"})
    evals.append({"name": "in_scope_true_positives", "value": tp,
                   "comment": f"{tp} in_scope preserved"})
    evals.append({"name": "in_scope_semantic_false_negatives", "value": fn_semantic,
                   "comment": f"{fn_semantic} semantic FN(s)"})
    evals.append({"name": "in_scope_operational_errors", "value": len(op_errors_in_scope),
                   "comment": f"{len(op_errors_in_scope)} error(s) in in_scope goldens"})

    evaluable = tp + fn_semantic
    if evaluable > 0:
        sr = tp / evaluable
        evals.append({"name": "in_scope_semantic_recall", "value": round(sr, 4),
                       "comment": f"recall among evaluable: {tp}/{evaluable}"})
    else:
        evals.append({"name": "in_scope_semantic_recall", "value": None,
                       "comment": "no evaluable in_scope goldens"})

    if total_in_scope > 0:
        e2e = tp / total_in_scope
        evals.append({"name": "in_scope_end_to_end_recall", "value": round(e2e, 4),
                       "comment": f"end-to-end recall: {tp}/{total_in_scope}"})
    else:
        evals.append({"name": "in_scope_end_to_end_recall", "value": None,
                       "comment": "no in_scope goldens"})

    return evals


# =============================================================================
# T06 — Out-of-scope precision
# =============================================================================


def run_eval_out_of_scope_precision(item_results: list[dict]) -> list[Evaluation]:
    """Precisão de out_of_scope.

    - total_pred_out_of_scope: total de predições out_of_scope
    - true_positives_out_of_scope: golden out_of_scope e pred out_of_scope
    - false_positives_out_of_scope: golden != out_of_scope, pred out_of_scope
    - out_of_scope_precision: TP / (TP + FP)
    - out_of_scope_support: casos avaliáveis (sem erro) para esta métrica
    """
    tp = 0
    fp = 0
    pred_oos = 0
    support = 0
    fp_ids: list[str] = []

    for ir in item_results:
        output = ir.get("output") or {}
        expected = ir.get("expected_output") or {}
        meta = ir.get("metadata") or {}

        if _is_error_output(output):
            continue
        pred = _pred_decision(output)
        exp = _expected_decision_from(expected)
        if pred is None or exp is None:
            continue

        support += 1
        if pred == "out_of_scope":
            pred_oos += 1
            if exp == "out_of_scope":
                tp += 1
            else:
                fp += 1
                fp_ids.append(_case_id(meta))

    evals: list[Evaluation] = []
    evals.append({"name": "out_of_scope_total_predictions", "value": pred_oos,
                   "comment": f"{pred_oos} predictions out_of_scope"})
    evals.append({"name": "out_of_scope_true_positives", "value": tp,
                   "comment": f"{tp} correct out_of_scope"})
    evals.append({"name": "out_of_scope_false_positives", "value": fp,
                   "comment": f"{fp} false positives"})
    evals.append({"name": "out_of_scope_support", "value": support,
                   "comment": f"{support} evaluable cases"})

    if tp + fp > 0:
        prec = tp / (tp + fp)
        evals.append({"name": "out_of_scope_precision", "value": round(prec, 4),
                       "comment": f"precision: {tp}/{tp+fp}"})
    else:
        evals.append({"name": "out_of_scope_precision", "value": None,
                       "comment": "no predictions or evaluable cases"})

    return evals


# =============================================================================
# T06 — Needs review rate
# =============================================================================


def run_eval_needs_review_rate(item_results: list[dict]) -> list[Evaluation]:
    """Taxa de needs_review.

    - total_needs_review_predictions
    - needs_review_support: casos avaliáveis
    - needs_review_operational_errors
    - needs_review_rate: predições needs_review / suporte avaliável
    - needs_review_by_kind: distribuição por kind
    """
    nr_preds = 0
    evaluable = 0
    op_errors = 0
    by_kind: dict[str, int] = {}

    for ir in item_results:
        output = ir.get("output") or {}
        meta = ir.get("metadata") or {}
        kind = _kind_from_meta(meta)

        if _is_error_output(output):
            op_errors += 1
            continue
        pred = _pred_decision(output)
        if pred is None:
            continue
        evaluable += 1
        if pred == "needs_review":
            nr_preds += 1
            by_kind[kind] = by_kind.get(kind, 0) + 1

    evals: list[Evaluation] = []
    evals.append({"name": "needs_review_total_predictions", "value": nr_preds,
                   "comment": f"{nr_preds} needs_review predictions"})
    evals.append({"name": "needs_review_support", "value": evaluable,
                   "comment": f"{evaluable} evaluable cases"})
    evals.append({"name": "needs_review_operational_errors", "value": op_errors,
                   "comment": f"{op_errors} operational errors"})

    if evaluable > 0:
        rate = nr_preds / evaluable
        evals.append({"name": "needs_review_rate", "value": round(rate, 4),
                       "comment": f"rate: {nr_preds}/{evaluable}"})
    else:
        evals.append({"name": "needs_review_rate", "value": None,
                       "comment": "no evaluable cases"})

    has_kind_dist = False
    for kind in sorted(by_kind):
        count = by_kind[kind]
        evals.append({"name": f"needs_review_{kind}", "value": count,
                       "comment": f"{count} needs_review in {kind}"})
        has_kind_dist = True
    if not has_kind_dist:
        evals.append({"name": "needs_review_kind_distribution", "value": None,
                       "comment": "no needs_review to distribute"})

    return evals


# =============================================================================
# T06 — Decision agreement
# =============================================================================


def run_eval_decision_agreement(item_results: list[dict]) -> list[Evaluation]:
    """Concordância de decisão com contagens explícitas.

    - decision_correct: pred == expected (sem erro)
    - decision_divergent: pred != expected (sem erro)
    - decision_operational_errors: erros operacionais
    - decision_support: total de casos avaliáveis
    """
    correct = 0
    divergent = 0
    op_errors = 0
    support = 0

    for ir in item_results:
        output = ir.get("output") or {}
        expected = ir.get("expected_output") or {}

        if _is_error_output(output):
            op_errors += 1
            continue
        pred = _pred_decision(output)
        exp = _expected_decision_from(expected)
        if pred is None or exp is None:
            continue
        support += 1
        if pred == exp:
            correct += 1
        else:
            divergent += 1

    evals: list[Evaluation] = []
    evals.append({"name": "decision_correct", "value": correct,
                   "comment": f"{correct} correct decisions"})
    evals.append({"name": "decision_divergent", "value": divergent,
                   "comment": f"{divergent} divergent decisions"})
    evals.append({"name": "decision_operational_errors", "value": op_errors,
                   "comment": f"{op_errors} operational errors"})
    evals.append({"name": "decision_support", "value": support,
                   "comment": f"{support} evaluable cases"})
    if support > 0:
        acc = correct / support
        evals.append({"name": "decision_accuracy_rate", "value": round(acc, 4),
                       "comment": f"accuracy: {correct}/{support}"})
    else:
        evals.append({"name": "decision_accuracy_rate", "value": None,
                       "comment": "no evaluable cases"})

    return evals


# =============================================================================
# T06 — Metrics by kind (complementar)
# =============================================================================


def _by_kind(item_results: list[dict]) -> dict[str, list[dict]]:
    from collections import defaultdict
    by_kind: dict[str, list[dict]] = defaultdict(list)
    for ir in item_results:
        k = _kind_from_meta(ir.get("metadata") or {})
        by_kind[k].append(ir)
    return by_kind


def run_eval_metrics_by_kind_t06(item_results: list[dict]) -> list[Evaluation]:
    """Métricas por kind — recall, precisão, needs_review rate e concordância.

    Produz para cada kind com suporte avaliável:
    - in_scope_semantic_recall, in_scope_e2e_recall
    - out_of_scope_precision
    - needs_review_rate
    - decision_agreement
    - fn_count, operational_errors

    Uma média global não esconde perda de um kind.
    """
    evals: list[Evaluation] = []
    by_kind = _by_kind(item_results)

    for kind in sorted(by_kind):
        items = by_kind[kind]

        # in_scope recall
        total_is = 0
        tp_is = 0
        fn_is = 0
        op_is = []

        # out_of_scope precision
        tp_oos = 0
        fp_oos = 0
        pred_oos = 0

        # needs_review
        nr_preds = 0

        # agreement
        correct = 0
        divergent = 0
        evaluable = 0
        op_total = 0

        for ir in items:
            output = ir.get("output") or {}
            expected = ir.get("expected_output") or {}
            meta = ir.get("metadata") or {}

            exp = _expected_decision_from(expected)
            is_err = _is_error_output(output)

            # Count total in_scope goldens BEFORE error continue (for e2e recall)
            if exp == "in_scope":
                total_is += 1

            if is_err:
                op_total += 1
                if exp == "in_scope":
                    op_is.append(_case_id(meta))
                continue

            pred = _pred_decision(output)
            if pred is None or exp is None:
                continue
            evaluable += 1

            # in_scope recall (only non-error items)
            if exp == "in_scope":
                if pred == "in_scope":
                    tp_is += 1
                else:
                    fn_is += 1

            # out_of_scope precision
            if pred == "out_of_scope":
                pred_oos += 1
                if exp == "out_of_scope":
                    tp_oos += 1
                else:
                    fp_oos += 1

            # needs_review
            if pred == "needs_review":
                nr_preds += 1

            # agreement
            if pred == exp:
                correct += 1
            else:
                divergent += 1

        # Emit metrics with kind prefix
        kp = f"kind_{kind}"

        # in_scope recall for this kind
        evaluable_is = tp_is + fn_is
        if evaluable_is > 0:
            sr = tp_is / evaluable_is
            evals.append({"name": f"{kp}_in_scope_recall", "value": round(sr, 4),
                           "comment": f"recall: {tp_is}/{evaluable_is}"})
        elif total_is > 0:
            # All errors
            evals.append({"name": f"{kp}_in_scope_recall", "value": None,
                           "comment": "no evaluable in_scope cases"})
        else:
            evals.append({"name": f"{kp}_in_scope_recall", "value": None,
                           "comment": "no in_scope goldens"})

        if total_is > 0:
            e2e = tp_is / total_is
            evals.append({"name": f"{kp}_in_scope_e2e_recall", "value": round(e2e, 4),
                           "comment": f"e2e recall: {tp_is}/{total_is}"})
        else:
            evals.append({"name": f"{kp}_in_scope_e2e_recall", "value": None,
                           "comment": "no in_scope goldens"})

        evals.append({"name": f"{kp}_fn_count", "value": fn_is,
                       "comment": f"{fn_is} FN(s)"})
        evals.append({"name": f"{kp}_in_scope_operational_errors", "value": len(op_is),
                       "comment": f"{len(op_is)} error(s) in in_scope"})

        # out_of_scope precision
        if tp_oos + fp_oos > 0:
            prec = tp_oos / (tp_oos + fp_oos)
            evals.append({"name": f"{kp}_out_of_scope_precision", "value": round(prec, 4),
                           "comment": f"precision: {tp_oos}/{tp_oos+fp_oos}"})
        else:
            evals.append({"name": f"{kp}_out_of_scope_precision", "value": None,
                           "comment": "no out_of_scope predictions"})

        # needs_review rate
        if evaluable > 0:
            nr_rate = nr_preds / evaluable
            evals.append({"name": f"{kp}_needs_review_rate", "value": round(nr_rate, 4),
                           "comment": f"nr rate: {nr_preds}/{evaluable}"})
        else:
            evals.append({"name": f"{kp}_needs_review_rate", "value": None,
                           "comment": "no evaluable cases"})

        # agreement
        if evaluable > 0:
            acc = correct / evaluable
            evals.append({"name": f"{kp}_decision_agreement", "value": round(acc, 4),
                           "comment": f"agreement: {correct}/{evaluable}"})
        else:
            evals.append({"name": f"{kp}_decision_agreement", "value": None,
                           "comment": "no evaluable cases"})

        evals.append({"name": f"{kp}_operational_errors", "value": op_total,
                       "comment": f"{op_total} error(s)"})

    return evals


# =============================================================================
# T06 — Metrics by code
# =============================================================================


def _all_codes_from_expected(expected: dict, field: str) -> set[str]:
    """Get codes from a specific field of expected_output."""
    vals = expected.get(field, []) if isinstance(expected, dict) else []
    return {v.value if hasattr(v, 'value') else str(v) for v in vals}


def _all_codes_from_pred(output: dict, field: str) -> set[str]:
    """Get codes from a specific field of predicted verdict."""
    verdict = output.get("verdict") if isinstance(output, dict) else {}
    if not isinstance(verdict, dict):
        return set()
    vals = verdict.get(field, [])
    return {v.value if hasattr(v, 'value') else str(v) for v in vals}


def run_eval_metrics_by_code(item_results: list[dict]) -> list[Evaluation]:
    """Métricas por código — precision/recall por code namespace.

    Namespaces:
      conf_ — reason_codes de confirmação (R1-R5, actor codes), exclui
              códigos que também aparecem em exclusion_codes (X1-X8).
      excl_ — exclusion_codes (X1-X8) de oportunidades.
      fail_ — failed_codes de atores (falha comprovada).

    Erro operacional não descarta o caso: support_expected inclui todas
    as ocorrências esperadas mesmo com erro; operational_miss contabiliza
    códigos esperados em casos com erro.
    """
    from collections import defaultdict

    code_stats: dict[str, dict] = defaultdict(lambda: {
        "exp": 0, "eval_exp": 0, "pred": 0,
        "tp": 0, "fp": 0, "semantic_fn": 0, "operational_miss": 0,
    })

    for ir in item_results:
        output = ir.get("output") or {}
        expected = ir.get("expected_output") or {}

        exp_rc = _all_codes_from_expected(expected, "reason_codes")
        exp_ec = _all_codes_from_expected(expected, "exclusion_codes")
        exp_fc = _all_codes_from_expected(expected, "failed_codes")

        is_err = _is_error_output(output)
        pred_rc = set()
        pred_ec = set()
        pred_fc = set()
        if not is_err:
            verdict = output.get("verdict") if isinstance(output, dict) else {}
            if isinstance(verdict, dict):
                pred_rc = {v.value if hasattr(v, 'value') else str(v)
                           for v in verdict.get("reason_codes", [])}
                pred_ec = {v.value if hasattr(v, 'value') else str(v)
                           for v in verdict.get("exclusion_codes", [])}
                pred_fc = {v.value if hasattr(v, 'value') else str(v)
                           for v in verdict.get("failed_codes", [])}

        # Strip codes from conf_ that end up in excl_ to avoid duplicating X1-X8
        # (opportunity out_of_scope puts X* in both reason_codes and exclusion_codes)
        excl_from_exp = exp_ec & exp_rc
        excl_from_pred = pred_ec & pred_rc
        conf_exp_rc = exp_rc - excl_from_exp
        conf_pred_rc = pred_rc - excl_from_pred

        # ---- conf_ namespace: confirmed reason_codes (no X* duplication) ----
        for code in conf_exp_rc | conf_pred_rc:
            ns = f"conf_{code}"
            stats = code_stats[ns]
            stats["exp"] += 1
            if not is_err:
                stats["eval_exp"] += 1
                stats["pred"] += 1 if code in conf_pred_rc else 0
                if code in conf_pred_rc and code in conf_exp_rc:
                    stats["tp"] += 1
                elif code in conf_pred_rc and code not in conf_exp_rc:
                    stats["fp"] += 1
                elif code not in conf_pred_rc and code in conf_exp_rc:
                    stats["semantic_fn"] += 1
            else:
                stats["operational_miss"] += 1

        # ---- excl_ namespace: exclusion_codes ----
        for code in exp_ec | pred_ec:
            ns = f"excl_{code}"
            stats = code_stats[ns]
            stats["exp"] += 1
            if not is_err:
                stats["eval_exp"] += 1
                stats["pred"] += 1 if code in pred_ec else 0
                if code in pred_ec and code in exp_ec:
                    stats["tp"] += 1
                elif code in pred_ec and code not in exp_ec:
                    stats["fp"] += 1
                elif code not in pred_ec and code in exp_ec:
                    stats["semantic_fn"] += 1
            else:
                stats["operational_miss"] += 1

        # ---- fail_ namespace: failed_codes ----
        for code in exp_fc | pred_fc:
            ns = f"fail_{code}"
            stats = code_stats[ns]
            stats["exp"] += 1
            if not is_err:
                stats["eval_exp"] += 1
                stats["pred"] += 1 if code in pred_fc else 0
                if code in pred_fc and code in exp_fc:
                    stats["tp"] += 1
                elif code in pred_fc and code not in exp_fc:
                    stats["fp"] += 1
                elif code not in pred_fc and code in exp_fc:
                    stats["semantic_fn"] += 1
            else:
                stats["operational_miss"] += 1

    evals: list[Evaluation] = []
    for ns in sorted(code_stats):
        s = code_stats[ns]
        evals.append({"name": f"code_{ns}_support_expected", "value": s["exp"],
                       "comment": f"total expected: {s['exp']}"})
        evals.append({"name": f"code_{ns}_support_evaluable_expected",
                       "value": s["eval_exp"],
                       "comment": f"evaluable expected: {s['eval_exp']}"})
        evals.append({"name": f"code_{ns}_support_predicted", "value": s["pred"],
                       "comment": f"predicted: {s['pred']}"})
        evals.append({"name": f"code_{ns}_tp", "value": s["tp"],
                       "comment": f"TP: {s['tp']}"})
        evals.append({"name": f"code_{ns}_fp", "value": s["fp"],
                       "comment": f"FP: {s['fp']}"})
        evals.append({"name": f"code_{ns}_semantic_fn", "value": s["semantic_fn"],
                       "comment": f"FN (evaluable): {s['semantic_fn']}"})
        evals.append({"name": f"code_{ns}_operational_miss", "value": s["operational_miss"],
                       "comment": f"missed by error: {s['operational_miss']}"})

        # Precision: TP / (TP + FP)
        if s["tp"] + s["fp"] > 0:
            prec = s["tp"] / (s["tp"] + s["fp"])
            evals.append({"name": f"code_{ns}_precision", "value": round(prec, 4),
                           "comment": f"{s['tp']}/{s['tp']+s['fp']}"})
        else:
            evals.append({"name": f"code_{ns}_precision", "value": None,
                           "comment": "no predicted codes"})

        # Semantic recall: TP / (TP + semantic_fn)
        if s["tp"] + s["semantic_fn"] > 0:
            srec = s["tp"] / (s["tp"] + s["semantic_fn"])
            evals.append({"name": f"code_{ns}_semantic_recall",
                           "value": round(srec, 4),
                           "comment": f"semantic: {s['tp']}/{s['tp']+s['semantic_fn']}"})
        else:
            evals.append({"name": f"code_{ns}_semantic_recall", "value": None,
                           "comment": "no evaluable expected codes"})

        # End-to-end recall: TP / support_expected
        if s["exp"] > 0:
            e2e = s["tp"] / s["exp"]
            evals.append({"name": f"code_{ns}_end_to_end_recall",
                           "value": round(e2e, 4),
                           "comment": f"e2e: {s['tp']}/{s['exp']}"})
        else:
            evals.append({"name": f"code_{ns}_end_to_end_recall", "value": None,
                           "comment": "no expected codes"})

    return evals


# =============================================================================
# T06 — Audit IDs
# =============================================================================


def run_eval_audit_ids(item_results: list[dict]) -> list[Evaluation]:
    """Lista de IDs auditáveis — divergências, FN, FP, erros e código.

    Inclui case_ids de:
    - divergências de decisão
    - falsos negativos
    - false positives de out_of_scope
    - erros operacionais
    - divergências de reason/failed codes
    """
    div_ids: list[str] = []
    fn_ids: list[str] = []
    fp_oos_ids: list[str] = []
    error_ids: list[str] = []
    code_div_ids: list[str] = []
    code_op_miss_ids: list[str] = []

    for ir in item_results:
        output = ir.get("output") or {}
        expected = ir.get("expected_output") or {}
        meta = ir.get("metadata") or {}
        cid = _case_id(meta)

        is_err = _is_error_output(output)

        # Track operational misses: any expected code in an error case
        if is_err:
            error_ids.append(cid)
            exp_any = _all_codes_from_expected(expected, "reason_codes")
            exp_any |= _all_codes_from_expected(expected, "exclusion_codes")
            exp_any |= _all_codes_from_expected(expected, "failed_codes")
            if exp_any:
                code_op_miss_ids.append(cid)
            continue

        pred = _pred_decision(output)
        exp = _expected_decision_from(expected)
        if pred is None or exp is None:
            continue

        # Decision divergences
        if pred != exp:
            div_ids.append(cid)

        # False negatives
        if exp == "in_scope" and pred != "in_scope":
            fn_ids.append(cid)

        # FP out_of_scope
        if pred == "out_of_scope" and exp != "out_of_scope":
            fp_oos_ids.append(cid)

        # Code divergences (only evaluable cases)
        pred_rc = _all_codes_from_pred(output, "reason_codes")
        exp_rc = _all_codes_from_expected(expected, "reason_codes")
        pred_ec = _all_codes_from_pred(output, "exclusion_codes")
        exp_ec = _all_codes_from_expected(expected, "exclusion_codes")
        pred_fc = _all_codes_from_pred(output, "failed_codes")
        exp_fc = _all_codes_from_expected(expected, "failed_codes")

        if pred_rc != exp_rc or pred_ec != exp_ec or pred_fc != exp_fc:
            code_div_ids.append(cid)

    evals: list[Evaluation] = []
    evals.append({"name": "audit_divergences", "value": len(div_ids),
                   "comment": f"case_ids: {', '.join(div_ids)}" if div_ids else "nenhuma divergência"})
    evals.append({"name": "audit_false_negatives", "value": len(fn_ids),
                   "comment": f"case_ids: {', '.join(fn_ids)}" if fn_ids else "nenhum FN"})
    evals.append({"name": "audit_false_positives_oos", "value": len(fp_oos_ids),
                   "comment": f"case_ids: {', '.join(fp_oos_ids)}" if fp_oos_ids else "nenhum FP out_of_scope"})
    evals.append({"name": "audit_operational_errors", "value": len(error_ids),
                   "comment": f"case_ids: {', '.join(error_ids)}" if error_ids else "nenhum erro"})
    evals.append({"name": "audit_code_divergences", "value": len(code_div_ids),
                   "comment": f"case_ids: {', '.join(code_div_ids)}" if code_div_ids else "nenhuma divergência de código"})
    evals.append({"name": "audit_code_operational_misses", "value": len(code_op_miss_ids),
                   "comment": f"case_ids: {', '.join(code_op_miss_ids)}" if code_op_miss_ids else "nenhum operational miss"})

    return evals


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
        eval_failed_code_exact_match,
        eval_operational_error,
    ],
    run_evaluators=[
        run_eval_metrics_by_kind,
        run_eval_divergences,
        run_eval_in_scope_recall,
        run_eval_out_of_scope_precision,
        run_eval_needs_review_rate,
        run_eval_decision_agreement,
        run_eval_metrics_by_kind_t06,
        run_eval_metrics_by_code,
        run_eval_audit_ids,
    ],
    prereqs=_prereqs,
    classification="diagnostic",
    version="2",
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
