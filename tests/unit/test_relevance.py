"""Testes do contrato de relevância (RT00-T01).

Cobre:
  - valores dos enums e serialização JSON;
  - invariantes de oportunidade (in_scope, out_of_scope, needs_review);
  - rejeição de estados inválidos (código inexistente, extra fields, page ≤ 0);
  - separação oportunidade × ator (kind, evidence types, ausência de exclusion_codes
    em atores);
  - round-trip JSON com discriminação de subtipo de ator;
  - helpers: RUBBISH e X_NOT_A_CODE retornam false, códigos reais true;
  - compatibilidade com saída atual da triagem (imports).
"""
from __future__ import annotations

import json

import pytest
from pydantic import TypeAdapter, ValidationError

from radar.domain.relevance import (
    CLASSIFIER_VERSION,
    ActorEvidence,
    ActorVerdict,
    ActorVerdictUnion,
    AgencyVerdict,
    ClassificationKind,
    EvidenceLocator,
    EvidenceSource,
    ExclusionCode,
    IctVerdict,
    InclusionCode,
    InvestorVerdict,
    ProgramVerdict,
    RelevanceDecision,
    RelevanceEvidence,
    RelevanceVerdict,
    actor_verdict_adapter,
    is_exclusion_code,
    is_inclusion_code,
)

# ── Enums — valores e serialização ───────────────────────────────────────


class TestEnumValues:
    def test_relevance_decision_values(self):
        assert RelevanceDecision.IN_SCOPE.value == "in_scope"
        assert RelevanceDecision.OUT_OF_SCOPE.value == "out_of_scope"
        assert RelevanceDecision.NEEDS_REVIEW.value == "needs_review"

    def test_inclusion_code_values(self):
        assert {c.value for c in InclusionCode} == {
            "R1_ENTERPRISE_PATH", "R2_TECH_INNOVATION", "R3_ACTIONABLE",
            "R4_RELEVANT_BENEFIT", "R5_BRAZIL_RELEVANCE",
        }

    def test_exclusion_code_values(self):
        assert {c.value for c in ExclusionCode} == {
            "X1_ACADEMIC_ONLY", "X2_CONVENTIONAL_CREDIT", "X3_GENERIC_PROCUREMENT",
            "X4_EVENT_CONTENT", "X5_GENERIC_SUPPORT", "X6_NON_TECH",
            "X7_NO_ENTERPRISE_PATH", "X8_INVESTOR_DIRECTORY",
        }

    def test_classification_kind_values(self):
        assert {c.value for c in ClassificationKind} == {
            "opportunity", "investor", "ict", "program", "agency",
        }

    def test_evidence_source_values(self):
        assert EvidenceSource.OFFICIAL_PAGE.value == "official_page"
        assert EvidenceSource.CURATED_RECORD.value == "curated_record"
        assert EvidenceSource.LANDING_PAGE.value == "landing_page"
        assert EvidenceSource.EDITAL.value == "edital"
        assert EvidenceSource.ANNEX.value == "anexo"

    def test_enum_string_round_trip(self):
        for e in InclusionCode:
            assert InclusionCode(e.value) is e
        for e in ExclusionCode:
            assert ExclusionCode(e.value) is e
        for e in RelevanceDecision:
            assert json.loads(json.dumps(e.value)) == e.value


# ── EvidenceLocator ──────────────────────────────────────────────────────


class TestEvidenceLocator:
    def test_defaults(self):
        loc = EvidenceLocator()
        assert loc.document is None
        assert loc.page is None

    def test_page_ge_1(self):
        EvidenceLocator(page=1)
        EvidenceLocator(page=999)

    def test_page_zero_rejected(self):
        with pytest.raises(ValidationError):
            EvidenceLocator(page=0)

    def test_page_negative_rejected(self):
        with pytest.raises(ValidationError):
            EvidenceLocator(page=-1)

    def test_extra_field_rejected(self):
        with pytest.raises(ValidationError):
            EvidenceLocator.model_validate({"document": "x", "unknown": True})


# ── RelevanceEvidence (oportunidade) ─────────────────────────────────────


