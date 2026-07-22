"""Testes do loader hermético de goldens de relevância (RT00-T02).

Cobre:
  - parse e round-trip dos códigos por kind;
  - rejeição de código pertencente a outro kind;
  - invariantes de ator in_scope;
  - loader hermético;
  - unicidade e completude do manifesto;
  - correspondência kind/arquivo;
  - integridade dos hashes dos snapshots;
  - distribuição contendo os três estados no corpus total;
  - manutenção dos 122 casos do triage legado.
"""
from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from radar.core.eval.relevance_goldens import RelevanceGoldenLoader
from radar.domain.relevance import (
    AgencyReasonCode,
    AgencyVerdict,
    IctReasonCode,
    IctVerdict,
    InvestorReasonCode,
    InvestorVerdict,
    ProgramReasonCode,
    ProgramVerdict,
    RelevanceDecision,
    actor_verdict_adapter,
)

# ── Loader hermético ──────────────────────────────────────────────────────


class TestGoldenLoader:
    def test_loads_all_datasets(self):
        loader = RelevanceGoldenLoader()
        data = loader.load_all()
        assert set(data.keys()) == {"opportunities", "investors", "icts", "programs", "agencies"}
        assert len(data["opportunities"]) >= 6
        assert len(data["investors"]) >= 2
        assert len(data["icts"]) >= 2
        assert len(data["programs"]) >= 2
        assert len(data["agencies"]) >= 2

    def test_validation_passes(self):
        loader = RelevanceGoldenLoader()
        loader.load_all()
        errors = loader.validate_all()
        assert not errors, f"validation errors: {errors}"

    def test_actor_sources_hashes_valid(self):
        loader = RelevanceGoldenLoader()
        loader.load_all()
        errors = loader.validate_actor_sources()
        assert not errors, f"actor source hash errors: {errors}"

    def test_distribution_contains_all_three_states(self):
        loader = RelevanceGoldenLoader()
        loader.load_all()
        dist = loader.distribution()
        assert dist.get("in_scope", 0) >= 2
        assert dist.get("out_of_scope", 0) >= 2
        assert dist.get("needs_review", 0) >= 2


# ── Manifesto ─────────────────────────────────────────────────────────────


class TestManifest:
    def test_manifest_ids_match_datasets(self):
        loader = RelevanceGoldenLoader()
        loader.load_all()
        errors = loader.validate_all()
        assert not errors

    def test_manifest_total_count(self):
        loader = RelevanceGoldenLoader()
        data = loader.load_all()
        total = sum(len(items) for items in data.values())
        manifest = loader.manifest
        assert manifest["corpus_stats"]["total_cases"] == total

    def test_triage_legacy_preserved(self):
        from radar.core.eval.relevance_goldens import GOLDEN_DIR
        triage_path = GOLDEN_DIR.parent / "triage.json"
        assert triage_path.exists()
        cases = json.loads(triage_path.read_text(encoding="utf-8"))
        assert len(cases) == 122


# ── Kind/arquivo correspondência ──────────────────────────────────────────


class TestKindFileMatch:
    def test_all_opportunities_have_correct_kind(self):
        loader = RelevanceGoldenLoader()
        data = loader.load_all()
        for item in data["opportunities"]:
            assert item["kind"] == "opportunity"

    def test_all_investors_have_correct_kind(self):
        loader = RelevanceGoldenLoader()
        data = loader.load_all()
        for item in data["investors"]:
            assert item["kind"] == "investor"

    def test_all_icts_have_correct_kind(self):
        loader = RelevanceGoldenLoader()
        data = loader.load_all()
        for item in data["icts"]:
            assert item["kind"] == "ict"

    def test_all_programs_have_correct_kind(self):
        loader = RelevanceGoldenLoader()
        data = loader.load_all()
        for item in data["programs"]:
            assert item["kind"] == "program"

    def test_all_agencies_have_correct_kind(self):
        loader = RelevanceGoldenLoader()
        data = loader.load_all()
        for item in data["agencies"]:
            assert item["kind"] == "agency"


# ── Veredictos — parse e round-trip ───────────────────────────────────────


