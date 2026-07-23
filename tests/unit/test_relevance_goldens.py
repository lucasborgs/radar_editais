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

import hashlib
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
        assert len(data["icts"]) >= 1
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
        dec_counts: dict[str, int] = {}
        for k, v in dist.items():
            _kind, dec = k.split(":", 1)
            dec_counts[dec] = dec_counts.get(dec, 0) + v
        assert dec_counts.get("in_scope", 0) >= 2
        assert dec_counts.get("out_of_scope", 0) >= 2
        assert dec_counts.get("needs_review", 0) >= 2


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
            evidence=[{"code": c.value, "quote": "verified"} for c in InvestorReasonCode],
        )
        dumped = json.loads(v.model_dump_json())
        restored = actor_verdict_adapter.validate_json(json.dumps(dumped))
        assert isinstance(restored, InvestorVerdict)

    def test_ict_verdict_json_round_trip(self):
        v = IctVerdict(
            decision=RelevanceDecision.IN_SCOPE,
            reason_codes=list(IctReasonCode),
            evidence=[{"code": c.value, "quote": "verified"} for c in IctReasonCode],
        )
        restored = actor_verdict_adapter.validate_json(json.dumps(json.loads(v.model_dump_json())))
        assert isinstance(restored, IctVerdict)

    def test_program_verdict_json_round_trip(self):
        v = ProgramVerdict(
            decision=RelevanceDecision.IN_SCOPE,
            reason_codes=list(ProgramReasonCode),
            evidence=[{"code": c.value, "quote": "verified"} for c in ProgramReasonCode],
        )
        restored = actor_verdict_adapter.validate_json(json.dumps(json.loads(v.model_dump_json())))
        assert isinstance(restored, ProgramVerdict)

    def test_agency_verdict_json_round_trip(self):
        v = AgencyVerdict(
            decision=RelevanceDecision.IN_SCOPE,
            reason_codes=list(AgencyReasonCode),
            evidence=[{"code": c.value, "quote": "verified"} for c in AgencyReasonCode],
        )
        restored = actor_verdict_adapter.validate_json(json.dumps(json.loads(v.model_dump_json())))
        assert isinstance(restored, AgencyVerdict)


# ── Negative tests ──────────────────────────────────────────────────────────


