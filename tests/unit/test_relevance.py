"""Testes do contrato de relevância (RT00-T01).

Cobre:
  - valores dos enums e serialização string;
  - round-trip JSON (pydantic) para todos os modelos;
  - rejeição de estados inválidos (enum errado, campo obrigatório ausente);
  - reason codes completos conforme spec;
  - separação oportunidade × ator;
  - constantes versionadas.
"""
from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from radar.domain.relevance import (
    CLASSIFIER_VERSION,
    ActorReasonCode,
    ActorVerdict,
    AgencyVerdict,
    ClassificationKind,
    EvidenceLocator,
    EvidenceSource,
    ExclusionCode,
    IctVerdict,
    InclusionCode,
    InvestorVerdict,
    OpportunityReasonCode,
    ProgramVerdict,
    RelevanceDecision,
    RelevanceEvidence,
    RelevanceVerdict,
    is_exclusion_code,
    is_inclusion_code,
)

# ── RelevanceDecision ────────────────────────────────────────────────────


class TestRelevanceDecision:
    def test_values(self):
        assert RelevanceDecision.IN_SCOPE.value == "in_scope"
        assert RelevanceDecision.OUT_OF_SCOPE.value == "out_of_scope"
        assert RelevanceDecision.NEEDS_REVIEW.value == "needs_review"

    def test_string_serialization(self):
        d = RelevanceDecision.IN_SCOPE
        assert json.loads(json.dumps(d.value)) == "in_scope"

    def test_from_string(self):
        assert RelevanceDecision("in_scope") is RelevanceDecision.IN_SCOPE
        assert RelevanceDecision("out_of_scope") is RelevanceDecision.OUT_OF_SCOPE
        assert RelevanceDecision("needs_review") is RelevanceDecision.NEEDS_REVIEW

    def test_invalid_value_raises(self):
        with pytest.raises(ValueError):
            RelevanceDecision("unsure")
        with pytest.raises(ValueError):
            RelevanceDecision("")


# ── InclusionCode ────────────────────────────────────────────────────────


class TestInclusionCode:
    def test_all_codes_match_spec(self):
        expected = {
            "R1_ENTERPRISE_PATH",
            "R2_TECH_INNOVATION",
            "R3_ACTIONABLE",
            "R4_RELEVANT_BENEFIT",
            "R5_BRAZIL_RELEVANCE",
        }
        assert {c.value for c in InclusionCode} == expected

    def test_from_string(self):
        assert InclusionCode("R1_ENTERPRISE_PATH") is InclusionCode.ENTERPRISE_PATH


# ── ExclusionCode ────────────────────────────────────────────────────────


class TestExclusionCode:
    def test_all_codes_match_spec(self):
        expected = {
            "X1_ACADEMIC_ONLY",
            "X2_CONVENTIONAL_CREDIT",
            "X3_GENERIC_PROCUREMENT",
            "X4_EVENT_CONTENT",
            "X5_GENERIC_SUPPORT",
            "X6_NON_TECH",
            "X7_NO_ENTERPRISE_PATH",
            "X8_INVESTOR_DIRECTORY",
        }
        assert {c.value for c in ExclusionCode} == expected


# ── OpportunityReasonCode ────────────────────────────────────────────────


class TestOpportunityReasonCode:
    def test_has_all_inclusion_and_exclusion(self):
        values = {c.value for c in OpportunityReasonCode}
        for inc in InclusionCode:
            assert inc.value in values
        for exc in ExclusionCode:
            assert exc.value in values

    def test_no_actor_codes(self):
        for c in OpportunityReasonCode:
            assert not c.value.startswith("A"), f"actor code in opportunity: {c}"


# ── ActorReasonCode ──────────────────────────────────────────────────────


class TestActorReasonCode:
    def test_all_codes_use_a_prefix(self):
        for c in ActorReasonCode:
            assert c.value.startswith("A"), f"non-actor prefix: {c}"

    def test_no_opportunity_codes(self):
        opp_values = {c.value for c in OpportunityReasonCode}
        for c in ActorReasonCode:
            assert c.value not in opp_values, f"opp code in actor: {c}"

    def test_from_string(self):
        assert ActorReasonCode("A1_IDENTITY_VERIFIABLE") is ActorReasonCode.IDENTITY_VERIFIABLE


