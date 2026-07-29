from __future__ import annotations

from datetime import date, datetime

import pytest

from radar.core.services import data_quality_exceptions as repository
from radar.core.services import data_quality_reviews as reviews
from radar.core.services.data_quality_exceptions import DataQualityStorageError
from radar.domain.data_quality import DataQualityReview, TemporalMode, ValidityState
from radar.domain.provenance import (
    EvidenceRef,
    FactState,
    LocatorQuality,
    ProducerKind,
)

pytestmark = pytest.mark.unit

AS_OF = date(2026, 7, 29)
REVIEWED_AT = datetime.fromisoformat("2026-07-29T10:00:00-03:00")


def evidence(suffix: str = "a") -> EvidenceRef:
    return EvidenceRef(
        source="finep",
        canonical_content_hash=f"sha256:{suffix * 64}",
        document="edital.html",
        locator_quality=LocatorQuality.DOCUMENT_ONLY,
    )


def exception_row(
    *,
    produced_value: str | None = "2026-12-31",
    refs: list[EvidenceRef] | None = None,
    status: str = "open",
    fingerprint: str = "sha256:current",
) -> dict:
    return {
        "id": "exc-1",
        "subject_kind": "opportunity",
        "subject_id": "finep:602",
        "field_path": "deadline",
        "issue_code": "temporal_status_conflict",
        "produced_state": "inferred",
        "produced_value": produced_value,
        "evidence_refs": [
            ref.model_dump(mode="json") for ref in (refs or [])
        ],
        "input_fingerprint": fingerprint,
        "status": status,
    }


class FakeReviewRepository:
    def __init__(self, row: dict):
        self.row = row
        self.review: DataQualityReview | None = None
        self.events: list[str] = []
        self.resolve_failures = 0

    def get_exception(self, exception_id: str):
        assert exception_id == self.row["id"]
        return dict(self.row)

    def get_review(self, exception_id: str):
        assert exception_id == self.row["id"]
        return self.review

    def append(self, review: DataQualityReview) -> bool:
        self.events.append("append")
        if self.review is None:
            self.review = review
        elif self.review != review:
            raise DataQualityStorageError("review collision")
        return True

    def resolve(self, exception_id: str) -> bool:
        self.events.append("resolved")
        assert exception_id == self.row["id"]
        if self.resolve_failures:
            self.resolve_failures -= 1
            raise DataQualityStorageError("sanitized failure")
        self.row["status"] = "resolved"
        return True


@pytest.fixture
def install_repo(monkeypatch):
    def _install(repo: FakeReviewRepository) -> FakeReviewRepository:
        monkeypatch.setattr(reviews, "get_exception", repo.get_exception)
        monkeypatch.setattr(
            reviews,
            "get_current_review_projection",
            repo.get_review,
        )
        monkeypatch.setattr(reviews, "append_review", repo.append)
        monkeypatch.setattr(reviews, "mark_exception_resolved", repo.resolve)
        return repo

    return _install


def submit(
    *,
    decision,
    evidence_refs=None,
    corrected_value=None,
):
    return reviews.review_temporal_exception(
        exception_id="exc-1",
        review_id="review-1",
        actor_id="admin-1",
        decision=decision,
        justification="Decisão sustentada pelo documento versionado.",
        corrected_value=corrected_value,
        evidence_refs=evidence_refs,
        reviewed_at=REVIEWED_AT,
        as_of=AS_OF,
    )


