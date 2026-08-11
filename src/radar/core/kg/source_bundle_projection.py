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
    evidence_refs: tuple[EvidenceRef, ...] = ()


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
        # Kept for compatibility where the hash is known to be unique.  The
        # resolver below uses the plural index so identical content in two
        # document identities is never silently collapsed.
        return {
            content_hash: docs[0]
            for content_hash, docs in self.documents_by_content_hash.items()
            if len(docs) == 1
        }

    @property
    def documents_by_content_hash(self) -> dict[str, list[DocumentMetadata]]:
        out: dict[str, list[DocumentMetadata]] = {}
        for doc in self.documents:
            out.setdefault(doc.content_hash, []).append(doc)
        return out


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
    documents = projection.documents_by_content_hash
    for claim in field_claims:
        candidates = documents.get(claim.content_hash, [])
        if len(candidates) > 1:
            named = {
                ref.document for ref in claim.evidence_refs
                if ref.document
            }
            candidates = [doc for doc in candidates if doc.doc_name in named]
        if len(candidates) != 1:
            limitations.append(
                f"Claim ignorado: documento ausente, ambiguo ou fora da projecao corrente ({claim.content_hash})."
            )
            continue
        document = candidates[0]
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


def current_complete_bundle(bundle: SourceBundle | None) -> SourceBundle | None:
    if bundle is None or bundle.acquisition_status is not AcquisitionStatus.COMPLETE:
        return None
    return bundle


def find_current_document(
    bundle: SourceBundle,
    *,
    content_hash: str,
    doc_name: str | None = None,
) -> DocumentMetadata | None:
    projection = project_current_documents(bundle)
    matches = [
        doc
        for doc in projection.documents
        if doc.content_hash == content_hash and (doc_name is None or doc.doc_name == doc_name)
    ]
    return matches[0] if len(matches) == 1 else None


def attach_bundle_lineage(
    ref: EvidenceRef,
    *,
    bundle: SourceBundle,
    document: DocumentMetadata,
) -> EvidenceRef:
    complete_bundle = current_complete_bundle(bundle)
    if complete_bundle is None:
        raise ValueError("Cannot attach bundle lineage: bundle is not complete")
    current = find_current_document(
        complete_bundle,
        content_hash=document.content_hash,
        doc_name=document.doc_name,
    )
    if current is None:
        raise ValueError(
            "Cannot attach bundle lineage: document is absent, ambiguous, or superseded"
        )
    return ref.model_copy(
        update={
            "bundle_hash": complete_bundle.compute_bundle_hash(),
            "content_hash": current.content_hash,
        }
    )


def attach_bundle_metadata_to_documents(
    documents: list[dict],
    bundle: SourceBundle | None,
) -> list[dict]:
    bundle = current_complete_bundle(bundle)
    if bundle is None:
        return documents
    bundle_hash = bundle.compute_bundle_hash()
    enriched: list[dict] = []
    for entry in documents:
        current = find_current_document(
            bundle,
            content_hash=_content_hash_for_units(entry.get("units") or []),
            doc_name=entry.get("doc_name"),
        )
        if current is None:
            enriched.append(entry)
            continue
        metadata = dict(entry.get("metadata") or {})
        metadata.update({
            "bundle_hash": bundle_hash,
            "content_hash": current.content_hash,
        })
        enriched.append({**entry, "metadata": metadata})
    return enriched


def _require_complete_bundle(bundle: SourceBundle) -> None:
    if bundle.acquisition_status is not AcquisitionStatus.COMPLETE:
        raise ValueError("BundleProjection requires a complete SourceBundle")


def _content_hash_for_units(units: list[str]) -> str:
    from radar.domain.source_bundle import compute_content_hash

    return compute_content_hash(units)


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
    seen: set[tuple[str, str, int | None, int | None, str | None]] = set()
    for support in supports:
        doc = support.document
        claim_refs = list(support.claim.evidence_refs)
        for original in claim_refs:
            if original.document not in {None, doc.doc_name}:
                continue
            try:
                enriched = attach_bundle_lineage(original, bundle=bundle, document=doc)
            except ValueError:
                continue
            key = (
                doc.content_hash,
                doc.doc_name,
                enriched.page,
                enriched.block_idx,
                enriched.quote,
            )
            if key not in seen:
                seen.add(key)
                refs.append(enriched)
        if claim_refs and any(ref.document in {None, doc.doc_name} for ref in claim_refs):
            continue
        key = (doc.content_hash, doc.doc_name, None, None, None)
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
        refs.append(
            attach_bundle_lineage(
                EvidenceRef(**ref_kwargs),
                bundle=bundle,
                document=doc,
            )
        )
    return refs


__all__ = [
    "BundleProjection",
    "ClaimSupport",
    "ExplicitClaim",
    "FieldResolution",
    "attach_bundle_metadata_to_documents",
    "attach_bundle_lineage",
    "current_complete_bundle",
    "find_current_document",
    "project_current_documents",
    "resolve_all",
    "resolve_field",
]
