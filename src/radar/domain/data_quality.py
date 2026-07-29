from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from radar.domain.provenance import EvidenceRef, FactState, ReviewInfo
from radar.domain.source_bundle import SubjectKind

DATA_QUALITY_SCHEMA_VERSION: Literal[1] = 1


class TemporalMode(str, Enum):
    FIXED = "fixed"
    CONTINUOUS = "continuous"
    UNKNOWN = "unknown"


class ValidityState(str, Enum):
    ACTIVE = "active"
    CLOSED = "closed"
    NEEDS_REVIEW = "needs_review"


class IssueCode(str, Enum):
    FACT_CONFLICT = "fact_conflict"
    CRITICAL_FACT_MISSING = "critical_fact_missing"
    VALIDATION_FAILED = "validation_failed"
    EVIDENCE_UNRESOLVED = "evidence_unresolved"
    TEMPORAL_STATUS_WITHOUT_BASIS = "temporal_status_without_basis"
    TEMPORAL_STATUS_CONFLICT = "temporal_status_conflict"


_OPEN_STATUSES = frozenset({"aberta"})
_CLOSED_STATUSES = frozenset({
    "encerrada", "resultado_divulgado", "fechada", "closed", "finished",
})


class TemporalEvaluation(BaseModel):
    model_config = {"extra": "forbid"}

    temporal_mode: TemporalMode
    validity_state: ValidityState
    issue_code: IssueCode | None = None
    issue_description: str | None = None

    @model_validator(mode="after")
    def _check_issue_consistency(self) -> TemporalEvaluation:
        if self.validity_state is ValidityState.NEEDS_REVIEW:
            if self.issue_code is None:
                raise ValueError("needs_review requires issue_code")
            if not self.issue_description:
                raise ValueError("needs_review requires issue_description")
        else:
            if self.issue_code is not None:
                raise ValueError(
                    f"active/closed must not have issue_code, "
                    f"got {self.issue_code}"
                )
            if self.issue_description is not None:
                raise ValueError(
                    "active/closed must not have issue_description"
                )
        return self


class DataQualityException(BaseModel):
    model_config = {"extra": "forbid"}

    schema_version: Literal[1] = DATA_QUALITY_SCHEMA_VERSION
    subject_kind: SubjectKind
    subject_id: str
    field_path: str
    issue_code: IssueCode
    produced_state: FactState | None = None
    produced_value: str | None = None
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    bundle_hash: str | None = None
    producer_version: str | None = None
    input_fingerprint: str | None = None
    status: Literal["open", "resolved", "superseded"] = "open"
    detected_at: datetime | None = None
    last_observed_at: datetime | None = None

    @field_validator("subject_id")
    @classmethod
    def _subject_id_non_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("subject_id must be non-empty")
        return v

    @field_validator("field_path")
    @classmethod
    def _field_path_non_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("field_path must be non-empty")
        return v


class DataQualityReview(BaseModel):
    model_config = {"extra": "forbid"}

    schema_version: Literal[1] = DATA_QUALITY_SCHEMA_VERSION
    exception_ref: str
    decision: Literal["confirm", "correct", "mark_unknown", "confirm_continuous"]
    corrected_value: str | None = None
    justification: str
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    review: ReviewInfo

    @field_validator("justification")
    @classmethod
    def _justification_non_empty(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("justification must be non-empty")
        if len(stripped) > 2000:
            raise ValueError("justification must not exceed 2000 characters")
        return stripped

    @field_validator("exception_ref")
    @classmethod
    def _exception_ref_non_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("exception_ref must be non-empty")
        return v

    @model_validator(mode="after")
    def _check_review_invariants(self) -> DataQualityReview:
        if self.decision == "confirm_continuous" and not self.evidence_refs:
            raise ValueError(
                "confirm_continuous requires at least one evidence_ref"
            )

        if self.decision == "correct":
            if not self.corrected_value:
                raise ValueError(
                    "corrected_value is required when decision=correct"
                )
            if not self.corrected_value.strip():
                raise ValueError(
                    "corrected_value must not be empty or whitespace"
                )
            if not self.evidence_refs:
                raise ValueError(
                    "correct requires at least one evidence_ref"
                )

        if self.corrected_value is not None and self.decision != "correct":
            raise ValueError(
                f"corrected_value is not allowed for decision={self.decision}"
            )

        return self


def evaluate_temporal(
    *,
    deadline: date | None,
    status: str | None,
    as_of: date,
    continuous_evidence: EvidenceRef | None = None,
) -> TemporalEvaluation:
    status_lower = (status or "").strip().lower()

    is_open = status_lower in _OPEN_STATUSES
    is_closed = status_lower in _CLOSED_STATUSES

    deadline_present = deadline is not None
    is_future_deadline = deadline_present and deadline >= as_of
    is_past_deadline = deadline_present and deadline < as_of

    has_continuous_evidence = continuous_evidence is not None

    if deadline_present and is_closed and is_future_deadline:
        return TemporalEvaluation(
            temporal_mode=TemporalMode.FIXED,
            validity_state=ValidityState.NEEDS_REVIEW,
            issue_code=IssueCode.TEMPORAL_STATUS_CONFLICT,
            issue_description=(
                f"deadline={deadline} is future but status={status} "
                "indicates closed"
            ),
        )

    if deadline_present and is_open and is_past_deadline:
        return TemporalEvaluation(
            temporal_mode=TemporalMode.FIXED,
            validity_state=ValidityState.NEEDS_REVIEW,
            issue_code=IssueCode.TEMPORAL_STATUS_CONFLICT,
            issue_description=(
                f"deadline={deadline} is past but status={status} "
                "indicates open"
            ),
        )

    if deadline_present and is_future_deadline:
        return TemporalEvaluation(
            temporal_mode=TemporalMode.FIXED,
            validity_state=ValidityState.ACTIVE,
        )

    if deadline_present and is_past_deadline:
        return TemporalEvaluation(
            temporal_mode=TemporalMode.FIXED,
            validity_state=ValidityState.CLOSED,
        )

    if has_continuous_evidence:
        return TemporalEvaluation(
            temporal_mode=TemporalMode.CONTINUOUS,
            validity_state=ValidityState.ACTIVE,
        )

    if is_closed:
        return TemporalEvaluation(
            temporal_mode=TemporalMode.UNKNOWN,
            validity_state=ValidityState.CLOSED,
        )

    if is_open:
        return TemporalEvaluation(
            temporal_mode=TemporalMode.UNKNOWN,
            validity_state=ValidityState.NEEDS_REVIEW,
            issue_code=IssueCode.TEMPORAL_STATUS_WITHOUT_BASIS,
            issue_description=(
                f"status={status} without deadline or continuous evidence"
            ),
        )

    return TemporalEvaluation(
        temporal_mode=TemporalMode.UNKNOWN,
        validity_state=ValidityState.NEEDS_REVIEW,
        issue_code=IssueCode.CRITICAL_FACT_MISSING,
        issue_description="no deadline, status, or continuous evidence available",
    )


__all__ = [
    "DATA_QUALITY_SCHEMA_VERSION",
    "TemporalMode",
    "ValidityState",
    "IssueCode",
    "TemporalEvaluation",
    "DataQualityException",
    "DataQualityReview",
    "evaluate_temporal",
]
