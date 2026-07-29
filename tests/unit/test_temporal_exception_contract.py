from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from radar.domain.data_quality import (
    DATA_QUALITY_SCHEMA_VERSION,
    DataQualityException,
    DataQualityReview,
    IssueCode,
    TemporalEvaluation,
    TemporalMode,
    ValidityState,
    evaluate_temporal,
)
from radar.domain.provenance import EvidenceRef, LocatorQuality, ReviewInfo
from tests.fixtures.data_quality.finep_eureka import finep_eureka_2024

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class TestEnumValues:
    def test_temporal_mode_values(self):
        assert {m.value for m in TemporalMode} == {"fixed", "continuous", "unknown"}

    def test_validity_state_values(self):
        assert {s.value for s in ValidityState} == {
            "active", "closed", "needs_review",
        }

    def test_issue_code_values(self):
        assert {c.value for c in IssueCode} == {
            "fact_conflict",
            "critical_fact_missing",
            "validation_failed",
            "evidence_unresolved",
            "temporal_status_without_basis",
            "temporal_status_conflict",
        }


# ---------------------------------------------------------------------------
# TemporalEvaluation
# ---------------------------------------------------------------------------


class TestTemporalEvaluationConstruction:
    def test_minimal_valid(self):
        te = TemporalEvaluation(
            temporal_mode=TemporalMode.FIXED,
            validity_state=ValidityState.ACTIVE,
        )
        assert te.temporal_mode is TemporalMode.FIXED
        assert te.validity_state is ValidityState.ACTIVE
        assert te.issue_code is None
        assert te.issue_description is None

    def test_with_issue(self):
        te = TemporalEvaluation(
            temporal_mode=TemporalMode.UNKNOWN,
            validity_state=ValidityState.NEEDS_REVIEW,
            issue_code=IssueCode.TEMPORAL_STATUS_WITHOUT_BASIS,
            issue_description="status aberto sem prazo nem evidencia",
        )
        assert te.issue_code is IssueCode.TEMPORAL_STATUS_WITHOUT_BASIS

    def test_issue_code_without_description_rejected(self):
        with pytest.raises(ValidationError, match="issue_description is required"):
            TemporalEvaluation(
                temporal_mode=TemporalMode.UNKNOWN,
                validity_state=ValidityState.NEEDS_REVIEW,
                issue_code=IssueCode.TEMPORAL_STATUS_WITHOUT_BASIS,
            )

    def test_description_without_issue_code_rejected(self):
        with pytest.raises(ValidationError, match="issue_description requires issue_code"):
            TemporalEvaluation(
                temporal_mode=TemporalMode.FIXED,
                validity_state=ValidityState.ACTIVE,
                issue_description="descricao sem codigo",
            )

    def test_extra_field_rejected(self):
        with pytest.raises(ValidationError):
            TemporalEvaluation(
                temporal_mode=TemporalMode.FIXED,
                validity_state=ValidityState.ACTIVE,
                confidence=0.9,
            )

    def test_roundtrip(self):
        te = TemporalEvaluation(
            temporal_mode=TemporalMode.FIXED,
            validity_state=ValidityState.ACTIVE,
        )
        again = TemporalEvaluation.model_validate(te.model_dump())
        assert again == te

    def test_roundtrip_json(self):
        te = TemporalEvaluation(
            temporal_mode=TemporalMode.UNKNOWN,
            validity_state=ValidityState.NEEDS_REVIEW,
            issue_code=IssueCode.TEMPORAL_STATUS_WITHOUT_BASIS,
            issue_description="desc",
        )
        again = TemporalEvaluation.model_validate_json(te.model_dump_json())
        assert again == te


# ---------------------------------------------------------------------------
# evaluate_temporal — regras da §4.1
# ---------------------------------------------------------------------------


