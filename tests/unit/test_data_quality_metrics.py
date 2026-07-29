"""Testes proporcionais das metricas diagnosticas RT05-T09."""
from __future__ import annotations

from datetime import date

from radar.core.services.data_quality_metrics import (
    DiagnosticExceptionRef,
    compute_data_quality_diagnostics,
)
from radar.domain.provenance import EvidenceRef, LocatorQuality
from tests.fixtures.data_quality.finep_eureka import finep_eureka_2024


def _evidence(
    *,
    source: str = "finep",
    document: str = "edital.html",
    quote: str | None = "Trecho versionado",
    locator_quality: str = "document_only",
) -> dict:
    payload = EvidenceRef(
        source=source,
        document=document,
        quote=quote,
        canonical_content_hash="sha256:" + "a" * 64,
        locator_quality=LocatorQuality(locator_quality),
    ).model_dump(mode="json")
    payload.pop("source_url", None)
    return payload


def _exception_row(
    *,
    exception_id: str,
    subject_id: str,
    issue_code: str,
    produced_value: str | None,
    status: str,
    detected_at: str,
    field_path: str = "deadline",
    input_fingerprint: str = "sha256:fp",
    source: str = "finep",
    evidence_refs: list[dict] | None = None,
) -> dict:
    refs = evidence_refs if evidence_refs is not None else [_evidence(source=source)]
    return {
        "id": exception_id,
        "subject_kind": "opportunity",
        "subject_id": subject_id,
        "field_path": field_path,
        "issue_code": issue_code,
        "produced_value": produced_value,
        "evidence_refs": refs,
        "input_fingerprint": input_fingerprint,
        "status": status,
        "detected_at": detected_at,
        "last_observed_at": detected_at,
    }


def _review_row(
    *,
    exception_id: str,
    review_id: str,
    decision: str,
    reviewed_at: str,
    corrected_value: str | None = None,
) -> dict:
    return {
        "review_id": review_id,
        "exception_id": exception_id,
        "decision": decision,
        "corrected_value": corrected_value,
        "reviewed_at": reviewed_at,
    }


def test_fixture_baseline_measures_status_source_field_and_open_age():
    eureka = finep_eureka_2024()
    diagnostics = compute_data_quality_diagnostics(
        [
            _exception_row(
                exception_id="exc-eureka",
                subject_id="finep:eureka",
                issue_code="temporal_status_without_basis",
                produced_value=eureka["status"],
                status="open",
                detected_at="2026-07-15T10:00:00+00:00",
            ),
            _exception_row(
                exception_id="exc-fapesc",
                subject_id="fapesc:37-2026",
                issue_code="temporal_status_conflict",
                produced_value="2026-08-30",
                status="resolved",
                detected_at="2026-07-20T10:00:00+00:00",
                source="fapesc",
            ),
            _exception_row(
                exception_id="exc-web",
                subject_id="web:desafio",
                issue_code="evidence_unresolved",
                produced_value=None,
                status="superseded",
                detected_at="2026-07-10T10:00:00+00:00",
                field_path="status",
                source="web",
                evidence_refs=[],
            ),
        ],
        as_of=date(2026, 7, 29),
    )

    assert diagnostics.exceptions_by_status == {
        "open": 1,
        "resolved": 1,
        "superseded": 1,
    }
    assert diagnostics.exceptions_by_issue_code == {
        "evidence_unresolved": 1,
        "temporal_status_conflict": 1,
        "temporal_status_without_basis": 1,
    }
    assert diagnostics.exceptions_by_source == {
        "fapesc": 1,
        "finep": 1,
        "unknown": 1,
    }
    assert diagnostics.exceptions_by_field_path == {"deadline": 2, "status": 1}
    assert diagnostics.open_exception_age_days == {"exc-eureka": 14}
    assert diagnostics.mean_open_exception_age_days == 14.0
    assert diagnostics.review_latency_days is None
    assert diagnostics.review_decisions is None


def test_review_denominator_is_null_only_when_reviews_are_not_observed():
    exception = _exception_row(
        exception_id="exc-1",
        subject_id="finep:eureka",
        issue_code="temporal_status_without_basis",
        produced_value="ABERTA",
        status="resolved",
        detected_at="2026-07-20T10:00:00+00:00",
    )

    not_observed = compute_data_quality_diagnostics(
        [exception],
        reviews=None,
        as_of=date(2026, 7, 29),
    )
    observed_empty = compute_data_quality_diagnostics(
        [exception],
        reviews=[],
        as_of=date(2026, 7, 29),
    )

    assert not_observed.review_latency_days is None
    assert not_observed.mean_review_latency_days is None
    assert not_observed.review_decisions is None
    assert observed_empty.review_latency_days == {}
    assert observed_empty.mean_review_latency_days is None
    assert observed_empty.review_decisions == {}


