"""API administrativa de exceções de qualidade de dados (RT05-T05).

Rotas internas, protegidas por ``AdminUserId``:

- ``GET /data-quality/exceptions``
- ``GET /data-quality/exceptions/{exception_id}``
- ``POST /data-quality/exceptions/{exception_id}/reviews``

O router só orquestra os serviços de RT05-T02 a T04 e nunca reimplementa
regra temporal. Falhas de storage e validação são sanitizadas.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field, field_validator

from radar.core.infra.auth import AdminUserId
from radar.core.services.data_quality_exceptions import (
    DataQualityReviewConflictError,
    DataQualityStorageError,
    get_current_review_projection,
    get_exception,
    list_exceptions,
)
from radar.core.services.data_quality_reviews import (
    ReviewValidationError,
    review_temporal_exception,
)
from radar.domain.data_quality import IssueCode
from radar.domain.provenance import EvidenceRef, LocatorQuality
from radar.domain.source_bundle import SubjectKind

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/data-quality", tags=["data-quality"])

ExceptionState = Literal["open", "resolved", "superseded"]
ReviewDecision = Literal[
    "confirm",
    "correct",
    "mark_unknown",
    "confirm_continuous",
]


class SafeEvidenceRef(BaseModel):
    model_config = {"extra": "forbid"}

    schema_version: Literal[1]
    source: str
    native_id: str | None = None
    edital_id: str | None = None
    document: str | None = None
    page: int | None = None
    block_idx: int | None = None
    section_path: list[str] = Field(default_factory=list)
    quote: str | None = None
    canonical_content_hash: str | None = None
    silver_source_hash: str | None = None
    bundle_hash: str | None = None
    content_hash: str | None = None
    collected_at: datetime | None = None
    locator_quality: LocatorQuality = LocatorQuality.UNRESOLVED

    @field_validator("source")
    @classmethod
    def _source_non_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("source must be non-empty")
        return value


class DataQualityReviewIn(BaseModel):
    model_config = {"extra": "forbid"}

    review_id: str
    decision: ReviewDecision
    justification: str
    corrected_value: str | None = None
    evidence_refs: list[SafeEvidenceRef] = Field(default_factory=list)

    @field_validator("review_id")
    @classmethod
    def _review_id_non_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("review_id must be non-empty")
        return value

    @field_validator("justification")
    @classmethod
    def _justification_non_empty(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("justification must be non-empty")
        if len(stripped) > 2000:
            raise ValueError("justification must not exceed 2000 characters")
        return stripped


class DataQualityReviewOut(BaseModel):
    model_config = {"extra": "forbid"}

    review_id: str
    decision: ReviewDecision
    corrected_value: str | None = None
    reviewed_at: datetime
    evidence_refs: list[SafeEvidenceRef] = Field(default_factory=list)


class DataQualityExceptionOut(BaseModel):
    model_config = {"extra": "forbid"}

    id: str
    subject_kind: SubjectKind
    subject_id: str
    source: str | None = None
    field_path: str
    issue_code: IssueCode
    safe_value: str | None = None
    evidence_refs: list[SafeEvidenceRef] = Field(default_factory=list)
    impact: str
    state: ExceptionState
    bundle_hash: str | None = None
    producer_version: str | None = None
    detected_at: datetime | None = None
    last_observed_at: datetime | None = None
    current_review: DataQualityReviewOut | None = None


class DataQualityExceptionListOut(BaseModel):
    model_config = {"extra": "forbid"}

    items: list[DataQualityExceptionOut]
    limit: int
    offset: int
    has_more: bool
    next_offset: int | None = None


def _sanitize_storage_error(exc: Exception) -> HTTPException:
    logger.warning(
        "data-quality api storage failure category=%s",
        type(exc).__name__,
    )
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Falha ao consultar a fila de exceções.",
    )


def _sanitize_review_error(exc: Exception) -> HTTPException:
    logger.warning(
        "data-quality api review failure category=%s",
        type(exc).__name__,
    )
    if isinstance(exc, DataQualityReviewConflictError):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Conflito de revisão para esta exceção.",
        )
    if isinstance(exc, ReviewValidationError) and "not found" in str(exc).lower():
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Exceção de qualidade não encontrada.",
        )
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail="Revisão inválida para a exceção.",
    )


def _normalize_source(row: dict) -> str | None:
    refs = row.get("evidence_refs") or []
    sources: list[str] = []
    for ref in refs:
        if isinstance(ref, dict):
            source = ref.get("source")
        else:
            source = getattr(ref, "source", None)
        if isinstance(source, str) and source.strip():
            normalized = source.strip()
            if normalized not in sources:
                sources.append(normalized)
    if not sources:
        return None
    if len(sources) == 1:
        return sources[0]
    return "multiple"


def _normalize_evidence_refs(raw_refs: list[dict] | list[SafeEvidenceRef] | None) -> list[SafeEvidenceRef]:
    refs = []
    for raw in raw_refs or []:
        if isinstance(raw, SafeEvidenceRef):
            refs.append(raw)
            continue
        payload = dict(raw)
        payload.pop("source_url", None)
        refs.append(SafeEvidenceRef.model_validate(payload))
    return refs


def _normalize_review(raw: object | None) -> DataQualityReviewOut | None:
    if raw is None:
        return None
    raw_evidence_refs = getattr(raw, "evidence_refs", [])
    return DataQualityReviewOut(
        review_id=raw.review.review_id,
        decision=raw.decision,
        corrected_value=raw.corrected_value,
        reviewed_at=raw.review.reviewed_at,
        evidence_refs=_normalize_evidence_refs(raw_evidence_refs),
    )


def _impact_from_exception(row: dict, review: DataQualityReviewOut | None) -> str:
    state = (row.get("status") or "open").strip().lower()
    if state == "open":
        return "needs_review"
    if state == "superseded":
        return "superseded"
    if review is None:
        return "resolved"
    if review.decision == "correct":
        return "corrected"
    if review.decision == "confirm_continuous":
        return "continuous"
    if review.decision == "mark_unknown":
        return "unknown"
    return "confirmed"


def _exception_out(row: dict, *, current_review: DataQualityReviewOut | None) -> DataQualityExceptionOut:
    try:
        evidence_refs = _normalize_evidence_refs(row.get("evidence_refs"))
        safe_value = row.get("produced_value")
        if isinstance(safe_value, str) and not safe_value.strip():
            safe_value = None
        return DataQualityExceptionOut(
            id=row["id"],
            subject_kind=SubjectKind(row["subject_kind"]),
            subject_id=row["subject_id"],
            source=_normalize_source(row),
            field_path=row["field_path"],
            issue_code=IssueCode(row["issue_code"]),
            safe_value=safe_value,
            evidence_refs=evidence_refs,
            impact=_impact_from_exception(row, current_review),
            state=row["status"],
            bundle_hash=row.get("bundle_hash"),
            producer_version=row.get("producer_version"),
            detected_at=row.get("detected_at"),
            last_observed_at=row.get("last_observed_at"),
            current_review=current_review,
        )
    except Exception as exc:  # noqa: BLE001 - response must fail closed
        raise _sanitize_storage_error(exc) from exc


def _fetch_current_review(exception_id: str) -> DataQualityReviewOut | None:
    try:
        return _normalize_review(get_current_review_projection(exception_id))
    except DataQualityStorageError as exc:
        raise _sanitize_storage_error(exc) from exc


@router.get(
    "/exceptions",
    response_model=DataQualityExceptionListOut,
    summary="Lista exceções de qualidade",
)
def list_data_quality_exceptions(
    user_id: AdminUserId,
    status: ExceptionState | None = Query(default=None),
    code: IssueCode | None = Query(default=None, alias="code"),
    source: str | None = Query(default=None, min_length=1),
    field: str | None = Query(default=None, min_length=1),
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    """Lista a fila administrativa com filtros simples e limitados."""
    try:
        rows = list_exceptions(
            status=status,
            issue_code=code.value if code else None,
            source=source,
            field_path=field,
            limit=limit + 1,
            offset=offset,
        )
    except DataQualityStorageError as exc:
        raise _sanitize_storage_error(exc) from exc

    has_more = len(rows) > limit
    window = rows[:limit]
    items = [
        _exception_out(row, current_review=_fetch_current_review(row["id"]))
        for row in window
    ]
    next_offset = offset + limit if has_more else None
    return DataQualityExceptionListOut(
        items=items,
        limit=limit,
        offset=offset,
        has_more=has_more,
        next_offset=next_offset,
    )


@router.get(
    "/exceptions/{exception_id}",
    response_model=DataQualityExceptionOut,
    summary="Detalha uma exceção de qualidade",
)
def get_data_quality_exception(exception_id: str, user_id: AdminUserId):
    """Recupera uma exceção com sua revisão corrente, se existir."""
    try:
        row = get_exception(exception_id)
    except DataQualityStorageError as exc:
        raise _sanitize_storage_error(exc) from exc
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Exceção de qualidade não encontrada.",
        )
    return _exception_out(row, current_review=_fetch_current_review(exception_id))


@router.post(
    "/exceptions/{exception_id}/reviews",
    response_model=DataQualityExceptionOut,
    status_code=status.HTTP_201_CREATED,
    summary="Registra revisão administrativa",
)
def create_data_quality_review(
    exception_id: str,
    body: DataQualityReviewIn,
    user_id: AdminUserId,
):
    """Registra uma revisão temporal autenticada pela identidade admin."""
    try:
        projection = review_temporal_exception(
            exception_id=exception_id,
            review_id=body.review_id,
            actor_id=user_id,
            decision=body.decision,
            justification=body.justification,
            corrected_value=body.corrected_value,
            evidence_refs=[
                EvidenceRef.model_validate(
                    {**ref.model_dump(mode="python"), "source_url": None}
                )
                for ref in body.evidence_refs
            ],
        )
    except ReviewValidationError as exc:
        raise _sanitize_review_error(exc) from exc
    except DataQualityReviewConflictError as exc:
        raise _sanitize_review_error(exc) from exc
    except DataQualityStorageError as exc:
        raise _sanitize_storage_error(exc) from exc

    try:
        row = get_exception(exception_id)
    except DataQualityStorageError as exc:
        raise _sanitize_storage_error(exc) from exc
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Exceção de qualidade não encontrada.",
        )

    current_review = _fetch_current_review(exception_id)
    if current_review is None:
        reviewed_at = None
        if projection.provenance and projection.provenance.review:
            reviewed_at = projection.provenance.review.reviewed_at
        current_review = DataQualityReviewOut(
            review_id=projection.review_id or body.review_id,
            decision=body.decision,
            corrected_value=body.corrected_value,
            reviewed_at=reviewed_at or datetime.now(timezone.utc),
            evidence_refs=body.evidence_refs,
        )
    return _exception_out(row, current_review=current_review)
