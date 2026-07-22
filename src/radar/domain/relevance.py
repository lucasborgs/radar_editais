"""
Radar Data Trust 00 — Contrato de relevância.

Tipos versionados para decisão, reason codes e evidência de relevância,
com contratos separados por kind (oportunidade ≠ ator/catálogo).

Sem lógica de classificação, sem chamadas LLM, sem alteração produtiva.
"""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field, field_validator, model_validator

CLASSIFIER_VERSION = "radar-data-trust-relevance-v1"


class RelevanceDecision(str, Enum):
    IN_SCOPE = "in_scope"
    OUT_OF_SCOPE = "out_of_scope"
    NEEDS_REVIEW = "needs_review"


class InclusionCode(str, Enum):
    R1_ENTERPRISE_PATH = "R1_ENTERPRISE_PATH"
    R2_TECH_INNOVATION = "R2_TECH_INNOVATION"
    R3_ACTIONABLE = "R3_ACTIONABLE"
    R4_RELEVANT_BENEFIT = "R4_RELEVANT_BENEFIT"
    R5_BRAZIL_RELEVANCE = "R5_BRAZIL_RELEVANCE"


class ExclusionCode(str, Enum):
    X1_ACADEMIC_ONLY = "X1_ACADEMIC_ONLY"
    X2_CONVENTIONAL_CREDIT = "X2_CONVENTIONAL_CREDIT"
    X3_GENERIC_PROCUREMENT = "X3_GENERIC_PROCUREMENT"
    X4_EVENT_CONTENT = "X4_EVENT_CONTENT"
    X5_GENERIC_SUPPORT = "X5_GENERIC_SUPPORT"
    X6_NON_TECH = "X6_NON_TECH"
    X7_NO_ENTERPRISE_PATH = "X7_NO_ENTERPRISE_PATH"
    X8_INVESTOR_DIRECTORY = "X8_INVESTOR_DIRECTORY"


class ClassificationKind(str, Enum):
    OPPORTUNITY = "opportunity"
    INVESTOR = "investor"
    ICT = "ict"
    PROGRAM = "program"
    AGENCY = "agency"


class EvidenceSource(str, Enum):
    LANDING_PAGE = "landing_page"
    EDITAL = "edital"
    ANNEX = "anexo"
    OFFICIAL_PAGE = "official_page"
    CURATED_RECORD = "curated_record"


class EvidenceLocator(BaseModel):
    model_config = {"extra": "forbid"}

    document: str | None = None
    page: int | None = None

    @field_validator("page")
    @classmethod
    def _page_must_be_positive(cls, v: int | None) -> int | None:
        if v is not None and v < 1:
            raise ValueError("page must be >= 1 (1-based)")
        return v


class _BaseEvidence(BaseModel):
    model_config = {"extra": "forbid"}

    quote: str | None = None
    source: EvidenceSource | None = None
    locator: EvidenceLocator | None = None


class RelevanceEvidence(_BaseEvidence):
    code: InclusionCode | ExclusionCode


class ActorEvidence(_BaseEvidence):
    code: str  # taxonomia de reason codes de atores definida em RT00-T02


class RelevanceVerdict(BaseModel):
    """Classificação de relevância de uma oportunidade.

    Invariantes:
      - in_scope: reason_codes contém todas as InclusionCodes; exclusion_codes vazio.
      - out_of_scope: exclusion_codes não vazio e cada exclusion_code em reason_codes.
      - needs_review: sem validação extra; ambiguidade não vira rejeição.
    """
    model_config = {"extra": "forbid"}

    decision: RelevanceDecision
    reason_codes: list[InclusionCode | ExclusionCode] = Field(default_factory=list)
    exclusion_codes: list[ExclusionCode] = Field(default_factory=list)
    evidence: list[RelevanceEvidence] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    classifier_version: str = CLASSIFIER_VERSION

    @model_validator(mode="after")
    def _check_invariants(self) -> RelevanceVerdict:
        dec = self.decision
        if dec is RelevanceDecision.IN_SCOPE:
            present_inc = {c for c in self.reason_codes if isinstance(c, InclusionCode)}
            required = set(InclusionCode)
            missing = required - present_inc
            if missing:
                raise ValueError(
                    f"in_scope requires all InclusionCodes, missing: "
                    f"{[m.value for m in sorted(missing, key=lambda x: x.value)]}"
                )
            if self.exclusion_codes:
                raise ValueError("in_scope must have empty exclusion_codes")
        elif dec is RelevanceDecision.OUT_OF_SCOPE:
            if not self.exclusion_codes:
                raise ValueError("out_of_scope requires at least one ExclusionCode")
            x_values = set(self.exclusion_codes)
            r_values = set(self.reason_codes)
            missing_x = x_values - r_values
            if missing_x:
                raise ValueError(
                    f"exclusion_codes not found in reason_codes: "
                    f"{[m.value for m in sorted(missing_x, key=lambda x: x.value)]}"
                )
        return self


class ActorVerdict(BaseModel):
    """Classificação de relevância de um ator (investidor, ICT, etc.).

    Reason codes de atores serão definidos em RT00-T02.
    """
    model_config = {"extra": "forbid"}

    decision: RelevanceDecision
    kind: ClassificationKind
    reason_codes: list[str] = Field(default_factory=list)
    evidence: list[ActorEvidence] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    classifier_version: str = CLASSIFIER_VERSION


class InvestorVerdict(ActorVerdict):
    kind: ClassificationKind = ClassificationKind.INVESTOR


class IctVerdict(ActorVerdict):
    kind: ClassificationKind = ClassificationKind.ICT


class ProgramVerdict(ActorVerdict):
    kind: ClassificationKind = ClassificationKind.PROGRAM


class AgencyVerdict(ActorVerdict):
    kind: ClassificationKind = ClassificationKind.AGENCY


def is_inclusion_code(code: str) -> bool:
    return code in {m.value for m in InclusionCode}


def is_exclusion_code(code: str) -> bool:
    return code in {m.value for m in ExclusionCode}


__all__ = [
    "CLASSIFIER_VERSION",
    "RelevanceDecision",
    "InclusionCode",
    "ExclusionCode",
    "ClassificationKind",
    "EvidenceSource",
    "EvidenceLocator",
    "RelevanceEvidence",
    "ActorEvidence",
    "RelevanceVerdict",
    "ActorVerdict",
    "InvestorVerdict",
    "IctVerdict",
    "ProgramVerdict",
    "AgencyVerdict",
    "is_inclusion_code",
    "is_exclusion_code",
]
