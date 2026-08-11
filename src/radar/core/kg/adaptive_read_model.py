"""Read model único da RT06-T06/T07.

Consumidores recebem esta projeção; nenhum deles conhece a tabela de artifacts,
o parser ou a seleção de rota.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from collections.abc import Callable, Iterable
from datetime import date
from typing import Any

from pydantic import BaseModel, Field

from radar.core.services import document_extractions
from radar.core.services.temporal_quality import evaluate_temporal
from radar.core.services.temporal_read_model import today_sao_paulo
from radar.domain.adaptive_extraction import (
    ADAPTIVE_EXTRACTION_SCHEMA_VERSION,
    ADAPTIVE_PRODUCER_SCHEMA,
    ADAPTIVE_TEXT_PRODUCER_VERSION,
    FIELD_VALUE_TYPES,
    ExtractionArtifact,
    ExtractionStatus,
)
from radar.domain.adaptive_extraction import (
    FAMILY_FIELDS as CANONICAL_FAMILY_FIELDS,
)
from radar.domain.provenance import FactState, ProducerKind
from radar.domain.source_bundle import SourceBundle

logger = logging.getLogger(__name__)

INITIAL_FAMILY = "eligibility"
INITIAL_FAMILY_FIELDS = CANONICAL_FAMILY_FIELDS["eligibility"]
ALL_FAMILY_FIELDS = frozenset().union(*CANONICAL_FAMILY_FIELDS.values())
MATERIAL_CONFLICT_FIELDS = ALL_FAMILY_FIELDS - {"table_references"}
FAMILY_KEY = "family"
ACTIVE_FAMILIES_ENV = "RADAR_ADAPTIVE_ACTIVE_FAMILIES"
_RESOLVED_CLAIM_STATES = frozenset({FactState.STATED.value, FactState.ABSENT.value})


def _canonical_family(family: str) -> str:
    return family


def _fields_for_family(family: str) -> frozenset[str]:
    canonical = _canonical_family(family)
    try:
        return CANONICAL_FAMILY_FIELDS[canonical]
    except KeyError as exc:
        raise ValueError(f"unsupported adaptive family: {family}") from exc


def active_families() -> frozenset[str]:
    """Retorna as famílias adaptativas habilitadas, com default seguro off."""
    configured = {
        _canonical_family(item.strip())
        for item in os.getenv(ACTIVE_FAMILIES_ENV, "").split(",")
        if item.strip()
    }
    supported = set(CANONICAL_FAMILY_FIELDS)
    unknown = configured - supported
    if unknown:
        logger.warning("adaptive_read_model: famílias ignoradas=%s", sorted(unknown))
    return frozenset(configured & supported)


def family_is_active(family: str) -> bool:
    return _canonical_family(family) in active_families()


class AdaptiveReadModel(BaseModel):
    """Snapshot seguro para adapters de gold/Knowledge/pathways/Writing."""

    model_config = {"extra": "forbid"}

    subject_id: str
    family: str = INITIAL_FAMILY
    artifact_fingerprint: str | None = None
    claims: list[dict[str, Any]] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    needs_review: bool = False
    source_state: str = "unknown"
    temporal_state: str = "needs_review"

    def claim(self, field_path: str) -> dict[str, Any] | None:
        return next((claim for claim in self.claims if claim.get("field_path") == field_path), None)

    def public_claims(self) -> list[dict[str, Any]]:
        """Claims seguros para os consumidores, sem expor o runtime.

        A projeção precisa transportar também ``absent``, ``unknown``,
        ``inferred`` e ``conflicting``. O valor desses estados nunca é
        publicado: o consumidor recebe o estado, a proveniência e a lacuna,
        mas não pode tratar um candidato ou conflito como fato.
        """
        public: list[dict[str, Any]] = []
        for claim in self.claims:
            provenance = claim.get("provenance") or {}
            state = provenance.get("state")
            if state not in {item.value for item in FactState}:
                continue
            if (
                state == FactState.STATED.value
                and self.source_state != "legacy"
                and not _has_resolvable_evidence(claim)
            ):
                continue
            sanitized = dict(claim)
            if state != FactState.STATED.value:
                sanitized["value"] = None
            public.append(sanitized)
        return public

    @property
    def has_effective_projection(self) -> bool:
        """Indica que há uma projeção adaptativa para bloquear fallback factual.

        Um snapshot com lacunas continua sendo uma projeção efetiva parcial;
        ``needs_review`` não autoriza um consumidor a consultar o legado para
        preencher seus campos.
        """
        return self.source_state in {"legacy", "adaptive", "needs_review"}

    def consumer_payload(self) -> dict[str, Any]:
        """Contrato comum e somente-leitura para KG, Knowledge e escrita."""
        return {
            "subject_id": self.subject_id,
            "family": self.family,
            "claims": self.public_claims(),
            "gaps": list(self.gaps),
            "needs_review": self.needs_review,
            "source_state": self.source_state,
            "temporal_state": self.temporal_state,
        }


def _has_resolvable_evidence(claim: dict[str, Any]) -> bool:
    return any(
        ref.get("locator_quality") in {"exact", "document_only"}
        and (ref.get("canonical_content_hash") or ref.get("silver_source_hash"))
        for ref in (claim.get("provenance") or {}).get("evidence_refs", [])
        if isinstance(ref, dict)
    )


def _legacy_projection(
    subject_id: str,
    family: str,
    legacy_values: dict[str, Any] | None,
) -> AdaptiveReadModel:
    fields = _fields_for_family(family)
    claims: list[dict[str, Any]] = []
    for field_path, value in (legacy_values or {}).items():
        if field_path not in fields:
            continue
        state = FactState.ABSENT if value is None else FactState.STATED
        claims.append({
            "subject_id": subject_id,
            "field_path": field_path,
            "value": value,
            "provenance": {
                "state": state.value,
                "evidence_refs": [],
                "producer": {
                    "kind": ProducerKind.DETERMINISTIC.value,
                    "name": "legacy_gold",
                    "version": "legacy",
                },
            },
        })
    return AdaptiveReadModel(
        subject_id=subject_id,
        family=family,
        claims=claims,
        source_state="legacy",
    )


def _family_targets_compatible(
    artifact: ExtractionArtifact,
    family: str,
) -> bool:
    fields = _fields_for_family(family)
    targets = [target for target in artifact.targets_requested if target.field_path in fields]
    if not targets:
        return False
    return all(FIELD_VALUE_TYPES.get(target.field_path) == target.value_type for target in targets)


def _family_artifact_healthy(artifact: ExtractionArtifact, family: str) -> bool:
    if artifact.status not in {ExtractionStatus.COMPLETE, ExtractionStatus.PARTIAL}:
        return False
    fields = _fields_for_family(family)
    return not any(target in fields for target in artifact.unresolved_targets)


def select_compatible_artifacts(
    artifacts: list[ExtractionArtifact],
    family: str,
) -> list[ExtractionArtifact]:
    """Seleciona artifacts da configuração corrente da família.

    A saúde é calculada somente pelos alvos da família. Um artifact parcial por
    outra família continua utilizável quando não há lacuna nos seus alvos.
    """
    selected = [
        artifact for artifact in artifacts
        if (
            artifact.producer_versions.get("adaptive_text") == ADAPTIVE_TEXT_PRODUCER_VERSION
            and (
                artifact.producer_versions.get(FAMILY_KEY) is None
                or _canonical_family(artifact.producer_versions[FAMILY_KEY])
                == _canonical_family(family)
            )
        )
        and artifact.producer_versions.get("edital_extraction_schema") == ADAPTIVE_PRODUCER_SCHEMA
        and artifact.schema_version == ADAPTIVE_EXTRACTION_SCHEMA_VERSION
        and _family_targets_compatible(artifact, family)
        and _family_artifact_healthy(artifact, family)
    ]
    return sorted(selected, key=lambda artifact: (artifact.created_at, artifact.fingerprint))


def _latest_artifact_per_document(
    artifacts: list[ExtractionArtifact],
) -> list[ExtractionArtifact]:
    """Retém somente a tentativa saudável mais recente de cada documento."""
    latest: dict[str, ExtractionArtifact] = {}
    for artifact in artifacts:
        # V1 artifacts sem nome de documento ainda precisam coexistir quando
        # representam assets diferentes.
        key = artifact.document or artifact.asset_hash
        current = latest.get(key)
        if current is None or (artifact.created_at, artifact.fingerprint) > (
            current.created_at, current.fingerprint,
        ):
            latest[key] = artifact
    return sorted(latest.values(), key=lambda artifact: (artifact.created_at, artifact.fingerprint))


def _healthy_previous(
    previous: AdaptiveReadModel | None,
    family: str,
) -> AdaptiveReadModel | None:
    """Aceita somente snapshot adaptativo saudável da mesma família."""
    if (
        previous is None
        or _canonical_family(previous.family) != _canonical_family(family)
        or previous.source_state != "adaptive"
        or previous.needs_review
        or previous.gaps
    ):
        return None
    return previous


def _review_override(claim: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    decision = override.get("decision")
    updated = dict(claim)
    provenance = dict(updated.get("provenance") or {})
    if decision == "mark_unknown":
        updated["value"] = None
        provenance["state"] = FactState.UNKNOWN.value
    elif decision in {"confirm", "correct"}:
        refs = override.get("evidence_refs") or []
        valid_refs = [
            ref for ref in refs
            if ref.get("locator_quality") in {"exact", "document_only"}
            and (ref.get("canonical_content_hash") or ref.get("silver_source_hash"))
        ]
        converted = _coerce_structured_value(
            str(claim.get("field_path") or ""),
            override.get("corrected_value") if decision == "correct" else claim.get("value"),
        )
        if not valid_refs or converted is _INVALID:
            updated["value"] = None
            provenance["state"] = FactState.UNKNOWN.value
        else:
            updated["value"] = converted
            provenance["state"] = FactState.STATED.value
            provenance["evidence_refs"] = valid_refs
    else:
        updated["value"] = None
        provenance["state"] = FactState.UNKNOWN.value
    provenance["review"] = override.get("review") or provenance.get("review")
    updated["provenance"] = provenance
    return updated


class _InvalidValue:
    pass


_INVALID = _InvalidValue()


def _coerce_structured_value(field_path: str, value: Any) -> Any:
    """Converte revisão estruturada; texto inválido fica unknown."""
    if not any(field_path in fields for fields in CANONICAL_FAMILY_FIELDS.values()):
        return _INVALID
    raw_value = value
    if field_path != "deadline" and isinstance(value, str):
        try:
            raw_value = json.loads(value)
        except json.JSONDecodeError:
            return _INVALID
    try:
        from radar.core.ingestion.adaptive_extraction import _normalize_target_value

        normalized, reason = _normalize_target_value(field_path, raw_value)
        return normalized if reason == "shape" else _INVALID
    except Exception:  # noqa: BLE001 — review conversion fails closed
        return _INVALID


def _recompute_gaps(
    existing_gaps: Iterable[str],
    claims: list[dict[str, Any]],
    fields: frozenset[str] | None = None,
) -> set[str]:
    """Rebuild field gaps from final claims while retaining structural gaps."""
    family_fields = fields or ALL_FAMILY_FIELDS
    claim_fields = {
        str(claim.get("field_path"))
        for claim in claims
        if claim.get("field_path")
    }
    gaps = {
        str(gap) for gap in existing_gaps
        if gap not in family_fields
        or gap not in claim_fields
    }
    gaps.update(
        str(claim.get("field_path"))
        for claim in claims
        if (claim.get("provenance") or {}).get("state") not in _RESOLVED_CLAIM_STATES
    )
    return gaps


def project_artifact(
    artifact: ExtractionArtifact,
    *,
    review_overrides: dict[str, dict[str, Any]] | None = None,
) -> AdaptiveReadModel:
    family = artifact.producer_versions.get(FAMILY_KEY, INITIAL_FAMILY)
    fields = _fields_for_family(family)
    if not _family_targets_compatible(artifact, family) or not _family_artifact_healthy(artifact, family):
        return AdaptiveReadModel(
            subject_id=artifact.subject_id,
            family=family,
            artifact_fingerprint=artifact.fingerprint,
            gaps=["Artifact fora da família adaptativa inicial."],
            needs_review=False,
            source_state="needs_review",
        )
    claims = [
        claim.model_dump(mode="json")
        for claim in artifact.claims
        if claim.field_path in fields
    ]
    for index, claim in enumerate(claims):
        override = (review_overrides or {}).get(str(claim.get("field_path") or ""))
        if override:
            claims[index] = _review_override(claim, override)
    gaps = _recompute_gaps(artifact.unresolved_targets, claims, fields)
    return AdaptiveReadModel(
        subject_id=artifact.subject_id,
        family=family,
        artifact_fingerprint=artifact.fingerprint,
        claims=claims,
        gaps=sorted(_recompute_gaps(gaps, claims, fields)),
        needs_review=False,
        source_state="adaptive",
    )


def _apply_rt05_overrides(
    projection: AdaptiveReadModel,
    overrides: dict[str, dict[str, Any]],
) -> AdaptiveReadModel:
    claims = [dict(claim) for claim in projection.claims]
    for index, claim in enumerate(claims):
        override = overrides.get(str(claim.get("field_path") or ""))
        if override:
            claims[index] = _review_override(claim, override)
    gaps = _recompute_gaps(
        projection.gaps, claims, _fields_for_family(projection.family),
    )
    return projection.model_copy(update={
        "claims": claims,
        "gaps": sorted(gaps),
        "needs_review": any(
            bool(override.get("rt05_open", override.get("decision") == "mark_unknown"))
            for override in overrides.values()
        ),
        "source_state": "adaptive",
    })


def _load_rt05_overrides(subject_id: str) -> dict[str, dict[str, Any]]:
    """Lê apenas a projeção pública das revisões RT05 para este sujeito."""
    return _load_rt05_overrides_many([subject_id]).get(subject_id, {})


def _load_rt05_overrides_many(
    subject_ids: list[str],
) -> dict[str, dict[str, dict[str, Any]]]:
    """Lê exceções e revisões correntes em lote, sem N+1 por card."""
    try:
        from radar.core.services.data_quality_exceptions import (
            list_exceptions_for_subjects,
            load_current_temporal_reviews,
        )
        from radar.domain.data_quality import IssueCode

        rows_by_subject = list_exceptions_for_subjects(subject_ids)
        current_by_subject: dict[str, dict[str, dict[str, Any]]] = {}
        all_ids: list[str] = []
        for subject_id, rows in rows_by_subject.items():
            grouped: dict[str, list[dict[str, Any]]] = {}
            for row in rows:
                field_path = row.get("field_path")
                if (
                    not isinstance(field_path, str)
                    or field_path not in ALL_FAMILY_FIELDS
                    or row.get("issue_code") != IssueCode.FACT_CONFLICT.value
                    or row.get("status") == "superseded"
                ):
                    continue
                grouped.setdefault(field_path, []).append(row)
                if row.get("id"):
                    all_ids.append(str(row["id"]))
            current_rows: dict[str, dict[str, Any]] = {}
            for field_path, field_rows in grouped.items():
                # Any open/non-resolved current exception blocks the field,
                # regardless of a review attached to another exception.
                if any(row.get("status") != "resolved" for row in field_rows):
                    current_rows[field_path] = {"decision": "mark_unknown"}
                else:
                    current_rows[field_path] = field_rows[0]
            current_by_subject[subject_id] = current_rows
        reviews = load_current_temporal_reviews(all_ids)
        result: dict[str, dict[str, dict[str, Any]]] = {}
        for subject_id, current_rows in current_by_subject.items():
            overrides: dict[str, dict[str, Any]] = {}
            for field_path, row in current_rows.items():
                review = reviews.get(str(row.get("id"))) if row.get("id") else None
                if review is not None:
                    overrides[field_path] = {
                        "decision": review.decision,
                        "rt05_open": row.get("status") == "open",
                        "corrected_value": review.corrected_value,
                        "evidence_refs": [ref.model_dump(mode="json") for ref in review.evidence_refs],
                        "review": review.review.model_dump(mode="json"),
                    }
                elif row.get("decision") == "mark_unknown":
                    overrides[field_path] = {
                        "decision": "mark_unknown",
                        "rt05_open": row.get("status") == "open",
                    }
                elif row.get("status") in {"open", "resolved"}:
                    overrides[field_path] = {
                        "decision": "mark_unknown",
                        "rt05_open": row.get("status") == "open",
                    }
            result[subject_id] = overrides
        return result
    except Exception as exc:  # noqa: BLE001 — falha na fila não escolhe valor
        logger.warning("adaptive_read_model: RT05 category=%s subjects=%d", type(exc).__name__, len(subject_ids))
        raise


def _temporal_state(projection: AdaptiveReadModel, *, as_of: date | None) -> str:
    deadline_claim = projection.claim("deadline")
    continuous_claim = projection.claim("continuous_flow")
    deadline: date | None = None
    if deadline_claim and (deadline_claim.get("provenance") or {}).get("state") == FactState.STATED.value:
        raw_deadline = deadline_claim.get("value")
        try:
            deadline = date.fromisoformat(raw_deadline) if isinstance(raw_deadline, str) else None
        except ValueError:
            deadline = None

    continuous_evidence = None
    if (
        continuous_claim
        and continuous_claim.get("value") is True
        and (continuous_claim.get("provenance") or {}).get("state") == FactState.STATED.value
    ):
        for ref in (continuous_claim.get("provenance") or {}).get("evidence_refs", []):
            if (
                ref.get("locator_quality") in {"exact", "document_only"}
                and (ref.get("canonical_content_hash") or ref.get("silver_source_hash"))
            ):
                from radar.domain.provenance import EvidenceRef

                continuous_evidence = EvidenceRef.model_validate(ref)
                break

    evaluation = evaluate_temporal(
        deadline=deadline,
        status=None,
        as_of=as_of or today_sao_paulo(),
        continuous_evidence=continuous_evidence,
    )
    return evaluation.validity_state.value


def _compose_with_rt04(
    subject_id: str,
    artifacts: list[ExtractionArtifact],
    bundle: SourceBundle,
    *,
    family: str = INITIAL_FAMILY,
    review_overrides: dict[str, dict[str, Any]] | None = None,
    as_of: date | None = None,
) -> AdaptiveReadModel:
    from radar.core.kg.source_bundle_projection import (
        ExplicitClaim,
        current_complete_bundle,
        project_current_documents,
        resolve_field,
    )

    fields = _fields_for_family(family)
    if (
        current_complete_bundle(bundle) is None
        or bundle.subject_id != subject_id
        or bundle.subject_kind.value != "opportunity"
    ):
        return AdaptiveReadModel(
            subject_id=subject_id,
            gaps=["SourceBundle não corresponde ao sujeito."],
            needs_review=False,
        )
    claims_by_field: list[ExplicitClaim] = []
    fingerprints: list[str] = []
    current_document_names = {
        document.doc_name for document in project_current_documents(bundle).documents
    }
    current_documents_by_name = {
        document.doc_name: document
        for document in project_current_documents(bundle).documents
    }
    states_by_document: dict[str, dict[str, list[FactState]]] = {}
    bundle_hash = bundle.compute_bundle_hash()
    for artifact in artifacts:
        fingerprints.append(artifact.fingerprint)
        if artifact.bundle_hash not in {None, bundle_hash}:
            continue
        document_name = artifact.document
        if document_name in current_document_names:
            document_states = states_by_document.setdefault(document_name, {})
            requested_fields = {
                target.field_path for target in artifact.targets_requested
                if target.field_path in fields
            }
            claims_by_field_for_document = {
                claim.field_path for claim in artifact.claims
                if claim.field_path in fields
            }
            for field_path in requested_fields - claims_by_field_for_document:
                document_states.setdefault(field_path, []).append(FactState.UNKNOWN)
        for claim in artifact.claims:
            if claim.field_path not in fields:
                continue
            if document_name in current_document_names:
                states_by_document.setdefault(document_name, {}).setdefault(
                    claim.field_path, []
                ).append(claim.provenance.state)
            if claim.provenance.state is not FactState.STATED:
                continue
            ref = next(
                (
                    item for item in claim.provenance.evidence_refs
                    if item.content_hash or item.canonical_content_hash or item.silver_source_hash
                ),
                None,
            )
            if ref is None:
                continue
            document_metadata = current_documents_by_name.get(document_name)
            claims_by_field.append(ExplicitClaim(
                field=claim.field_path,
                value=claim.value,
                content_hash=ref.content_hash or ref.canonical_content_hash or ref.silver_source_hash or "",
                supersedes_content_hash=(
                    claim.supersedes_content_hash
                    or (document_metadata.amends_content_hash if document_metadata else None)
                ),
                evidence_refs=tuple(claim.provenance.evidence_refs),
            ))
    explicit_absent_fields = {
        field_path
        for field_path in fields
        if current_document_names
        and all(
            states_by_document.get(document_name, {}).get(field_path)
            and all(state is FactState.ABSENT for state in states_by_document[document_name][field_path])
            for document_name in current_document_names
        )
    }
    resolutions = {
        field_path: resolve_field(bundle, claims_by_field, field_path)
        for field_path in sorted(fields)
    }
    projected: list[dict[str, Any]] = []
    gaps: list[str] = []
    for field_path, resolution in resolutions.items():
        state = (
            FactState.ABSENT
            if resolution.state is FactState.UNKNOWN and field_path in explicit_absent_fields
            else resolution.state
        )
        value = resolution.value if state is FactState.STATED else None
        if state is FactState.CONFLICTING and field_path in MATERIAL_CONFLICT_FIELDS:
            try:
                from radar.core.services.data_quality_exceptions import open_or_observe_exception
                from radar.domain.data_quality import DataQualityException, IssueCode
                open_or_observe_exception(DataQualityException(
                    subject_kind="opportunity",
                    subject_id=subject_id,
                    field_path=field_path,
                    issue_code=IssueCode.FACT_CONFLICT,
                    produced_state=FactState.CONFLICTING,
                    evidence_refs=resolution.evidence_refs,
                    bundle_hash=bundle_hash,
                    producer_version="rt04-v1",
                    input_fingerprint="sha256:" + hashlib.sha256(
                        json.dumps([bundle.compute_bundle_hash(), *sorted(fingerprints), field_path]).encode()
                    ).hexdigest(),
                ))
            except Exception as exc:  # noqa: BLE001 — queueing is best effort
                logger.warning("adaptive_read_model: conflict observation category=%s", type(exc).__name__)
        provenance = {
            "state": state.value,
            "evidence_refs": [ref.model_dump(mode="json") for ref in resolution.evidence_refs],
            "producer": {
                "kind": ProducerKind.DETERMINISTIC.value,
                "name": "source_bundle_projection",
                "version": "rt04-v1",
            },
            "derivation": {
                "inputs": sorted(fingerprints),
                "rule": "RT04 current documents and explicit precedence",
            },
            "validations": [{
                "name": "rt04_composition",
                "status": "passed" if state in {FactState.STATED, FactState.ABSENT} else "needs_review",
            }],
        }
        projected.append({"subject_id": subject_id, "field_path": field_path, "value": value, "provenance": provenance})
        if state not in _RESOLVED_CLAIM_STATES:
            gaps.append(field_path)
    digest = hashlib.sha256(
        json.dumps(sorted(fingerprints) + [bundle_hash], ensure_ascii=False).encode()
    ).hexdigest()
    projection = AdaptiveReadModel(
        subject_id=subject_id,
        family=family,
        artifact_fingerprint=f"sha256:{digest}",
        claims=projected,
        gaps=sorted(set(gaps)),
        needs_review=any(
            field_path in MATERIAL_CONFLICT_FIELDS
            and resolution.state is FactState.CONFLICTING
            for field_path, resolution in resolutions.items()
        ),
        source_state="adaptive",
    )
    if review_overrides:
        projection = _apply_rt05_overrides(projection, review_overrides)
    if _canonical_family(family) == "temporal":
        projection = projection.model_copy(update={
            "temporal_state": _temporal_state(projection, as_of=as_of),
        })
        if projection.temporal_state == "needs_review":
            projection = projection.model_copy(update={"needs_review": True})
    return projection


def resolve(
    subject_id: str,
    *,
    artifacts: list[ExtractionArtifact] | None = None,
    previous: AdaptiveReadModel | None = None,
    review_overrides: dict[str, dict[str, Any]] | None = None,
    bundle: SourceBundle | None = None,
    family: str = INITIAL_FAMILY,
    as_of: date | None = None,
    legacy_values: dict[str, Any] | None = None,
    legacy_factory: Callable[[], dict[str, Any]] | None = None,
) -> AdaptiveReadModel:
    """Resolve a projeção conforme a configuração da família.

    Família inativa retorna o candidato legado sem tocar em artifacts, bundle ou
    RT05. Família ativa é fail-closed: uma falha adaptativa preserva o snapshot
    adaptativo saudável disponível e nunca consulta o legado.
    """
    _fields_for_family(family)
    if not family_is_active(family):
        if legacy_values is None and legacy_factory is not None:
            legacy_values = legacy_factory()
        return _legacy_projection(subject_id, family, legacy_values)
    bundle_load_failed = False
    if bundle is None:
        try:
            from radar.core.kg import source_bundles

            bundle = source_bundles.load("opportunity", subject_id)
        except Exception as exc:  # noqa: BLE001 — RT04 is fail-closed
            logger.warning("adaptive_read_model: bundle category=%s subject=%s", type(exc).__name__, subject_id)
            bundle_load_failed = True
    try:
        available = artifacts if artifacts is not None else document_extractions.list_for_subject(subject_id)
        selected_artifacts = _latest_artifact_per_document(
            select_compatible_artifacts(available, family)
        )
        selected = selected_artifacts[-1] if selected_artifacts else None
        blocked_attempt = any(
            artifact.producer_versions.get("adaptive_text") == ADAPTIVE_TEXT_PRODUCER_VERSION
            and _family_targets_compatible(artifact, family)
            and not _family_artifact_healthy(artifact, family)
            for artifact in available
        )
    except Exception as exc:  # noqa: BLE001 — fallback conservador
        logger.warning("adaptive_read_model: category=%s subject=%s", type(exc).__name__, subject_id)
        selected = None
        selected_artifacts = []
        blocked_attempt = True
    healthy_previous = _healthy_previous(previous, family)
    if bundle_load_failed or bundle is None:
        if healthy_previous is not None:
            return healthy_previous.model_copy(update={
                "gaps": sorted(set(healthy_previous.gaps + [
                    "SourceBundle corrente indisponível; snapshot saudável preservado."
                ])),
                "needs_review": False,
            })
        return AdaptiveReadModel(
            subject_id=subject_id,
            family=family,
            artifact_fingerprint=selected.fingerprint if selected else None,
            gaps=["SourceBundle corrente indisponível; claims não publicados."],
            needs_review=False,
            source_state="needs_review",
        )
    if selected is not None:
        try:
            effective_overrides = (
                review_overrides
                if review_overrides is not None
                else _load_rt05_overrides(subject_id)
            )
        except Exception:
            if healthy_previous is not None:
                return healthy_previous.model_copy(update={
                    "gaps": sorted(set(healthy_previous.gaps + [
                        "Projeção RT05 indisponível; snapshot saudável preservado."
                    ])),
                    "needs_review": False,
                })
            return AdaptiveReadModel(
                subject_id=subject_id,
                artifact_fingerprint=selected.fingerprint,
                gaps=["Projeção RT05 indisponível; claim original não publicado."],
                needs_review=False,
                source_state="needs_review",
            )
        try:
            projection = _compose_with_rt04(
                subject_id, selected_artifacts, bundle,
                family=family,
                review_overrides=effective_overrides,
                as_of=as_of,
            )
            if blocked_attempt:
                projection = projection.model_copy(update={
                    "gaps": sorted(set(projection.gaps + [
                        "Nova extração não compatível; snapshot saudável preservado."
                    ])),
                    "needs_review": False,
                })
            return projection
        except Exception as exc:  # noqa: BLE001 — RT04 failure is a gap
            logger.warning("adaptive_read_model: RT04 category=%s subject=%s", type(exc).__name__, subject_id)
            if healthy_previous is not None:
                return healthy_previous.model_copy(update={
                    "gaps": sorted(set(healthy_previous.gaps + [
                        "Composição RT04 indisponível; snapshot saudável preservado."
                    ])),
                    "needs_review": False,
                })
            return AdaptiveReadModel(
                subject_id=subject_id,
                family=family,
                artifact_fingerprint=selected.fingerprint,
                gaps=["Composição RT04 indisponível; claims não publicados."],
                needs_review=False,
                source_state="needs_review",
            )
    if healthy_previous is not None:
        return healthy_previous.model_copy(update={
            "gaps": sorted(set(healthy_previous.gaps + ["Nova extração não promovível."])),
            "needs_review": False,
        })
    return AdaptiveReadModel(
        subject_id=subject_id,
        family=family,
        gaps=["Nenhum artifact adaptativo compatível e saudável."],
        needs_review=False,
        source_state="adaptive",
    )


def claims_for_subject(subject_id: str) -> AdaptiveReadModel:
    """Pequeno adapter usado por gold; não expõe persistência ao consumidor."""
    return resolve(subject_id)


def resolve_many(
    subject_ids: list[str],
    *,
    artifacts_by_subject: dict[str, list[ExtractionArtifact]] | None = None,
    review_overrides_by_subject: dict[str, dict[str, dict[str, Any]]] | None = None,
    legacy_values_by_subject: dict[str, dict[str, Any]] | None = None,
    family: str = INITIAL_FAMILY,
    as_of: date | None = None,
) -> dict[str, AdaptiveReadModel]:
    """Resolve vários sujeitos usando a mesma decisão de família."""
    ids = list(dict.fromkeys(subject_id for subject_id in subject_ids if subject_id))
    if not family_is_active(family):
        return {
            subject_id: _legacy_projection(
                subject_id, family, (legacy_values_by_subject or {}).get(subject_id),
            )
            for subject_id in ids
        }
    loaded_artifacts = (
        artifacts_by_subject
        if artifacts_by_subject is not None
        else document_extractions.list_for_subjects(ids)
    )
    loaded_overrides = (
        review_overrides_by_subject
        if review_overrides_by_subject is not None
        else _load_rt05_overrides_many(ids)
    )
    return {
        subject_id: resolve(
            subject_id,
            artifacts=loaded_artifacts.get(subject_id, []),
            review_overrides=loaded_overrides.get(subject_id, {}),
            legacy_values=(legacy_values_by_subject or {}).get(subject_id),
            family=family,
            as_of=as_of,
        )
        for subject_id in ids
    }


def family_values(
    subject_id: str,
    *,
    artifacts: list[ExtractionArtifact] | None = None,
    review_overrides: dict[str, dict[str, Any]] | None = None,
    bundle: SourceBundle | None = None,
    legacy_values: dict[str, Any] | None = None,
    legacy_factory: Callable[[], dict[str, Any]] | None = None,
    family: str = INITIAL_FAMILY,
) -> dict[str, Any]:
    projection = resolve(
        subject_id,
        artifacts=artifacts,
        review_overrides=review_overrides,
        bundle=bundle,
        legacy_values=legacy_values,
        legacy_factory=legacy_factory,
        family=family,
    )
    return {
        str(claim.get("field_path")): claim.get("value")
        for claim in projection.claims
        if (
            claim.get("field_path") in _fields_for_family(family)
            and (claim.get("provenance") or {}).get("state") == FactState.STATED.value
            and (
                projection.source_state == "legacy"
                or _has_resolvable_evidence(claim)
            )
        )
    }


__all__ = [
    "AdaptiveReadModel",
    "active_families",
    "family_is_active",
    "family_values",
    "INITIAL_FAMILY",
    "INITIAL_FAMILY_FIELDS",
    "claims_for_subject",
    "project_artifact",
    "resolve",
    "resolve_many",
    "select_compatible_artifacts",
]
