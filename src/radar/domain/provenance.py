"""Radar Data Trust 01 — Contrato de proveniência (RT01-T01).

Tipos versionados de evidência e proveniência por fato, do produtor ao
consumo. Sem banco, sem migration, sem chamada LLM e sem novo consumidor
produtivo — apenas o contrato puro descrito em
docs/specs/radar-data-trust-01-provenance.md (§4).

Compatibilidade: `Extracted[T]` (edital_extraction.py) e seu `FieldState`
legado não são alterados. `state` factual aqui é um tipo próprio
(`FactState`), com um estado a mais (`conflicting`) que o par
stated/inferred/absent do schema de extração-LLM — os dois não são
intercambiáveis. `evidence_ref_from_extracted` é o adaptador explícito que a
spec pede como a extensão aditiva mínima: converte a substring legado em
`EvidenceRef` sob demanda, sem tocar o schema de `Extracted`.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from radar.domain.edital_extraction import Extracted

PROVENANCE_SCHEMA_VERSION: Literal[1] = 1
_SHA256_PREFIX = "sha256:"
_SHA256_HEX_LEN = 64


class FactState(str, Enum):
    """Estado factual de um valor materializado no gold (spec §4.1).

    Distinto do `FieldState` legado de `edital_extraction.py`: aquele é a
    saída binária da extração-LLM (stated/inferred/absent) para um campo de
    DECISÃO; este cobre o ciclo completo de um fato publicado, incluindo
    `conflicting` (fontes incompatíveis) e `unknown` (legado/material
    insuficiente), que não existem no schema de extração.
    """

    STATED = "stated"
    INFERRED = "inferred"
    ABSENT = "absent"
    CONFLICTING = "conflicting"
    UNKNOWN = "unknown"


class LocatorQuality(str, Enum):
    """Qualidade da localização de uma evidência (spec §4.2)."""

    EXACT = "exact"
    DOCUMENT_ONLY = "document_only"
    UNRESOLVED = "unresolved"


class ProducerKind(str, Enum):
    """Tipo de produtor de um fato (spec §4.3)."""

    ADAPTER = "adapter"
    DETERMINISTIC = "deterministic"
    LLM = "llm"
    HUMAN = "human"
    DEFAULT = "default"
    BACKFILL = "backfill"


class EvidenceRef(BaseModel):
    """Referência estruturada a um trecho verificável (spec §4.2).

    Requisitos estruturais mínimos:
      - `schema_version` é fixo em `1` — qualquer outro valor é rejeitado;
      - `source` é obrigatório e não vazio;
      - `page`, quando presente, é 1-based; `block_idx`, quando presente,
        é `>= 0`;
      - ao menos um de `canonical_content_hash`/`silver_source_hash`
        identifica a versão recuperável;
      - `locator_quality` é semanticamente consistente com as coordenadas
        declaradas (ver `_check_invariants`).
    """

    model_config = {"extra": "forbid"}

    schema_version: Literal[1] = PROVENANCE_SCHEMA_VERSION
    source: str
    native_id: str | None = None
    edital_id: str | None = None
    source_url: str | None = None
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
    def _source_must_be_non_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("source must be non-empty")
        return v

    @field_validator("page")
    @classmethod
    def _page_must_be_positive(cls, v: int | None) -> int | None:
        if v is not None and v < 1:
            raise ValueError("page must be >= 1 (1-based)")
        return v

    @field_validator("block_idx")
    @classmethod
    def _block_idx_must_be_non_negative(cls, v: int | None) -> int | None:
        if v is not None and v < 0:
            raise ValueError("block_idx must be >= 0")
        return v

    @field_validator("bundle_hash", "content_hash")
    @classmethod
    def _sha256_hash_must_be_valid(cls, v: str | None, info) -> str | None:
        if v is None:
            return None
        if not v.startswith(_SHA256_PREFIX):
            raise ValueError(f"{info.field_name} must start with '{_SHA256_PREFIX}'")
        hex_part = v[len(_SHA256_PREFIX):]
        if len(hex_part) != _SHA256_HEX_LEN:
            raise ValueError(
                f"{info.field_name} hex part must have {_SHA256_HEX_LEN} chars"
            )
        try:
            int(hex_part, 16)
        except ValueError as exc:
            raise ValueError(f"{info.field_name} hex part is not valid hex") from exc
        return v

    @model_validator(mode="after")
    def _check_invariants(self) -> EvidenceRef:
        if not self.canonical_content_hash and not self.silver_source_hash:
            raise ValueError(
                "EvidenceRef requires canonical_content_hash or silver_source_hash "
                "to identify a recoverable version"
            )
        if (self.bundle_hash is None) != (self.content_hash is None):
            raise ValueError(
                "bundle_hash and content_hash must appear together or both be absent"
            )
        has_exact_coordinate = (
            self.page is not None or self.block_idx is not None or bool(self.section_path)
        )
        if self.locator_quality is LocatorQuality.UNRESOLVED:
            # unresolved: quote (content) is allowed, but no fabricated
            # positional coordinate — resolution failed, position unknown.
            if has_exact_coordinate:
                raise ValueError(
                    "locator_quality=unresolved must not carry fabricated positional "
                    "coordinates (page/block_idx/section_path); quote text alone is "
                    "allowed (content without confirmed position)"
                )
        elif self.locator_quality is LocatorQuality.DOCUMENT_ONLY:
            # document_only: document identified, but resolution stopped at
            # document granularity — no exact position within it.
            if not self.document or not self.document.strip():
                raise ValueError("locator_quality=document_only requires a non-empty document")
            if has_exact_coordinate:
                raise ValueError(
                    "locator_quality=document_only must not declare exact coordinates "
                    "(page/block_idx/section_path)"
                )
        elif self.locator_quality is LocatorQuality.EXACT:
            # exact: at least one resolved coordinate must back the claim.
            if not has_exact_coordinate:
                raise ValueError(
                    "locator_quality=exact requires at least one resolved coordinate "
                    "(page/block_idx/section_path)"
                )
        return self


class ProducerInfo(BaseModel):
    """Identidade do produtor de um fato (spec §4.3).

    `model`/`prompt_version` são opcionais e não são exigidos de produtores
    não-LLM (adapter/deterministic/human/default/backfill).
    """

    model_config = {"extra": "forbid"}

    kind: ProducerKind
    name: str | None = None
    version: str | None = None
    model: str | None = None
    prompt_version: str | None = None


class DerivationInfo(BaseModel):
    """Regra e entradas de uma derivação determinística (spec §4.3)."""

    model_config = {"extra": "forbid"}

    inputs: list[str] = Field(default_factory=list)
    rule: str | None = None


class ValidationResult(BaseModel):
    """Resultado de um validador estrutural (spec §8.1)."""

    model_config = {"extra": "forbid"}

    name: str
    status: Literal["passed", "failed"]


class ReviewInfo(BaseModel):
    """Referência a uma revisão humana ou override, append-only (spec §5.3:
    "ator e data").

    Este tipo é apenas a *referência* — quem revisou, quando e se houve
    override — não o histórico ou o registro operacional completo, que não
    são modelados aqui (fora de escopo do T01). Sem armazenamento, migration
    ou exposição em API.
    """

    model_config = {"extra": "forbid"}

    review_id: str
    actor_id: str
    reviewed_at: datetime
    overridden: bool = False

    @field_validator("review_id")
    @classmethod
    def _review_id_must_be_non_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("review_id must be non-empty")
        return v

    @field_validator("actor_id")
    @classmethod
    def _actor_id_must_be_non_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("actor_id must be non-empty")
        return v


class FactProvenance(BaseModel):
    """Proveniência completa de um fato materializado (spec §4.3).

    Sem score numérico de confiança (spec §4.4, §7): confiança operacional é
    derivada de `state`, `locator_quality`, `validations`, contagem de
    evidências e `review` por um consumidor posterior, não persistida aqui.
    """

    model_config = {"extra": "forbid"}

    state: FactState
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    producer: ProducerInfo
    derivation: DerivationInfo | None = None
    validations: list[ValidationResult] = Field(default_factory=list)
    review: ReviewInfo | None = None
    updated_at: datetime | None = None

    @model_validator(mode="after")
    def _check_invariants(self) -> FactProvenance:
        if self.producer.kind is ProducerKind.DEFAULT and self.state is FactState.STATED:
            raise ValueError(
                "producer.kind=default cannot declare state=stated "
                "(spec radar-data-trust-01-provenance.md §4.1)"
            )
        if self.state is FactState.STATED and not self.evidence_refs:
            raise ValueError(
                "state=stated requires at least one EvidenceRef "
                "(spec radar-data-trust-01-provenance.md §8.1 state_consistent)"
            )
        return self


def evidence_ref_from_extracted(
    extracted: Extracted[Any],
    *,
    source: str,
    canonical_content_hash: str | None = None,
    silver_source_hash: str | None = None,
    native_id: str | None = None,
    edital_id: str | None = None,
    source_url: str | None = None,
    document: str | None = None,
    page: int | None = None,
    block_idx: int | None = None,
    section_path: list[str] | None = None,
    collected_at: datetime | None = None,
    locator_quality: LocatorQuality | None = None,
) -> EvidenceRef | None:
    """Adaptador explícito: `Extracted.evidence` (substring legado) → `EvidenceRef`.

    Extensão aditiva mínima pedida pela spec (§5.3): não altera o schema de
    `Extracted`, não é chamado por nenhum produtor ainda. Retorna `None`
    quando não há evidence textual (`extracted.evidence is None`) — nada a
    converter, não um `EvidenceRef` vazio.

    Ao menos um hash (`canonical_content_hash`/`silver_source_hash`) deve ser
    informado pelo chamador: este adaptador não resolve versão sozinho, o
    caller (resolvedor da spec 01, tasks futuras) é quem conhece o
    documento/hash de origem.
    """
    if extracted.evidence is None:
        return None
    quality = locator_quality
    if quality is None:
        if page is not None or block_idx is not None or section_path:
            quality = LocatorQuality.EXACT
        elif document:
            quality = LocatorQuality.DOCUMENT_ONLY
        else:
            quality = LocatorQuality.UNRESOLVED
    return EvidenceRef(
        source=source,
        native_id=native_id,
        edital_id=edital_id,
        source_url=source_url,
        document=document,
        page=page,
        block_idx=block_idx,
        section_path=section_path or [],
        quote=extracted.evidence,
        canonical_content_hash=canonical_content_hash,
        silver_source_hash=silver_source_hash,
        collected_at=collected_at,
        locator_quality=quality,
    )


__all__ = [
    "PROVENANCE_SCHEMA_VERSION",
    "FactState",
    "LocatorQuality",
    "ProducerKind",
    "EvidenceRef",
    "ProducerInfo",
    "DerivationInfo",
    "ValidationResult",
    "ReviewInfo",
    "FactProvenance",
    "evidence_ref_from_extracted",
]
