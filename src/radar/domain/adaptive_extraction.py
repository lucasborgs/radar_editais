"""Contratos canônicos da extração adaptativa (RT06).

Este módulo contém somente tipos e regras de identidade. A aquisição continua
nos adapters de fonte e a precedência entre documentos continua na RT04.
"""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from enum import Enum
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator, model_validator

from radar.domain.provenance import FactProvenance

ADAPTIVE_EXTRACTION_SCHEMA_VERSION: Literal[9] = 9
SHA256_PREFIX = "sha256:"
ADAPTIVE_TEXT_PRODUCER_VERSION = "text-v9"
ADAPTIVE_PRODUCER_SCHEMA = "v3"
FAMILY_KEYS = ("eligibility", "temporal", "financial", "table_evidence")
FAMILY_FIELDS: dict[str, frozenset[str]] = {
    "eligibility": frozenset({
        "eligibility_constraints", "requirements", "exclusions",
        "eligible_entities", "publico_alvo",
    }),
    "temporal": frozenset({"deadline", "submission_window", "continuous_flow"}),
    "financial": frozenset({"funding_amount", "funding_limits", "counterpart"}),
    "table_evidence": frozenset({"table_references"}),
}
FIELD_VALUE_TYPES: dict[str, str] = {
    "eligibility_constraints": "list[constraint]",
    "requirements": "list[str]",
    "exclusions": "list[str]",
    "eligible_entities": "list[str]",
    "publico_alvo": "list[str]",
    "deadline": "date",
    "submission_window": "submission_window",
    "continuous_flow": "bool",
    "funding_amount": "monetary_range",
    "funding_limits": "funding_limits",
    "counterpart": "counterpart",
    "table_references": "list[table_reference]",
}


def _sha256(value: bytes) -> str:
    return SHA256_PREFIX + hashlib.sha256(value).hexdigest()


def normalize_hash(value: str | None) -> str | None:
    if value is None:
        return None
    raw = value.removeprefix(SHA256_PREFIX)
    if len(raw) != 64:
        raise ValueError("hash must contain 64 hexadecimal characters")
    try:
        int(raw, 16)
    except ValueError as exc:
        raise ValueError("hash must be hexadecimal") from exc
    return SHA256_PREFIX + raw.lower()


class ExtractionRoute(str, Enum):
    TEXT = "text"
    LAYOUT = "layout"
    OCR = "ocr"
    VISION = "vision"


