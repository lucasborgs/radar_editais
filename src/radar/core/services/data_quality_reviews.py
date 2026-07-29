from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel

from radar.core.services.data_quality_exceptions import (
    DataQualityStorageError,
    append_review,
    get_current_review_projection,
    get_exception,
    mark_exception_resolved,
)
from radar.domain.data_quality import (
    DataQualityReview,
    TemporalMode,
    ValidityState,
    evaluate_temporal,
)
from radar.domain.provenance import (
    DerivationInfo,
    EvidenceRef,
    FactProvenance,
    FactState,
    ProducerInfo,
    ProducerKind,
    ReviewInfo,
)

logger = logging.getLogger(__name__)

REVIEW_PRODUCER_VERSION = "data_quality_review:v1"
ReviewDecision = Literal[
    "confirm",
    "correct",
    "mark_unknown",
    "confirm_continuous",
]

_CLOSED_STATUSES = frozenset({
    "encerrada",
    "resultado_divulgado",
    "fechada",
    "closed",
    "finished",
})
_OPEN_STATUSES = frozenset({"aberta"})
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class ReviewValidationError(ValueError):
    """Decisão humana incompatível com a exceção ou sua evidência."""


class TemporalValidityProjection(BaseModel):
    model_config = {"extra": "forbid"}

    temporal_mode: TemporalMode
    validity_state: ValidityState
    value: str | None
    input_fingerprint: str
    exception_id: str | None = None
    review_id: str | None = None
    provenance: FactProvenance | None = None


def _today_sao_paulo() -> date:
    return datetime.now(ZoneInfo("America/Sao_Paulo")).date()


def _now_sao_paulo() -> datetime:
    return datetime.now(ZoneInfo("America/Sao_Paulo"))


def _parse_iso_date(value: str | None) -> date | None:
    if value is None or not _ISO_DATE_RE.fullmatch(value):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _evidence_key(ref: EvidenceRef) -> str:
    material = {
        key: value
        for key, value in ref.model_dump(mode="json").items()
        if key != "source_url"
    }
    return json.dumps(material, sort_keys=True, ensure_ascii=False)


def _exception_evidence(row: dict) -> list[EvidenceRef]:
    try:
        return [EvidenceRef(**item) for item in (row.get("evidence_refs") or [])]
    except (TypeError, ValueError) as exc:
        raise ReviewValidationError(
            "exception contains invalid evidence references"
        ) from exc


def _validate_linked_evidence(
    selected: list[EvidenceRef],
    available: list[EvidenceRef],
    *,
    required: bool,
) -> list[EvidenceRef]:
    if required and not selected:
        raise ReviewValidationError("decision requires linked versioned evidence")

    available_by_key = {_evidence_key(ref): ref for ref in available}
    preserved: list[EvidenceRef] = []
    seen: set[str] = set()
    for candidate in selected:
        key = _evidence_key(candidate)
        linked = available_by_key.get(key)
        if linked is None:
            raise ReviewValidationError(
                "evidence must already be linked to the current exception; "
                "new evidence must enter through its bundle or producer first"
            )
        if key not in seen:
            seen.add(key)
            preserved.append(linked)
    return preserved


def _validate_exception(row: dict, exception_id: str) -> None:
    if row.get("id") != exception_id:
        raise ReviewValidationError("loaded exception does not match exception_id")
    if row.get("field_path") != "deadline":
        raise ReviewValidationError("temporal reviews require field_path=deadline")
    if not row.get("input_fingerprint"):
        raise ReviewValidationError("exception requires input_fingerprint")
    if row.get("status") == "superseded":
        raise ReviewValidationError("superseded exception cannot be reviewed")
    if row.get("status") not in {"open", "resolved"}:
        raise ReviewValidationError("exception has invalid status")


def _validate_decision(
    *,
    row: dict,
    decision: ReviewDecision,
    corrected_value: str | None,
    evidence_refs: list[EvidenceRef],
) -> tuple[str | None, list[EvidenceRef]]:
    produced_value = row.get("produced_value")
    available = _exception_evidence(row)

    if decision == "mark_unknown":
        if corrected_value is not None:
            raise ReviewValidationError(
                "mark_unknown does not accept corrected_value"
            )
        return None, _validate_linked_evidence(
            evidence_refs, available, required=False
        )

    if decision == "confirm_continuous":
        if corrected_value is not None:
            raise ReviewValidationError(
                "confirm_continuous does not accept corrected_value"
            )
        linked = _validate_linked_evidence(
            evidence_refs, available, required=True
        )
        if any(not ref.quote or not ref.quote.strip() for ref in linked):
            raise ReviewValidationError(
                "confirm_continuous requires linked evidence with a non-empty quote"
            )
        return None, linked

    if decision == "correct":
        corrected_date = _parse_iso_date(corrected_value)
        if corrected_date is None:
            raise ReviewValidationError(
                "correct requires corrected_value in YYYY-MM-DD format"
            )
        linked = _validate_linked_evidence(
            evidence_refs, available, required=True
        )
        return corrected_date.isoformat(), linked

    if corrected_value is not None:
        raise ReviewValidationError("confirm does not accept corrected_value")
    if not isinstance(produced_value, str) or not produced_value.strip():
        raise ReviewValidationError("confirm requires a recoverable produced value")

    linked = _validate_linked_evidence(
        evidence_refs, available, required=True
    )
    normalized = produced_value.strip()
    if _parse_iso_date(normalized) is not None:
        return normalized, linked
    status = normalized.lower()
    if status in _CLOSED_STATUSES:
        return normalized, linked
    if status in _OPEN_STATUSES:
        raise ReviewValidationError(
            "open status without deadline cannot be confirmed as active"
        )
    raise ReviewValidationError(
        "confirm supports only an ISO date or a closed status"
    )