def test_reopens_review_latency_and_prevented_active_cases_are_explicit():
    exceptions = [
        _exception_row(
            exception_id="exc-eureka",
            subject_id="finep:eureka",
            issue_code="temporal_status_without_basis",
            produced_value="ABERTA",
            status="open",
            detected_at="2026-07-15T10:00:00+00:00",
            input_fingerprint="sha256:fp-eureka-1",
        ),
        _exception_row(
            exception_id="exc-resolved-v1",
            subject_id="fapesc:37-2026",
            issue_code="temporal_status_conflict",
            produced_value="2026-08-30",
            status="resolved",
            detected_at="2026-07-16T10:00:00+00:00",
            input_fingerprint="sha256:fp-v1",
            source="fapesc",
        ),
        _exception_row(
            exception_id="exc-open-v2",
            subject_id="fapesc:37-2026",
            issue_code="temporal_status_conflict",
            produced_value="ABERTA",
            status="open",
            detected_at="2026-07-25T10:00:00+00:00",
            input_fingerprint="sha256:fp-v2",
            source="fapesc",
        ),
        _exception_row(
            exception_id="exc-continuous",
            subject_id="finep:continuous",
            issue_code="temporal_status_without_basis",
            produced_value="ABERTA",
            status="resolved",
            detected_at="2026-07-18T10:00:00+00:00",
            input_fingerprint="sha256:fp-cont",
        ),
    ]
    reviews = [
        _review_row(
            exception_id="exc-resolved-v1",
            review_id="review-1",
            decision="mark_unknown",
            reviewed_at="2026-07-20T10:00:00+00:00",
        ),
        _review_row(
            exception_id="exc-continuous",
            review_id="review-2",
            decision="confirm_continuous",
            reviewed_at="2026-07-19T10:00:00+00:00",
        ),
    ]

    diagnostics = compute_data_quality_diagnostics(
        exceptions,
        reviews=reviews,
        as_of=date(2026, 7, 29),
    )

    assert diagnostics.reopened_exceptions == 1
    assert diagnostics.review_latency_days == {
        "exc-continuous": 1.0,
        "exc-resolved-v1": 4.0,
    }
    assert diagnostics.mean_review_latency_days == 2.5
    assert diagnostics.review_decisions == {
        "confirm_continuous": 1,
        "mark_unknown": 1,
    }
    assert diagnostics.cases_prevented_from_active == (
        DiagnosticExceptionRef(
            exception_id="exc-resolved-v1", subject_id="fapesc:37-2026"
        ),
        DiagnosticExceptionRef(
            exception_id="exc-open-v2", subject_id="fapesc:37-2026"
        ),
        DiagnosticExceptionRef(
            exception_id="exc-eureka", subject_id="finep:eureka"
        ),
    )


def test_spec06_signals_bucket_temporal_incomplete_layout_and_insufficient():
    diagnostics = compute_data_quality_diagnostics(
        [
            _exception_row(
                exception_id="exc-eureka",
                subject_id="finep:eureka",
                issue_code="temporal_status_without_basis",
                produced_value="ABERTA",
                status="open",
                detected_at="2026-07-15T10:00:00+00:00",
            ),
            _exception_row(
                exception_id="exc-layout",
                subject_id="web:layout",
                issue_code="evidence_unresolved",
                produced_value=None,
                status="open",
                detected_at="2026-07-15T10:00:00+00:00",
                field_path="status",
                evidence_refs=[_evidence(source="web", quote=None)],
            ),
            _exception_row(
                exception_id="exc-insufficient",
                subject_id="finep:missing",
                issue_code="critical_fact_missing",
                produced_value="unknown",
                status="open",
                detected_at="2026-07-15T10:00:00+00:00",
                evidence_refs=[],
            ),
        ],
        as_of=date(2026, 7, 29),
    )

    assert diagnostics.spec06_signals.temporal_missing_or_conflicting == (
        DiagnosticExceptionRef("exc-eureka", "finep:eureka"),
        DiagnosticExceptionRef("exc-insufficient", "finep:missing"),
    )
    assert diagnostics.spec06_signals.document_incomplete == (
        DiagnosticExceptionRef("exc-insufficient", "finep:missing"),
    )
    assert diagnostics.spec06_signals.layout_or_ocr_candidates == (
        DiagnosticExceptionRef("exc-layout", "web:layout"),
    )
    assert diagnostics.spec06_signals.insufficient_for_any_decision == (
        DiagnosticExceptionRef("exc-insufficient", "finep:missing"),
    )
