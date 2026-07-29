"""Metricas diagnosticas puras da fila RT05.

Este modulo nao le banco, nao cria thresholds e nao decide prioridade.
Ele resume excecoes e revisoes ja carregadas pelo chamador para apoiar
reconciliacao local e sinais da spec 06.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class DiagnosticExceptionRef:
    exception_id: str
    subject_id: str


@dataclass(frozen=True)
class DataQualitySpec06Signals:
    temporal_missing_or_conflicting: tuple[DiagnosticExceptionRef, ...] = field(
        default_factory=tuple
    )
    document_incomplete: tuple[DiagnosticExceptionRef, ...] = field(
        default_factory=tuple
    )
    layout_or_ocr_candidates: tuple[DiagnosticExceptionRef, ...] = field(
        default_factory=tuple
    )
    insufficient_for_any_decision: tuple[DiagnosticExceptionRef, ...] = field(
        default_factory=tuple
    )


@dataclass(frozen=True)
class DataQualityDiagnostics:
    exceptions_by_status: dict[str, int]
    exceptions_by_issue_code: dict[str, int]
    exceptions_by_source: dict[str, int]
    exceptions_by_field_path: dict[str, int]
    open_exception_age_days: dict[str, int]
    mean_open_exception_age_days: float | None
    review_latency_days: dict[str, float] | None
    mean_review_latency_days: float | None
    reopened_exceptions: int
    review_decisions: dict[str, int] | None
    cases_prevented_from_active: tuple[DiagnosticExceptionRef, ...]
    spec06_signals: DataQualitySpec06Signals


_OPEN_STATUS_VALUES = frozenset({"aberta", "aberto", "ativa", "active", "open"})
_TEMPORAL_SIGNAL_CODES = frozenset({
    "temporal_status_without_basis",
    "temporal_status_conflict",
    "critical_fact_missing",
})
_INCOMPLETE_DOCUMENT_CODES = frozenset({
    "critical_fact_missing",
    "evidence_unresolved",
    "validation_failed",
})
_LAYOUT_OR_OCR_CODES = frozenset({
    "evidence_unresolved",
    "validation_failed",
})


def compute_data_quality_diagnostics(
    exceptions: Iterable[dict],
    *,
    reviews: Iterable[dict] | None = None,
    as_of: date,
) -> DataQualityDiagnostics:
    exception_rows = [row for row in exceptions if isinstance(row, dict)]
    review_rows = None if reviews is None else [
        row for row in reviews if isinstance(row, dict)
    ]
    reviews_by_exception = (
        _latest_reviews_by_exception(review_rows) if review_rows is not None else {}
    )

    open_ages = _open_exception_age_days(exception_rows, as_of)
    latencies = (
        None
        if review_rows is None
        else _review_latency_days(exception_rows, review_rows)
    )
    decisions = (
        None
        if review_rows is None
        else _sorted_counts(_count_values(
            _normalize_key(row.get("decision")) for row in review_rows
        ))
    )

    return DataQualityDiagnostics(
        exceptions_by_status=_sorted_counts(_count_values(
            _normalize_key(row.get("status")) for row in exception_rows
        )),
        exceptions_by_issue_code=_sorted_counts(_count_values(
            _normalize_key(row.get("issue_code")) for row in exception_rows
        )),
        exceptions_by_source=_count_sources(exception_rows),
        exceptions_by_field_path=_sorted_counts(_count_values(
            _normalize_key(row.get("field_path")) for row in exception_rows
        )),
        open_exception_age_days=open_ages,
        mean_open_exception_age_days=_mean_or_none(open_ages.values()),
        review_latency_days=latencies,
        mean_review_latency_days=None if latencies is None else _mean_or_none(
            latencies.values()
        ),
        reopened_exceptions=_reopened_exception_count(exception_rows),
        review_decisions=decisions,
        cases_prevented_from_active=_prevented_from_active(
            exception_rows,
            reviews_by_exception=reviews_by_exception,
            as_of=as_of,
        ),
        spec06_signals=_spec06_signals(exception_rows),
    )


def _normalize_key(value: object, *, empty: str = "unknown") -> str:
    if not isinstance(value, str):
        return empty
    normalized = value.strip().lower()
    return normalized or empty


def _count_values(values: Iterable[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return counts


def _sorted_counts(counts: dict[str, int]) -> dict[str, int]:
    return dict(sorted(counts.items()))


def _parse_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _parse_iso_date(value: object) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _exception_ref(row: dict) -> DiagnosticExceptionRef | None:
    exception_id = row.get("id")
    subject_id = row.get("subject_id")
    if not isinstance(exception_id, str) or not isinstance(subject_id, str):
        return None
    if not exception_id.strip() or not subject_id.strip():
        return None
    return DiagnosticExceptionRef(exception_id=exception_id, subject_id=subject_id)


def _evidence_refs(row: dict) -> list[dict]:
    refs = row.get("evidence_refs") or []
    return [ref for ref in refs if isinstance(ref, dict)]


def _sources_for_exception(row: dict) -> set[str]:
    refs = _evidence_refs(row)
    sources = {
        _normalize_key(ref.get("source"))
        for ref in refs
        if _normalize_key(ref.get("source")) != "unknown"
    }
    return sources or {"unknown"}


def _count_sources(rows: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        for source in sorted(_sources_for_exception(row)):
            counts[source] = counts.get(source, 0) + 1
    return dict(sorted(counts.items()))


def _open_exception_age_days(rows: list[dict], as_of: date) -> dict[str, int]:
    ages: dict[str, int] = {}
    for row in rows:
        if _normalize_key(row.get("status")) != "open":
            continue
        exception_id = row.get("id")
        detected_at = _parse_datetime(row.get("detected_at"))
        if not isinstance(exception_id, str) or detected_at is None:
            continue
        detected_local_date = detected_at.astimezone(
            ZoneInfo("America/Sao_Paulo")
        ).date()
        age = max(0, (as_of - detected_local_date).days)
        ages[exception_id] = age
    return dict(sorted(ages.items()))


def _mean_or_none(values: Iterable[float | int]) -> float | None:
    items = list(values)
    if not items:
        return None
    return sum(items) / len(items)


def _review_latency_days(
    exception_rows: list[dict], review_rows: list[dict],
) -> dict[str, float]:
    detected_by_exception = {
        row.get("id"): _parse_datetime(row.get("detected_at"))
        for row in exception_rows
        if isinstance(row.get("id"), str)
    }
    latest_reviews = _latest_reviews_by_exception(review_rows)
    latencies: dict[str, float] = {}
    for exception_id, review_row in latest_reviews.items():
        detected_at = detected_by_exception.get(exception_id)
        reviewed_at = _parse_datetime(review_row.get("reviewed_at"))
        if detected_at is None or reviewed_at is None:
            continue
        delta = max(0.0, (reviewed_at - detected_at).total_seconds() / 86400.0)
        latencies[exception_id] = delta
    return dict(sorted(latencies.items()))


def _latest_reviews_by_exception(review_rows: list[dict]) -> dict[str, dict]:
    latest: dict[str, dict] = {}
    for row in review_rows:
        exception_id = row.get("exception_id")
        if not isinstance(exception_id, str) or not exception_id.strip():
            continue
        current = latest.get(exception_id)
        if current is None:
            latest[exception_id] = row
            continue
        current_ts = _parse_datetime(current.get("reviewed_at"))
        row_ts = _parse_datetime(row.get("reviewed_at"))
        if current_ts is None:
            latest[exception_id] = row
        elif row_ts is not None and row_ts >= current_ts:
            latest[exception_id] = row
    return latest


def _group_key(row: dict) -> tuple[str, str, str, str]:
    return (
        _normalize_key(row.get("subject_kind")),
        _normalize_key(row.get("subject_id")),
        _normalize_key(row.get("field_path")),
        _normalize_key(row.get("issue_code")),
    )


def _sort_key(row: dict) -> tuple[str, str]:
    detected = _parse_datetime(row.get("detected_at"))
    observed = _parse_datetime(row.get("last_observed_at"))
    return (
        detected.isoformat() if detected is not None else "",
        observed.isoformat() if observed is not None else "",
    )


def _reopened_exception_count(rows: list[dict]) -> int:
    grouped: dict[tuple[str, str, str, str], list[dict]] = {}
    for row in rows:
        grouped.setdefault(_group_key(row), []).append(row)

    count = 0
    for group_rows in grouped.values():
        resolved_fingerprints: set[str] = set()
        for row in sorted(group_rows, key=_sort_key):
            fingerprint = row.get("input_fingerprint")
            if not isinstance(fingerprint, str) or not fingerprint.strip():
                continue
            status = _normalize_key(row.get("status"))
            if fingerprint not in resolved_fingerprints and resolved_fingerprints:
                count += 1
            if status == "resolved":
                resolved_fingerprints.add(fingerprint)
    return count


def _legacy_would_have_looked_active(row: dict, as_of: date) -> bool:
    if _normalize_key(row.get("field_path")) != "deadline":
        return False
    produced_value = row.get("produced_value")
    parsed_date = _parse_iso_date(produced_value)
    if parsed_date is not None:
        return parsed_date >= as_of
    return _normalize_key(produced_value) in _OPEN_STATUS_VALUES


def _final_state_is_active(
    row: dict, *, review_row: dict | None, as_of: date,
) -> bool:
    if _normalize_key(row.get("status")) != "resolved" or review_row is None:
        return False

    decision = _normalize_key(review_row.get("decision"))
    if decision == "confirm_continuous":
        return True
    if decision == "mark_unknown":
        return False

    value = (
        review_row.get("corrected_value")
        if decision == "correct"
        else row.get("produced_value")
    )
    parsed_date = _parse_iso_date(value)
    if parsed_date is not None:
        return parsed_date >= as_of
    return False


def _prevented_from_active(
    rows: list[dict], *, reviews_by_exception: dict[str, dict], as_of: date,
) -> tuple[DiagnosticExceptionRef, ...]:
    prevented: list[tuple[tuple[str, str], DiagnosticExceptionRef]] = []
    for row in rows:
        ref = _exception_ref(row)
        if ref is None or not _legacy_would_have_looked_active(row, as_of):
            continue
        review_row = reviews_by_exception.get(ref.exception_id)
        if _final_state_is_active(row, review_row=review_row, as_of=as_of):
            continue
        prevented.append((_sort_key(row), ref))
    return tuple(
        ref for _, ref in sorted(
            prevented,
            key=lambda item: (item[1].subject_id, item[0][0], item[0][1], item[1].exception_id),
        )
    )


def _has_incomplete_document_signal(row: dict) -> bool:
    refs = _evidence_refs(row)
    if not refs:
        return True
    return any(
        _normalize_key(ref.get("locator_quality")) == "unresolved"
        for ref in refs
    )


def _has_layout_or_ocr_signal(row: dict) -> bool:
    refs = _evidence_refs(row)
    if not refs:
        return False
    has_document = any(
        isinstance(ref.get("document"), str) and ref.get("document", "").strip()
        for ref in refs
    )
    has_quote = any(
        isinstance(ref.get("quote"), str) and ref.get("quote", "").strip()
        for ref in refs
    )
    locator_qualities = {
        _normalize_key(ref.get("locator_quality")) for ref in refs
    }
    return has_document and (
        "document_only" in locator_qualities
        or "unresolved" in locator_qualities
        or not has_quote
    )


def _insufficient_for_any_decision(row: dict) -> bool:
    refs = _evidence_refs(row)
    produced_value = _normalize_key(row.get("produced_value"))
    return not refs and produced_value in {"unknown", ""}


def _sorted_signal_refs(
    rows: list[dict], predicate,
) -> tuple[DiagnosticExceptionRef, ...]:
    refs = [ref for row in rows if predicate(row) and (ref := _exception_ref(row))]
    return tuple(sorted(refs, key=lambda item: (item.subject_id, item.exception_id)))


def _spec06_signals(rows: list[dict]) -> DataQualitySpec06Signals:
    return DataQualitySpec06Signals(
        temporal_missing_or_conflicting=_sorted_signal_refs(
            rows,
            lambda row: _normalize_key(row.get("field_path")) in {"deadline", "status"}
            and _normalize_key(row.get("issue_code")) in _TEMPORAL_SIGNAL_CODES,
        ),
        document_incomplete=_sorted_signal_refs(
            rows,
            lambda row: _normalize_key(row.get("issue_code")) in _INCOMPLETE_DOCUMENT_CODES
            and _has_incomplete_document_signal(row),
        ),
        layout_or_ocr_candidates=_sorted_signal_refs(
            rows,
            lambda row: _normalize_key(row.get("issue_code")) in _LAYOUT_OR_OCR_CODES
            and _has_layout_or_ocr_signal(row),
        ),
        insufficient_for_any_decision=_sorted_signal_refs(
            rows, _insufficient_for_any_decision
        ),
    )


__all__ = [
    "DataQualityDiagnostics",
    "DataQualitySpec06Signals",
    "DiagnosticExceptionRef",
    "compute_data_quality_diagnostics",
]
