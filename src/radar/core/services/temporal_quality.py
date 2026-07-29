from __future__ import annotations

import hashlib
import json
import logging
from datetime import date, datetime
from zoneinfo import ZoneInfo

from radar.core.services.data_quality_exceptions import (
    DataQualityStorageError,
    open_or_observe_exception,
)
from radar.domain.data_quality import (
    DataQualityException,
    ValidityState,
    evaluate_temporal,
)
from radar.domain.provenance import EvidenceRef, FactState
from radar.domain.source_bundle import SubjectKind

logger = logging.getLogger(__name__)

DETECTOR_PRODUCER_VERSION = "temporal_quality:v1"


def _today_brasilia() -> date:
    return datetime.now(ZoneInfo("America/Sao_Paulo")).date()


def _collect_ref_identities(refs: list[EvidenceRef]) -> list[str]:
    out = []
    for ref in refs:
        if ref.canonical_content_hash:
            out.append(ref.canonical_content_hash)
        if ref.silver_source_hash:
            out.append(ref.silver_source_hash)
        if ref.bundle_hash:
            out.append(ref.bundle_hash)
        if ref.content_hash:
            out.append(ref.content_hash)
    return out


def _build_temporal_fingerprint(
    deadline: date | None,
    status: str | None,
    evidence_hashes: list[str] | None = None,
    bundle_hash: str | None = None,
) -> str:
    material = {
        "producer_version": DETECTOR_PRODUCER_VERSION,
        "deadline": deadline.isoformat() if deadline else None,
        "status": (status or "").strip().lower() or None,
        "evidence_hashes": sorted(set(evidence_hashes or [])),
        "bundle_hash": bundle_hash,
    }
    raw = json.dumps(material, sort_keys=True, ensure_ascii=False)
    return "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


def detect_temporal_exception(
    *,
    subject_id: str,
    deadline: date | None,
    status: str | None,
    as_of: date,
    continuous_evidence: EvidenceRef | None = None,
    evidence_refs: list[EvidenceRef] | None = None,
    bundle_hash: str | None = None,
) -> DataQualityException | None:
    evaluation = evaluate_temporal(
        deadline=deadline,
        status=status,
        as_of=as_of,
        continuous_evidence=continuous_evidence,
    )
    if evaluation.validity_state is not ValidityState.NEEDS_REVIEW:
        return None

    all_refs = list(evidence_refs or [])
    if continuous_evidence is not None and continuous_evidence not in all_refs:
        all_refs.append(continuous_evidence)

    evidence_identities = _collect_ref_identities(all_refs)
    norm_status = (status or "").strip().lower() or None

    fingerprint = _build_temporal_fingerprint(
        deadline=deadline,
        status=norm_status,
        evidence_hashes=evidence_identities,
        bundle_hash=bundle_hash,
    )
    produced = (
        deadline.isoformat() if deadline
        else status or "unknown"
    )
    return DataQualityException(
        subject_kind=SubjectKind.OPPORTUNITY,
        subject_id=subject_id,
        field_path="deadline",
        issue_code=evaluation.issue_code,
        produced_state=FactState.INFERRED,
        produced_value=produced,
        evidence_refs=all_refs,
        bundle_hash=bundle_hash,
        producer_version=DETECTOR_PRODUCER_VERSION,
        input_fingerprint=fingerprint,
        status="open",
    )


def check_edital_temporal_quality(
    *,
    subject_id: str,
    deadline: date | None,
    status: str | None,
    as_of: date | None = None,
    continuous_evidence: EvidenceRef | None = None,
    evidence_refs: list[EvidenceRef] | None = None,
    bundle_hash: str | None = None,
) -> None:
    if as_of is None:
        as_of = _today_brasilia()

    try:
        exception = detect_temporal_exception(
            subject_id=subject_id,
            deadline=deadline,
            status=status,
            as_of=as_of,
            continuous_evidence=continuous_evidence,
            evidence_refs=evidence_refs,
            bundle_hash=bundle_hash,
        )
        if exception is not None:
            open_or_observe_exception(exception)
            logger.info(
                "temporal_quality: subject=%s issue=%s",
                subject_id,
                exception.issue_code.value,
            )
    except (DataQualityStorageError, ValueError) as exc:
        logger.warning(
            "temporal_quality: storage error category=%s subject=%s",
            type(exc).__name__,
            subject_id,
        )
    except Exception as exc:
        logger.warning(
            "temporal_quality: unexpected error category=%s subject=%s",
            type(exc).__name__,
            subject_id,
        )


__all__ = [
    "DETECTOR_PRODUCER_VERSION",
    "_today_brasilia",
    "_collect_ref_identities",
    "_build_temporal_fingerprint",
    "detect_temporal_exception",
    "check_edital_temporal_quality",
]