class TestVerdictParsing:
    def test_opportunity_verdict_validated(self):
        loader = RelevanceGoldenLoader()
        data = loader.load_all()
        for item in data["opportunities"]:
            from radar.domain.relevance import RelevanceVerdict
            v = RelevanceVerdict.model_validate(item["verdict"])
            assert v.decision in (
                RelevanceDecision.IN_SCOPE,
                RelevanceDecision.OUT_OF_SCOPE,
                RelevanceDecision.NEEDS_REVIEW,
            )

    def test_investor_verdict_validated(self):
        loader = RelevanceGoldenLoader()
        data = loader.load_all()
        for item in data["investors"]:
            v = actor_verdict_adapter.validate_python(item["verdict"])
            assert isinstance(v, InvestorVerdict)

    def test_ict_verdict_validated(self):
        loader = RelevanceGoldenLoader()
        data = loader.load_all()
        for item in data["icts"]:
            v = actor_verdict_adapter.validate_python(item["verdict"])
            assert isinstance(v, IctVerdict)

    def test_program_verdict_validated(self):
        loader = RelevanceGoldenLoader()
        data = loader.load_all()
        for item in data["programs"]:
            v = actor_verdict_adapter.validate_python(item["verdict"])
            assert isinstance(v, ProgramVerdict)

    def test_agency_verdict_validated(self):
        loader = RelevanceGoldenLoader()
        data = loader.load_all()
        for item in data["agencies"]:
            v = actor_verdict_adapter.validate_python(item["verdict"])
            assert isinstance(v, AgencyVerdict)


# ── Reason codes — rejeição cross-kind ────────────────────────────────────


class TestCrossKindRejection:
    def test_ict_code_in_investor_verdict_fails(self):
        with pytest.raises(ValidationError):
            InvestorVerdict(
                decision=RelevanceDecision.IN_SCOPE,
                reason_codes=[IctReasonCode.ICT_IDENTITY_VERIFIED],
            )

    def test_investor_code_in_ict_verdict_fails(self):
        with pytest.raises(ValidationError):
            IctVerdict(
                decision=RelevanceDecision.IN_SCOPE,
                reason_codes=[InvestorReasonCode.INV_BRAZIL_RELEVANCE],
            )

    def test_program_code_in_agency_verdict_fails(self):
        with pytest.raises(ValidationError):
            AgencyVerdict(
                decision=RelevanceDecision.IN_SCOPE,
                reason_codes=[ProgramReasonCode.PRG_ENTERPRISE_RELEVANCE],
            )

    def test_agency_code_in_program_verdict_fails(self):
        with pytest.raises(ValidationError):
            ProgramVerdict(
                decision=RelevanceDecision.IN_SCOPE,
                reason_codes=[AgencyReasonCode.AGY_BRAZIL_RELEVANCE],
            )


# ── Round-trip JSON ───────────────────────────────────────────────────────


class TestRoundTrip:
    def test_investor_verdict_json_round_trip(self):
        v = InvestorVerdict(
            decision=RelevanceDecision.IN_SCOPE,
            reason_codes=list(InvestorReasonCode),
            evidence=[{"code": "INV_IDENTITY_VERIFIED", "quote": "verified"}],
        )
        dumped = json.loads(v.model_dump_json())
        restored = actor_verdict_adapter.validate_json(json.dumps(dumped))
        assert isinstance(restored, InvestorVerdict)

    def test_ict_verdict_json_round_trip(self):
        v = IctVerdict(
            decision=RelevanceDecision.IN_SCOPE,
            reason_codes=list(IctReasonCode),
        )
        restored = actor_verdict_adapter.validate_json(json.dumps(json.loads(v.model_dump_json())))
        assert isinstance(restored, IctVerdict)

    def test_program_verdict_json_round_trip(self):
        v = ProgramVerdict(
            decision=RelevanceDecision.IN_SCOPE,
            reason_codes=list(ProgramReasonCode),
        )
        restored = actor_verdict_adapter.validate_json(json.dumps(json.loads(v.model_dump_json())))
        assert isinstance(restored, ProgramVerdict)

    def test_agency_verdict_json_round_trip(self):
        v = AgencyVerdict(
            decision=RelevanceDecision.IN_SCOPE,
            reason_codes=list(AgencyReasonCode),
        )
        restored = actor_verdict_adapter.validate_json(json.dumps(json.loads(v.model_dump_json())))
        assert isinstance(restored, AgencyVerdict)