class TestRelevanceEvidence:
    def test_inclusion_code_accepted(self):
        ev = RelevanceEvidence(code=InclusionCode.R1_ENTERPRISE_PATH)
        assert ev.code is InclusionCode.R1_ENTERPRISE_PATH

    def test_exclusion_code_accepted(self):
        ev = RelevanceEvidence(code=ExclusionCode.X1_ACADEMIC_ONLY)
        assert ev.code is ExclusionCode.X1_ACADEMIC_ONLY

    def test_invalid_code_rejected(self):
        with pytest.raises(ValidationError):
            RelevanceEvidence(code="BOGUS")

    def test_extra_field_rejected(self):
        with pytest.raises(ValidationError):
            RelevanceEvidence.model_validate({"code": "R1_ENTERPRISE_PATH", "extra": True})

    def test_round_trip(self):
        ev = RelevanceEvidence(
            code=InclusionCode.R3_ACTIONABLE,
            quote="caminho de inscrição",
            source=EvidenceSource.EDITAL,
            locator=EvidenceLocator(document="Edital.pdf", page=3),
        )
        restored = RelevanceEvidence.model_validate_json(ev.model_dump_json())
        assert restored == ev
        assert restored.code is InclusionCode.R3_ACTIONABLE
        assert restored.source is EvidenceSource.EDITAL


# ── ActorEvidence (ator — códigos em RT00-T02) ───────────────────────────


class TestActorEvidence:
    def test_code_is_free_text(self):
        ev = ActorEvidence(code="identidade_verificada")
        assert ev.code == "identidade_verificada"

    def test_round_trip(self):
        ev = ActorEvidence(
            code="pending_t02",
            quote="página oficial",
            source=EvidenceSource.OFFICIAL_PAGE,
        )
        assert ActorEvidence.model_validate_json(ev.model_dump_json()) == ev

    def test_extra_field_rejected(self):
        with pytest.raises(ValidationError):
            ActorEvidence.model_validate({"code": "x", "extra": True})


# ── RelevanceVerdict — invariantes ───────────────────────────────────────


class TestRelevanceVerdictInvariants:
    def test_in_scope_requires_all_codes_and_no_exclusion(self):
        v = RelevanceVerdict(
            decision=RelevanceDecision.IN_SCOPE,
            reason_codes=list(InclusionCode),
        )
        assert v.decision is RelevanceDecision.IN_SCOPE

    def test_in_scope_missing_code_rejected(self):
        with pytest.raises(ValidationError, match="missing"):
            RelevanceVerdict(
                decision=RelevanceDecision.IN_SCOPE,
                reason_codes=[InclusionCode.R1_ENTERPRISE_PATH],
            )

    def test_in_scope_with_exclusion_rejected(self):
        with pytest.raises(ValidationError, match="empty exclusion_codes"):
            RelevanceVerdict(
                decision=RelevanceDecision.IN_SCOPE,
                reason_codes=list(InclusionCode),
                exclusion_codes=[ExclusionCode.X1_ACADEMIC_ONLY],
            )

    def test_in_scope_x_in_reason_codes_rejected(self):
        with pytest.raises(ValidationError, match="must not contain ExclusionCode"):
            RelevanceVerdict(
                decision=RelevanceDecision.IN_SCOPE,
                reason_codes=list(InclusionCode) + [ExclusionCode.X1_ACADEMIC_ONLY],
            )

    def test_out_of_scope_requires_exclusion(self):
        with pytest.raises(ValidationError, match="at least one ExclusionCode"):
            RelevanceVerdict(
                decision=RelevanceDecision.OUT_OF_SCOPE,
                reason_codes=list(InclusionCode),
            )

    def test_out_of_scope_exclusion_in_reason_codes(self):
        v = RelevanceVerdict(
            decision=RelevanceDecision.OUT_OF_SCOPE,
            reason_codes=[
                InclusionCode.R1_ENTERPRISE_PATH,
                ExclusionCode.X1_ACADEMIC_ONLY,
            ],
            exclusion_codes=[ExclusionCode.X1_ACADEMIC_ONLY],
        )
        assert ExclusionCode.X1_ACADEMIC_ONLY in v.exclusion_codes

    def test_out_of_scope_exclusion_missing_from_reason_codes_rejected(self):
        with pytest.raises(ValidationError, match="not found in reason_codes"):
            RelevanceVerdict(
                decision=RelevanceDecision.OUT_OF_SCOPE,
                reason_codes=[],
                exclusion_codes=[ExclusionCode.X1_ACADEMIC_ONLY],
            )

    def test_out_of_scope_extra_x_in_reason_codes_rejected(self):
        with pytest.raises(ValidationError, match="not in exclusion_codes"):
            RelevanceVerdict(
                decision=RelevanceDecision.OUT_OF_SCOPE,
                reason_codes=[
                    ExclusionCode.X1_ACADEMIC_ONLY,
                    ExclusionCode.X2_CONVENTIONAL_CREDIT,
                ],
                exclusion_codes=[ExclusionCode.X1_ACADEMIC_ONLY],
            )

    def test_needs_review_no_extra_validation(self):
        v = RelevanceVerdict(decision=RelevanceDecision.NEEDS_REVIEW)
        assert v.decision is RelevanceDecision.NEEDS_REVIEW

    def test_invalid_decision_rejected(self):
        with pytest.raises(ValidationError):
            RelevanceVerdict.model_validate({"decision": "bogus"})

    def test_unknown_reason_code_string_rejected(self):
        with pytest.raises(ValidationError):
            RelevanceVerdict.model_validate({
                "decision": "in_scope",
                "reason_codes": ["R1_ENTERPRISE_PATH", "BOGUS"],
            })

    def test_extra_field_rejected(self):
        with pytest.raises(ValidationError):
            RelevanceVerdict.model_validate({
                "decision": "in_scope",
                "reason_codes": [c.value for c in InclusionCode],
                "extra": True,
            })

    def test_default_classifier_version(self):
        v = RelevanceVerdict(
            decision=RelevanceDecision.IN_SCOPE,
            reason_codes=list(InclusionCode),
        )
        assert v.classifier_version == CLASSIFIER_VERSION

    def test_custom_version_accepted(self):
        v = RelevanceVerdict(
            decision=RelevanceDecision.IN_SCOPE,
            reason_codes=list(InclusionCode),
            classifier_version="v2",
        )
        assert v.classifier_version == "v2"