# ── ClassificationKind ───────────────────────────────────────────────────


class TestClassificationKind:
    def test_values(self):
        assert ClassificationKind.OPPORTUNITY.value == "opportunity"
        assert ClassificationKind.INVESTOR.value == "investor"
        assert ClassificationKind.ICT.value == "ict"
        assert ClassificationKind.PROGRAM.value == "program"
        assert ClassificationKind.AGENCY.value == "agency"

    def test_separate_kinds(self):
        """Verifica que os kinds existem e são distintos."""
        kinds = set(ClassificationKind)
        assert len(kinds) == 5


# ── EvidenceSource ───────────────────────────────────────────────────────


class TestEvidenceSource:
    def test_values(self):
        assert EvidenceSource.LANDING_PAGE.value == "landing_page"
        assert EvidenceSource.EDITAL.value == "edital"
        assert EvidenceSource.ANNEX.value == "anexo"


# ── EvidenceLocator ──────────────────────────────────────────────────────


class TestEvidenceLocator:
    def test_defaults(self):
        loc = EvidenceLocator()
        assert loc.document is None
        assert loc.page is None

    def test_partial(self):
        loc = EvidenceLocator(document="Edital.pdf")
        assert loc.document == "Edital.pdf"
        assert loc.page is None

    def test_full(self):
        loc = EvidenceLocator(document="Edital.pdf", page=3)
        assert loc.document == "Edital.pdf"
        assert loc.page == 3

    def test_round_trip(self):
        loc = EvidenceLocator(document="Edital.pdf", page=3)
        assert EvidenceLocator.model_validate_json(loc.model_dump_json()) == loc


# ── RelevanceEvidence ────────────────────────────────────────────────────


class TestRelevanceEvidence:
    def test_minimal(self):
        ev = RelevanceEvidence(code="R1_ENTERPRISE_PATH")
        assert ev.code == "R1_ENTERPRISE_PATH"
        assert ev.quote is None

    def test_full(self):
        ev = RelevanceEvidence(
            code="R1_ENTERPRISE_PATH",
            quote="startups e PMEs",
            source=EvidenceSource.EDITAL,
            locator=EvidenceLocator(document="Edital.pdf", page=3),
        )
        assert ev.source is EvidenceSource.EDITAL
        assert ev.locator.document == "Edital.pdf"

    def test_round_trip(self):
        ev = RelevanceEvidence(
            code="R2_TECH_INNOVATION",
            quote="desenvolvimento tecnológico",
            source=EvidenceSource.LANDING_PAGE,
        )
        assert RelevanceEvidence.model_validate_json(ev.model_dump_json()) == ev


# ── RelevanceVerdict (oportunidade) ──────────────────────────────────────