class TestEvaluateTemporal:
    """Testes determinísticos com as_of injetado; nunca date.today()."""

    def test_future_deadline_fixed_active(self):
        result = evaluate_temporal(
            deadline=date(2026, 12, 31),
            status="ABERTA",
            as_of=date(2026, 7, 29),
        )
        assert result.temporal_mode is TemporalMode.FIXED
        assert result.validity_state is ValidityState.ACTIVE
        assert result.issue_code is None

    def test_deadline_today_fixed_active(self):
        """O dia do encerramento permanece ativo."""
        result = evaluate_temporal(
            deadline=date(2026, 7, 29),
            status="ABERTA",
            as_of=date(2026, 7, 29),
        )
        assert result.temporal_mode is TemporalMode.FIXED
        assert result.validity_state is ValidityState.ACTIVE
        assert result.issue_code is None

    def test_past_deadline_fixed_closed(self):
        result = evaluate_temporal(
            deadline=date(2026, 1, 31),
            status="ENCERRADA",
            as_of=date(2026, 7, 29),
        )
        assert result.temporal_mode is TemporalMode.FIXED
        assert result.validity_state is ValidityState.CLOSED
        assert result.issue_code is None

    def test_continuous_active_with_evidence(self):
        evidence = EvidenceRef(
            source="finep",
            canonical_content_hash="sha256:" + "a" * 64,
            locator_quality=LocatorQuality.DOCUMENT_ONLY,
            document="pagina_oficial.html",
            quote="fluxo continuo: inscricoes permanentes",
        )
        result = evaluate_temporal(
            deadline=None,
            status="ABERTA",
            as_of=date(2026, 7, 29),
            continuous_evidence=evidence,
        )
        assert result.temporal_mode is TemporalMode.CONTINUOUS
        assert result.validity_state is ValidityState.ACTIVE
        assert result.issue_code is None

    def test_closed_without_deadline_unknown_closed(self):
        result = evaluate_temporal(
            deadline=None,
            status="ENCERRADA",
            as_of=date(2026, 7, 29),
        )
        assert result.temporal_mode is TemporalMode.UNKNOWN
        assert result.validity_state is ValidityState.CLOSED
        assert result.issue_code is None

    def test_closed_without_deadline_unknown_closed_resultado(self):
        result = evaluate_temporal(
            deadline=None,
            status="RESULTADO_DIVULGADO",
            as_of=date(2026, 7, 29),
        )
        assert result.temporal_mode is TemporalMode.UNKNOWN
        assert result.validity_state is ValidityState.CLOSED

    def test_open_without_deadline_needs_review(self):
        """Finep/Eureka: ABERTA sem prazo → needs_review."""
        result = evaluate_temporal(
            deadline=None,
            status="ABERTA",
            as_of=date(2026, 7, 29),
        )
        assert result.temporal_mode is TemporalMode.UNKNOWN
        assert result.validity_state is ValidityState.NEEDS_REVIEW
        assert result.issue_code is IssueCode.TEMPORAL_STATUS_WITHOUT_BASIS

    def test_open_without_deadline_needs_review_via_fixture(self):
        data = finep_eureka_2024()
        result = evaluate_temporal(
            deadline=data["deadline"],
            status=data["status"],
            as_of=date(2026, 7, 29),
            closed_status_values=data["closed_status_values"],
        )
        assert result.temporal_mode is TemporalMode.UNKNOWN
        assert result.validity_state is ValidityState.NEEDS_REVIEW
        assert result.issue_code is IssueCode.TEMPORAL_STATUS_WITHOUT_BASIS

    def test_no_status_no_deadline_needs_review(self):
        result = evaluate_temporal(
            deadline=None,
            status=None,
            as_of=date(2026, 7, 29),
        )
        assert result.temporal_mode is TemporalMode.UNKNOWN
        assert result.validity_state is ValidityState.NEEDS_REVIEW
        assert result.issue_code is IssueCode.TEMPORAL_STATUS_WITHOUT_BASIS


# ---------------------------------------------------------------------------
# Conflitos (§4.1 — precedência)
# ---------------------------------------------------------------------------


class TestConflicts:
    """Prazo e status incompatíveis → needs_review."""

    def test_future_deadline_with_closed_status_conflict(self):
        result = evaluate_temporal(
            deadline=date(2026, 12, 31),
            status="ENCERRADA",
            as_of=date(2026, 7, 29),
        )
        assert result.validity_state is ValidityState.NEEDS_REVIEW
        assert result.issue_code is IssueCode.TEMPORAL_STATUS_CONFLICT
        assert result.temporal_mode is TemporalMode.FIXED

    def test_past_deadline_with_open_status_conflict(self):
        result = evaluate_temporal(
            deadline=date(2024, 1, 31),
            status="ABERTA",
            as_of=date(2026, 7, 29),
        )
        assert result.validity_state is ValidityState.NEEDS_REVIEW
        assert result.issue_code is IssueCode.TEMPORAL_STATUS_CONFLICT
        assert result.temporal_mode is TemporalMode.FIXED


# ---------------------------------------------------------------------------
# Rejeitar continuidade sem evidência
# ---------------------------------------------------------------------------