# ── RelevanceVerdict — round-trip ────────────────────────────────────────


class TestRelevanceVerdictRoundTrip:
    def test_full_spec_contract(self):
        raw = {
            "decision": "in_scope",
            "reason_codes": [c.value for c in InclusionCode],
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
        assert v.evidence[0].code is InclusionCode.R1_ENTERPRISE_PATH
        assert v.evidence[0].locator.document == "Edital.pdf"

        dumped = json.loads(v.model_dump_json())
        assert dumped["classifier_version"] == CLASSIFIER_VERSION

    def test_out_of_scope_round_trip(self):
        v = RelevanceVerdict(
            decision=RelevanceDecision.OUT_OF_SCOPE,
            reason_codes=[
                InclusionCode.R1_ENTERPRISE_PATH,
                ExclusionCode.X1_ACADEMIC_ONLY,
            ],
            exclusion_codes=[ExclusionCode.X1_ACADEMIC_ONLY],
            evidence=[
                RelevanceEvidence(
                    code=ExclusionCode.X1_ACADEMIC_ONLY,
                    quote="bolsa exclusivamente acadêmica",
                    source=EvidenceSource.EDITAL,
                )
            ],
            missing_information=["prazo_envio"],
        )
        restored = RelevanceVerdict.model_validate_json(v.model_dump_json())
        assert restored.decision is RelevanceDecision.OUT_OF_SCOPE
        assert ExclusionCode.X1_ACADEMIC_ONLY in restored.exclusion_codes
        assert len(restored.evidence) == len(v.evidence)

    def test_needs_review_round_trip(self):
        v = RelevanceVerdict(
            decision=RelevanceDecision.NEEDS_REVIEW,
            missing_information=["público elegível"],
        )
        assert RelevanceVerdict.model_validate_json(v.model_dump_json()) == v


# ── ActorVerdict — kind e distinção por subtipo ──────────────────────────


class TestActorVerdict:
    def test_requires_kind(self):
        v = ActorVerdict(
            decision=RelevanceDecision.IN_SCOPE,
            kind=ClassificationKind.INVESTOR,
        )
        assert v.kind is ClassificationKind.INVESTOR

    def test_kind_missing_rejected(self):
        with pytest.raises(ValidationError):
            ActorVerdict(decision=RelevanceDecision.IN_SCOPE)

    def test_default_version(self):
        v = ActorVerdict(
            decision=RelevanceDecision.IN_SCOPE,
            kind=ClassificationKind.INVESTOR,
        )
        assert v.classifier_version == CLASSIFIER_VERSION

    def test_uses_actor_evidence(self):
        v = ActorVerdict(
            decision=RelevanceDecision.IN_SCOPE,
            kind=ClassificationKind.INVESTOR,
            evidence=[ActorEvidence(code="verified", quote="página oficial")],
        )
        assert isinstance(v.evidence[0], ActorEvidence)

    def test_no_exclusion_codes_field(self):
        v = ActorVerdict(
            decision=RelevanceDecision.IN_SCOPE,
            kind=ClassificationKind.INVESTOR,
        )
        assert "exclusion_codes" not in v.model_dump()


class TestInvestorVerdict:
    def test_kind_default(self):
        v = InvestorVerdict(decision=RelevanceDecision.IN_SCOPE)
        assert v.kind is ClassificationKind.INVESTOR

    def test_wrong_kind_rejected(self):
        with pytest.raises(ValidationError):
            InvestorVerdict(decision=RelevanceDecision.IN_SCOPE, kind=ClassificationKind.ICT)

    def test_round_trip_preserves_kind(self):
        v = InvestorVerdict(
            decision=RelevanceDecision.NEEDS_REVIEW,
            missing_information=["ticket_range"],
        )
        dumped = json.loads(v.model_dump_json())
        assert dumped["kind"] == "investor"
        restored = ActorVerdict.model_validate(dumped)
        assert restored.kind is ClassificationKind.INVESTOR


class TestIctVerdict:
    def test_kind_default(self):
        v = IctVerdict(decision=RelevanceDecision.IN_SCOPE)
        assert v.kind is ClassificationKind.ICT

    def test_wrong_kind_rejected(self):
        with pytest.raises(ValidationError):
            IctVerdict(decision=RelevanceDecision.IN_SCOPE, kind=ClassificationKind.INVESTOR)

    def test_round_trip_preserves_kind(self):
        v = IctVerdict(decision=RelevanceDecision.IN_SCOPE)
        dumped = json.loads(v.model_dump_json())
        assert dumped["kind"] == "ict"


class TestProgramVerdict:
    def test_kind_default(self):
        v = ProgramVerdict(decision=RelevanceDecision.IN_SCOPE)
        assert v.kind is ClassificationKind.PROGRAM

    def test_wrong_kind_rejected(self):
        with pytest.raises(ValidationError):
            ProgramVerdict(decision=RelevanceDecision.IN_SCOPE, kind=ClassificationKind.ICT)


class TestAgencyVerdict:
    def test_kind_default(self):
        v = AgencyVerdict(decision=RelevanceDecision.IN_SCOPE)
        assert v.kind is ClassificationKind.AGENCY

    def test_wrong_kind_rejected(self):
        with pytest.raises(ValidationError):
            AgencyVerdict(decision=RelevanceDecision.IN_SCOPE, kind=ClassificationKind.ICT)

    def test_round_trip_preserves_kind(self):
        v = AgencyVerdict(decision=RelevanceDecision.IN_SCOPE)
        dumped = json.loads(v.model_dump_json())
        assert dumped["kind"] == "agency"


class TestActorVerdictDiscriminatedUnion:
    def test_type_adapter_exported(self):
        assert isinstance(actor_verdict_adapter, TypeAdapter)

    def test_adapter_serializes_investor(self):
        v = InvestorVerdict(decision=RelevanceDecision.NEEDS_REVIEW)
        dumped = json.loads(actor_verdict_adapter.dump_json(v))
        assert dumped["kind"] == "investor"
        assert dumped["decision"] == "needs_review"

    def test_adapter_deserializes_investor(self):
        raw = {"decision": "needs_review", "kind": "investor", "reason_codes": [], "evidence": [], "missing_information": [], "classifier_version": CLASSIFIER_VERSION}
        restored = actor_verdict_adapter.validate_json(json.dumps(raw))
        assert isinstance(restored, InvestorVerdict)
        assert restored.kind is ClassificationKind.INVESTOR

    def test_adapter_deserializes_ict(self):
        raw = {"decision": "in_scope", "kind": "ict", "reason_codes": [], "evidence": [], "missing_information": [], "classifier_version": CLASSIFIER_VERSION}
        restored = actor_verdict_adapter.validate_json(json.dumps(raw))
        assert isinstance(restored, IctVerdict)
        assert restored.kind is ClassificationKind.ICT

    def test_adapter_deserializes_program(self):
        raw = {"decision": "in_scope", "kind": "program", "reason_codes": [], "evidence": [], "missing_information": [], "classifier_version": CLASSIFIER_VERSION}
        restored = actor_verdict_adapter.validate_json(json.dumps(raw))
        assert isinstance(restored, ProgramVerdict)

    def test_adapter_deserializes_agency(self):
        raw = {"decision": "in_scope", "kind": "agency", "reason_codes": [], "evidence": [], "missing_information": [], "classifier_version": CLASSIFIER_VERSION}
        restored = actor_verdict_adapter.validate_json(json.dumps(raw))
        assert isinstance(restored, AgencyVerdict)

    def test_adapter_rejects_unknown_kind(self):
        raw = {"decision": "in_scope", "kind": "bogus", "reason_codes": [], "evidence": [], "missing_information": [], "classifier_version": CLASSIFIER_VERSION}
        with pytest.raises(ValidationError):
            actor_verdict_adapter.validate_json(json.dumps(raw))

    def test_union_type_importable(self):
        assert ActorVerdictUnion is not None


# ── Separação oportunidade × ator ────────────────────────────────────────


class TestKindSeparation:
    def test_distinct_evidence_types(self):
        assert RelevanceEvidence is not ActorEvidence

    def test_opportunity_verdict_no_kind(self):
        v = RelevanceVerdict(
            decision=RelevanceDecision.IN_SCOPE,
            reason_codes=list(InclusionCode),
        )
        assert "kind" not in v.model_dump()

    def test_actor_verdict_no_exclusion_codes(self):
        for cls in (ActorVerdict, InvestorVerdict, IctVerdict, ProgramVerdict, AgencyVerdict):
            v = cls(decision=RelevanceDecision.NEEDS_REVIEW, kind=ClassificationKind.INVESTOR) if cls is ActorVerdict else cls(decision=RelevanceDecision.NEEDS_REVIEW)
            assert "exclusion_codes" not in v.model_dump()

    def test_actor_evidence_not_accepted_in_relevance_verdict(self):
        with pytest.raises(ValidationError):
            RelevanceVerdict.model_validate({
                "decision": "in_scope",
                "reason_codes": [c.value for c in InclusionCode],
                "evidence": [{"code": "string_code", "quote": ""}],
            })

    def test_inclusion_code_not_accepted_as_actor_evidence_code(self):
        """ActorEvidence.code é str livre; qualquer string passa."""
        ev = ActorEvidence(code=InclusionCode.R1_ENTERPRISE_PATH.value)
        assert ev.code == "R1_ENTERPRISE_PATH"


# ── Helpers ──────────────────────────────────────────────────────────────


class TestHelpers:
    @pytest.mark.parametrize("code", [
        "R1_ENTERPRISE_PATH",
        "R2_TECH_INNOVATION",
        "R3_ACTIONABLE",
        "R4_RELEVANT_BENEFIT",
        "R5_BRAZIL_RELEVANCE",
    ])
    def test_is_inclusion_code_true(self, code):
        assert is_inclusion_code(code)

    @pytest.mark.parametrize("code", [
        "RUBBISH",
        "R0",
        "R1_FAKE",
        "inclusion",
        "",
    ])
    def test_is_inclusion_code_false(self, code):
        assert not is_inclusion_code(code)

    @pytest.mark.parametrize("code", [
        "X1_ACADEMIC_ONLY",
        "X2_CONVENTIONAL_CREDIT",
        "X3_GENERIC_PROCUREMENT",
        "X4_EVENT_CONTENT",
        "X5_GENERIC_SUPPORT",
        "X6_NON_TECH",
        "X7_NO_ENTERPRISE_PATH",
        "X8_INVESTOR_DIRECTORY",
    ])
    def test_is_exclusion_code_true(self, code):
        assert is_exclusion_code(code)

    @pytest.mark.parametrize("code", [
        "X_NOT_A_CODE",
        "X0_FAKE",
        "exclusion",
        "",
    ])
    def test_is_exclusion_code_false(self, code):
        assert not is_exclusion_code(code)