class TestRelevanceVerdict:
    def test_minimal(self):
        v = RelevanceVerdict(decision=RelevanceDecision.IN_SCOPE)
        assert v.decision is RelevanceDecision.IN_SCOPE
        assert v.reason_codes == []
        assert v.exclusion_codes == []
        assert v.evidence == []
        assert v.missing_information == []
        assert v.classifier_version == CLASSIFIER_VERSION

    def test_invalid_decision_raises(self):
        with pytest.raises(ValidationError):
            RelevanceVerdict(decision="bogus")

    def test_full_verdict(self):
        v = RelevanceVerdict(
            decision=RelevanceDecision.IN_SCOPE,
            reason_codes=["R1_ENTERPRISE_PATH", "R2_TECH_INNOVATION"],
            evidence=[
                RelevanceEvidence(
                    code="R1_ENTERPRISE_PATH",
                    quote="empresas de base tecnológica",
                    source=EvidenceSource.EDITAL,
                )
            ],
            missing_information=["prazo_envio"],
        )
        assert len(v.evidence) == 1
        assert v.evidence[0].source is EvidenceSource.EDITAL

    def test_out_of_scope_with_exclusion(self):
        v = RelevanceVerdict(
            decision=RelevanceDecision.OUT_OF_SCOPE,
            exclusion_codes=["X1_ACADEMIC_ONLY"],
            reason_codes=["X1_ACADEMIC_ONLY"],
        )
        assert v.decision is RelevanceDecision.OUT_OF_SCOPE
        assert "X1_ACADEMIC_ONLY" in v.exclusion_codes

    def test_needs_review_with_missing(self):
        v = RelevanceVerdict(
            decision=RelevanceDecision.NEEDS_REVIEW,
            missing_information=["público elegível", "benefício financeiro"],
        )
        assert v.decision is RelevanceDecision.NEEDS_REVIEW
        assert "público elegível" in v.missing_information

    def test_round_trip_spec_contract(self):
        """Round-trip do contrato lógico da Spec §7.1."""
        raw = {
            "decision": "in_scope",
            "reason_codes": ["R1_ENTERPRISE_PATH", "R2_TECH_INNOVATION"],
            "exclusion_codes": [],
            "evidence": [
                {
                    "code": "R1_ENTERPRISE_PATH",
                    "quote": "...",
                    "source": "landing_page",
                    "locator": {"document": "Edital.pdf", "page": 3},
                }
            ],
            "missing_information": [],
            "classifier_version": CLASSIFIER_VERSION,
        }
        v = RelevanceVerdict.model_validate(raw)
        assert v.decision is RelevanceDecision.IN_SCOPE
        assert v.evidence[0].locator.document == "Edital.pdf"
        assert v.evidence[0].source is EvidenceSource.LANDING_PAGE
        dumped = json.loads(v.model_dump_json())
        assert dumped["classifier_version"] == CLASSIFIER_VERSION
        assert dumped["decision"] == "in_scope"

    def test_non_string_reason_code_allowed(self):
        """reason_codes é list[str]; valores de enum devem ser convertidos."""
        v = RelevanceVerdict(
            decision=RelevanceDecision.IN_SCOPE,
            reason_codes=[InclusionCode.ENTERPRISE_PATH.value],
        )
        assert "R1_ENTERPRISE_PATH" in v.reason_codes

    def test_unknown_classifier_version_allowed(self):
        """classifier_version é str livre; qualquer versão é aceita."""
        v = RelevanceVerdict(
            decision=RelevanceDecision.IN_SCOPE,
            classifier_version="custom-v2",
        )
        assert v.classifier_version == "custom-v2"


# ── ActorVerdict e subtipos ──────────────────────────────────────────────


class TestActorVerdict:
    def test_no_exclusion_codes_field(self):
        """ActorVerdict não tem exclusion_codes — usa reason_codes."""
        v = ActorVerdict(decision=RelevanceDecision.IN_SCOPE)
        assert not hasattr(v, "exclusion_codes") or v.model_dump().get("exclusion_codes") is None

    def test_verdict_default_version(self):
        v = ActorVerdict(decision=RelevanceDecision.IN_SCOPE)
        assert v.classifier_version == f"{CLASSIFIER_VERSION}.actor-v1"

    def test_round_trip(self):
        v = ActorVerdict(
            decision=RelevanceDecision.IN_SCOPE,
            reason_codes=["A1_IDENTITY_VERIFIABLE", "A2_OFFICIAL_PAGE"],
        )
        assert ActorVerdict.model_validate_json(v.model_dump_json()) == v


class TestInvestorVerdict:
    def test_is_actor_verdict(self):
        v = InvestorVerdict(decision=RelevanceDecision.OUT_OF_SCOPE)
        assert isinstance(v, ActorVerdict)

    def test_default_version(self):
        v = InvestorVerdict(decision=RelevanceDecision.IN_SCOPE)
        assert v.classifier_version == f"{CLASSIFIER_VERSION}.actor-v1"

    def test_round_trip(self):
        v = InvestorVerdict(
            decision=RelevanceDecision.NEEDS_REVIEW,
            missing_information=["ticket_range", "setores"],
        )
        assert InvestorVerdict.model_validate_json(v.model_dump_json()) == v


