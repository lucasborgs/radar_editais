"""RT04-T06-A — projeção corrente e resolução conservadora de claims explícitos.

Read model puro: opera somente sobre `SourceBundle` já validado e `complete`,
sem alterar bundle persistido, sem extrair texto e sem inferir consolidação.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from radar.domain.provenance import EvidenceRef, FactState, LocatorQuality
from radar.domain.source_bundle import (
    AcquisitionStatus,
    AuthorityState,
    DocumentMetadata,
    DocumentRole,
    SourceBundle,
    SubjectKind,
)

_OFFICIAL_ROLES = frozenset({
    DocumentRole.BASE_NOTICE,
    DocumentRole.OPPORTUNITY_PAGE,
    DocumentRole.PROGRAM_PAGE,
    DocumentRole.ANNEX,
    DocumentRole.AMENDMENT,
    DocumentRole.OFFICIAL_PAGE,
    DocumentRole.FAQ,
    DocumentRole.OFFICIAL_RECORD,
})


@dataclass(frozen=True)
class ExplicitClaim:
    field: str
    value: Any
    content_hash: str
    supersedes_content_hash: str | None = None


@dataclass(frozen=True)
class ClaimSupport:
    claim: ExplicitClaim
    document: DocumentMetadata


@dataclass
class FieldResolution:
    field: str
    state: FactState
    value: Any | None = None
    evidence_refs: list[EvidenceRef] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class BundleProjection:
    bundle: SourceBundle
    documents: tuple[DocumentMetadata, ...]

    @property
    def by_content_hash(self) -> dict[str, DocumentMetadata]:
        return {doc.content_hash: doc for doc in self.documents}


def project_current_documents(bundle: SourceBundle) -> BundleProjection:
    _require_complete_bundle(bundle)
    documents = tuple(
        doc for doc in bundle.documents
        if doc.authority_state is not AuthorityState.SUPERSEDED
    )
    return BundleProjection(bundle=bundle, documents=documents)


def resolve_field(bundle: SourceBundle, claims: list[ExplicitClaim], field: str) -> FieldResolution:
    projection = project_current_documents(bundle)
    field_claims = [claim for claim in claims if claim.field == field]
    if not field_claims:
        return FieldResolution(
            field=field,
            state=FactState.UNKNOWN,
            limitations=["Sem claims explicitos para o campo."],
        )

    valid_supports: list[ClaimSupport] = []
    limitations: list[str] = []
    documents = projection.by_content_hash
    for claim in field_claims:
        document = documents.get(claim.content_hash)
        if document is None:
            limitations.append(
                f"Claim ignorado: documento ausente ou fora da projecao corrente ({claim.content_hash})."
            )
            continue
        valid_supports.append(ClaimSupport(claim=claim, document=document))

    if not valid_supports:
        return FieldResolution(
            field=field,
            state=FactState.UNKNOWN,
            limitations=limitations or ["Nenhum claim autorizado para o campo."],
        )

    grouped = _group_by_value(valid_supports)
    if len(grouped) == 1:
        group = grouped[0]
        return FieldResolution(
            field=field,
            state=FactState.STATED,
            value=group[0].claim.value,
            evidence_refs=_evidence_refs(bundle, group),
            limitations=limitations,
        )

    winner = _resolve_precedence(grouped)
    if winner is not None:
        return FieldResolution(
            field=field,
            state=FactState.STATED,
            value=winner[0].claim.value,
            evidence_refs=_evidence_refs(bundle, winner),
            limitations=limitations,
        )

    return FieldResolution(
        field=field,
        state=FactState.CONFLICTING,
        evidence_refs=_evidence_refs(bundle, [support for group in grouped for support in group]),
        limitations=limitations or ["Claims incompativeis sem precedencia confiavel."],
    )


def resolve_all(bundle: SourceBundle, claims: list[ExplicitClaim]) -> dict[str, FieldResolution]:
    fields = list(dict.fromkeys(claim.field for claim in claims))
    return {field: resolve_field(bundle, claims, field) for field in fields}


def _require_complete_bundle(bundle: SourceBundle) -> None:
    if bundle.acquisition_status is not AcquisitionStatus.COMPLETE:
        raise ValueError("BundleProjection requires a complete SourceBundle")


def _group_by_value(supports: list[ClaimSupport]) -> list[list[ClaimSupport]]:
    by_value: dict[str, list[ClaimSupport]] = {}
    for support in supports:
        key = json.dumps(support.claim.value, sort_keys=True, ensure_ascii=False)
        by_value.setdefault(key, []).append(support)
    return list(by_value.values())


def _resolve_precedence(groups: list[list[ClaimSupport]]) -> list[ClaimSupport] | None:
    winners: list[list[ClaimSupport]] = []
    for candidate in groups:
        if all(candidate is other or _group_dominates(candidate, other) for other in groups):
            winners.append(candidate)
    return winners[0] if len(winners) == 1 else None


def _group_dominates(candidate: list[ClaimSupport], other: list[ClaimSupport]) -> bool:
    return all(
        any(_support_precedes(winner, loser) for winner in candidate)
        for loser in other
    )


def _support_precedes(winner: ClaimSupport, loser: ClaimSupport) -> bool:
    if (
        winner.document.role is DocumentRole.AMENDMENT
        and winner.claim.supersedes_content_hash == loser.document.content_hash
        and winner.document.amends_content_hash == loser.document.content_hash
    ):
        return True
    if (
        winner.document.role is DocumentRole.OPPORTUNITY_PAGE
        and loser.document.role is DocumentRole.PROGRAM_PAGE
    ):
        return True
    if winner.document.role in _OFFICIAL_ROLES and loser.document.role is DocumentRole.CURATED_RECORD:
        return True
    return False


def _evidence_refs(bundle: SourceBundle, supports: list[ClaimSupport]) -> list[EvidenceRef]:
    refs: list[EvidenceRef] = []
    seen: set[tuple[str, str]] = set()
    for support in supports:
        doc = support.document
        key = (doc.content_hash, doc.doc_name)
        if key in seen:
            continue
        seen.add(key)
        ref_kwargs = {
            "source": bundle.source,
            "source_url": doc.source_url,
            "document": doc.doc_name,
            "canonical_content_hash": doc.content_hash,
            "collected_at": bundle.collected_at,
            "locator_quality": LocatorQuality.DOCUMENT_ONLY,
        }
        if bundle.subject_kind is SubjectKind.OPPORTUNITY:
            ref_kwargs["edital_id"] = bundle.subject_id
        else:
            ref_kwargs["native_id"] = bundle.subject_id
        refs.append(EvidenceRef(**ref_kwargs))
    return refs


__all__ = [
    "BundleProjection",
    "ClaimSupport",
    "ExplicitClaim",
    "FieldResolution",
    "project_current_documents",
    "resolve_all",
    "resolve_field",
]