def _human_provenance(
    *,
    row: dict,
    review: DataQualityReview,
) -> FactProvenance:
    state = (
        FactState.UNKNOWN
        if review.decision == "mark_unknown"
        else FactState.STATED
    )
    return FactProvenance(
        state=state,
        evidence_refs=review.evidence_refs,
        producer=ProducerInfo(
            kind=ProducerKind.HUMAN,
            name="data_quality_review",
            version=REVIEW_PRODUCER_VERSION,
        ),
        derivation=DerivationInfo(
            inputs=[
                f"exception:{review.exception_ref}",
                f"input_fingerprint:{row['input_fingerprint']}",
                f"previous_value:{row.get('produced_value') or 'null'}",
            ],
            rule=f"temporal_review:{review.decision}",
        ),
        review=review.review,
        updated_at=review.review.reviewed_at,
    )


def _projection_from_review(
    *,
    row: dict,
    review: DataQualityReview,
    as_of: date,
) -> TemporalValidityProjection:
    if review.decision == "mark_unknown":
        mode = TemporalMode.UNKNOWN
        validity = ValidityState.NEEDS_REVIEW
        value = None
    elif review.decision == "confirm_continuous":
        mode = TemporalMode.CONTINUOUS
        validity = ValidityState.ACTIVE
        value = None
    else:
        value = (
            review.corrected_value
            if review.decision == "correct"
            else row.get("produced_value")
        )
        parsed = _parse_iso_date(value)
        if parsed is not None:
            mode = TemporalMode.FIXED
            validity = (
                ValidityState.ACTIVE
                if parsed >= as_of
                else ValidityState.CLOSED
            )
        else:
            mode = TemporalMode.UNKNOWN
            validity = ValidityState.CLOSED

    return TemporalValidityProjection(
        temporal_mode=mode,
        validity_state=validity,
        value=value,
        input_fingerprint=row["input_fingerprint"],
        exception_id=row["id"],
        review_id=review.review.review_id,
        provenance=_human_provenance(row=row, review=review),
    )


def _validate_review_link(row: dict, review: DataQualityReview) -> None:
    if review.exception_ref != row["id"]:
        raise ReviewValidationError(
            "review does not reference the current exception"
        )


def review_temporal_exception(
    *,
    exception_id: str,
    review_id: str,
    actor_id: str,
    decision: ReviewDecision,
    justification: str,
    corrected_value: str | None = None,
    evidence_refs: list[EvidenceRef] | None = None,
    reviewed_at: datetime | None = None,
    as_of: date | None = None,
) -> TemporalValidityProjection:
    """Valida e registra uma decisão temporal na ordem append -> resolved."""
    row = get_exception(exception_id)
    if row is None:
        raise ReviewValidationError("exception not found")
    _validate_exception(row, exception_id)

    existing_review = get_current_review_projection(exception_id)
    if existing_review is not None:
        _validate_review_link(row, existing_review)
        if existing_review.review.review_id != review_id:
            raise ReviewValidationError("exception already has a different review")
        effective_reviewed_at = existing_review.review.reviewed_at
    else:
        if row["status"] == "resolved":
            raise ReviewValidationError("resolved exception has no review")
        effective_reviewed_at = reviewed_at or _now_sao_paulo()

    value, linked_evidence = _validate_decision(
        row=row,
        decision=decision,
        corrected_value=corrected_value,
        evidence_refs=list(evidence_refs or []),
    )
    review = DataQualityReview(
        exception_ref=exception_id,
        decision=decision,
        corrected_value=value if decision == "correct" else None,
        justification=justification,
        evidence_refs=linked_evidence,
        review=ReviewInfo(
            review_id=review_id,
            actor_id=actor_id,
            reviewed_at=effective_reviewed_at,
            overridden=decision == "correct",
        ),
    )

    if not append_review(review):
        raise DataQualityStorageError("append_review failed")
    if not mark_exception_resolved(exception_id):
        raise DataQualityStorageError("mark_exception_resolved failed")

    return _projection_from_review(
        row=row,
        review=review,
        as_of=as_of or _today_sao_paulo(),
    )