class TestValidDecisions:
    def test_confirm_date(self, install_repo):
        ref = evidence()
        repo = install_repo(
            FakeReviewRepository(exception_row(refs=[ref]))
        )

        projection = submit(decision="confirm", evidence_refs=[ref])

        assert projection.temporal_mode is TemporalMode.FIXED
        assert projection.validity_state is ValidityState.ACTIVE
        assert projection.value == "2026-12-31"
        assert repo.review is not None
        assert repo.review.review.overridden is False

    def test_correct_date(self, install_repo):
        ref = evidence()
        repo = install_repo(
            FakeReviewRepository(exception_row(refs=[ref]))
        )

        projection = submit(
            decision="correct",
            corrected_value="2026-08-31",
            evidence_refs=[ref],
        )

        assert projection.temporal_mode is TemporalMode.FIXED
        assert projection.validity_state is ValidityState.ACTIVE
        assert projection.value == "2026-08-31"
        assert repo.review is not None
        assert repo.review.review.overridden is True

    def test_confirm_continuous(self, install_repo):
        ref = evidence()
        install_repo(
            FakeReviewRepository(
                exception_row(produced_value="ABERTA", refs=[ref])
            )
        )

        projection = submit(
            decision="confirm_continuous",
            evidence_refs=[ref],
        )

        assert projection.temporal_mode is TemporalMode.CONTINUOUS
        assert projection.validity_state is ValidityState.ACTIVE
        assert projection.value is None
        assert projection.provenance.review.overridden is False

    def test_mark_unknown(self, install_repo):
        install_repo(
            FakeReviewRepository(
                exception_row(produced_value="ABERTA")
            )
        )

        projection = submit(decision="mark_unknown")

        assert projection.temporal_mode is TemporalMode.UNKNOWN
        assert projection.validity_state is ValidityState.NEEDS_REVIEW
        assert projection.value is None
        assert projection.provenance.state is FactState.UNKNOWN

    def test_confirm_closed_status_without_deadline(self, install_repo):
        ref = evidence()
        install_repo(
            FakeReviewRepository(
                exception_row(produced_value="ENCERRADA", refs=[ref])
            )
        )

        projection = submit(decision="confirm", evidence_refs=[ref])

        assert projection.temporal_mode is TemporalMode.UNKNOWN
        assert projection.validity_state is ValidityState.CLOSED
        assert projection.value == "ENCERRADA"


class TestDecisionValidation:
    def test_confirm_open_status_without_deadline_rejected(self, install_repo):
        ref = evidence()
        install_repo(
            FakeReviewRepository(
                exception_row(produced_value="ABERTA", refs=[ref])
            )
        )

        with pytest.raises(
            reviews.ReviewValidationError,
            match="cannot be confirmed",
        ):
            submit(decision="confirm", evidence_refs=[ref])

    @pytest.mark.parametrize(
        ("decision", "corrected_value"),
        [
            ("confirm", None),
            ("correct", "2026-08-31"),
            ("confirm_continuous", None),
        ],
    )
    def test_evidence_required(
        self,
        install_repo,
        decision,
        corrected_value,
    ):
        install_repo(FakeReviewRepository(exception_row()))

        with pytest.raises(
            reviews.ReviewValidationError,
            match="linked versioned evidence",
        ):
            submit(
                decision=decision,
                corrected_value=corrected_value,
            )

    def test_unlinked_evidence_rejected(self, install_repo):
        linked = evidence("a")
        unlinked = evidence("b")
        install_repo(
            FakeReviewRepository(exception_row(refs=[linked]))
        )

        with pytest.raises(
            reviews.ReviewValidationError,
            match="bundle or producer",
        ):
            submit(
                decision="correct",
                corrected_value="2026-08-31",
                evidence_refs=[unlinked],
            )

    @pytest.mark.parametrize(
        "corrected_value",
        [None, "", "31/08/2026", "2026-02-30"],
    )
    def test_correct_requires_valid_iso_date(
        self,
        install_repo,
        corrected_value,
    ):
        ref = evidence()
        install_repo(
            FakeReviewRepository(exception_row(refs=[ref]))
        )

        with pytest.raises(reviews.ReviewValidationError, match="YYYY-MM-DD"):
            submit(
                decision="correct",
                corrected_value=corrected_value,
                evidence_refs=[ref],
            )

    def test_mark_unknown_rejects_corrected_value(self, install_repo):
        install_repo(FakeReviewRepository(exception_row()))

        with pytest.raises(
            reviews.ReviewValidationError,
            match="does not accept",
        ):
            submit(
                decision="mark_unknown",
                corrected_value="2026-08-31",
            )

    def test_superseded_cannot_be_reviewed(self, install_repo):
        ref = evidence()
        install_repo(
            FakeReviewRepository(
                exception_row(refs=[ref], status="superseded")
            )
        )

        with pytest.raises(
            reviews.ReviewValidationError,
            match="superseded",
        ):
            submit(decision="confirm", evidence_refs=[ref])


class TestPersistenceOrderAndRetry:
    def test_append_happens_before_resolved(self, install_repo):
        ref = evidence()
        repo = install_repo(
            FakeReviewRepository(exception_row(refs=[ref]))
        )

        submit(decision="confirm", evidence_refs=[ref])

        assert repo.events == ["append", "resolved"]
        assert repo.row["status"] == "resolved"

    def test_retry_finishes_resolution_without_duplicate_review(
        self,
        install_repo,
    ):
        ref = evidence()
        repo = FakeReviewRepository(exception_row(refs=[ref]))
        repo.resolve_failures = 1
        install_repo(repo)

        with pytest.raises(DataQualityStorageError):
            submit(decision="confirm", evidence_refs=[ref])
        first_review = repo.review
        assert first_review is not None
        assert repo.row["status"] == "open"

        projection = submit(decision="confirm", evidence_refs=[ref])

        assert repo.review is first_review
        assert repo.row["status"] == "resolved"
        assert projection.review_id == "review-1"
        assert repo.events == [
            "append",
            "resolved",
            "append",
            "resolved",
        ]


