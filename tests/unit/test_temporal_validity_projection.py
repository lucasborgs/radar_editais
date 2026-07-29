from __future__ import annotations

from datetime import date, datetime

import pytest

from radar.core.services import data_quality_reviews as reviews
from radar.core.services.data_quality_exceptions import DataQualityStorageError
from radar.domain.data_quality import DataQualityReview, TemporalMode, ValidityState
from radar.domain.provenance import (
    EvidenceRef,
    LocatorQuality,
    ReviewInfo,
)

pytestmark = pytest.mark.unit

AS_OF = date(2026, 7, 29)
FINGERPRINT = "sha256:current"


def evidence() -> EvidenceRef:
    return EvidenceRef(
        source="finep",
        silver_source_hash="md5:" + "a" * 32,
        document="finep-602.json",
        locator_quality=LocatorQuality.DOCUMENT_ONLY,
    )


def exception_row(
    *,
    status: str = "resolved",
    fingerprint: str = FINGERPRINT,
    produced_value: str = "ABERTA",
    refs: list[EvidenceRef] | None = None,
) -> dict:
    return {
        "id": "exc-1",
        "subject_kind": "opportunity",
        "subject_id": "finep:602",
        "field_path": "deadline",
        "issue_code": "temporal_status_without_basis",
        "produced_state": "inferred",
        "produced_value": produced_value,
        "evidence_refs": [
            ref.model_dump(mode="json") for ref in (refs or [])
        ],
        "input_fingerprint": fingerprint,
        "status": status,
    }


def review(
    decision,
    *,
    corrected_value=None,
    refs: list[EvidenceRef] | None = None,
) -> DataQualityReview:
    return DataQualityReview(
        exception_ref="exc-1",
        decision=decision,
        corrected_value=corrected_value,
        justification="Revisão temporal.",
        evidence_refs=refs or [],
        review=ReviewInfo(
            review_id="review-1",
            actor_id="admin-1",
            reviewed_at=datetime.fromisoformat(
                "2026-07-29T10:00:00-03:00"
            ),
            overridden=decision == "correct",
        ),
    )


def install_reads(monkeypatch, row, current_review):
    monkeypatch.setattr(reviews, "get_exception", lambda _: row)
    monkeypatch.setattr(
        reviews,
        "get_current_review_projection",
        lambda _: current_review,
    )


class TestDeterministicProjection:
    def test_coherent_future_deadline_is_active(self):
        projection = reviews.project_temporal_validity(
            deadline=date(2026, 12, 31),
            status="aberta",
            input_fingerprint=FINGERPRINT,
            as_of=AS_OF,
        )

        assert projection.temporal_mode is TemporalMode.FIXED
        assert projection.validity_state is ValidityState.ACTIVE
        assert projection.value == "2026-12-31"
        assert projection.exception_id is None
        assert projection.review_id is None

    def test_coherent_past_deadline_is_closed(self):
        projection = reviews.project_temporal_validity(
            deadline=date(2026, 1, 1),
            status="encerrada",
            input_fingerprint=FINGERPRINT,
            as_of=AS_OF,
        )

        assert projection.temporal_mode is TemporalMode.FIXED
        assert projection.validity_state is ValidityState.CLOSED

    def test_finep_eureka_without_exception_is_needs_review(self):
        projection = reviews.project_temporal_validity(
            deadline=None,
            status="ABERTA",
            input_fingerprint=FINGERPRINT,
            as_of=AS_OF,
        )

        assert projection.temporal_mode is TemporalMode.UNKNOWN
        assert projection.validity_state is ValidityState.NEEDS_REVIEW