class TestContinuousWithoutEvidence:
    def test_no_evidence_is_not_continuous(self):
        """Ausência de prazo nunca basta para continuous."""
        result = evaluate_temporal(
            deadline=None,
            status="ABERTA",
            as_of=date(2026, 7, 29),
        )
        assert result.temporal_mode is not TemporalMode.CONTINUOUS
        assert result.temporal_mode is TemporalMode.UNKNOWN

    def test_none_evidence_is_not_continuous(self):
        result = evaluate_temporal(
            deadline=None,
            status="ABERTA",
            as_of=date(2026, 7, 29),
            continuous_evidence=None,
        )
        assert result.temporal_mode is not TemporalMode.CONTINUOUS

    def test_continuity_requires_recoverable_evidence(self):
        """EvidenceRef válido é necessário para continuous."""
        result = evaluate_temporal(
            deadline=None,
            status="ABERTA",
            as_of=date(2026, 7, 29),
            continuous_evidence=None,
        )
        assert result.temporal_mode is not TemporalMode.CONTINUOUS


# ---------------------------------------------------------------------------
# DataQualityException
# ---------------------------------------------------------------------------


class TestDataQualityException:
    def test_minimal_valid(self):
        exc = DataQualityException(
            subject_kind="opportunity",
            subject_id="finep:589",
            field_path="deadline",
            issue_code=IssueCode.TEMPORAL_STATUS_WITHOUT_BASIS,
        )
        assert exc.schema_version == DATA_QUALITY_SCHEMA_VERSION
        assert exc.status == "open"

    def test_empty_subject_kind_rejected(self):
        with pytest.raises(ValidationError):
            DataQualityException(
                subject_kind="",
                subject_id="x",
                field_path="deadline",
                issue_code=IssueCode.TEMPORAL_STATUS_WITHOUT_BASIS,
            )

    def test_empty_field_path_rejected(self):
        with pytest.raises(ValidationError):
            DataQualityException(
                subject_kind="opportunity",
                subject_id="x",
                field_path="",
                issue_code=IssueCode.TEMPORAL_STATUS_WITHOUT_BASIS,
            )

    def test_invalid_issue_code_rejected(self):
        with pytest.raises(ValidationError):
            DataQualityException(
                subject_kind="opportunity",
                subject_id="x",
                field_path="deadline",
                issue_code="unknown_code",
            )

    def test_extra_field_rejected(self):
        with pytest.raises(ValidationError):
            DataQualityException(
                subject_kind="opportunity",
                subject_id="x",
                field_path="deadline",
                issue_code=IssueCode.TEMPORAL_STATUS_WITHOUT_BASIS,
                score=0.5,
            )

    def test_roundtrip(self):
        exc = DataQualityException(
            subject_kind="opportunity",
            subject_id="finep:589",
            field_path="deadline",
            issue_code=IssueCode.TEMPORAL_STATUS_CONFLICT,
            produced_state="fixed",
            produced_value="2026-12-31",
            status="open",
        )
        again = DataQualityException.model_validate(exc.model_dump())
        assert again == exc


# ---------------------------------------------------------------------------
# DataQualityReview
# ---------------------------------------------------------------------------


