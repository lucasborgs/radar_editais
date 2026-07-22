"""
Radar Data Trust 00 — Contrato de relevância.

Tipos versionados para decisão, reason codes e evidência de relevância,
com contratos separados por kind (oportunidade ≠ ator/catálogo).

Sem lógica de classificação, sem chamadas LLM, sem alteração produtiva.
"""
from __future__ import annotations

from enum import Enum
from typing import Annotated, Literal

from pydantic import BaseModel, Field, TypeAdapter, field_validator, model_validator

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


class InvestorReasonCode(str, Enum):
    INV_IDENTITY_VERIFIED = "INV_IDENTITY_VERIFIED"
    INV_TECH_STARTUP_ACTIVITY = "INV_TECH_STARTUP_ACTIVITY"
    INV_BRAZIL_RELEVANCE = "INV_BRAZIL_RELEVANCE"


class IctReasonCode(str, Enum):
    ICT_IDENTITY_VERIFIED = "ICT_IDENTITY_VERIFIED"
    ICT_INSTITUTIONAL_LINK_VERIFIED = "ICT_INSTITUTIONAL_LINK_VERIFIED"
    ICT_ENTERPRISE_TECH_COOP = "ICT_ENTERPRISE_TECH_COOP"
    ICT_CURRENT_STATUS_VERIFIED = "ICT_CURRENT_STATUS_VERIFIED"


class ProgramReasonCode(str, Enum):
    PRG_IDENTITY_OPERATOR_VERIFIED = "PRG_IDENTITY_OPERATOR_VERIFIED"
    PRG_RELEVANT_INNOVATION_MECHANISM = "PRG_RELEVANT_INNOVATION_MECHANISM"
    PRG_ENTERPRISE_RELEVANCE = "PRG_ENTERPRISE_RELEVANCE"


class AgencyReasonCode(str, Enum):
    AGY_IDENTITY_VERIFIED = "AGY_IDENTITY_VERIFIED"
    AGY_RELEVANT_INNOVATION_MANDATE = "AGY_RELEVANT_INNOVATION_MANDATE"
    AGY_BRAZIL_RELEVANCE = "AGY_BRAZIL_RELEVANCE"


class _BaseEvidence(BaseModel):
    model_config = {"extra": "forbid"}

    quote: str | None = None
    source: EvidenceSource | None = None
    locator: EvidenceLocator | None = None


class RelevanceEvidence(_BaseEvidence):
    code: InclusionCode | ExclusionCode


class ActorEvidence(_BaseEvidence):
    code: str


class InvestorEvidence(ActorEvidence):
    code: InvestorReasonCode


class IctEvidence(ActorEvidence):
    code: IctReasonCode


class ProgramEvidence(ActorEvidence):
    code: ProgramReasonCode


class AgencyEvidence(ActorEvidence):
    code: AgencyReasonCode


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
            if self.exclusion_codes:
                raise ValueError("in_scope must have empty exclusion_codes")
            present_inc = {c for c in self.reason_codes if isinstance(c, InclusionCode)}
            required = set(InclusionCode)
            missing = required - present_inc
            if missing:
                raise ValueError(
                    f"in_scope requires all InclusionCodes, missing: "
                    f"{[m.value for m in sorted(missing, key=lambda x: x.value)]}"
                )
            x_in_reason = {c for c in self.reason_codes if isinstance(c, ExclusionCode)}
            if x_in_reason:
                raise ValueError(
                    f"in_scope must not contain ExclusionCode in reason_codes, got: "
                    f"{[m.value for m in sorted(x_in_reason, key=lambda x: x.value)]}"
                )
        elif dec is RelevanceDecision.OUT_OF_SCOPE:
            if not self.exclusion_codes:
                raise ValueError("out_of_scope requires at least one ExclusionCode")
            x_in_reason = {c for c in self.reason_codes if isinstance(c, ExclusionCode)}
            x_expected = set(self.exclusion_codes)
            missing_x = x_expected - x_in_reason
            if missing_x:
                raise ValueError(
                    f"exclusion_codes not found in reason_codes: "
                    f"{[m.value for m in sorted(missing_x, key=lambda x: x.value)]}"
                )
            extra_x = x_in_reason - x_expected
            if extra_x:
                raise ValueError(
                    f"reason_codes contain ExclusionCodes not in exclusion_codes: "
                    f"{[m.value for m in sorted(extra_x, key=lambda x: x.value)]}"
                )
        return self