class TestExceptionProjection:
    def test_open_exception_is_needs_review(self, monkeypatch):
        ref = evidence()
        install_reads(
            monkeypatch,
            exception_row(status="open", refs=[ref]),
            review("confirm_continuous", refs=[ref]),
        )

        projection = reviews.project_temporal_validity(
            deadline=None,
            status="ABERTA",
            input_fingerprint=FINGERPRINT,
            exception_id="exc-1",
            as_of=AS_OF,
        )

        assert projection.validity_state is ValidityState.NEEDS_REVIEW
        assert projection.review_id is None

    def test_correct_applies_only_to_projection(self, monkeypatch):
        ref = evidence()
        row = exception_row(
            produced_value="2026-12-31",
            refs=[ref],
        )
        entity = {"deadline": "2026-12-31", "status": "aberta"}
        original = dict(entity)
        install_reads(
            monkeypatch,
            row,
            review(
                "correct",
                corrected_value="2026-08-31",
                refs=[ref],
            ),
        )

        projection = reviews.project_temporal_validity(
            deadline=date(2026, 12, 31),
            status="aberta",
            input_fingerprint=FINGERPRINT,
            exception_id="exc-1",
            as_of=AS_OF,
        )

        assert projection.value == "2026-08-31"
        assert projection.validity_state is ValidityState.ACTIVE
        assert entity == original

    def test_confirm_continuous_is_active(self, monkeypatch):
        ref = evidence()
        install_reads(
            monkeypatch,
            exception_row(refs=[ref]),
            review("confirm_continuous", refs=[ref]),
        )

        projection = reviews.project_temporal_validity(
            deadline=None,
            status="ABERTA",
            input_fingerprint=FINGERPRINT,
            exception_id="exc-1",
            as_of=AS_OF,
        )

        assert projection.temporal_mode is TemporalMode.CONTINUOUS
        assert projection.validity_state is ValidityState.ACTIVE
        assert projection.value is None

    def test_mark_unknown_never_becomes_active(self, monkeypatch):
        install_reads(
            monkeypatch,
            exception_row(),
            review("mark_unknown"),
        )

        projection = reviews.project_temporal_validity(
            deadline=None,
            status="ABERTA",
            input_fingerprint=FINGERPRINT,
            exception_id="exc-1",
            as_of=AS_OF,
        )

        assert projection.temporal_mode is TemporalMode.UNKNOWN
        assert projection.validity_state is ValidityState.NEEDS_REVIEW

    def test_new_fingerprint_does_not_inherit_review(self, monkeypatch):
        ref = evidence()
        install_reads(
            monkeypatch,
            exception_row(fingerprint="sha256:old", refs=[ref]),
            review("confirm_continuous", refs=[ref]),
        )

        projection = reviews.project_temporal_validity(
            deadline=None,
            status="ABERTA",
            input_fingerprint="sha256:new",
            exception_id="exc-1",
            as_of=AS_OF,
        )

        assert projection.validity_state is ValidityState.NEEDS_REVIEW
        assert projection.review_id is None
        assert projection.input_fingerprint == "sha256:new"

    def test_missing_exception_is_conservative(self, monkeypatch):
        monkeypatch.setattr(reviews, "get_exception", lambda _: None)

        projection = reviews.project_temporal_validity(
            deadline=date(2026, 12, 31),
            status="aberta",
            input_fingerprint=FINGERPRINT,
            exception_id="missing",
            as_of=AS_OF,
        )

        assert projection.validity_state is ValidityState.NEEDS_REVIEW

    def test_missing_review_is_conservative(self, monkeypatch):
        install_reads(monkeypatch, exception_row(), None)

        projection = reviews.project_temporal_validity(
            deadline=None,
            status="ABERTA",
            input_fingerprint=FINGERPRINT,
            exception_id="exc-1",
            as_of=AS_OF,
        )

        assert projection.validity_state is ValidityState.NEEDS_REVIEW

    def test_review_for_another_exception_is_conservative(self, monkeypatch):
        ref = evidence()
        wrong_review = review("confirm_continuous", refs=[ref]).model_copy(
            update={"exception_ref": "exc-other"}
        )
        install_reads(
            monkeypatch,
            exception_row(refs=[ref]),
            wrong_review,
        )

        projection = reviews.project_temporal_validity(
            deadline=None,
            status="ABERTA",
            input_fingerprint=FINGERPRINT,
            exception_id="exc-1",
            as_of=AS_OF,
        )

        assert projection.validity_state is ValidityState.NEEDS_REVIEW
        assert projection.review_id is None

    def test_read_failure_never_grants_active(
        self,
        monkeypatch,
        caplog,
    ):
        def _fail(_):
            raise DataQualityStorageError("secret raw payload")

        monkeypatch.setattr(reviews, "get_exception", _fail)

        projection = reviews.project_temporal_validity(
            deadline=date(2026, 12, 31),
            status="aberta",
            input_fingerprint=FINGERPRINT,
            exception_id="exc-1",
            as_of=AS_OF,
        )

        assert projection.validity_state is ValidityState.NEEDS_REVIEW
        assert "secret raw payload" not in caplog.text
        assert "DataQualityStorageError" in caplog.text

    def test_unexpected_read_failure_never_grants_active(
        self,
        monkeypatch,
        caplog,
    ):
        def _fail(_):
            raise RuntimeError("secret unexpected payload")

        monkeypatch.setattr(reviews, "get_exception", _fail)

        projection = reviews.project_temporal_validity(
            deadline=date(2026, 12, 31),
            status="aberta",
            input_fingerprint=FINGERPRINT,
            exception_id="exc-1",
            as_of=AS_OF,
        )

        assert projection.validity_state is ValidityState.NEEDS_REVIEW
        assert "secret unexpected payload" not in caplog.text
        assert "RuntimeError" in caplog.text

    def test_confirmed_date_uses_as_of(self, monkeypatch):
        ref = evidence()
        install_reads(
            monkeypatch,
            exception_row(
                produced_value="2026-07-28",
                refs=[ref],
            ),
            review("confirm", refs=[ref]),
        )

        projection = reviews.project_temporal_validity(
            deadline=date(2026, 7, 28),
            status="aberta",
            input_fingerprint=FINGERPRINT,
            exception_id="exc-1",
            as_of=AS_OF,
        )

        assert projection.temporal_mode is TemporalMode.FIXED
        assert projection.validity_state is ValidityState.CLOSED

    def test_default_as_of_is_sao_paulo(self, monkeypatch):
        monkeypatch.setattr(
            reviews,
            "_today_sao_paulo",
            lambda: AS_OF,
        )

        projection = reviews.project_temporal_validity(
            deadline=AS_OF,
            status="aberta",
            input_fingerprint=FINGERPRINT,
        )

        assert projection.validity_state is ValidityState.ACTIVE