class TestNegativeValidation:
    """Negative scenarios that should produce errors."""

    @pytest.fixture
    def empty_minimal_dataset(self, tmp_path):
        """Creates skeleton golden dir with bare-minimum valid content."""
        manifest = {
            "dataset_ids": {"opportunities": []},
            "corpus_stats": {"total_cases": 0, "by_kind": {"opportunities": 0}, "by_decision": {}},
            "review_status": "pending_owner",
        }
        (tmp_path / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False))
        (tmp_path / "opportunities.json").write_text("[]")
        (tmp_path / "investors.json").write_text("[]")
        (tmp_path / "icts.json").write_text("[]")
        (tmp_path / "programs.json").write_text("[]")
        (tmp_path / "agencies.json").write_text("[]")
        (tmp_path / "actor_sources.json").write_text("[]")
        return tmp_path

    def test_nonexistent_source_id(self, empty_minimal_dataset):
        manifest = {
            "dataset_ids": {"opportunities": ["op:bad-src"]},
            "corpus_stats": {"total_cases": 1, "by_kind": {"opportunities": 1}, "by_decision": {"opportunity:out_of_scope": 1}},
            "review_status": "pending_owner",
        }
        (empty_minimal_dataset / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False))
        opportunity = {
            "case_id": "op:bad-src",
            "kind": "opportunity",
            "source_ref": "src:nonexistent",
            "as_of": "2026-07-22",
            "human_reviewed": False,
            "verdict": {
                "decision": "out_of_scope",
                "reason_codes": ["X1_ACADEMIC_ONLY"],
                "exclusion_codes": ["X1_ACADEMIC_ONLY"],
                "classifier_version": "radar-data-trust-relevance-v1",
            },
        }
        (empty_minimal_dataset / "opportunities.json").write_text(json.dumps([opportunity], ensure_ascii=False))
        loader = RelevanceGoldenLoader(golden_dir=empty_minimal_dataset)
        loader.load_all()
        errors = loader.validate_all()
        assert any("not found in actor_sources" in e for e in errors), f"expected source_ref error, got: {errors}"

    def test_cross_file_duplicate(self, empty_minimal_dataset):
        manifest = {
            "dataset_ids": {"investors": ["dup:001"], "programs": ["dup:001"]},
            "corpus_stats": {"total_cases": 1, "by_kind": {"investors": 1, "programs": 1}, "by_decision": {"investor:needs_review": 1, "program:needs_review": 1}},
            "review_status": "pending_owner",
        }
        (empty_minimal_dataset / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False))
        item = {
            "case_id": "dup:001",
            "kind": "investor",
            "source_ref": "legacy_triage_case",
            "as_of": "2026-07-22",
            "human_reviewed": False,
            "verdict": {"decision": "needs_review", "reason_codes": [], "classifier_version": "radar-data-trust-relevance-v1"},
        }
        (empty_minimal_dataset / "investors.json").write_text(json.dumps([item], ensure_ascii=False))
        dup = dict(item)
        dup["kind"] = "program"
        (empty_minimal_dataset / "programs.json").write_text(json.dumps([dup], ensure_ascii=False))
        loader = RelevanceGoldenLoader(golden_dir=empty_minimal_dataset)
        loader.load_all()
        errors = loader.validate_all()
        assert any("duplicate case_id across files" in e for e in errors), f"expected cross-file dup error, got: {errors}"

    def test_wrong_manifest_total(self, empty_minimal_dataset):
        manifest = {
            "dataset_ids": {"opportunities": ["op:001"]},
            "corpus_stats": {"total_cases": 99, "by_kind": {"opportunities": 1}, "by_decision": {"opportunity:needs_review": 1}},
            "review_status": "pending_owner",
        }
        (empty_minimal_dataset / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False))
        item = {
            "case_id": "op:001",
            "kind": "opportunity",
            "source_ref": "legacy_triage_case",
            "as_of": "2026-07-22",
            "human_reviewed": False,
            "verdict": {"decision": "needs_review", "reason_codes": [], "classifier_version": "radar-data-trust-relevance-v1"},
        }
        (empty_minimal_dataset / "opportunities.json").write_text(json.dumps([item], ensure_ascii=False))
        loader = RelevanceGoldenLoader(golden_dir=empty_minimal_dataset)
        loader.load_all()
        errors = loader.validate_all()
        assert any("total_cases" in e and "99" in e for e in errors), f"expected total_cases mismatch error, got: {errors}"

    def test_human_reviewed_not_bool(self, empty_minimal_dataset):
        manifest = {
            "dataset_ids": {"opportunities": ["op:bad-hr"]},
            "corpus_stats": {"total_cases": 1, "by_kind": {"opportunities": 1}, "by_decision": {"opportunity:needs_review": 1}},
            "review_status": "pending_owner",
        }
        (empty_minimal_dataset / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False))
        item = {
            "case_id": "op:bad-hr",
            "kind": "opportunity",
            "source_ref": "legacy_triage_case",
            "as_of": "2026-07-22",
            "human_reviewed": "yes",
            "verdict": {"decision": "needs_review", "reason_codes": [], "classifier_version": "radar-data-trust-relevance-v1"},
        }
        (empty_minimal_dataset / "opportunities.json").write_text(json.dumps([item], ensure_ascii=False))
        loader = RelevanceGoldenLoader(golden_dir=empty_minimal_dataset)
        loader.load_all()
        errors = loader.validate_all()
        assert any("human_reviewed" in e and "bool" in e for e in errors), f"expected bool type error, got: {errors}"

    def test_review_status_not_pending_when_unreviewed(self, empty_minimal_dataset):
        manifest = {
            "dataset_ids": {"opportunities": ["op:unreviewed"]},
            "corpus_stats": {"total_cases": 1, "by_kind": {"opportunities": 1}, "by_decision": {"opportunity:needs_review": 1}},
            "review_status": "approved",
        }
        (empty_minimal_dataset / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False))
        item = {
            "case_id": "op:unreviewed",
            "kind": "opportunity",
            "source_ref": "legacy_triage_case",
            "as_of": "2026-07-22",
            "human_reviewed": False,
            "verdict": {"decision": "needs_review", "reason_codes": [], "classifier_version": "radar-data-trust-relevance-v1"},
        }
        (empty_minimal_dataset / "opportunities.json").write_text(json.dumps([item], ensure_ascii=False))
        loader = RelevanceGoldenLoader(golden_dir=empty_minimal_dataset)
        loader.load_all()
        errors = loader.validate_all()
        assert any("pending_owner" in e for e in errors), f"expected review_status error, got: {errors}"

    def test_actor_source_missing_hash(self, empty_minimal_dataset):
        manifest = {
            "dataset_ids": {"investors": ["inv:no-hash"]},
            "corpus_stats": {"total_cases": 1, "by_kind": {"investors": 1}, "by_decision": {"investor:in_scope": 1}},
            "review_status": "pending_owner",
        }
        (empty_minimal_dataset / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False))
        item = {
            "case_id": "inv:no-hash",
            "kind": "investor",
            "source_ref": "src:no-hash",
            "as_of": "2026-07-22",
            "human_reviewed": False,
            "verdict": {
                "decision": "in_scope",
                "kind": "investor",
                "reason_codes": ["INV_IDENTITY_VERIFIED", "INV_TECH_STARTUP_ACTIVITY", "INV_BRAZIL_RELEVANCE"],
                "classifier_version": "radar-data-trust-relevance-v1",
            },
        }
        (empty_minimal_dataset / "investors.json").write_text(json.dumps([item], ensure_ascii=False))
        source = {
            "source_id": "src:no-hash",
            "url": "https://example.com",
            "retrieved_at": "2026-07-22",
            "quote": "some quote",
            "kind": "investor",
            "source_record_id": "inv:no-hash",
        }
        (empty_minimal_dataset / "actor_sources.json").write_text(json.dumps([source], ensure_ascii=False))
        loader = RelevanceGoldenLoader(golden_dir=empty_minimal_dataset)
        loader.load_all()
        err1 = loader.validate_actor_sources()
        err2 = loader.validate_all()
        assert any("hash_sha256" in e for e in err1), f"expected hash_sha256 error in validate_actor_sources, got: {err1}"
        assert any("no hash_sha256" in e for e in err2), f"expected no hash error in validate_all, got: {err2}"

    def test_duplicate_source_id(self, empty_minimal_dataset):
        (empty_minimal_dataset / "actor_sources.json").write_text(json.dumps([
            {"source_id": "src:dup", "url": "https://a.com", "retrieved_at": "2026-07-22", "quote": "same", "hash_sha256": hashlib.sha256(b"same").hexdigest(), "kind": "investor", "source_record_id": "inv:a"},
            {"source_id": "src:dup", "url": "https://b.com", "retrieved_at": "2026-07-22", "quote": "same", "hash_sha256": hashlib.sha256(b"same").hexdigest(), "kind": "investor", "source_record_id": "inv:b"},
        ], ensure_ascii=False))
        loader = RelevanceGoldenLoader(golden_dir=empty_minimal_dataset)
        loader.load_all()
        errors = loader.validate_actor_sources()
        assert any("duplicate source_id" in e for e in errors), f"expected duplicate source_id error, got: {errors}"

    def test_evidence_quote_not_in_snapshot(self, empty_minimal_dataset):
        manifest = {
            "dataset_ids": {"investors": ["inv:bad-q"]},
            "corpus_stats": {"total_cases": 1, "by_kind": {"investors": 1}, "by_decision": {"investor:in_scope": 1}},
            "review_status": "pending_owner",
        }
        (empty_minimal_dataset / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False))
        item = {
            "case_id": "inv:bad-q", "kind": "investor", "source_ref": "src:bad-q", "source_record_id": "inv:bad-q",
            "as_of": "2026-07-22", "human_reviewed": False,
            "verdict": {
                "decision": "in_scope", "kind": "investor",
                "reason_codes": ["INV_IDENTITY_VERIFIED", "INV_TECH_STARTUP_ACTIVITY", "INV_BRAZIL_RELEVANCE"],
                "evidence": [{"code": "INV_IDENTITY_VERIFIED", "quote": "this text is NOT in the snapshot at all"}],
                "classifier_version": "radar-data-trust-relevance-v1",
            },
        }
        (empty_minimal_dataset / "investors.json").write_text(json.dumps([item], ensure_ascii=False))
        source = {
            "source_id": "src:bad-q", "url": "https://example.com", "retrieved_at": "2026-07-22",
            "quote": "Real snapshot text from the official page. Only this is the actual content.",
            "hash_sha256": hashlib.sha256(b"Real snapshot text from the official page. Only this is the actual content.").hexdigest(),
            "kind": "investor", "source_record_id": "inv:bad-q",
        }
        (empty_minimal_dataset / "actor_sources.json").write_text(json.dumps([source], ensure_ascii=False))
        loader = RelevanceGoldenLoader(golden_dir=empty_minimal_dataset)
        loader.load_all()
        errors = loader.validate_all()
        assert any("evidence quote not in source snapshot" in e for e in errors), f"expected quote mismatch error, got: {errors}"

    def test_legacy_case_id_not_in_triage(self, empty_minimal_dataset):
        manifest = {
            "dataset_ids": {"opportunities": ["op:no-triage"]},
            "corpus_stats": {"total_cases": 1, "by_kind": {"opportunities": 1}, "by_decision": {"opportunity:needs_review": 1}},
            "review_status": "pending_owner",
        }
        (empty_minimal_dataset / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False))
        item = {
            "case_id": "op:no-triage", "kind": "opportunity", "source_ref": "legacy_triage_case",
            "source_record_id": "nonexistent-case-id", "as_of": "2026-07-22", "human_reviewed": False,
            "verdict": {"decision": "needs_review", "reason_codes": [], "classifier_version": "radar-data-trust-relevance-v1"},
        }
        (empty_minimal_dataset / "opportunities.json").write_text(json.dumps([item], ensure_ascii=False))
        loader = RelevanceGoldenLoader(golden_dir=empty_minimal_dataset)
        loader.load_all()
        errors = loader.validate_all()
        assert any("not found in triage.json" in e for e in errors), f"expected triage not found error, got: {errors}"

    def test_legacy_quote_not_in_triage_body(self, empty_minimal_dataset):
        import json as _j
        # Need a real triage.json with the case
        triage_data = [{"case_id": "triage:real-case", "title": "Real Edital Title", "snippet": "Fomento à pesquisa científica", "content": "Conteúdo completo do edital de fomento."}]
        (empty_minimal_dataset.parent / "triage.json").write_text(_j.dumps(triage_data, ensure_ascii=False))
        manifest = {
            "dataset_ids": {"opportunities": ["triage:real-case"]},
            "corpus_stats": {"total_cases": 1, "by_kind": {"opportunities": 1}, "by_decision": {"opportunity:needs_review": 1}},
            "review_status": "pending_owner",
        }
        (empty_minimal_dataset / "manifest.json").write_text(_j.dumps(manifest, ensure_ascii=False))
        item = {
            "case_id": "triage:real-case", "kind": "opportunity", "source_ref": "legacy_triage_case",
            "source_record_id": "triage:real-case", "as_of": "2026-07-22", "human_reviewed": False,
            "verdict": {
                "decision": "needs_review", "reason_codes": [],
                "evidence": [{"code": "R4_RELEVANT_BENEFIT", "quote": "this text is nowhere in the triage entry"}],
                "classifier_version": "radar-data-trust-relevance-v1",
            },
        }
        (empty_minimal_dataset / "opportunities.json").write_text(_j.dumps([item], ensure_ascii=False))
        loader = RelevanceGoldenLoader(golden_dir=empty_minimal_dataset)
        loader.load_all()
        errors = loader.validate_all()
        assert any("evidence quote not found in triage entry body" in e for e in errors), f"expected triage quote error, got: {errors}"

    def test_curated_record_id_not_in_silver(self, empty_minimal_dataset):
        manifest = {
            "dataset_ids": {"investors": ["inv:no-silver"]},
            "corpus_stats": {"total_cases": 1, "by_kind": {"investors": 1}, "by_decision": {"investor:needs_review": 1}},
            "review_status": "pending_owner",
        }
        (empty_minimal_dataset / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False))
        item = {
            "case_id": "inv:no-silver", "kind": "investor", "source_ref": "curated_record",
            "source_record_id": "investidor:nonexistent", "as_of": "2026-07-22", "human_reviewed": False,
            "verdict": {"decision": "needs_review", "kind": "investor", "reason_codes": [], "classifier_version": "radar-data-trust-relevance-v1"},
        }
        (empty_minimal_dataset / "investors.json").write_text(json.dumps([item], ensure_ascii=False))
        loader = RelevanceGoldenLoader(golden_dir=empty_minimal_dataset)
        loader.load_all()
        errors = loader.validate_all()
        assert any("not found in" in e and "silver catalog" in e for e in errors), f"expected silver catalog error, got: {errors}"

    def test_reason_code_without_evidence(self, empty_minimal_dataset):
        manifest = {
            "dataset_ids": {"opportunities": ["op:no-ev"]},
            "corpus_stats": {"total_cases": 1, "by_kind": {"opportunities": 1}, "by_decision": {"opportunity:needs_review": 1}},
            "review_status": "pending_owner",
        }
        (empty_minimal_dataset / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False))
        item = {
            "case_id": "op:no-ev", "kind": "opportunity", "source_ref": "legacy_triage_case",
            "source_record_id": "op:no-ev", "as_of": "2026-07-22", "human_reviewed": False,
            "verdict": {
                "decision": "needs_review",
                "reason_codes": ["R1_ENTERPRISE_PATH", "R2_TECH_INNOVATION"],
                "exclusion_codes": [],
                "evidence": [
                    {"code": "R1_ENTERPRISE_PATH", "quote": "some quote", "source": "landing_page"},
                ],
                "classifier_version": "radar-data-trust-relevance-v1",
            },
        }
        (empty_minimal_dataset / "opportunities.json").write_text(json.dumps([item], ensure_ascii=False))
        loader = RelevanceGoldenLoader(golden_dir=empty_minimal_dataset)
        loader.load_all()
        errors = loader.validate_all()
        assert any("no matching evidence" in e and "R2_TECH_INNOVATION" in e for e in errors), f"expected missing evidence error, got: {errors}"

    def test_reason_code_also_in_missing_information(self, empty_minimal_dataset):
        manifest = {
            "dataset_ids": {"opportunities": ["op:dup-rc"]},
            "corpus_stats": {"total_cases": 1, "by_kind": {"opportunities": 1}, "by_decision": {"opportunity:needs_review": 1}},
            "review_status": "pending_owner",
        }
        (empty_minimal_dataset / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False))
        item = {
            "case_id": "op:dup-rc", "kind": "opportunity", "source_ref": "legacy_triage_case",
            "source_record_id": "op:dup-rc", "as_of": "2026-07-22", "human_reviewed": False,
            "verdict": {
                "decision": "needs_review",
                "reason_codes": ["R1_ENTERPRISE_PATH"],
                "exclusion_codes": [],
                "evidence": [
                    {"code": "R1_ENTERPRISE_PATH", "quote": "some quote", "source": "landing_page"},
                ],
                "missing_information": [
                    "R1_ENTERPRISE_PATH: ambiguous — cannot confirm enterprise path",
                ],
                "classifier_version": "radar-data-trust-relevance-v1",
            },
        }
        (empty_minimal_dataset / "opportunities.json").write_text(json.dumps([item], ensure_ascii=False))
        loader = RelevanceGoldenLoader(golden_dir=empty_minimal_dataset)
        loader.load_all()
        errors = loader.validate_all()
        assert any("also present in missing_information" in e and "R1_ENTERPRISE_PATH" in e for e in errors), f"expected dup rc/MI error, got: {errors}"

    def test_source_ref_kind_mismatch(self, empty_minimal_dataset):
        manifest = {
            "dataset_ids": {"programs": ["prg:kind-bad"]},
            "corpus_stats": {"total_cases": 1, "by_kind": {"programs": 1}, "by_decision": {"program:needs_review": 1}},
            "review_status": "pending_owner",
        }
        (empty_minimal_dataset / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False))
        item = {
            "case_id": "prg:kind-bad",
            "kind": "program",
            "source_ref": "src:kind-bad",
            "as_of": "2026-07-22",
            "human_reviewed": False,
            "verdict": {
                "decision": "needs_review",
                "kind": "program",
                "reason_codes": [],
                "classifier_version": "radar-data-trust-relevance-v1",
            },
        }
        (empty_minimal_dataset / "programs.json").write_text(json.dumps([item], ensure_ascii=False))
        source = {
            "source_id": "src:kind-bad",
            "url": "https://example.com",
            "retrieved_at": "2026-07-22",
            "quote": "some quote",
            "hash_sha256": hashlib.sha256(b"some quote").hexdigest(),
            "kind": "investor",
            "source_record_id": "prg:kind-bad",
        }
        (empty_minimal_dataset / "actor_sources.json").write_text(json.dumps([source], ensure_ascii=False))
        loader = RelevanceGoldenLoader(golden_dir=empty_minimal_dataset)
        loader.load_all()
        errors = loader.validate_all()
        assert any("kind mismatch" in e for e in errors), f"expected kind mismatch error, got: {errors}"