class TestHumanProvenance:
    def test_preserves_review_evidence_and_derivation(self, install_repo):
        ref = evidence()
        install_repo(
            FakeReviewRepository(exception_row(refs=[ref]))
        )

        projection = submit(
            decision="correct",
            corrected_value="2026-08-31",
            evidence_refs=[ref],
        )
        provenance = projection.provenance

        assert provenance.producer.kind is ProducerKind.HUMAN
        assert provenance.producer.name == "data_quality_review"
        assert provenance.producer.version == reviews.REVIEW_PRODUCER_VERSION
        assert provenance.state is FactState.STATED
        assert provenance.review.review_id == "review-1"
        assert provenance.review.actor_id == "admin-1"
        assert provenance.review.reviewed_at == REVIEWED_AT
        assert provenance.review.overridden is True
        assert provenance.evidence_refs == [ref]
        assert "exception:exc-1" in provenance.derivation.inputs
        assert "input_fingerprint:sha256:current" in (
            provenance.derivation.inputs
        )
        assert "previous_value:2026-12-31" in provenance.derivation.inputs

    def test_correct_changes_only_projection(self, install_repo):
        ref = evidence()
        entity = {
            "status": "aberta",
            "deadline": "2026-12-31",
        }
        original = dict(entity)
        install_repo(
            FakeReviewRepository(exception_row(refs=[ref]))
        )

        projection = submit(
            decision="correct",
            corrected_value="2026-08-31",
            evidence_refs=[ref],
        )

        assert projection.value == "2026-08-31"
        assert entity == original


class _Response:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, rows, *, payload=None, failure=None):
        self.rows = rows
        self.payload = payload
        self.failure = failure
        self.filters = []

    def eq(self, key, value):
        self.filters.append((key, value))
        return self

    def limit(self, _):
        return self

    def execute(self):
        if self.failure is not None:
            raise self.failure
        matched = [
            row for row in self.rows
            if all(row.get(key) == value for key, value in self.filters)
        ]
        if self.payload is not None:
            for row in matched:
                row.update(self.payload)
        return _Response(matched)


class _Table:
    def __init__(self, rows, failure=None):
        self.rows = rows
        self.failure = failure

    def update(self, payload):
        return _Query(
            self.rows,
            payload=payload,
            failure=self.failure,
        )

    def select(self, *_):
        return _Query(self.rows, failure=self.failure)


class _Supabase:
    def __init__(self, rows, failure=None):
        self.rows = rows
        self.failure = failure

    def table(self, _):
        return _Table(self.rows, self.failure)


class TestMarkExceptionResolvedRepository:
    def _install(self, monkeypatch, service):
        monkeypatch.setenv("SUPABASE_URL", "http://test")
        monkeypatch.setenv("SUPABASE_SERVICE_KEY", "test-key")
        import radar.core.infra.db

        monkeypatch.setattr(
            radar.core.infra.db,
            "get_supabase_service",
            lambda: service,
        )

    def test_open_transitions_to_resolved(self, monkeypatch):
        rows = [{"id": "exc-1", "status": "open"}]
        self._install(monkeypatch, _Supabase(rows))

        assert repository.mark_exception_resolved("exc-1") is True
        assert rows[0]["status"] == "resolved"

    def test_superseded_is_not_resolved(self, monkeypatch):
        rows = [{"id": "exc-1", "status": "superseded"}]
        self._install(monkeypatch, _Supabase(rows))

        assert repository.mark_exception_resolved("exc-1") is False
        assert rows[0]["status"] == "superseded"

    def test_failure_is_sanitized(self, monkeypatch, caplog):
        self._install(
            monkeypatch,
            _Supabase([], failure=RuntimeError("secret raw payload")),
        )

        with pytest.raises(
            DataQualityStorageError,
            match="type=RuntimeError",
        ) as caught:
            repository.mark_exception_resolved("exc-1")

        assert "secret raw payload" not in str(caught.value)
        assert "secret raw payload" not in caplog.text