class TestDataQualityReview:
    def test_minimal_confirm(self):
        review = DataQualityReview(
            exception_ref="exc-001",
            decision="confirm",
            justification="prazo futuro confirmado pelo edital",
            review=ReviewInfo(
                review_id="rev-001",
                actor_id="admin",
                reviewed_at="2026-07-29T12:00:00Z",
            ),
        )
        assert review.decision == "confirm"

    def test_correct_requires_corrected_value(self):
        with pytest.raises(ValidationError, match="corrected_value is required"):
            DataQualityReview(
                exception_ref="exc-001",
                decision="correct",
                justification="corrigindo prazo",
                review=ReviewInfo(
                    review_id="rev-002",
                    actor_id="admin",
                    reviewed_at="2026-07-29T12:00:00Z",
                ),
            )

    def test_correct_with_value_valid(self):
        review = DataQualityReview(
            exception_ref="exc-001",
            decision="correct",
            corrected_value="2026-12-31",
            justification="prazo correto conforme anexo",
            review=ReviewInfo(
                review_id="rev-003",
                actor_id="admin",
                reviewed_at="2026-07-29T12:00:00Z",
            ),
        )
        assert review.corrected_value == "2026-12-31"

    def test_confirm_continuous_valid(self):
        review = DataQualityReview(
            exception_ref="exc-002",
            decision="confirm_continuous",
            justification="fluxo continuo comprovado por evidencia oficial",
            evidence_refs=[
                EvidenceRef(
                    source="finep",
                    canonical_content_hash="sha256:" + "b" * 64,
                    locator_quality=LocatorQuality.DOCUMENT_ONLY,
                    document="edital.html",
                ),
            ],
            review=ReviewInfo(
                review_id="rev-004",
                actor_id="admin",
                reviewed_at="2026-07-29T12:00:00Z",
            ),
        )
        assert review.decision == "confirm_continuous"

    def test_mark_unknown_valid(self):
        review = DataQualityReview(
            exception_ref="exc-003",
            decision="mark_unknown",
            justification="dado insuficiente para determinar prazo",
            review=ReviewInfo(
                review_id="rev-005",
                actor_id="admin",
                reviewed_at="2026-07-29T12:00:00Z",
            ),
        )
        assert review.decision == "mark_unknown"

    def test_empty_justification_rejected(self):
        with pytest.raises(ValidationError, match="justification must be non-empty"):
            DataQualityReview(
                exception_ref="exc-001",
                decision="confirm",
                justification="",
                review=ReviewInfo(
                    review_id="rev-001",
                    actor_id="admin",
                    reviewed_at="2026-07-29T12:00:00Z",
                ),
            )

    def test_long_justification_rejected(self):
        with pytest.raises(ValidationError, match="must not exceed"):
            DataQualityReview(
                exception_ref="exc-001",
                decision="confirm",
                justification="x" * 2001,
                review=ReviewInfo(
                    review_id="rev-001",
                    actor_id="admin",
                    reviewed_at="2026-07-29T12:00:00Z",
                ),
            )

    def test_invalid_decision_rejected(self):
        with pytest.raises(ValidationError):
            DataQualityReview(
                exception_ref="exc-001",
                decision="ignore",
                justification="n/a",
                review=ReviewInfo(
                    review_id="rev-001",
                    actor_id="admin",
                    reviewed_at="2026-07-29T12:00:00Z",
                ),
            )

    def test_extra_field_rejected(self):
        with pytest.raises(ValidationError):
            DataQualityReview(
                exception_ref="exc-001",
                decision="confirm",
                justification="ok",
                score=5,
                review=ReviewInfo(
                    review_id="rev-001",
                    actor_id="admin",
                    reviewed_at="2026-07-29T12:00:00Z",
                ),
            )

    def test_roundtrip(self):
        review = DataQualityReview(
            exception_ref="exc-001",
            decision="confirm",
            justification="prazo correto",
            review=ReviewInfo(
                review_id="rev-001",
                actor_id="admin",
                reviewed_at="2026-07-29T12:00:00Z",
            ),
        )
        again = DataQualityReview.model_validate(review.model_dump())
        assert again == review


# ---------------------------------------------------------------------------
# Schema version
# ---------------------------------------------------------------------------


class TestSchemaVersion:
    def test_version_constant(self):
        assert DATA_QUALITY_SCHEMA_VERSION == 1
        assert isinstance(DATA_QUALITY_SCHEMA_VERSION, int)


# ---------------------------------------------------------------------------
# Sem score, confiança ou campos abertos
# ---------------------------------------------------------------------------


class TestNoScoreOrConfidence:
    def test_no_confidence_in_temporal_evaluation(self):
        for field_name in TemporalEvaluation.model_fields:
            assert "confidence" not in field_name.lower()
            assert "score" not in field_name.lower()

    def test_no_confidence_in_data_quality_exception(self):
        for field_name in DataQualityException.model_fields:
            assert "confidence" not in field_name.lower()
            assert "score" not in field_name.lower()

    def test_no_confidence_in_data_quality_review(self):
        for field_name in DataQualityReview.model_fields:
            assert "confidence" not in field_name.lower()
            assert "score" not in field_name.lower()


# ---------------------------------------------------------------------------
# Data Quality schema version não é campo aberto
# ---------------------------------------------------------------------------


class TestDataQualitySchemaVersionFixed:
    def test_schema_version_default_and_fixed(self):
        exc = DataQualityException(
            subject_kind="opportunity",
            subject_id="finep:589",
            field_path="deadline",
            issue_code=IssueCode.TEMPORAL_STATUS_WITHOUT_BASIS,
        )
        assert exc.schema_version == 1

    def test_schema_version_rejects_other_values(self):
        with pytest.raises(ValidationError):
            DataQualityException(
                schema_version=2,
                subject_kind="opportunity",
                subject_id="finep:589",
                field_path="deadline",
                issue_code=IssueCode.TEMPORAL_STATUS_WITHOUT_BASIS,
            )

    def test_review_schema_version_fixed(self):
        review = DataQualityReview(
            exception_ref="exc-001",
            decision="confirm",
            justification="ok",
            review=ReviewInfo(
                review_id="rev-001",
                actor_id="admin",
                reviewed_at="2026-07-29T12:00:00Z",
            ),
        )
        assert review.schema_version == 1
