"""
Radar Data Trust 00 — Contrato de relevância.

Tipos versionados para decisão, reason codes e evidência de relevância,
com contratos separados por kind (oportunidade ≠ ator/catálogo).

Sem lógica de classificação, sem chamadas LLM, sem alteração produtiva.
"""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field

# ── Constantes versionadas ───────────────────────────────────────────────

CLASSIFIER_VERSION = "radar-data-trust-relevance-v1"
"""Versão canônica do classificador de relevância, fixada na spec 00."""

# ── Decisão ──────────────────────────────────────────────────────────────


class RelevanceDecision(str, Enum):
    IN_SCOPE = "in_scope"
    OUT_OF_SCOPE = "out_of_scope"
    NEEDS_REVIEW = "needs_review"


# ── Reason codes ─────────────────────────────────────────────────────────


class InclusionCode(str, Enum):
    ENTERPRISE_PATH = "R1_ENTERPRISE_PATH"
    TECH_INNOVATION = "R2_TECH_INNOVATION"
    ACTIONABLE = "R3_ACTIONABLE"
    RELEVANT_BENEFIT = "R4_RELEVANT_BENEFIT"
    BRAZIL_RELEVANCE = "R5_BRAZIL_RELEVANCE"


class ExclusionCode(str, Enum):
    ACADEMIC_ONLY = "X1_ACADEMIC_ONLY"
    CONVENTIONAL_CREDIT = "X2_CONVENTIONAL_CREDIT"
    GENERIC_PROCUREMENT = "X3_GENERIC_PROCUREMENT"
    EVENT_CONTENT = "X4_EVENT_CONTENT"
    GENERIC_SUPPORT = "X5_GENERIC_SUPPORT"
    NON_TECH = "X6_NON_TECH"
    NO_ENTERPRISE_PATH = "X7_NO_ENTERPRISE_PATH"
    INVESTOR_DIRECTORY = "X8_INVESTOR_DIRECTORY"


class OpportunityReasonCode(str, Enum):
    """Reason codes aplicáveis a oportunidades — inclusão ou exclusão."""
    R1_ENTERPRISE_PATH = "R1_ENTERPRISE_PATH"
    R2_TECH_INNOVATION = "R2_TECH_INNOVATION"
    R3_ACTIONABLE = "R3_ACTIONABLE"
    R4_RELEVANT_BENEFIT = "R4_RELEVANT_BENEFIT"
    R5_BRAZIL_RELEVANCE = "R5_BRAZIL_RELEVANCE"
    X1_ACADEMIC_ONLY = "X1_ACADEMIC_ONLY"
    X2_CONVENTIONAL_CREDIT = "X2_CONVENTIONAL_CREDIT"
    X3_GENERIC_PROCUREMENT = "X3_GENERIC_PROCUREMENT"
    X4_EVENT_CONTENT = "X4_EVENT_CONTENT"
    X5_GENERIC_SUPPORT = "X5_GENERIC_SUPPORT"
    X6_NON_TECH = "X6_NON_TECH"
    X7_NO_ENTERPRISE_PATH = "X7_NO_ENTERPRISE_PATH"
    X8_INVESTOR_DIRECTORY = "X8_INVESTOR_DIRECTORY"


class ActorReasonCode(str, Enum):
    """Reason codes para atores (investidor, ICT, programa, agência).

    Cada kind pode validar um subconjunto destes; a avaliação específica
    pertence ao classificador de cada tipo, não a um prompt genérico.
    """
    IDENTITY_VERIFIABLE = "A1_IDENTITY_VERIFIABLE"
    OFFICIAL_PAGE = "A2_OFFICIAL_PAGE"
    BRAZIL_RELEVANCE = "A3_BRAZIL_RELEVANCE"
    RELATION_TO_OPPORTUNITY = "A4_RELATION_TO_OPPORTUNITY"
    CAPACITY_KNOWN = "A5_CAPACITY_KNOWN"
    FIELD_UNKNOWN = "A6_FIELD_UNKNOWN"
    NO_ENTERPRISE_PATH = "A7_NO_ENTERPRISE_PATH"
    INSUFFICIENT_EVIDENCE = "A8_INSUFFICIENT_EVIDENCE"