class ExtractionStatus(str, Enum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    FAILED = "failed"
    UNAVAILABLE = "unavailable"


class TextUnit(BaseModel):
    """Unidade textual sanitizada preservada para resolver evidência."""

    model_config = {"extra": "allow"}

    text: str = ""
    unit_id: str | None = None
    document: str | None = None
    page: int | None = None
    section_path: list[str] = Field(default_factory=list)
    block_idx: int | None = None

    @field_validator("page")
    @classmethod
    def _page_positive(cls, value: int | None) -> int | None:
        if value is not None and value < 1:
            raise ValueError("page must be >= 1")
        return value

    @field_validator("block_idx")
    @classmethod
    def _block_non_negative(cls, value: int | None) -> int | None:
        if value is not None and value < 0:
            raise ValueError("block_idx must be >= 0")
        return value


class DocumentAsset(BaseModel):
    """Documento adquirido entregue ao seam interno de interpretação."""

    model_config = {"extra": "forbid", "arbitrary_types_allowed": True}

    subject_id: str
    source: str
    doc_name: str
    document_role: str = "opportunity_page"
    source_url: str | None = None
    published_at: datetime | None = None
    authority_state: str = "current"
    media_type: str = "text/plain"
    asset_hash: str | None = None
    text_units: list[TextUnit] = Field(default_factory=list)
    payload: bytes | None = Field(default=None, repr=False)
    asset_ref: str | None = None
    bundle_hash: str | None = None

    @field_validator("subject_id", "source", "doc_name")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("document identity fields must be non-empty")
        return value

    @field_validator("asset_hash", "bundle_hash")
    @classmethod
    def _valid_hash(cls, value: str | None) -> str | None:
        return normalize_hash(value)

    @model_validator(mode="after")
    def _compute_or_require_hash(self) -> DocumentAsset:
        if self.asset_hash is None:
            if self.payload is not None:
                self.asset_hash = _sha256(self.payload)
            elif self.text_units:
                self.asset_hash = _sha256(self.canonical_text_payload())
            else:
                raise ValueError("asset_hash or an accessible document payload is required")
        return self

    def canonical_text_payload(self) -> bytes:
        units = [unit.model_dump(mode="json", exclude_none=True) for unit in self.text_units]
        return json.dumps(units, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()

    def material_for_fingerprint(self) -> dict[str, Any]:
        return {
            "asset_hash": self.asset_hash,
            "bundle_hash": self.bundle_hash,
            "subject_id": self.subject_id,
            "source": self.source,
            "doc_name": self.doc_name,
        }


class ExtractionTarget(BaseModel):
    model_config = {"extra": "forbid"}

    field_path: str
    value_type: str
    required_for: Literal["exploration", "eligibility", "writing"]
    criticality: Literal["advisory", "decision"] = "advisory"

    @field_validator("field_path", "value_type")
    @classmethod
    def _target_text_non_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("target fields must be non-empty")
        return value


class SubmissionWindow(BaseModel):
    """Janela textual normalizada para submissão."""

    model_config = {"extra": "forbid"}

    start: date | None = None
    end: date | None = None

    @model_validator(mode="after")
    def _ordered(self) -> SubmissionWindow:
        if self.start is not None and self.end is not None and self.start > self.end:
            raise ValueError("submission window start must not exceed end")
        return self


class MonetaryRange(BaseModel):
    """Faixa monetária em moeda explícita, sem conversão cambial."""

    model_config = {"extra": "forbid"}

    currency: str
    min: float | None = None
    max: float | None = None

    @field_validator("currency")
    @classmethod
    def _currency_non_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("currency must be non-empty")
        return value.upper()

    @field_validator("min", "max")
    @classmethod
    def _amount_non_negative(cls, value: float | None) -> float | None:
        if value is not None and value < 0:
            raise ValueError("monetary values must not be negative")
        return value

    @model_validator(mode="after")
    def _ordered(self) -> MonetaryRange:
        if self.min is not None and self.max is not None and self.min > self.max:
            raise ValueError("monetary min must not exceed max")
        return self


class FundingLimits(MonetaryRange):
    """Limites de financiamento, incluindo teto explícito por projeto."""

    per_project: float | None = None

    @field_validator("per_project")
    @classmethod
    def _per_project_non_negative(cls, value: float | None) -> float | None:
        if value is not None and value < 0:
            raise ValueError("per_project must not be negative")
        return value


class CounterpartValue(BaseModel):
    """Contrapartida declarada e sua base de cálculo, quando disponível."""

    model_config = {"extra": "forbid"}

    required: bool
    percentage: float | None = None
    base: str | None = None

    @field_validator("percentage")
    @classmethod
    def _percentage_in_range(cls, value: float | None) -> float | None:
        if value is not None and not 0 <= value <= 100:
            raise ValueError("counterpart percentage must be between 0 and 100")
        return value

    @field_validator("base")
    @classmethod
    def _base_non_empty(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("counterpart base must be non-empty")
        return value


class TableReference(BaseModel):
    """Referência textual a uma tabela que sustenta um fato decisório."""

    model_config = {"extra": "forbid"}

    document: str
    title: str | None = None
    caption: str | None = None
    page: int | None = None
    section: str | None = None
    purpose: str

    @field_validator("document", "purpose")
    @classmethod
    def _reference_text_non_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("table reference text must be non-empty")
        return value

    @field_validator("page")
    @classmethod
    def _page_positive(cls, value: int | None) -> int | None:
        if value is not None and value < 1:
            raise ValueError("table reference page must be >= 1")
        return value

    @model_validator(mode="after")
    def _title_or_caption(self) -> TableReference:
        if not (self.title and self.title.strip()) and not (self.caption and self.caption.strip()):
            raise ValueError("table reference requires title or caption")
        return self


INITIAL_TARGET_FIELDS = FAMILY_FIELDS["eligibility"]


class ExtractedClaim(BaseModel):
    model_config = {"extra": "forbid"}

    subject_id: str
    field_path: str
    value: Any = None
    supersedes_content_hash: str | None = None
    provenance: FactProvenance

    @field_validator("subject_id", "field_path")
    @classmethod
    def _claim_identity_non_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("claim identity fields must be non-empty")
        return value

    @model_validator(mode="after")
    def _state_value_consistency(self) -> ExtractedClaim:
        state = self.provenance.state.value
        if state == "absent" and self.value is not None:
            raise ValueError("absent claims cannot carry a value")
        return self


class RouteTrace(BaseModel):
    model_config = {"extra": "forbid"}

    route: ExtractionRoute
    reason: str
    pages_or_units: list[str | int] = Field(default_factory=list)
    targets_before: list[str] = Field(default_factory=list)
    targets_resolved: list[str] = Field(default_factory=list)
    duration_ms: int = Field(default=0, ge=0)
    status: Literal["complete", "partial", "failed", "skipped"]


class ExtractionArtifact(BaseModel):
    model_config = {"extra": "forbid"}

    schema_version: Literal[9] = ADAPTIVE_EXTRACTION_SCHEMA_VERSION
    subject_id: str
    # A identidade do documento é parte do artifact, mesmo quando vários
    # documentos pertencem ao mesmo edital.  Opcional apenas para ler os
    # artifacts v1 já existentes; produtores novos sempre o preenchem.
    document: str | None = None
    document_role: str | None = None
    asset_hash: str
    bundle_hash: str | None = None
    targets_requested: list[ExtractionTarget] = Field(default_factory=list)
    claims: list[ExtractedClaim] = Field(default_factory=list)
    unresolved_targets: list[str] = Field(default_factory=list)
    structured_blocks: list[dict[str, Any]] = Field(default_factory=list)
    table_fragments: list[dict[str, Any]] = Field(default_factory=list)
    route_trace: list[RouteTrace] = Field(default_factory=list)
    status: ExtractionStatus
    producer_versions: dict[str, str] = Field(default_factory=dict)
    # Identidade da tentativa, separada do fingerprint material. Isso permite
    # preservar falhas e reexecutar a mesma entrada sem sobrescrever histórico.
    attempt_id: str = Field(default_factory=lambda: uuid4().hex)
    fingerprint: str
    created_at: datetime

    @field_validator("asset_hash", "bundle_hash")
    @classmethod
    def _artifact_hash(cls, value: str | None) -> str | None:
        return normalize_hash(value)

    @field_validator("subject_id", "fingerprint")
    @classmethod
    def _artifact_identity_non_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("artifact identity fields must be non-empty")
        return value

    @model_validator(mode="after")
    def _fingerprint_is_sha256(self) -> ExtractionArtifact:
        if not self.fingerprint.startswith(SHA256_PREFIX):
            raise ValueError("fingerprint must start with 'sha256:'")
        normalize_hash(self.fingerprint)
        return self


def extraction_fingerprint(
    document: DocumentAsset,
    targets: list[ExtractionTarget],
    *,
    schema_version: int = ADAPTIVE_EXTRACTION_SCHEMA_VERSION,
    producer_versions: dict[str, str] | None = None,
) -> str:
    """Fingerprinta apenas a entrada material do seam, em forma canônica."""
    material = {
        "asset": document.material_for_fingerprint(),
        "schema_version": schema_version,
        "producer_versions": dict(sorted((producer_versions or {}).items())),
        "targets": sorted(
            (target.model_dump(mode="json", exclude_none=True) for target in targets),
            key=lambda target: json.dumps(target, ensure_ascii=False, sort_keys=True),
        ),
    }
    encoded = json.dumps(material, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return _sha256(encoded)


__all__ = [
    "ADAPTIVE_EXTRACTION_SCHEMA_VERSION",
    "ADAPTIVE_PRODUCER_SCHEMA",
    "ADAPTIVE_TEXT_PRODUCER_VERSION",
    "DocumentAsset",
    "ExtractionArtifact",
    "ExtractionRoute",
    "ExtractionStatus",
    "ExtractionTarget",
    "ExtractedClaim",
    "FAMILY_FIELDS",
    "FAMILY_KEYS",
    "FIELD_VALUE_TYPES",
    "INITIAL_TARGET_FIELDS",
    "RouteTrace",
    "TextUnit",
    "CounterpartValue",
    "FundingLimits",
    "MonetaryRange",
    "SubmissionWindow",
    "TableReference",
    "extraction_fingerprint",
    "normalize_hash",
]
