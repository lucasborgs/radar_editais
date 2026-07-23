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

PROVENANCE_SCHEMA_VERSION = 1


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
      - `page`, quando presente, é 1-based;
      - ao menos um de `canonical_content_hash`/`silver_source_hash`
        identifica a versão recuperável;
      - `locator_quality=unresolved` registra falha sem fabricar
        coordenadas exatas (page/block_idx/section_path ficam vazios; o
        texto de `quote`, quando existir, não é uma coordenada — é o
        conteúdo capturado sem posição confirmada).
    """

    model_config = {"extra": "forbid"}

    schema_version: int = PROVENANCE_SCHEMA_VERSION
    source: str | None = None
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
    collected_at: datetime | None = None
    locator_quality: LocatorQuality = LocatorQuality.UNRESOLVED

    @field_validator("page")
    @classmethod
    def _page_must_be_positive(cls, v: int | None) -> int | None:
        if v is not None and v < 1:
            raise ValueError("page must be >= 1 (1-based)")
        return v

    @model_validator(mode="after")
    def _check_invariants(self) -> EvidenceRef:
        if not self.canonical_content_hash and not self.silver_source_hash:
            raise ValueError(
                "EvidenceRef requires canonical_content_hash or silver_source_hash "
                "to identify a recoverable version"
            )
        if self.locator_quality is LocatorQuality.UNRESOLVED and (
            self.page is not None
            or self.block_idx is not None
            or self.section_path
        ):
            raise ValueError(
                "locator_quality=unresolved must not carry fabricated positional "
                "coordinates (page/block_idx/section_path); quote text alone is "
                "allowed (content without confirmed position)"
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
    """Revisão humana ou override append-only (spec §5.3: "ator e data").

    Sem score, sem julgamento livre-form além de uma nota curta — o
    histórico operacional completo não é modelado aqui (fora de escopo do
    T01).
    """

    model_config = {"extra": "forbid"}

    reviewer: str | None = None
    note: str | None = None
    reviewed_at: datetime | None = None
    overridden: bool = False


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
        if page is not None or block_idx is not None:
            quality = LocatorQuality.EXACT
        elif document is not None:
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
