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
from radar.domain.provenance import EvidenceRef, FactState, LocatorQuality, ReviewInfo
from radar.domain.source_bundle import SubjectKind
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
# TemporalEvaluation — invariantes
# ---------------------------------------------------------------------------


class TestTemporalEvaluationInvariants:
    def test_active_without_issue_valid(self):
        te = TemporalEvaluation(
            temporal_mode=TemporalMode.FIXED,
            validity_state=ValidityState.ACTIVE,
        )
        assert te.issue_code is None
        assert te.issue_description is None

    def test_closed_without_issue_valid(self):
        te = TemporalEvaluation(
            temporal_mode=TemporalMode.FIXED,
            validity_state=ValidityState.CLOSED,
        )
        assert te.issue_code is None

    def test_needs_review_with_issue_valid(self):
        te = TemporalEvaluation(
            temporal_mode=TemporalMode.UNKNOWN,
            validity_state=ValidityState.NEEDS_REVIEW,
            issue_code=IssueCode.TEMPORAL_STATUS_WITHOUT_BASIS,
            issue_description="status aberto sem prazo",
        )
        assert te.issue_code is IssueCode.TEMPORAL_STATUS_WITHOUT_BASIS

    def test_active_with_issue_code_rejected(self):
        with pytest.raises(ValidationError, match="must not have issue_code"):
            TemporalEvaluation(
                temporal_mode=TemporalMode.FIXED,
                validity_state=ValidityState.ACTIVE,
                issue_code=IssueCode.TEMPORAL_STATUS_CONFLICT,
                issue_description="conflito",
            )

    def test_active_with_description_rejected(self):
        with pytest.raises(ValidationError, match="must not have issue_description"):
            TemporalEvaluation(
                temporal_mode=TemporalMode.FIXED,
                validity_state=ValidityState.ACTIVE,
                issue_description="descricao sem codigo",
            )

    def test_closed_with_issue_code_rejected(self):
        with pytest.raises(ValidationError, match="must not have issue_code"):
            TemporalEvaluation(
                temporal_mode=TemporalMode.FIXED,
                validity_state=ValidityState.CLOSED,
                issue_code=IssueCode.TEMPORAL_STATUS_CONFLICT,
                issue_description="conflito",
            )

    def test_needs_review_without_issue_code_rejected(self):
        with pytest.raises(ValidationError, match="needs_review requires issue_code"):
            TemporalEvaluation(
                temporal_mode=TemporalMode.UNKNOWN,
                validity_state=ValidityState.NEEDS_REVIEW,
            )

    def test_needs_review_without_description_rejected(self):
        with pytest.raises(ValidationError, match="needs_review requires issue_description"):
            TemporalEvaluation(
                temporal_mode=TemporalMode.UNKNOWN,
                validity_state=ValidityState.NEEDS_REVIEW,
                issue_code=IssueCode.TEMPORAL_STATUS_WITHOUT_BASIS,
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
        result = evaluate_temporal(
            deadline=date(2026, 7, 29),
            status="ABERTA",
            as_of=date(2026, 7, 29),
        )
        assert result.temporal_mode is TemporalMode.FIXED
        assert result.validity_state is ValidityState.ACTIVE
        assert result.issue_code is None

    def test_past_deadline_with_closed_status_fixed_closed(self):
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
        result = evaluate_temporal(
            deadline=None,
            status="ABERTA",
            as_of=date(2026, 7, 29),
        )
        assert result.temporal_mode is TemporalMode.UNKNOWN
        assert result.validity_state is ValidityState.NEEDS_REVIEW
        assert result.issue_code is IssueCode.TEMPORAL_STATUS_WITHOUT_BASIS

    def test_finep_eureka_fixture(self):
        data = finep_eureka_2024()
        result = evaluate_temporal(
            deadline=data["deadline"],
            status=data["status"],
            as_of=date(2026, 7, 29),
        )
        assert result.temporal_mode is TemporalMode.UNKNOWN
        assert result.validity_state is ValidityState.NEEDS_REVIEW
        assert result.issue_code is IssueCode.TEMPORAL_STATUS_WITHOUT_BASIS

    def test_no_status_no_deadline_critical_fact_missing(self):
        result = evaluate_temporal(
            deadline=None,
            status=None,
            as_of=date(2026, 7, 29),
        )
        assert result.temporal_mode is TemporalMode.UNKNOWN
        assert result.validity_state is ValidityState.NEEDS_REVIEW
        assert result.issue_code is IssueCode.CRITICAL_FACT_MISSING


# ---------------------------------------------------------------------------
# Continuous- evidence conflicts (precedência sobre deadline/status)
# ---------------------------------------------------------------------------


class TestContinuousEvidenceConflicts:
    """CONTINUOUS/ACTIVE só é válido com deadline=None,
    status não fechado e continuous_evidence válida."""

    def _make_evidence(self, suffix: str = "a") -> EvidenceRef:
        return EvidenceRef(
            source="finep",
            canonical_content_hash=f"sha256:{suffix * 64}",
            locator_quality=LocatorQuality.DOCUMENT_ONLY,
            document="pagina_oficial.html",
            quote="fluxo continuo: inscricoes permanentes",
        )

    def test_future_deadline_with_continuous_evidence_conflict(self):
        result = evaluate_temporal(
            deadline=date(2027, 1, 31),
            status="ABERTA",
            as_of=date(2026, 7, 29),
            continuous_evidence=self._make_evidence("b"),
        )
        assert result.temporal_mode is TemporalMode.UNKNOWN
        assert result.validity_state is ValidityState.NEEDS_REVIEW
        assert result.issue_code is IssueCode.TEMPORAL_STATUS_CONFLICT

    def test_past_deadline_with_continuous_evidence_conflict(self):
        result = evaluate_temporal(
            deadline=date(2024, 1, 31),
            status="ABERTA",
            as_of=date(2026, 7, 29),
            continuous_evidence=self._make_evidence("c"),
        )
        assert result.temporal_mode is TemporalMode.UNKNOWN
        assert result.validity_state is ValidityState.NEEDS_REVIEW
        assert result.issue_code is IssueCode.TEMPORAL_STATUS_CONFLICT

    def test_closed_status_without_deadline_with_continuous_evidence_conflict(self):
        result = evaluate_temporal(
            deadline=None,
            status="ENCERRADA",
            as_of=date(2026, 7, 29),
            continuous_evidence=self._make_evidence("d"),
        )
        assert result.temporal_mode is TemporalMode.UNKNOWN
        assert result.validity_state is ValidityState.NEEDS_REVIEW
        assert result.issue_code is IssueCode.TEMPORAL_STATUS_CONFLICT

    def test_resultado_divulgado_with_continuous_evidence_conflict(self):
        result = evaluate_temporal(
            deadline=None,
            status="RESULTADO_DIVULGADO",
            as_of=date(2026, 7, 29),
            continuous_evidence=self._make_evidence("e"),
        )
        assert result.temporal_mode is TemporalMode.UNKNOWN
        assert result.validity_state is ValidityState.NEEDS_REVIEW
        assert result.issue_code is IssueCode.TEMPORAL_STATUS_CONFLICT

    def test_no_deadline_open_status_with_continuous_active(self):
        result = evaluate_temporal(
            deadline=None,
            status="ABERTA",
            as_of=date(2026, 7, 29),
            continuous_evidence=self._make_evidence("f"),
        )
        assert result.temporal_mode is TemporalMode.CONTINUOUS
        assert result.validity_state is ValidityState.ACTIVE
        assert result.issue_code is None

    def test_no_deadline_neutral_status_with_continuous_active(self):
        result = evaluate_temporal(
            deadline=None,
            status="Desconhecido",
            as_of=date(2026, 7, 29),
            continuous_evidence=self._make_evidence("g"),
        )
        assert result.temporal_mode is TemporalMode.CONTINUOUS
        assert result.validity_state is ValidityState.ACTIVE
        assert result.issue_code is None

    def test_no_deadline_no_status_with_continuous_active(self):
        result = evaluate_temporal(
            deadline=None,
            status=None,
            as_of=date(2026, 7, 29),
            continuous_evidence=self._make_evidence("h"),
        )
        assert result.temporal_mode is TemporalMode.CONTINUOUS
        assert result.validity_state is ValidityState.ACTIVE
        assert result.issue_code is None


# ---------------------------------------------------------------------------
# Desconhecido — neutro, não classificado como aberto
# ---------------------------------------------------------------------------


class TestDesconhecido:
    def test_desconhecido_with_past_deadline_fixed_closed(self):
        result = evaluate_temporal(
            deadline=date(2026, 1, 31),
            status="Desconhecido",
            as_of=date(2026, 7, 29),
        )
        assert result.temporal_mode is TemporalMode.FIXED
        assert result.validity_state is ValidityState.CLOSED
        assert result.issue_code is None

    def test_desconhecido_with_future_deadline_fixed_active(self):
        result = evaluate_temporal(
            deadline=date(2026, 12, 31),
            status="Desconhecido",
            as_of=date(2026, 7, 29),
        )
        assert result.temporal_mode is TemporalMode.FIXED
        assert result.validity_state is ValidityState.ACTIVE
        assert result.issue_code is None

    def test_desconhecido_without_deadline_critical_fact_missing(self):
        result = evaluate_temporal(
            deadline=None,
            status="Desconhecido",
            as_of=date(2026, 7, 29),
        )
        assert result.temporal_mode is TemporalMode.UNKNOWN
        assert result.validity_state is ValidityState.NEEDS_REVIEW
        assert result.issue_code is IssueCode.CRITICAL_FACT_MISSING


# ---------------------------------------------------------------------------
# Valor arbitrário não é classificado como aberto
# ---------------------------------------------------------------------------


class TestArbitraryStatusNotOpen:
    def test_arbitrary_status_without_deadline_critical_fact_missing(self):
        result = evaluate_temporal(
            deadline=None,
            status="QUALQUER_COISA",
            as_of=date(2026, 7, 29),
        )
        assert result.temporal_mode is TemporalMode.UNKNOWN
        assert result.validity_state is ValidityState.NEEDS_REVIEW
        assert result.issue_code is IssueCode.CRITICAL_FACT_MISSING

    def test_arbitrary_status_with_future_deadline_fixed_active(self):
        result = evaluate_temporal(
            deadline=date(2026, 12, 31),
            status="invalido",
            as_of=date(2026, 7, 29),
        )
        assert result.temporal_mode is TemporalMode.FIXED
        assert result.validity_state is ValidityState.ACTIVE
        assert result.issue_code is None

    def test_arbitrary_status_with_past_deadline_fixed_closed(self):
        result = evaluate_temporal(
            deadline=date(2026, 1, 31),
            status="invalido",
            as_of=date(2026, 7, 29),
        )
        assert result.temporal_mode is TemporalMode.FIXED
        assert result.validity_state is ValidityState.CLOSED
        assert result.issue_code is None

    def test_empty_status_without_deadline_critical_fact_missing(self):
        result = evaluate_temporal(
            deadline=None,
            status="",
            as_of=date(2026, 7, 29),
        )
        assert result.temporal_mode is TemporalMode.UNKNOWN
        assert result.validity_state is ValidityState.NEEDS_REVIEW
        assert result.issue_code is IssueCode.CRITICAL_FACT_MISSING


# ---------------------------------------------------------------------------
# Conflitos (§4.1 — precedência)
# ---------------------------------------------------------------------------


class TestConflicts:
    def test_future_deadline_with_closed_status(self):
        result = evaluate_temporal(
            deadline=date(2026, 12, 31),
            status="ENCERRADA",
            as_of=date(2026, 7, 29),
        )
        assert result.validity_state is ValidityState.NEEDS_REVIEW
        assert result.issue_code is IssueCode.TEMPORAL_STATUS_CONFLICT
        assert result.temporal_mode is TemporalMode.FIXED

    def test_past_deadline_with_open_status(self):
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
        result = evaluate_temporal(
            deadline=None,
            status="ABERTA",
            as_of=date(2026, 7, 29),
            continuous_evidence=None,
        )
        assert result.temporal_mode is not TemporalMode.CONTINUOUS


# ---------------------------------------------------------------------------
# DataQualityException — SubjectKind e FactState
# ---------------------------------------------------------------------------


class TestDataQualityException:
    def test_minimal_valid(self):
        exc = DataQualityException(
            subject_kind=SubjectKind.OPPORTUNITY,
            subject_id="finep:589",
            field_path="deadline",
            issue_code=IssueCode.TEMPORAL_STATUS_WITHOUT_BASIS,
        )
        assert exc.schema_version == DATA_QUALITY_SCHEMA_VERSION
        assert exc.status == "open"

    def test_with_optional_fields(self):
        exc = DataQualityException(
            subject_kind=SubjectKind.OPPORTUNITY,
            subject_id="finep:589",
            field_path="deadline",
            issue_code=IssueCode.TEMPORAL_STATUS_CONFLICT,
            produced_state=FactState.CONFLICTING,
            produced_value="2026-12-31",
        )
        assert exc.produced_state is FactState.CONFLICTING

    def test_invalid_subject_kind_rejected(self):
        with pytest.raises(ValidationError):
            DataQualityException(
                subject_kind="invalid",
                subject_id="finep:589",
                field_path="deadline",
                issue_code=IssueCode.TEMPORAL_STATUS_WITHOUT_BASIS,
            )

    def test_all_subject_kinds_accepted(self):
        for kind in SubjectKind:
            exc = DataQualityException(
                subject_kind=kind,
                subject_id="test:id",
                field_path="deadline",
                issue_code=IssueCode.TEMPORAL_STATUS_WITHOUT_BASIS,
            )
            assert exc.subject_kind is kind

    def test_invalid_fact_state_rejected(self):
        with pytest.raises(ValidationError):
            DataQualityException(
                subject_kind=SubjectKind.OPPORTUNITY,
                subject_id="finep:589",
                field_path="deadline",
                issue_code=IssueCode.TEMPORAL_STATUS_WITHOUT_BASIS,
                produced_state="invalid_state",
            )

    def test_empty_subject_id_rejected(self):
        with pytest.raises(ValidationError):
            DataQualityException(
                subject_kind=SubjectKind.OPPORTUNITY,
                subject_id="",
                field_path="deadline",
                issue_code=IssueCode.TEMPORAL_STATUS_WITHOUT_BASIS,
            )

    def test_empty_field_path_rejected(self):
        with pytest.raises(ValidationError):
            DataQualityException(
                subject_kind=SubjectKind.OPPORTUNITY,
                subject_id="x",
                field_path="",
                issue_code=IssueCode.TEMPORAL_STATUS_WITHOUT_BASIS,
            )

    def test_invalid_issue_code_rejected(self):
        with pytest.raises(ValidationError):
            DataQualityException(
                subject_kind=SubjectKind.OPPORTUNITY,
                subject_id="x",
                field_path="deadline",
                issue_code="unknown_code",
            )

    def test_extra_field_rejected(self):
        with pytest.raises(ValidationError):
            DataQualityException(
                subject_kind=SubjectKind.OPPORTUNITY,
                subject_id="x",
                field_path="deadline",
                issue_code=IssueCode.TEMPORAL_STATUS_WITHOUT_BASIS,
                score=0.5,
            )

    def test_roundtrip(self):
        exc = DataQualityException(
            subject_kind=SubjectKind.OPPORTUNITY,
            subject_id="finep:589",
            field_path="deadline",
            issue_code=IssueCode.TEMPORAL_STATUS_CONFLICT,
            produced_state=FactState.CONFLICTING,
            produced_value="2026-12-31",
        )
        again = DataQualityException.model_validate(exc.model_dump())
        assert again == exc


# ---------------------------------------------------------------------------
# DataQualityReview — invariantes
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

    def test_correct_requires_evidence_refs(self):
        with pytest.raises(ValidationError, match="correct requires at least one evidence_ref"):
            DataQualityReview(
                exception_ref="exc-001",
                decision="correct",
                corrected_value="2026-12-31",
                justification="corrigindo prazo",
                review=ReviewInfo(
                    review_id="rev-002",
                    actor_id="admin",
                    reviewed_at="2026-07-29T12:00:00Z",
                ),
            )

    def test_correct_with_value_and_evidence_valid(self):
        review = DataQualityReview(
            exception_ref="exc-001",
            decision="correct",
            corrected_value="2026-12-31",
            justification="prazo correto conforme anexo",
            evidence_refs=[
                EvidenceRef(
                    source="finep",
                    canonical_content_hash="sha256:" + "c" * 64,
                    locator_quality=LocatorQuality.DOCUMENT_ONLY,
                    document="anexo.pdf",
                ),
            ],
            review=ReviewInfo(
                review_id="rev-003",
                actor_id="admin",
                reviewed_at="2026-07-29T12:00:00Z",
            ),
        )
        assert review.corrected_value == "2026-12-31"
        assert len(review.evidence_refs) == 1

    def test_correct_with_empty_corrected_value_rejected(self):
        with pytest.raises(ValidationError, match="corrected_value must not be empty"):
            DataQualityReview(
                exception_ref="exc-001",
                decision="correct",
                corrected_value="   ",
                justification="corrigindo",
                evidence_refs=[
                    EvidenceRef(
                        source="finep",
                        canonical_content_hash="sha256:" + "d" * 64,
                        locator_quality=LocatorQuality.DOCUMENT_ONLY,
                        document="doc.pdf",
                    ),
                ],
                review=ReviewInfo(
                    review_id="rev-003",
                    actor_id="admin",
                    reviewed_at="2026-07-29T12:00:00Z",
                ),
            )

    def test_confirm_continuous_requires_evidence_refs(self):
        with pytest.raises(ValidationError, match="confirm_continuous requires at least one evidence_ref"):
            DataQualityReview(
                exception_ref="exc-002",
                decision="confirm_continuous",
                justification="fluxo continuo comprovado",
                review=ReviewInfo(
                    review_id="rev-004",
                    actor_id="admin",
                    reviewed_at="2026-07-29T12:00:00Z",
                ),
            )

    def test_confirm_continuous_with_evidence_valid(self):
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

    def test_corrected_value_on_confirm_rejected(self):
        with pytest.raises(ValidationError, match="corrected_value is not allowed"):
            DataQualityReview(
                exception_ref="exc-001",
                decision="confirm",
                corrected_value="2026-12-31",
                justification="prazo confirmado",
                review=ReviewInfo(
                    review_id="rev-006",
                    actor_id="admin",
                    reviewed_at="2026-07-29T12:00:00Z",
                ),
            )

    def test_corrected_value_on_mark_unknown_rejected(self):
        with pytest.raises(ValidationError, match="corrected_value is not allowed"):
            DataQualityReview(
                exception_ref="exc-001",
                decision="mark_unknown",
                corrected_value="2026-12-31",
                justification="desconhecido",
                review=ReviewInfo(
                    review_id="rev-007",
                    actor_id="admin",
                    reviewed_at="2026-07-29T12:00:00Z",
                ),
            )

    def test_corrected_value_on_confirm_continuous_rejected(self):
        with pytest.raises(ValidationError, match="corrected_value is not allowed"):
            DataQualityReview(
                exception_ref="exc-001",
                decision="confirm_continuous",
                corrected_value="continuo",
                justification="continuo",
                evidence_refs=[
                    EvidenceRef(
                        source="finep",
                        canonical_content_hash="sha256:" + "e" * 64,
                        locator_quality=LocatorQuality.DOCUMENT_ONLY,
                        document="doc.pdf",
                    ),
                ],
                review=ReviewInfo(
                    review_id="rev-008",
                    actor_id="admin",
                    reviewed_at="2026-07-29T12:00:00Z",
                ),
            )

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
# Data Quality schema version fixo
# ---------------------------------------------------------------------------


class TestDataQualitySchemaVersionFixed:
    def test_schema_version_default_and_fixed(self):
        exc = DataQualityException(
            subject_kind=SubjectKind.OPPORTUNITY,
            subject_id="finep:589",
            field_path="deadline",
            issue_code=IssueCode.TEMPORAL_STATUS_WITHOUT_BASIS,
        )
        assert exc.schema_version == 1

    def test_schema_version_rejects_other_values(self):
        with pytest.raises(ValidationError):
            DataQualityException(
                schema_version=2,
                subject_kind=SubjectKind.OPPORTUNITY,
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