# ── Kind ─────────────────────────────────────────────────────────────────


class ClassificationKind(str, Enum):
    """Tipo de entidade a ser classificada.

    Cada kind usa reason codes, evidência mínima e regras próprias.
    O classificador de um kind não pode ser reutilizado como se outro fosse.
    """
    OPPORTUNITY = "opportunity"
    INVESTOR = "investor"
    ICT = "ict"
    PROGRAM = "program"
    AGENCY = "agency"


# ── Evidência ────────────────────────────────────────────────────────────


class EvidenceSource(str, Enum):
    LANDING_PAGE = "landing_page"
    EDITAL = "edital"
    ANNEX = "anexo"


class EvidenceLocator(BaseModel):
    document: str | None = None
    page: int | None = None


class RelevanceEvidence(BaseModel):
    code: str
    quote: str | None = None
    source: EvidenceSource | None = None
    locator: EvidenceLocator | None = None


# ── Verdictos ────────────────────────────────────────────────────────────


class RelevanceVerdict(BaseModel):
    """Classificação de relevância de uma oportunidade.

    Output canônico conforme contrato Spec §7.1.
    """
    decision: RelevanceDecision
    reason_codes: list[str] = Field(default_factory=list)
    exclusion_codes: list[str] = Field(default_factory=list)
    evidence: list[RelevanceEvidence] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    classifier_version: str = CLASSIFIER_VERSION


class ActorVerdict(BaseModel):
    """Classificação de relevância de um ator (investidor, ICT, etc.).

    Compatível com o contrato lógico da Spec §7.1, mas com reason codes
    próprios de ator (ActorReasonCode) e evidência mínima distinta.
    """
    decision: RelevanceDecision
    reason_codes: list[str] = Field(default_factory=list)
    evidence: list[RelevanceEvidence] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    classifier_version: str = f"{CLASSIFIER_VERSION}.actor-v1"


class InvestorVerdict(ActorVerdict):
    """Critérios mínimos: identidade, página oficial, atuação material
    com startups/tecnologia, relevância Brasil, tese/estágio/setores/
    geografia/ticket explícitos ou unknown quando sem evidência.
    """


class IctVerdict(ActorVerdict):
    """Critérios mínimos: identidade e vínculo, capacidade de cooperação
    tecnológica com empresas, competências/localização/status sustentados
    por fonte oficial, atualização ou data de verificação explícita.
    """


class ProgramVerdict(ActorVerdict):
    """Critérios mínimos: identidade e operador verificáveis, relação
    demonstrável com oportunidade ou mecanismo relevante, campos
    específicos validados conforme schema próprio do kind.
    """


class AgencyVerdict(ActorVerdict):
    """Critérios mínimos: identidade e operador verificáveis, relação
    demonstrável com oportunidade ou mecanismo relevante, campos
    específicos validados conforme schema próprio do kind.
    """


# ── Utilitários ──────────────────────────────────────────────────────────


def is_inclusion_code(code: str) -> bool:
    return code.startswith("R")


def is_exclusion_code(code: str) -> bool:
    return code.startswith("X")


__all__ = [
    "CLASSIFIER_VERSION",
    "RelevanceDecision",
    "InclusionCode",
    "ExclusionCode",
    "OpportunityReasonCode",
    "ActorReasonCode",
    "ClassificationKind",
    "EvidenceSource",
    "EvidenceLocator",
    "RelevanceEvidence",
    "RelevanceVerdict",
    "ActorVerdict",
    "InvestorVerdict",
    "IctVerdict",
    "ProgramVerdict",
    "AgencyVerdict",
    "is_inclusion_code",
    "is_exclusion_code",
]
