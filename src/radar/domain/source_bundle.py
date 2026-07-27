"""Radar Data Trust 04 — Contrato SourceBundle (RT04-T01).

Pacote documental versionado para oportunidades e atores.
Contrato puro: sem persistência, rede, LLM ou alteração produtiva.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, field_validator, model_validator

SOURCE_BUNDLE_SCHEMA_VERSION: Literal[1] = 1

_ACTOR_ID_PREFIXES = ("investidor:", "ict:", "programa:", "agencia:")
_OPPORTUNITY_ROLES = frozenset({
    "base_notice",
    "opportunity_page",
    "program_page",
    "annex",
    "amendment",
    "official_page",
    "faq",
})
_ACTOR_ROLES = frozenset({"official_page", "official_record", "curated_record"})


class SubjectKind(str, Enum):
    OPPORTUNITY = "opportunity"
    INVESTOR = "investor"
    ICT = "ict"
    PROGRAM = "program"
    AGENCY = "agency"


class AcquisitionStatus(str, Enum):
    COMPLETE = "complete"
    PARTIAL = "partial"


class DocumentRole(str, Enum):
    BASE_NOTICE = "base_notice"
    OPPORTUNITY_PAGE = "opportunity_page"
    PROGRAM_PAGE = "program_page"
    ANNEX = "annex"
    AMENDMENT = "amendment"
    OFFICIAL_PAGE = "official_page"
    FAQ = "faq"
    OFFICIAL_RECORD = "official_record"
    CURATED_RECORD = "curated_record"


class AuthorityState(str, Enum):
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    CONTEXTUAL = "contextual"


def _canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False)


def _sha256(data: str) -> str:
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def compute_content_hash(units: list[str]) -> str:
    """SHA-256 determinístico do conteúdo documental (units)."""
    canonical = _canonical_json(units)
    return f"sha256:{_sha256(canonical)}"


_CONTENT_HASH_PREFIX = "sha256:"
_CONTENT_HASH_LEN = 64


def _validate_sha256(v: str, field_name: str) -> str:
    if not v.startswith(_CONTENT_HASH_PREFIX):
        raise ValueError(
            f"{field_name} must start with '{_CONTENT_HASH_PREFIX}', "
            f"got: {v[:20]}..."
        )
    hex_part = v[len(_CONTENT_HASH_PREFIX):]
    if len(hex_part) != _CONTENT_HASH_LEN:
        raise ValueError(
            f"{field_name} hex part must have {_CONTENT_HASH_LEN} chars, "
            f"got {len(hex_part)}"
        )
    try:
        int(hex_part, 16)
    except ValueError as exc:
        raise ValueError(f"{field_name} hex part is not valid hex: {exc}") from exc
    return v


def _trim_non_empty(v: str, field_name: str) -> str:
    stripped = v.strip()
    if not stripped:
        raise ValueError(f"{field_name} must be non-empty")
    return stripped


class DocumentMetadata(BaseModel):
    """Metadado material de um documento no pacote.

    `units` deve conter ao menos uma unidade textual não vazia.
    `content_hash` é validado no formato `sha256:<hex>` e por consistência
    contra o conteúdo real de `units`.
    `amends_content_hash` (opcional) só é permitido em documentos com
    role=amendment e deve referir-se a outro documento do mesmo bundle.
    """

    model_config = {"extra": "forbid"}

    doc_name: str
    units: list[str]
    role: DocumentRole
    source_url: str | None = None
    published_at: str | None = None
    content_hash: str
    authority_state: AuthorityState = AuthorityState.ACTIVE
    composition_order: int | None = None
    amends_content_hash: str | None = None

    @field_validator("doc_name")
    @classmethod
    def _doc_name_valid(cls, v: str) -> str:
        return _trim_non_empty(v, "doc_name")

    @field_validator("units")
    @classmethod
    def _units_valid(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("units must be non-empty")
        for i, unit in enumerate(v):
            if not unit or not unit.strip():
                raise ValueError(f"units[{i}] is empty")
        return v

    @field_validator("content_hash")
    @classmethod
    def _content_hash_format(cls, v: str) -> str:
        return _validate_sha256(v, "content_hash")

    @field_validator("amends_content_hash")
    @classmethod
    def _amends_content_hash_format(cls, v: str | None) -> str | None:
        if v is not None:
            _validate_sha256(v, "amends_content_hash")
        return v

    @field_validator("composition_order")
    @classmethod
    def _composition_order_non_negative(cls, v: int | None) -> int | None:
        if v is not None and v < 0:
            raise ValueError("composition_order must be >= 0")
        return v

    @model_validator(mode="after")
    def _check_amendment_role_consistency(self) -> DocumentMetadata:
        if self.amends_content_hash is not None and self.role is not DocumentRole.AMENDMENT:
            raise ValueError(
                "amends_content_hash is only allowed on documents "
                f"with role=amendment, got role={self.role.value}"
            )
        return self


class SourceBundle(BaseModel):
    """Envelope lógico de um pacote documental versionado.

    A identidade material do pacote é capturada por `compute_bundle_hash()`:
    schema_version, subject_kind, subject_id, source, acquisition_status,
    conjunto documental, papéis e autoridades.
    `collected_at` e `producer_version` NÃO alteram o hash.
    O método retorna o hash — ele não é um campo do modelo (é coluna DB
    separada, spec §6).
    """

    model_config = {"extra": "forbid"}

    schema_version: Literal[1] = SOURCE_BUNDLE_SCHEMA_VERSION
    subject_kind: SubjectKind
    subject_id: str
    source: str
    collected_at: datetime
    producer_version: str
    acquisition_status: AcquisitionStatus
    documents: list[DocumentMetadata]

    @field_validator("subject_id")
    @classmethod
    def _subject_id_valid(cls, v: str) -> str:
        return _trim_non_empty(v, "subject_id")

    @field_validator("source")
    @classmethod
    def _source_valid(cls, v: str) -> str:
        return _trim_non_empty(v, "source")

    @field_validator("producer_version")
    @classmethod
    def _producer_version_valid(cls, v: str) -> str:
        return _trim_non_empty(v, "producer_version")

    @field_validator("collected_at")
    @classmethod
    def _collected_at_tz_aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("collected_at must be timezone-aware (use UTC)")
        return v

    @model_validator(mode="after")
    def _check_invariants(self) -> SourceBundle:
        if not self.documents:
            raise ValueError("SourceBundle requires at least one document")

        # content_hash consistency
        for doc in self.documents:
            expected = compute_content_hash(doc.units)
            if doc.content_hash != expected:
                raise ValueError(
                    f"content_hash mismatch for '{doc.doc_name}': "
                    f"expected {expected}, got {doc.content_hash}"
                )

        # canonical ID per kind
        self._validate_canonical_id()

        # role per kind
        self._validate_roles()

        # amends_content_hash cross-reference
        self._validate_amends_cross_ref()

        return self

    def _validate_canonical_id(self) -> None:
        kind = self.subject_kind
        sid = self.subject_id

        if kind is SubjectKind.OPPORTUNITY:
            if ":" not in sid:
                raise ValueError(
                    f"opportunity subject_id must be '<source>:<native_id>', "
                    f"got '{sid}'"
                )
            if sid.startswith(_ACTOR_ID_PREFIXES):
                raise ValueError(
                    f"opportunity subject_id must not start with actor prefix, "
                    f"got '{sid}'"
                )
        elif kind is SubjectKind.INVESTOR:
            if not sid.startswith("investidor:"):
                raise ValueError(
                    f"investor subject_id must start with 'investidor:', "
                    f"got '{sid}'"
                )
        elif kind is SubjectKind.ICT:
            if not sid.startswith("ict:"):
                raise ValueError(
                    f"ict subject_id must start with 'ict:', got '{sid}'"
                )
        elif kind is SubjectKind.PROGRAM:
            if not sid.startswith("programa:"):
                raise ValueError(
                    f"program subject_id must start with 'programa:', "
                    f"got '{sid}'"
                )
        elif kind is SubjectKind.AGENCY:
            if not sid.startswith("agencia:"):
                raise ValueError(
                    f"agency subject_id must start with 'agencia:', "
                    f"got '{sid}'"
                )

    def _validate_roles(self) -> None:
        is_actor = self.subject_kind is not SubjectKind.OPPORTUNITY
        allowed = _ACTOR_ROLES if is_actor else _OPPORTUNITY_ROLES
        for doc in self.documents:
            if doc.role.value not in allowed:
                raise ValueError(
                    f"role '{doc.role.value}' not allowed for "
                    f"subject_kind={self.subject_kind.value}; "
                    f"allowed roles: {sorted(allowed)}"
                )

    def _validate_amends_cross_ref(self) -> None:
        known_hashes = {d.content_hash for d in self.documents}
        for doc in self.documents:
            if doc.amends_content_hash is not None:
                if doc.amends_content_hash not in known_hashes:
                    raise ValueError(
                        f"amends_content_hash '{doc.amends_content_hash}' "
                        f"in document '{doc.doc_name}' does not match any "
                        f"document's content_hash in this bundle"
                    )

    def compute_bundle_hash(self) -> str:
        """SHA-256 determinístico; exclui collected_at e producer_version.

        Inclui schema_version e acquisition_status como campos materiais
        da identidade do pacote.
        """
        material_docs = [self._material_doc_dict(d) for d in self.documents]
        material_docs.sort(
            key=lambda d: (
                d.get("composition_order", 0) if d.get("composition_order") is not None else 0,
                d["doc_name"],
                d["content_hash"],
            )
        )
        payload = _canonical_json({
            "schema_version": self.schema_version,
            "subject_kind": self.subject_kind.value,
            "subject_id": self.subject_id,
            "source": self.source,
            "acquisition_status": self.acquisition_status.value,
            "documents": material_docs,
        })
        return f"sha256:{_sha256(payload)}"

    @staticmethod
    def _material_doc_dict(doc: DocumentMetadata) -> dict[str, Any]:
        result: dict[str, Any] = {
            "doc_name": doc.doc_name,
            "units": doc.units,
            "role": doc.role.value,
            "content_hash": doc.content_hash,
            "authority_state": doc.authority_state.value,
        }
        if doc.source_url is not None:
            result["source_url"] = doc.source_url
        if doc.published_at is not None:
            result["published_at"] = doc.published_at
        if doc.composition_order is not None:
            result["composition_order"] = doc.composition_order
        if doc.amends_content_hash is not None:
            result["amends_content_hash"] = doc.amends_content_hash
        return result


__all__ = [
    "SOURCE_BUNDLE_SCHEMA_VERSION",
    "SubjectKind",
    "AcquisitionStatus",
    "DocumentRole",
    "AuthorityState",
    "DocumentMetadata",
    "SourceBundle",
    "compute_content_hash",
]