def _conservative_projection(
    *,
    value: str | None,
    input_fingerprint: str,
    exception_id: str | None,
    original_provenance: FactProvenance | None,
) -> TemporalValidityProjection:
    return TemporalValidityProjection(
        temporal_mode=TemporalMode.UNKNOWN,
        validity_state=ValidityState.NEEDS_REVIEW,
        value=value,
        input_fingerprint=input_fingerprint,
        exception_id=exception_id,
        provenance=original_provenance,
    )


def project_temporal_validity(
    *,
    deadline: date | None,
    status: str | None,
    input_fingerprint: str,
    exception_id: str | None = None,
    original_provenance: FactProvenance | None = None,
    as_of: date | None = None,
) -> TemporalValidityProjection:
    """Projeta validade temporal sem conceder ``active`` em falha de leitura."""
    if not input_fingerprint or not input_fingerprint.strip():
        raise ValueError("input_fingerprint must be non-empty")

    try:
        row = get_exception(exception_id) if exception_id is not None else None
        review = (
            get_current_review_projection(exception_id)
            if exception_id is not None and row is not None and row.get("status") == "resolved"
            else None
        )
    except Exception as exc:  # noqa: BLE001 — the single-reader path also fails closed
        logger.warning(
            "temporal_validity_projection: read failure category=%s exception_id=%s",
            type(exc).__name__, exception_id,
        )
        return _conservative_projection(
            value=deadline.isoformat() if deadline else (status.strip() if status else None),
            input_fingerprint=input_fingerprint,
            exception_id=exception_id,
            original_provenance=original_provenance,
        )
    return project_loaded_temporal_validity(
        deadline=deadline,
        status=status,
        input_fingerprint=input_fingerprint,
        exception_row=row,
        expected_exception_id=exception_id,
        review=review,
        original_provenance=original_provenance,
        as_of=as_of,
    )


def project_loaded_temporal_validity(
    *,
    deadline: date | None,
    status: str | None,
    input_fingerprint: str,
    exception_row: dict | None,
    expected_exception_id: str | None = None,
    review: DataQualityReview | None,
    original_provenance: FactProvenance | None = None,
    as_of: date | None = None,
) -> TemporalValidityProjection:
    """Projeção T04 sobre registros já carregados pelo leitor em lote.

    Não acessa armazenamento.  Qualquer linha ausente, incompatível ou inválida
    conserva ``needs_review`` para que consumidores não concedam atividade.
    """
    if not input_fingerprint or not input_fingerprint.strip():
        raise ValueError("input_fingerprint must be non-empty")
    effective_as_of = as_of or _today_sao_paulo()
    value = deadline.isoformat() if deadline else (status.strip() if status else None)
    evaluation = evaluate_temporal(deadline=deadline, status=status, as_of=effective_as_of)
    exception_id = exception_row.get("id") if exception_row else None

    if exception_row is None:
        if expected_exception_id is not None:
            return _conservative_projection(
                value=value, input_fingerprint=input_fingerprint,
                exception_id=expected_exception_id, original_provenance=original_provenance,
            )
        if evaluation.validity_state is ValidityState.NEEDS_REVIEW:
            return _conservative_projection(
                value=value, input_fingerprint=input_fingerprint,
                exception_id=None, original_provenance=original_provenance,
            )
        return TemporalValidityProjection(
            temporal_mode=evaluation.temporal_mode,
            validity_state=evaluation.validity_state,
            value=value,
            input_fingerprint=input_fingerprint,
            provenance=original_provenance,
        )

    try:
        if not exception_id:
            raise ReviewValidationError("loaded exception requires id")
        _validate_exception(exception_row, exception_id)
        if exception_row["input_fingerprint"] != input_fingerprint:
            raise ReviewValidationError("loaded exception has a stale fingerprint")
        if exception_row["status"] != "resolved" or review is None:
            raise ReviewValidationError("exception has no resolved review")
        _validate_review_link(exception_row, review)
        _validate_decision(
            row=exception_row,
            decision=review.decision,
            corrected_value=review.corrected_value,
            evidence_refs=review.evidence_refs,
        )
        return _projection_from_review(
            row=exception_row, review=review, as_of=effective_as_of,
        )
    except Exception as exc:  # noqa: BLE001 — read model must fail closed
        logger.warning(
            "temporal_validity_projection: loaded read failure category=%s exception_id=%s",
            type(exc).__name__, exception_id,
        )
        return _conservative_projection(
            value=value, input_fingerprint=input_fingerprint,
            exception_id=exception_id, original_provenance=original_provenance,
        )


__all__ = [
    "REVIEW_PRODUCER_VERSION",
    "ReviewValidationError",
    "TemporalValidityProjection",
    "review_temporal_exception",
    "project_temporal_validity",
    "project_loaded_temporal_validity",
]