class ActorVerdict(BaseModel):
    """Classificação de relevância de um ator (investidor, ICT, etc.).
    """
    model_config = {"extra": "forbid"}

    decision: RelevanceDecision
    kind: ClassificationKind
    reason_codes: list[str] = Field(default_factory=list)
    evidence: list[ActorEvidence] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    classifier_version: str = CLASSIFIER_VERSION


class InvestorVerdict(ActorVerdict):
    kind: Literal[ClassificationKind.INVESTOR] = ClassificationKind.INVESTOR
    reason_codes: list[InvestorReasonCode] = Field(default_factory=list)
    evidence: list[InvestorEvidence] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_investor_invariants(self) -> InvestorVerdict:
        if self.decision is RelevanceDecision.IN_SCOPE:
            required = set(InvestorReasonCode)
            present = set(self.reason_codes)
            missing = required - present
            if missing:
                raise ValueError(
                    f"investor in_scope requires all InvestorReasonCodes, missing: "
                    f"{[m.value for m in sorted(missing, key=lambda x: x.value)]}"
                )
        return self


class IctVerdict(ActorVerdict):
    kind: Literal[ClassificationKind.ICT] = ClassificationKind.ICT
    reason_codes: list[IctReasonCode] = Field(default_factory=list)
    evidence: list[IctEvidence] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_ict_invariants(self) -> IctVerdict:
        if self.decision is RelevanceDecision.IN_SCOPE:
            required = set(IctReasonCode)
            present = set(self.reason_codes)
            missing = required - present
            if missing:
                raise ValueError(
                    f"ict in_scope requires all IctReasonCodes, missing: "
                    f"{[m.value for m in sorted(missing, key=lambda x: x.value)]}"
                )
        return self


class ProgramVerdict(ActorVerdict):
    kind: Literal[ClassificationKind.PROGRAM] = ClassificationKind.PROGRAM
    reason_codes: list[ProgramReasonCode] = Field(default_factory=list)
    evidence: list[ProgramEvidence] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_program_invariants(self) -> ProgramVerdict:
        if self.decision is RelevanceDecision.IN_SCOPE:
            required = set(ProgramReasonCode)
            present = set(self.reason_codes)
            missing = required - present
            if missing:
                raise ValueError(
                    f"program in_scope requires all ProgramReasonCodes, missing: "
                    f"{[m.value for m in sorted(missing, key=lambda x: x.value)]}"
                )
        return self


class AgencyVerdict(ActorVerdict):
    kind: Literal[ClassificationKind.AGENCY] = ClassificationKind.AGENCY
    reason_codes: list[AgencyReasonCode] = Field(default_factory=list)
    evidence: list[AgencyEvidence] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_agency_invariants(self) -> AgencyVerdict:
        if self.decision is RelevanceDecision.IN_SCOPE:
            required = set(AgencyReasonCode)
            present = set(self.reason_codes)
            missing = required - present
            if missing:
                raise ValueError(
                    f"agency in_scope requires all AgencyReasonCodes, missing: "
                    f"{[m.value for m in sorted(missing, key=lambda x: x.value)]}"
                )
        return self


ActorVerdictUnion = Annotated[
    InvestorVerdict | IctVerdict | ProgramVerdict | AgencyVerdict,
    Field(discriminator="kind"),
]
actor_verdict_adapter: TypeAdapter = TypeAdapter(ActorVerdictUnion)


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
    "InvestorReasonCode",
    "IctReasonCode",
    "ProgramReasonCode",
    "AgencyReasonCode",
    "EvidenceSource",
    "EvidenceLocator",
    "RelevanceEvidence",
    "ActorEvidence",
    "InvestorEvidence",
    "IctEvidence",
    "ProgramEvidence",
    "AgencyEvidence",
    "RelevanceVerdict",
    "ActorVerdict",
    "InvestorVerdict",
    "IctVerdict",
    "ProgramVerdict",
    "AgencyVerdict",
    "ActorVerdictUnion",
    "actor_verdict_adapter",
    "is_inclusion_code",
    "is_exclusion_code",
]