class TestIctVerdict:
    def test_is_actor_verdict(self):
        v = IctVerdict(decision=RelevanceDecision.IN_SCOPE)
        assert isinstance(v, ActorVerdict)

    def test_round_trip(self):
        v = IctVerdict(
            decision=RelevanceDecision.IN_SCOPE,
            reason_codes=["A1_IDENTITY_VERIFIABLE", "A5_CAPACITY_KNOWN"],
            evidence=[
                RelevanceEvidence(
                    code="A1_IDENTITY_VERIFIABLE",
                    quote="EMBRAPII Unidade Embrapii",
                    source=EvidenceSource.LANDING_PAGE,
                )
            ],
        )
        assert IctVerdict.model_validate_json(v.model_dump_json()) == v


class TestProgramVerdict:
    def test_is_actor_verdict(self):
        v = ProgramVerdict(decision=RelevanceDecision.IN_SCOPE)
        assert isinstance(v, ActorVerdict)

    def test_round_trip(self):
        v = ProgramVerdict(
            decision=RelevanceDecision.NEEDS_REVIEW,
            missing_information=["operador", "relação com mecanismo"],
        )
        assert ProgramVerdict.model_validate_json(v.model_dump_json()) == v


class TestAgencyVerdict:
    def test_is_actor_verdict(self):
        v = AgencyVerdict(decision=RelevanceDecision.IN_SCOPE)
        assert isinstance(v, ActorVerdict)

    def test_round_trip(self):
        v = AgencyVerdict(decision=RelevanceDecision.IN_SCOPE)
        assert AgencyVerdict.model_validate_json(v.model_dump_json()) == v


# ── Separação oportunidade × ator ────────────────────────────────────────


class TestKindSeparation:
    def test_opportunity_not_actor(self):
        """OpportunityReasonCode é diferente de ActorReasonCode."""
        opp_codes = {c.value for c in OpportunityReasonCode}
        actor_codes = {c.value for c in ActorReasonCode}
        assert opp_codes.isdisjoint(actor_codes)

    def test_verdict_types_are_distinct(self):
        v_opp = RelevanceVerdict(decision=RelevanceDecision.IN_SCOPE)
        v_actor = ActorVerdict(decision=RelevanceDecision.IN_SCOPE)
        assert type(v_opp) is not type(v_actor)
        assert isinstance(v_opp, RelevanceVerdict)
        assert isinstance(v_actor, ActorVerdict)

    def test_actor_verdict_no_exclusion_codes(self):
        """ActorVerdict e subtipos não usam exclusion_codes."""
        for cls in (ActorVerdict, InvestorVerdict, IctVerdict, ProgramVerdict, AgencyVerdict):
            v = cls(decision=RelevanceDecision.IN_SCOPE)
            dumped = v.model_dump()
            assert "exclusion_codes" not in dumped


# ── CLASSIFIER_VERSION ────────────────────────────────────────────────────


class TestClassifierVersion:
    def test_constant(self):
        assert CLASSIFIER_VERSION == "radar-data-trust-relevance-v1"

    def test_used_in_verdict_default(self):
        v = RelevanceVerdict(decision=RelevanceDecision.IN_SCOPE)
        assert v.classifier_version == CLASSIFIER_VERSION


# ── Utilitários ──────────────────────────────────────────────────────────


class TestUtils:
    def test_is_inclusion_code(self):
        assert is_inclusion_code("R1_ENTERPRISE_PATH")
        assert is_inclusion_code("R2_TECH_INNOVATION")
        assert not is_inclusion_code("X1_ACADEMIC_ONLY")
        assert not is_inclusion_code("A1_IDENTITY_VERIFIABLE")
        assert not is_inclusion_code("")

    def test_is_exclusion_code(self):
        assert is_exclusion_code("X1_ACADEMIC_ONLY")
        assert is_exclusion_code("X8_INVESTOR_DIRECTORY")
        assert not is_exclusion_code("R1_ENTERPRISE_PATH")
        assert not is_exclusion_code("A1_IDENTITY_VERIFIABLE")
        assert not is_exclusion_code("")
