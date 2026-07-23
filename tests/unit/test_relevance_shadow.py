from __future__ import annotations

import inspect
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from radar.core.eval.relevance_goldens import RelevanceGoldenLoader  # noqa: E402
from radar.core.eval.relevance_shadow import (  # noqa: E402
    SUITE,
    eval_decision_accuracy,
    eval_evidence_grounding,
    eval_failed_code_exact_match,
    eval_fn_guard,
    eval_operational_error,
    eval_reason_code_coverage,
    eval_reason_code_precision,
    run_eval_audit_ids,
    run_eval_decision_agreement,
    run_eval_divergences,
    run_eval_in_scope_recall,
    run_eval_metrics_by_code,
    run_eval_metrics_by_kind,
    run_eval_metrics_by_kind_t06,
    run_eval_needs_review_rate,
    run_eval_out_of_scope_precision,
)
from radar.core.ingestion.relevance_classifier import (  # noqa: E402
    _check_quote_grounding,
    _validate_actor_verdict,
    classify,
    classify_investor,
    classify_opportunity,
)
from radar.domain.relevance import (  # noqa: E402
    AgencyReasonCode,
    IctReasonCode,
    InclusionCode,
    InvestorReasonCode,
    ProgramReasonCode,
    RelevanceDecision,
    RelevanceEvidence,
    RelevanceVerdict,
)

pytestmark = pytest.mark.unit

ACTOR_ENUMS = {
    "investor": InvestorReasonCode,
    "ict": IctReasonCode,
    "program": ProgramReasonCode,
    "agency": AgencyReasonCode,
}

IDENTITY_CODES = {
    "investor": "INV_IDENTITY_VERIFIED",
    "ict": "ICT_IDENTITY_VERIFIED",
    "program": "PRG_IDENTITY_OPERATOR_VERIFIED",
    "agency": "AGY_IDENTITY_VERIFIED",
}


@pytest.fixture
def loader():
    ldr = RelevanceGoldenLoader()
    ldr.load_all()
    return ldr


def _mock_classify(result: dict):
    return patch(
        "radar.core.ingestion.relevance_classifier._json_from_llm",
        return_value=result,
    )


def _mock_classify_side_effect(exc: Exception):
    return patch(
        "radar.core.ingestion.relevance_classifier._json_from_llm",
        side_effect=exc,
    )


# =============================================================================
# 1. Domain invariants — failed_codes
# =============================================================================


class TestActorFailedCodesInvariants:
    @pytest.mark.parametrize("kind", ["investor", "ict", "program", "agency"])
    def test_public_dispatch_classifies_each_actor_kind(self, kind):
        codes = [code.value for code in ACTOR_ENUMS[kind]]
        response = {
            "decision": "in_scope",
            "kind": kind,
            "reason_codes": codes,
            "failed_codes": [],
            "evidence": [
                {"code": code, "quote": "evidence text"} for code in codes
            ],
            "missing_information": [],
        }
        with _mock_classify(response):
            result = classify(kind, "evidence text")
        assert result["verdict"]["decision"] == "in_scope"

    @pytest.mark.parametrize("kind", ["investor", "ict", "program", "agency"])
    def test_in_scope_requires_all_codes_and_empty_failed(self, kind):
        enum_cls = ACTOR_ENUMS[kind]
        all_codes = list(enum_cls)
        evidence = [{"code": c.value, "quote": "evidence text"} for c in all_codes]
        data = {"decision": "in_scope", "kind": kind,
                "reason_codes": [c.value for c in all_codes],
                "failed_codes": [], "evidence": evidence}
        v = _validate_actor_verdict(data, kind)
        assert v.decision == RelevanceDecision.IN_SCOPE

    @pytest.mark.parametrize("kind", ["investor", "ict", "program", "agency"])
    def test_in_scope_rejects_failed_codes(self, kind):
        """in_scope with any non-empty failed_codes is rejected.
        Since all codes are in reason_codes for in_scope, the disjoint
        check fires first — that's the observable error."""
        enum_cls = ACTOR_ENUMS[kind]
        all_codes = list(enum_cls)
        evidence = [{"code": c.value, "quote": "text"} for c in all_codes]
        non_identity = [c for c in list(enum_cls) if "IDENTITY" not in c.value]
        failed_extra = non_identity[0] if non_identity else list(enum_cls)[0]
        failed_code = failed_extra.value if hasattr(failed_extra, 'value') else failed_extra
        data = {"decision": "in_scope", "kind": kind,
                "reason_codes": [c.value for c in all_codes],
                "failed_codes": [failed_code],
                "evidence": evidence + [{"code": failed_code, "quote": "extra"}]}
        with pytest.raises(ValidationError, match="cannot appear in both"):
            _validate_actor_verdict(data, kind)

    @pytest.mark.parametrize("kind", ["investor", "ict", "program", "agency"])
    def test_out_of_scope_requires_identity_and_failed(self, kind):
        enum_cls = ACTOR_ENUMS[kind]
        id_code = IDENTITY_CODES[kind]
        non_id = [c.value for c in enum_cls if c.value != id_code]
        evidence = [{"code": id_code, "quote": "identity text"},
                    {"code": non_id[0], "quote": "failed text"}]
        data = {"decision": "out_of_scope", "kind": kind,
                "reason_codes": [id_code],
                "failed_codes": [non_id[0]],
                "evidence": evidence}
        v = _validate_actor_verdict(data, kind)
        assert v.decision == RelevanceDecision.OUT_OF_SCOPE

    @pytest.mark.parametrize("kind", ["investor", "ict", "program", "agency"])
    def test_out_of_scope_without_failed_code_rejected(self, kind):
        id_code = IDENTITY_CODES[kind]
        data = {"decision": "out_of_scope", "kind": kind,
                "reason_codes": [id_code],
                "failed_codes": [],
                "evidence": [{"code": id_code, "quote": "text"}]}
        with pytest.raises(ValidationError, match="requires at least one non-identity code"):
            _validate_actor_verdict(data, kind)

    @pytest.mark.parametrize("kind", ["investor", "ict", "program", "agency"])
    def test_out_of_scope_without_identity_rejected(self, kind):
        enum_cls = ACTOR_ENUMS[kind]
        non_id = [c for c in list(enum_cls) if "IDENTITY" not in c.value]
        if not non_id:
            return
        non_id_code = non_id[0].value if hasattr(non_id[0], 'value') else non_id[0]
        evidence = [{"code": non_id_code, "quote": "text"}]
        data = {"decision": "out_of_scope", "kind": kind,
                "reason_codes": [],
                "failed_codes": [non_id_code],
                "evidence": evidence}
        with pytest.raises(ValidationError, match="requires identity code"):
            _validate_actor_verdict(data, kind)

    @pytest.mark.parametrize("kind", ["investor", "ict", "program", "agency"])
    def test_needs_review_requires_empty_failed(self, kind):
        enum_cls = ACTOR_ENUMS[kind]
        first = list(enum_cls)[0]
        non_id = [c for c in list(enum_cls) if "IDENTITY" not in c.value]
        extra_code = non_id[0] if non_id else list(enum_cls)[1]
        extra_val = extra_code.value if hasattr(extra_code, 'value') else extra_code
        data = {"decision": "needs_review", "kind": kind,
                "reason_codes": [],
                "failed_codes": [extra_val],
                "evidence": [{"code": extra_val, "quote": "text"}],
                "missing_information": [f"{first.value}: missing"]}
        with pytest.raises(ValidationError, match="needs_review requires failed_codes to be empty"):
            _validate_actor_verdict(data, kind)

    @pytest.mark.parametrize("kind", ["investor", "ict", "program", "agency"])
    def test_needs_review_requires_obligatory_prefix(self, kind):
        enum_cls = ACTOR_ENUMS[kind]
        first = list(enum_cls)[0]
        data = {"decision": "needs_review", "kind": kind,
                "reason_codes": [first.value],
                "failed_codes": [],
                "evidence": [{"code": first.value, "quote": "text"}],
                "missing_information": ["foo: some info"]}
        with pytest.raises(ValidationError, match="requires at least one obligatory code"):
            _validate_actor_verdict(data, kind)

    @pytest.mark.parametrize("kind", ["investor", "ict", "program", "agency"])
    def test_reason_and_failed_disjoint(self, kind):
        enum_cls = ACTOR_ENUMS[kind]
        first = list(enum_cls)[0]
        data = {"decision": "needs_review", "kind": kind,
                "reason_codes": [first.value],
                "failed_codes": [first.value],
                "evidence": [{"code": first.value, "quote": "text"}],
                "missing_information": ["something: else"]}
        with pytest.raises(ValidationError, match="cannot appear in both"):
            _validate_actor_verdict(data, kind)

    @pytest.mark.parametrize("kind", ["investor", "ict", "program", "agency"])
    def test_failed_code_without_evidence_rejected(self, kind):
        id_code = IDENTITY_CODES[kind]
        enum_cls = ACTOR_ENUMS[kind]
        non_id = [c.value for c in enum_cls if c.value != id_code]
        if not non_id:
            return
        data = {"decision": "out_of_scope", "kind": kind,
                "reason_codes": [id_code],
                "failed_codes": [non_id[0]],
                "evidence": [{"code": id_code, "quote": "text"}]}
        with pytest.raises(ValidationError, match="no matching evidence"):
            _validate_actor_verdict(data, kind)

    @pytest.mark.parametrize("kind", ["investor", "ict", "program", "agency"])
    def test_evidence_without_reason_or_failed_rejected(self, kind):
        """Extra evidence code not matching any known enum value fails at enum level."""
        enum_cls = ACTOR_ENUMS[kind]
        all_codes = list(enum_cls)
        evidence = [{"code": c.value, "quote": "text"} for c in all_codes]
        extra = {"code": "EXTRA_CODE", "quote": "no matching code"}
        evidence.append(extra)
        data = {"decision": "in_scope", "kind": kind,
                "reason_codes": [c.value for c in all_codes],
                "failed_codes": [], "evidence": evidence}
        with pytest.raises(ValidationError):
            _validate_actor_verdict(data, kind)

    @pytest.mark.parametrize("kind", ["investor", "ict", "program", "agency"])
    def test_known_code_in_missing_and_confirmed_rejected(self, kind):
        enum_cls = ACTOR_ENUMS[kind]
        first = list(enum_cls)[0]
        data = {"decision": "needs_review", "kind": kind,
                "reason_codes": [first.value],
                "failed_codes": [],
                "evidence": [{"code": first.value, "quote": "text"}],
                "missing_information": [f"{first.value}: also missing"]}
        with pytest.raises(ValidationError, match="appears in confirmed codes"):
            _validate_actor_verdict(data, kind)


class TestOpportunityInvariants:
    def test_in_scope_missing_code_rejected(self):
        data = {"decision": "in_scope",
                "reason_codes": ["R1_ENTERPRISE_PATH"],
                "exclusion_codes": [],
                "evidence": [{"code": "R1_ENTERPRISE_PATH", "quote": "x"}]}
        with pytest.raises(ValidationError):
            RelevanceVerdict.model_validate(data)

    def test_in_scope_with_exclusion_rejected(self):
        all_r = [c.value for c in InclusionCode]
        data = {"decision": "in_scope", "reason_codes": all_r,
                "exclusion_codes": ["X1_ACADEMIC_ONLY"]}
        with pytest.raises(ValidationError, match="must have empty exclusion_codes"):
            RelevanceVerdict.model_validate(data)


# =============================================================================
# 2. Markdown fence rejected
# =============================================================================


class TestMarkdownFenceRejected:
    def test_markdown_fence_returns_parse_error(self):
        import radar.core.ingestion.relevance_classifier as rc
        with patch.object(rc, "_json_from_llm",
                          side_effect=json.JSONDecodeError("msg", "```json\n{}", 0)):
            result = classify_opportunity("material")
        assert "error" in result
        assert "parse_failure" in result["error"]


# =============================================================================
# 3. Sanitized error messages
# =============================================================================


class TestSanitizedErrors:
    def _patch_exc(self, exc):
        import radar.core.ingestion.relevance_classifier as rc
        return patch.object(rc, "_json_from_llm", side_effect=exc)

    def test_timeout_message_sanitized(self):
        with self._patch_exc(TimeoutError("real timeout details")):
            result = classify_opportunity("x")
        assert "error" in result
        assert "timeout" in result["error"]
        assert "real timeout details" not in result["error"]

    def test_openai_sdk_timeout_is_classified_as_timeout(self):
        import httpx
        from openai import APITimeoutError

        exc = APITimeoutError(
            request=httpx.Request("POST", "https://example.invalid/v1/chat/completions")
        )
        with self._patch_exc(exc):
            result = classify_opportunity("x")
        assert result["error"].startswith("timeout:")

    def test_provider_error_message_sanitized(self):
        with self._patch_exc(RuntimeError("API key invalid: sk-xxx...")):
            result = classify_opportunity("x")
        assert "error" in result
        assert "provider_error" in result["error"]
        assert "sk-" not in result["error"]

    def test_parse_error_message_sanitized(self):
        with self._patch_exc(json.JSONDecodeError("msg", "some raw LLM output", 0)):
            result = classify_opportunity("x")
        assert "error" in result
        assert "parse_failure" in result["error"]
        assert "some raw LLM output" not in result["error"]

    def test_error_never_out_of_scope(self):
        with self._patch_exc(TimeoutError("t")):
            result = classify("opportunity", "x")
        assert "error" in result
        assert result["error"] != "out_of_scope"

    def test_unknown_kind_returns_error(self):
        result = classify("bogus", "x")
        assert "error" in result


# =============================================================================
# 4. Grounding
# =============================================================================


class TestGrounding:
    def test_quote_in_input_passes(self):
        material = "Empresas de base tecnológica podem participar."
        v = RelevanceVerdict(
            decision=RelevanceDecision.NEEDS_REVIEW,
            reason_codes=[InclusionCode.R1_ENTERPRISE_PATH],
            evidence=[RelevanceEvidence(
                code=InclusionCode.R1_ENTERPRISE_PATH,
                quote="Empresas de base tecnológica podem participar",
            )],
        )
        _check_quote_grounding(v, material)

    def test_quote_not_in_input_raises(self):
        material = "Texto sobre agronegócio."
        v = RelevanceVerdict(
            decision=RelevanceDecision.NEEDS_REVIEW,
            reason_codes=[InclusionCode.R1_ENTERPRISE_PATH],
            evidence=[RelevanceEvidence(
                code=InclusionCode.R1_ENTERPRISE_PATH,
                quote="Empresas de tecnologia podem participar",
            )],
        )
        with pytest.raises(ValueError, match="grounding error"):
            _check_quote_grounding(v, material)

    def test_empty_quote_raises(self):
        v = RelevanceVerdict(
            decision=RelevanceDecision.NEEDS_REVIEW,
            reason_codes=[InclusionCode.R1_ENTERPRISE_PATH],
            evidence=[RelevanceEvidence(
                code=InclusionCode.R1_ENTERPRISE_PATH,
                quote="",
            )],
        )
        with pytest.raises(ValueError, match="is empty"):
            _check_quote_grounding(v, "material")

    def test_classifier_grounding_error(self):
        import radar.core.ingestion.relevance_classifier as rc
        material = "short text"
        good_data = {
            "decision": "needs_review",
            "reason_codes": ["R1_ENTERPRISE_PATH"],
            "exclusion_codes": [],
            "evidence": [{"code": "R1_ENTERPRISE_PATH",
                          "quote": "this quote is NOT in the short text",
                          "source": "landing_page"}],
            "missing_information": [],
        }
        with patch.object(rc, "_json_from_llm", return_value=good_data):
            result = classify_opportunity(material)
        assert "error" in result
        assert "grounding_error" in result["error"]

    def test_actor_grounding_error(self):
        import radar.core.ingestion.relevance_classifier as rc
        material = "short"
        # Provide all 3 evidence entries so correspondence check passes
        bad_data = {
            "decision": "in_scope",
            "kind": "investor",
            "reason_codes": [c.value for c in InvestorReasonCode],
            "failed_codes": [],
            "evidence": [
                {"code": "INV_IDENTITY_VERIFIED", "quote": "not in the text"},
                {"code": "INV_TECH_STARTUP_ACTIVITY", "quote": "not in the text either"},
                {"code": "INV_BRAZIL_RELEVANCE", "quote": "still not in text"},
            ],
        }
        with patch.object(rc, "_json_from_llm", return_value=bad_data):
            result = classify_investor(material)
        assert "error" in result
        assert "grounding_error" in result["error"]


# =============================================================================
# 5. Contract violation → sanitized message
# =============================================================================


class TestContractViolation:
    def test_missing_required_fields(self):
        import radar.core.ingestion.relevance_classifier as rc
        bad_data = {"decision": "in_scope",
                     "reason_codes": ["R1_ENTERPRISE_PATH"],
                     "exclusion_codes": []}
        with patch.object(rc, "_json_from_llm", return_value=bad_data):
            result = classify_opportunity("material")
        assert "error" in result
        assert "contract_violation" in result["error"]

    def test_invalid_decision(self):
        import radar.core.ingestion.relevance_classifier as rc
        bad_data = {"decision": "maybe", "reason_codes": []}
        with patch.object(rc, "_json_from_llm", return_value=bad_data):
            result = classify_opportunity("material")
        assert "error" in result
        assert "contract_violation" in result["error"]


# =============================================================================
# 6. Prompt constants
# =============================================================================


class TestPromptConstants:
    @pytest.mark.parametrize("attr", [
        "_OPPORTUNITY_CLASSIFIER_SYSTEM",
        "_INVESTOR_CLASSIFIER_SYSTEM",
        "_ICT_CLASSIFIER_SYSTEM",
        "_PROGRAM_CLASSIFIER_SYSTEM",
        "_AGENCY_CLASSIFIER_SYSTEM",
    ])
    def test_prompt_not_empty(self, attr):
        import radar.core.ingestion.relevance_classifier as rc
        val = getattr(rc, attr)
        assert isinstance(val, str) and len(val) > 50

    def test_actor_prompts_mention_failed_codes(self):
        import radar.core.ingestion.relevance_classifier as rc
        for attr in ["_INVESTOR_CLASSIFIER_SYSTEM", "_ICT_CLASSIFIER_SYSTEM",
                      "_PROGRAM_CLASSIFIER_SYSTEM", "_AGENCY_CLASSIFIER_SYSTEM"]:
            val = getattr(rc, attr)
            assert "failed_codes" in val, f"{attr} missing failed_codes"
            assert "reason_codes" in val

    def test_actor_prompts_forbid_absence_as_failed(self):
        import radar.core.ingestion.relevance_classifier as rc
        for attr in ["_INVESTOR_CLASSIFIER_SYSTEM", "_ICT_CLASSIFIER_SYSTEM",
                      "_PROGRAM_CLASSIFIER_SYSTEM", "_AGENCY_CLASSIFIER_SYSTEM"]:
            val = getattr(rc, attr)
            assert "Ausência de evidência não equivale" in val

    def test_actor_prompts_mention_json_structure(self):
        import radar.core.ingestion.relevance_classifier as rc
        for attr in ["_INVESTOR_CLASSIFIER_SYSTEM", "_ICT_CLASSIFIER_SYSTEM",
                      "_PROGRAM_CLASSIFIER_SYSTEM", "_AGENCY_CLASSIFIER_SYSTEM"]:
            val = getattr(rc, attr)
            assert '"failed_codes"' in val


# =============================================================================
# 7. Source ref routing
# =============================================================================


class TestSourceRefRouting:
    def test_src_source_resolves(self, loader):
        for item in loader.data["opportunities"]:
            if item["case_id"] == "triage-tavily-093":
                assert item["source_ref"].startswith("src:")
                break
        else:
            pytest.fail("triage-tavily-093 not found")

    def test_legacy_triage_case_resolves(self):
        loader = RelevanceGoldenLoader()
        loader.load_all()
        for item in loader.data["opportunities"]:
            if item["case_id"] == "triage-dou-000":
                assert item["source_ref"] == "legacy_triage_case"
                break
        else:
            pytest.fail("triage-dou-000 not found")

    def test_curated_record_kptl(self):
        loader = RelevanceGoldenLoader()
        loader.load_all()
        for item in loader.data["investors"]:
            if item["case_id"] == "investidor:kptl":
                assert item["source_ref"] == "curated_record"
                break
        else:
            pytest.fail("investidor:kptl not found")


# =============================================================================
# 8. 14 cases
# =============================================================================


EXPECTED_14_IDS = {
    "triage-tavily-082", "triage-tavily-093", "triage-dou-000",
    "triage-tavily-084", "triage-tavily-118", "triage-tavily-079",
    "triage-tavily-098",
    "investidor:indicator-capital", "investidor:kptl",
    "ict:embrapii:senai-cimatec",
    "programa:pipe-fapesp", "programa:centelha",
    "agencia:finep", "agencia:fapesp",
}


class TestFourteenCases:
    def test_loader_returns_14(self, loader):
        total = sum(len(v) for v in loader.data.values())
        assert total == 14

    def test_all_ids_match(self, loader):
        found = set()
        for items in loader.data.values():
            for item in items:
                found.add(item["case_id"])
        assert found == EXPECTED_14_IDS

    def test_shadow_suite_loads_14(self):
        items = SUITE.load_data()
        assert len(items) == 14

    def test_shadow_suite_case_ids(self):
        items = SUITE.load_data()
        found = {item.get("metadata", {}).get("case_id") for item in items}
        assert found == EXPECTED_14_IDS

    def test_expected_case_ids_config(self):
        ids = SUITE.expected_case_ids
        if callable(ids):
            ids = ids()
        assert set(ids) == EXPECTED_14_IDS


# =============================================================================
# 9. Metrics
# =============================================================================


class TestMetrics:
    def test_decision_accuracy_correct(self):
        ev = eval_decision_accuracy(
            output={"verdict": {"decision": "in_scope"}},
            expected_output={"decision": "in_scope"},
            input={}, metadata={})
        assert ev["value"] == 1.0

    def test_decision_accuracy_wrong(self):
        ev = eval_decision_accuracy(
            output={"verdict": {"decision": "needs_review"}},
            expected_output={"decision": "in_scope"},
            input={}, metadata={})
        assert ev["value"] == 0.0

    def test_decision_accuracy_error_none(self):
        ev = eval_decision_accuracy(
            output={"error": "timeout"},
            expected_output={"decision": "in_scope"},
            input={}, metadata={})
        assert ev["value"] is None

    def test_fn_guard_ok_when_in_scope_kept(self):
        ev = eval_fn_guard(
            output={"verdict": {"decision": "in_scope"}},
            expected_output={"decision": "in_scope"},
            input={}, metadata={})
        assert ev["value"] == 1.0

    def test_fn_guard_zero_when_in_scope_lost(self):
        ev = eval_fn_guard(
            output={"verdict": {"decision": "out_of_scope"}},
            expected_output={"decision": "in_scope"},
            input={}, metadata={})
        assert ev["value"] == 0.0

    def test_fn_guard_non_in_scope_no_issue(self):
        ev = eval_fn_guard(
            output={"verdict": {"decision": "out_of_scope"}},
            expected_output={"decision": "out_of_scope"},
            input={}, metadata={})
        assert ev["value"] == 1.0

    def test_fn_guard_error_none(self):
        ev = eval_fn_guard(
            output={"error": "timeout"},
            expected_output={"decision": "in_scope"},
            input={}, metadata={})
        assert ev["value"] is None

    def test_coverage_all(self):
        ev = eval_reason_code_coverage(
            output={"verdict": {"reason_codes": ["R1", "R2"]}},
            expected_output={"reason_codes": ["R1", "R2"]},
            input={}, metadata={})
        assert ev["value"] == 1.0

    def test_coverage_partial(self):
        ev = eval_reason_code_coverage(
            output={"verdict": {"reason_codes": ["R1"]}},
            expected_output={"reason_codes": ["R1", "R2"]},
            input={}, metadata={})
        assert ev["value"] == 0.5

    def test_precision_all_correct(self):
        ev = eval_reason_code_precision(
            output={"verdict": {"reason_codes": ["R1", "R2"]}},
            expected_output={"reason_codes": ["R1", "R2"]},
            input={}, metadata={})
        assert ev["value"] == 1.0

    def test_precision_with_extra(self):
        ev = eval_reason_code_precision(
            output={"verdict": {"reason_codes": ["R1", "R2", "R3"]}},
            expected_output={"reason_codes": ["R1", "R2"]},
            input={}, metadata={})
        assert ev["value"] == 2.0 / 3.0

    def test_precision_empty_prediction_is_undefined(self):
        ev = eval_reason_code_precision(
            output={"verdict": {"reason_codes": []}},
            expected_output={"reason_codes": ["R1"]},
            input={}, metadata={})
        assert ev["value"] is None

    def test_coverage_empty_prediction_is_zero(self):
        ev = eval_reason_code_coverage(
            output={"verdict": {"reason_codes": []}},
            expected_output={"reason_codes": ["R1"]},
            input={}, metadata={})
        assert ev["value"] == 0.0

    def test_failed_code_exact_match_ok(self):
        ev = eval_failed_code_exact_match(
            output={"verdict": {"failed_codes": ["FC1"]}},
            expected_output={"failed_codes": ["FC1"]},
            input={}, metadata={})
        assert ev["value"] == 1.0

    def test_failed_code_exact_match_diff(self):
        ev = eval_failed_code_exact_match(
            output={"verdict": {"failed_codes": ["FC1"]}},
            expected_output={"failed_codes": []},
            input={}, metadata={})
        assert ev["value"] == 0.0

    def test_failed_code_exact_match_error_none(self):
        ev = eval_failed_code_exact_match(
            output={"error": "timeout"},
            expected_output={"failed_codes": []},
            input={}, metadata={})
        assert ev["value"] is None

    def test_evidence_grounding_valid(self):
        ev = eval_evidence_grounding(
            output={"verdict": {"reason_codes": ["R1"], "failed_codes": [],
                                "evidence": [{"code": "R1", "quote": "some quote"}]}},
            expected_output={},
            input={"content": "some quote here"}, metadata={})
        assert ev["value"] == 1.0

    def test_evidence_grounding_quote_not_found(self):
        ev = eval_evidence_grounding(
            output={"verdict": {"reason_codes": ["R1"], "failed_codes": [],
                                "evidence": [{"code": "R1", "quote": "missing quote"}]}},
            expected_output={},
            input={"content": "completely different text"}, metadata={})
        assert ev["value"] == 0.0

    def test_evidence_grounding_empty_quote(self):
        ev = eval_evidence_grounding(
            output={"verdict": {"reason_codes": ["R1"], "failed_codes": [],
                                "evidence": [{"code": "R1", "quote": ""}]}},
            expected_output={},
            input={"content": "some text"}, metadata={})
        assert ev["value"] == 0.0

    def test_evidence_grounding_no_evidence(self):
        ev = eval_evidence_grounding(
            output={"verdict": {"reason_codes": [], "failed_codes": [],
                                "evidence": []}},
            expected_output={},
            input={"content": "some text"}, metadata={})
        assert ev["value"] == 0.0

    def test_evidence_grounding_missing_code_evidence(self):
        ev = eval_evidence_grounding(
            output={"verdict": {"reason_codes": ["R1"], "failed_codes": [],
                                "evidence": []}},
            expected_output={},
            input={"content": "some text"}, metadata={})
        assert ev["value"] == 0.0

    def test_evidence_grounding_orphan_evidence_code(self):
        ev = eval_evidence_grounding(
            output={"verdict": {"reason_codes": [], "failed_codes": [],
                                "evidence": [{"code": "R1", "quote": "text"}]}},
            expected_output={},
            input={"content": "text"}, metadata={})
        assert ev["value"] == 0.0

    def test_evidence_grounding_error_none(self):
        ev = eval_evidence_grounding(
            output={"error": "timeout"},
            expected_output={}, input={}, metadata={})
        assert ev["value"] is None

    def test_operational_error_on_success(self):
        ev = eval_operational_error(
            output={"verdict": {"decision": "in_scope"}},
            expected_output={}, input={}, metadata={})
        assert ev["value"] == 0

    def test_operational_error_on_error(self):
        ev = eval_operational_error(
            output={"error": "parse_failure"},
            expected_output={}, input={}, metadata={})
        assert ev["value"] == 1


# =============================================================================
# 10. Stratification
# =============================================================================


class TestStratification:
    def test_all_kinds_present(self, loader):
        kinds = set()
        for _fk, items in loader.data.items():
            for item in items:
                kinds.add(item["kind"])
        assert kinds == {"opportunity", "investor", "ict", "program", "agency"}

    def test_suite_items_have_kind(self):
        items = SUITE.load_data()
        kinds = set()
        for item in items:
            kinds.add(item.get("metadata", {}).get("kind"))
        assert kinds == {"opportunity", "investor", "ict", "program", "agency"}

    def test_operational_error_is_not_counted_as_false_negative(self):
        results = [{
            "metadata": {"kind": "opportunity", "case_id": "op:error"},
            "output": {"error": "timeout"},
            "expected_output": {"decision": "in_scope", "reason_codes": ["R1"]},
        }]
        metrics = {ev["name"]: ev for ev in run_eval_metrics_by_kind(results)}
        assert metrics["opportunity_fn_count"]["value"] == 0
        assert metrics["opportunity_error_count"]["value"] == 1

        divergences = {ev["name"]: ev for ev in run_eval_divergences(results)}
        assert divergences["fn_case_ids"]["value"] == 0
        assert divergences["error_case_ids"]["value"] == 1

    def test_empty_prediction_reduces_coverage_without_inflating_precision(self):
        results = [
            {
                "metadata": {"kind": "investor", "case_id": "empty"},
                "output": {"verdict": {
                    "decision": "needs_review", "reason_codes": [], "failed_codes": [],
                }},
                "expected_output": {
                    "decision": "needs_review", "reason_codes": ["INV_IDENTITY_VERIFIED"],
                    "failed_codes": [],
                },
            },
            {
                "metadata": {"kind": "investor", "case_id": "correct"},
                "output": {"verdict": {
                    "decision": "in_scope",
                    "reason_codes": ["INV_IDENTITY_VERIFIED"],
                    "failed_codes": [],
                }},
                "expected_output": {
                    "decision": "in_scope", "reason_codes": ["INV_IDENTITY_VERIFIED"],
                    "failed_codes": [],
                },
            },
        ]
        metrics = {ev["name"]: ev for ev in run_eval_metrics_by_kind(results)}
        assert metrics["investor_mean_coverage"]["value"] == 0.5
        assert metrics["investor_mean_precision"]["value"] == 1.0


# =============================================================================
# 11. Absence of wiring with discovery/staging
# =============================================================================


class TestNoWiring:
    def test_classifier_no_opportunity_discovery_import(self):
        import radar.core.ingestion.relevance_classifier as rc
        src = inspect.getsource(rc)
        assert "opportunity_discovery" not in src

    def test_shadow_no_opportunity_discovery_import(self):
        import radar.core.eval.relevance_shadow as rs
        src = inspect.getsource(rs)
        assert "opportunity_discovery" not in src

    def test_registry_has_shadow(self):
        import radar.core.eval.registry as reg
        src = inspect.getsource(reg)
        assert "relevance_shadow" in src

    def test_goldens_still_have_failed_codes(self, loader):
        for _kind, items in loader.data.items():
            for item in items:
                v = item.get("verdict", {})
                if item["kind"] != "opportunity":
                    assert "failed_codes" in v, f"{item['case_id']} missing failed_codes"
                    assert isinstance(v["failed_codes"], list)

    def test_all_human_reviewed_true(self, loader):
        for _kind, items in loader.data.items():
            for item in items:
                assert item.get("human_reviewed") is True, \
                    f"{item['case_id']} human_reviewed is not True"


# =============================================================================
# 12. T06 — In-scope recall
# =============================================================================


class TestT06InScopeRecall:
    def test_perfect_recall(self):
        results = [
            {"output": {"verdict": {"decision": "in_scope"}},
             "expected_output": {"decision": "in_scope"},
             "metadata": {"kind": "opportunity", "case_id": "a"}},
            {"output": {"verdict": {"decision": "in_scope"}},
             "expected_output": {"decision": "in_scope"},
             "metadata": {"kind": "opportunity", "case_id": "b"}},
        ]
        metrics = {ev["name"]: ev for ev in run_eval_in_scope_recall(results)}
        assert metrics["total_in_scope_goldens"]["value"] == 2
        assert metrics["in_scope_true_positives"]["value"] == 2
        assert metrics["in_scope_semantic_false_negatives"]["value"] == 0
        assert metrics["in_scope_operational_errors"]["value"] == 0
        assert metrics["in_scope_semantic_recall"]["value"] == 1.0
        assert metrics["in_scope_end_to_end_recall"]["value"] == 1.0

    def test_semantic_false_negative(self):
        results = [
            {"output": {"verdict": {"decision": "needs_review"}},
             "expected_output": {"decision": "in_scope"},
             "metadata": {"kind": "opportunity", "case_id": "fn-1"}},
            {"output": {"verdict": {"decision": "in_scope"}},
             "expected_output": {"decision": "in_scope"},
             "metadata": {"kind": "opportunity", "case_id": "tp-1"}},
        ]
        metrics = {ev["name"]: ev for ev in run_eval_in_scope_recall(results)}
        assert metrics["total_in_scope_goldens"]["value"] == 2
        assert metrics["in_scope_true_positives"]["value"] == 1
        assert metrics["in_scope_semantic_false_negatives"]["value"] == 1
        assert metrics["in_scope_semantic_recall"]["value"] == 0.5
        assert metrics["in_scope_end_to_end_recall"]["value"] == 0.5

    def test_operational_error_in_scope_golden(self):
        results = [
            {"output": {"error": "timeout"},
             "expected_output": {"decision": "in_scope"},
             "metadata": {"kind": "opportunity", "case_id": "err-1"}},
            {"output": {"verdict": {"decision": "in_scope"}},
             "expected_output": {"decision": "in_scope"},
             "metadata": {"kind": "opportunity", "case_id": "tp-1"}},
        ]
        metrics = {ev["name"]: ev for ev in run_eval_in_scope_recall(results)}
        assert metrics["total_in_scope_goldens"]["value"] == 2
        assert metrics["in_scope_true_positives"]["value"] == 1
        assert metrics["in_scope_semantic_false_negatives"]["value"] == 0
        assert metrics["in_scope_operational_errors"]["value"] == 1
        # semantic recall: only evaluable (FN is 0, so 1/1 = 1.0)
        assert metrics["in_scope_semantic_recall"]["value"] == 1.0
        # end-to-end: 1/2 = 0.5
        assert metrics["in_scope_end_to_end_recall"]["value"] == 0.5

    def test_semantic_vs_end_to_end_recall(self):
        """Diferença entre semântico (exclui erro) e end-to-end (inclui erro)."""
        results = [
            {"output": {"error": "parse_failure"},
             "expected_output": {"decision": "in_scope"},
             "metadata": {"kind": "opportunity", "case_id": "err-1"}},
            {"output": {"verdict": {"decision": "needs_review"}},
             "expected_output": {"decision": "in_scope"},
             "metadata": {"kind": "opportunity", "case_id": "fn-1"}},
            {"output": {"verdict": {"decision": "in_scope"}},
             "expected_output": {"decision": "in_scope"},
             "metadata": {"kind": "opportunity", "case_id": "tp-1"}},
        ]
        metrics = {ev["name"]: ev for ev in run_eval_in_scope_recall(results)}
        # semantic: TP=1, FN=1 → 0.5
        assert metrics["in_scope_semantic_recall"]["value"] == 0.5
        # e2e: TP=1, total=3 → 0.333...
        assert round(metrics["in_scope_end_to_end_recall"]["value"], 4) == round(1.0 / 3.0, 4)

    def test_no_in_scope_goldens(self):
        results = [
            {"output": {"verdict": {"decision": "out_of_scope"}},
             "expected_output": {"decision": "out_of_scope"},
             "metadata": {"kind": "opportunity", "case_id": "a"}},
        ]
        metrics = {ev["name"]: ev for ev in run_eval_in_scope_recall(results)}
        assert metrics["total_in_scope_goldens"]["value"] == 0
        assert metrics["in_scope_semantic_recall"]["value"] is None
        assert metrics["in_scope_end_to_end_recall"]["value"] is None


# =============================================================================
# 13. T06 — Out-of-scope precision
# =============================================================================


class TestT06OutOfScopePrecision:
    def test_perfect_precision(self):
        results = [
            {"output": {"verdict": {"decision": "out_of_scope"}},
             "expected_output": {"decision": "out_of_scope"},
             "metadata": {"kind": "opportunity", "case_id": "a"}},
            {"output": {"verdict": {"decision": "in_scope"}},
             "expected_output": {"decision": "in_scope"},
             "metadata": {"kind": "opportunity", "case_id": "b"}},
        ]
        metrics = {ev["name"]: ev for ev in run_eval_out_of_scope_precision(results)}
        assert metrics["out_of_scope_true_positives"]["value"] == 1
        assert metrics["out_of_scope_false_positives"]["value"] == 0
        assert metrics["out_of_scope_precision"]["value"] == 1.0

    def test_false_positive_out_of_scope(self):
        results = [
            {"output": {"verdict": {"decision": "out_of_scope"}},
             "expected_output": {"decision": "in_scope"},
             "metadata": {"kind": "opportunity", "case_id": "fp-1"}},
            {"output": {"verdict": {"decision": "out_of_scope"}},
             "expected_output": {"decision": "out_of_scope"},
             "metadata": {"kind": "opportunity", "case_id": "tp-1"}},
        ]
        metrics = {ev["name"]: ev for ev in run_eval_out_of_scope_precision(results)}
        assert metrics["out_of_scope_true_positives"]["value"] == 1
        assert metrics["out_of_scope_false_positives"]["value"] == 1
        assert metrics["out_of_scope_precision"]["value"] == 0.5

    def test_precision_undefined_no_predictions(self):
        results = [
            {"output": {"verdict": {"decision": "in_scope"}},
             "expected_output": {"decision": "in_scope"},
             "metadata": {"kind": "opportunity", "case_id": "a"}},
        ]
        metrics = {ev["name"]: ev for ev in run_eval_out_of_scope_precision(results)}
        assert metrics["out_of_scope_total_predictions"]["value"] == 0
        assert metrics["out_of_scope_precision"]["value"] is None

    def test_precision_undefined_no_support(self):
        """Sem predição nem golden out_of_scope, precision fica None."""
        results = [
            {"output": {"error": "timeout"},
             "expected_output": {"decision": "in_scope"},
             "metadata": {"kind": "opportunity", "case_id": "a"}},
        ]
        metrics = {ev["name"]: ev for ev in run_eval_out_of_scope_precision(results)}
        assert metrics["out_of_scope_precision"]["value"] is None


# =============================================================================
# 14. T06 — Needs review rate
# =============================================================================


class TestT06NeedsReviewRate:
    def test_needs_review_rate_calculation(self):
        results = [
            {"output": {"verdict": {"decision": "needs_review"}},
             "expected_output": {"decision": "in_scope"},
             "metadata": {"kind": "opportunity", "case_id": "a"}},
            {"output": {"verdict": {"decision": "in_scope"}},
             "expected_output": {"decision": "in_scope"},
             "metadata": {"kind": "opportunity", "case_id": "b"}},
        ]
        metrics = {ev["name"]: ev for ev in run_eval_needs_review_rate(results)}
        assert metrics["needs_review_total_predictions"]["value"] == 1
        assert metrics["needs_review_support"]["value"] == 2
        assert metrics["needs_review_rate"]["value"] == 0.5

    def test_needs_review_operational_errors_separated(self):
        results = [
            {"output": {"error": "timeout"},
             "expected_output": {"decision": "in_scope"},
             "metadata": {"kind": "opportunity", "case_id": "err-1"}},
            {"output": {"verdict": {"decision": "needs_review"}},
             "expected_output": {"decision": "in_scope"},
             "metadata": {"kind": "opportunity", "case_id": "nr-1"}},
        ]
        metrics = {ev["name"]: ev for ev in run_eval_needs_review_rate(results)}
        assert metrics["needs_review_operational_errors"]["value"] == 1
        assert metrics["needs_review_support"]["value"] == 1
        assert metrics["needs_review_total_predictions"]["value"] == 1
        assert metrics["needs_review_rate"]["value"] == 1.0

    def test_needs_review_distribution_by_kind(self):
        results = [
            {"output": {"verdict": {"decision": "needs_review"}},
             "expected_output": {"decision": "needs_review"},
             "metadata": {"kind": "opportunity", "case_id": "a"}},
            {"output": {"verdict": {"decision": "needs_review"}},
             "expected_output": {"decision": "needs_review"},
             "metadata": {"kind": "investor", "case_id": "b"}},
            {"output": {"verdict": {"decision": "in_scope"}},
             "expected_output": {"decision": "in_scope"},
             "metadata": {"kind": "opportunity", "case_id": "c"}},
        ]
        metrics = {ev["name"]: ev for ev in run_eval_needs_review_rate(results)}
        assert metrics["needs_review_opportunity"]["value"] == 1
        assert metrics["needs_review_investor"]["value"] == 1

    def test_needs_review_zero_evaluable(self):
        results = [
            {"output": {"error": "timeout"},
             "expected_output": {"decision": "in_scope"},
             "metadata": {"kind": "opportunity", "case_id": "a"}},
        ]
        metrics = {ev["name"]: ev for ev in run_eval_needs_review_rate(results)}
        assert metrics["needs_review_rate"]["value"] is None
        assert metrics["needs_review_operational_errors"]["value"] == 1


# =============================================================================
# 15. T06 — Decision agreement
# =============================================================================


class TestT06DecisionAgreement:
    def test_correct_divergent_error(self):
        results = [
            {"output": {"verdict": {"decision": "in_scope"}},
             "expected_output": {"decision": "in_scope"},
             "metadata": {"kind": "opportunity", "case_id": "correct"}},
            {"output": {"verdict": {"decision": "out_of_scope"}},
             "expected_output": {"decision": "in_scope"},
             "metadata": {"kind": "opportunity", "case_id": "divergent"}},
            {"output": {"error": "timeout"},
             "expected_output": {"decision": "in_scope"},
             "metadata": {"kind": "opportunity", "case_id": "error"}},
        ]
        metrics = {ev["name"]: ev for ev in run_eval_decision_agreement(results)}
        assert metrics["decision_correct"]["value"] == 1
        assert metrics["decision_divergent"]["value"] == 1
        assert metrics["decision_operational_errors"]["value"] == 1
        assert metrics["decision_support"]["value"] == 2
        assert metrics["decision_accuracy_rate"]["value"] == 0.5


# =============================================================================
# 16. T06 — Metrics by kind
# =============================================================================


class TestT06MetricsByKind:
    def test_kind_without_support(self):
        """Kind sem nenhum caso avaliável (todos erro) ou sem casos."""
        results = [
            {"output": {"error": "timeout"},
             "expected_output": {"decision": "in_scope"},
             "metadata": {"kind": "opportunity", "case_id": "a"}},
        ]
        metrics = {ev["name"]: ev for ev in run_eval_metrics_by_kind_t06(results)}
        kind_keys = {k for k in metrics if k.startswith("kind_opportunity")}
        assert kind_keys  # at least some metrics emitted
        assert metrics["kind_opportunity_in_scope_recall"]["value"] is None
        assert metrics["kind_opportunity_in_scope_e2e_recall"]["value"] == 0.0  # TP=0, total=1

    def test_kind_missing_entirely(self):
        """No items for a given kind produces no metrics for that kind."""
        results = [
            {"output": {"verdict": {"decision": "in_scope"}},
             "expected_output": {"decision": "in_scope"},
             "metadata": {"kind": "investor", "case_id": "a"}},
        ]
        metrics = {ev["name"]: ev for ev in run_eval_metrics_by_kind_t06(results)}
        assert "kind_investor_in_scope_recall" in metrics
        assert "kind_opportunity_in_scope_recall" not in metrics

    def test_global_average_does_not_hide_kind_loss(self):
        """Um kind com recall baixo não é mascarado por outros."""
        results = [
            # kind A: perfect recall in 2 cases
            {"output": {"verdict": {"decision": "in_scope"}},
             "expected_output": {"decision": "in_scope"},
             "metadata": {"kind": "investor", "case_id": "a1"}},
            {"output": {"verdict": {"decision": "in_scope"}},
             "expected_output": {"decision": "in_scope"},
             "metadata": {"kind": "investor", "case_id": "a2"}},
            # kind B: zero recall in 1 case
            {"output": {"verdict": {"decision": "out_of_scope"}},
             "expected_output": {"decision": "in_scope"},
             "metadata": {"kind": "opportunity", "case_id": "b1"}},
        ]
        metrics = {ev["name"]: ev for ev in run_eval_metrics_by_kind_t06(results)}
        assert metrics["kind_investor_in_scope_recall"]["value"] == 1.0
        assert metrics["kind_opportunity_in_scope_recall"]["value"] == 0.0


# =============================================================================
# 17. T06 — Metrics by code
# =============================================================================


class TestT06MetricsByCode:
    def test_code_tp_fp_semantic_fn(self):
        results = [
            {"output": {"verdict": {"reason_codes": ["R1_ENTERPRISE_PATH", "R2_TECH_INNOVATION"],
                                    "exclusion_codes": [], "failed_codes": []}},
             "expected_output": {"reason_codes": ["R1_ENTERPRISE_PATH", "R2_TECH_INNOVATION"],
                                 "exclusion_codes": [], "failed_codes": []},
             "metadata": {"kind": "opportunity", "case_id": "a"}},
        ]
        metrics = {ev["name"]: ev for ev in run_eval_metrics_by_code(results)}
        r1 = "code_conf_R1_ENTERPRISE_PATH"
        assert metrics[f"{r1}_tp"]["value"] == 1
        assert metrics[f"{r1}_fp"]["value"] == 0
        assert metrics[f"{r1}_semantic_fn"]["value"] == 0
        assert metrics[f"{r1}_operational_miss"]["value"] == 0
        assert metrics[f"{r1}_precision"]["value"] == 1.0
        assert metrics[f"{r1}_semantic_recall"]["value"] == 1.0
        assert metrics[f"{r1}_end_to_end_recall"]["value"] == 1.0

    def test_code_fp_and_semantic_fn(self):
        results = [
            {"output": {"verdict": {"reason_codes": ["R1_ENTERPRISE_PATH", "R3_ACTIONABLE"],
                                    "exclusion_codes": [], "failed_codes": []}},
             "expected_output": {"reason_codes": ["R1_ENTERPRISE_PATH", "R2_TECH_INNOVATION"],
                                 "exclusion_codes": [], "failed_codes": []},
             "metadata": {"kind": "opportunity", "case_id": "a"}},
        ]
        metrics = {ev["name"]: ev for ev in run_eval_metrics_by_code(results)}
        r1 = "code_conf_R1_ENTERPRISE_PATH"
        r2 = "code_conf_R2_TECH_INNOVATION"
        r3 = "code_conf_R3_ACTIONABLE"
        assert metrics[f"{r1}_tp"]["value"] == 1
        assert metrics[f"{r2}_tp"]["value"] == 0  # R2 expected but not predicted → FN
        assert metrics[f"{r2}_semantic_fn"]["value"] == 1
        assert metrics[f"{r2}_semantic_recall"]["value"] == 0.0
        assert metrics[f"{r3}_fp"]["value"] == 1  # R3 predicted but not expected → FP

    def test_operational_miss(self):
        """R1 esperado em erro operacional → operational_miss=1, e2e recall penalizado."""
        results = [
            {"output": {"verdict": {"reason_codes": ["R1_ENTERPRISE_PATH"],
                                    "exclusion_codes": [], "failed_codes": []}},
             "expected_output": {"reason_codes": ["R1_ENTERPRISE_PATH"],
                                 "exclusion_codes": [], "failed_codes": []},
             "metadata": {"kind": "opportunity", "case_id": "a"}},
            {"output": {"error": "timeout"},
             "expected_output": {"reason_codes": ["R1_ENTERPRISE_PATH"],
                                 "exclusion_codes": [], "failed_codes": []},
             "metadata": {"kind": "opportunity", "case_id": "b"}},
        ]
        metrics = {ev["name"]: ev for ev in run_eval_metrics_by_code(results)}
        r1 = "code_conf_R1_ENTERPRISE_PATH"
        assert metrics[f"{r1}_support_expected"]["value"] == 2
        assert metrics[f"{r1}_support_evaluable_expected"]["value"] == 1
        assert metrics[f"{r1}_tp"]["value"] == 1
        assert metrics[f"{r1}_semantic_fn"]["value"] == 0
        assert metrics[f"{r1}_operational_miss"]["value"] == 1
        # semantic: 1/1 = 1.0
        assert metrics[f"{r1}_semantic_recall"]["value"] == 1.0
        # e2e: 1/2 = 0.5
        assert metrics[f"{r1}_end_to_end_recall"]["value"] == 0.5

    def test_failed_codes_separate(self):
        """failed_codes use distinct namespace 'fail_' prefix."""
        results = [
            {"output": {"verdict": {"reason_codes": ["INV_IDENTITY_VERIFIED"],
                                    "failed_codes": ["INV_TECH_STARTUP_ACTIVITY"]}},
             "expected_output": {"reason_codes": ["INV_IDENTITY_VERIFIED"],
                                 "failed_codes": ["INV_TECH_STARTUP_ACTIVITY"]},
             "metadata": {"kind": "investor", "case_id": "a"}},
        ]
        metrics = {ev["name"]: ev for ev in run_eval_metrics_by_code(results)}
        # Confirmed code (reason_codes)
        assert "code_conf_INV_IDENTITY_VERIFIED_tp" in metrics
        # Failed code (separate namespace)
        assert "code_fail_INV_TECH_STARTUP_ACTIVITY_tp" in metrics
        assert metrics["code_fail_INV_TECH_STARTUP_ACTIVITY_tp"]["value"] == 1

    def test_code_no_support_returns_no_metrics(self):
        results = [
            {"output": {"verdict": {"reason_codes": [], "failed_codes": []}},
             "expected_output": {"reason_codes": [], "failed_codes": []},
             "metadata": {"kind": "opportunity", "case_id": "a"}},
        ]
        metrics = {ev["name"]: ev for ev in run_eval_metrics_by_code(results)}
        code_metrics = {k: v for k, v in metrics.items() if k.startswith("code_")}
        assert len(code_metrics) == 0

    def test_x_only_in_excl_not_conf(self):
        """X1-X8 aparecem apenas no namespace excl_, nunca em conf_."""
        results = [
            {"output": {"verdict": {"reason_codes": ["X1_ACADEMIC_ONLY"],
                                    "exclusion_codes": ["X1_ACADEMIC_ONLY"]}},
             "expected_output": {"reason_codes": ["X1_ACADEMIC_ONLY"],
                                 "exclusion_codes": ["X1_ACADEMIC_ONLY"]},
             "metadata": {"kind": "opportunity", "case_id": "a"}},
        ]
        metrics = {ev["name"]: ev for ev in run_eval_metrics_by_code(results)}
        # Exclusion code X1 em excl_ namespace
        excl_key = "code_excl_X1_ACADEMIC_ONLY"
        assert f"{excl_key}_tp" in metrics
        assert metrics[f"{excl_key}_tp"]["value"] == 1
        # conf_ namespace não deve conter X1
        conf_x1_keys = {k for k in metrics if "conf_X1" in k}
        assert len(conf_x1_keys) == 0, f"X1 encontrado em conf_: {conf_x1_keys}"


# =============================================================================
# 18. T06 — Audit IDs
# =============================================================================


class TestT06AuditIDs:
    def test_divergences_ids(self):
        results = [
            {"output": {"verdict": {"decision": "out_of_scope"}},
             "expected_output": {"decision": "in_scope"},
             "metadata": {"kind": "opportunity", "case_id": "div-1"}},
            {"output": {"verdict": {"decision": "in_scope"}},
             "expected_output": {"decision": "in_scope"},
             "metadata": {"kind": "opportunity", "case_id": "ok-1"}},
        ]
        metrics = {ev["name"]: ev for ev in run_eval_audit_ids(results)}
        assert metrics["audit_divergences"]["value"] == 1
        assert "div-1" in metrics["audit_divergences"]["comment"]

    def test_false_negative_ids(self):
        results = [
            {"output": {"verdict": {"decision": "needs_review"}},
             "expected_output": {"decision": "in_scope"},
             "metadata": {"kind": "opportunity", "case_id": "fn-1"}},
            {"output": {"verdict": {"decision": "in_scope"}},
             "expected_output": {"decision": "in_scope"},
             "metadata": {"kind": "opportunity", "case_id": "tp-1"}},
        ]
        metrics = {ev["name"]: ev for ev in run_eval_audit_ids(results)}
        assert metrics["audit_false_negatives"]["value"] == 1
        assert "fn-1" in metrics["audit_false_negatives"]["comment"]

    def test_false_positive_oos_ids(self):
        results = [
            {"output": {"verdict": {"decision": "out_of_scope"}},
             "expected_output": {"decision": "in_scope"},
             "metadata": {"kind": "opportunity", "case_id": "fp-1"}},
        ]
        metrics = {ev["name"]: ev for ev in run_eval_audit_ids(results)}
        assert metrics["audit_false_positives_oos"]["value"] == 1
        assert "fp-1" in metrics["audit_false_positives_oos"]["comment"]

    def test_operational_error_ids(self):
        results = [
            {"output": {"error": "timeout"},
             "expected_output": {"decision": "in_scope"},
             "metadata": {"kind": "opportunity", "case_id": "err-1"}},
        ]
        metrics = {ev["name"]: ev for ev in run_eval_audit_ids(results)}
        assert metrics["audit_operational_errors"]["value"] == 1
        assert "err-1" in metrics["audit_operational_errors"]["comment"]

    def test_code_divergence_ids(self):
        results = [
            {"output": {"verdict": {"decision": "in_scope",
                                    "reason_codes": ["R1_ENTERPRISE_PATH"],
                                    "failed_codes": []}},
             "expected_output": {"decision": "in_scope",
                                 "reason_codes": ["R1_ENTERPRISE_PATH", "R2_TECH_INNOVATION"],
                                 "failed_codes": []},
             "metadata": {"kind": "opportunity", "case_id": "code-div-1"}},
        ]
        metrics = {ev["name"]: ev for ev in run_eval_audit_ids(results)}
        assert metrics["audit_code_divergences"]["value"] == 1
        assert "code-div-1" in metrics["audit_code_divergences"]["comment"]

    def test_code_operational_miss_ids(self):
        results = [
            {"output": {"error": "timeout"},
             "expected_output": {"decision": "in_scope",
                                 "reason_codes": ["R1_ENTERPRISE_PATH"]},
             "metadata": {"kind": "opportunity", "case_id": "err-1"}},
            {"output": {"error": "timeout"},
             "expected_output": {"decision": "in_scope",
                                 "reason_codes": []},
             "metadata": {"kind": "opportunity", "case_id": "err-2"}},
        ]
        metrics = {ev["name"]: ev for ev in run_eval_audit_ids(results)}
        assert metrics["audit_code_operational_misses"]["value"] == 1
        assert "err-1" in metrics["audit_code_operational_misses"]["comment"]


# =============================================================================
# 19. T06 — Edge cases
# =============================================================================


class TestT06EdgeCases:
    def test_zero_cases(self):
        metrics = {ev["name"]: ev for ev in run_eval_in_scope_recall([])}
        assert metrics["total_in_scope_goldens"]["value"] == 0
        assert metrics["in_scope_semantic_recall"]["value"] is None
        assert metrics["in_scope_end_to_end_recall"]["value"] is None

        metrics2 = {ev["name"]: ev for ev in run_eval_out_of_scope_precision([])}
        assert metrics2["out_of_scope_precision"]["value"] is None

        metrics3 = {ev["name"]: ev for ev in run_eval_needs_review_rate([])}
        assert metrics3["needs_review_rate"]["value"] is None

        metrics4 = {ev["name"]: ev for ev in run_eval_decision_agreement([])}
        assert metrics4["decision_support"]["value"] == 0

        metrics5 = {ev["name"]: ev for ev in run_eval_metrics_by_kind_t06([])}
        assert len(metrics5) == 0  # no kinds → no metrics

        metrics6 = {ev["name"]: ev for ev in run_eval_metrics_by_code([])}
        assert len(metrics6) == 0

        metrics7 = {ev["name"]: ev for ev in run_eval_audit_ids([])}
        assert metrics7["audit_divergences"]["value"] == 0
        assert metrics7["audit_false_negatives"]["value"] == 0

    def test_error_never_inflates_precision_or_recall(self):
        """Error cases are excluded from numerator AND denominator of precision/recall."""
        results = [
            {"output": {"error": "timeout"},
             "expected_output": {"decision": "in_scope"},
             "metadata": {"kind": "opportunity", "case_id": "err-1"}},
            {"output": {"error": "parse_failure"},
             "expected_output": {"decision": "in_scope"},
             "metadata": {"kind": "opportunity", "case_id": "err-2"}},
        ]

        # In-scope recall — semantic = None (no evaluable), e2e = 0/2
        rec = {ev["name"]: ev for ev in run_eval_in_scope_recall(results)}
        assert rec["in_scope_semantic_recall"]["value"] is None
        assert rec["in_scope_end_to_end_recall"]["value"] == 0.0

        # Out-of-scope precision — errors not included, so no predictions
        prec = {ev["name"]: ev for ev in run_eval_out_of_scope_precision(results)}
        assert prec["out_of_scope_precision"]["value"] is None

        # Needs review rate — errors excluded from support
        nr = {ev["name"]: ev for ev in run_eval_needs_review_rate(results)}
        assert nr["needs_review_rate"]["value"] is None

        # Decision agreement — errors not evaluable
        agree = {ev["name"]: ev for ev in run_eval_decision_agreement(results)}
        assert agree["decision_accuracy_rate"]["value"] is None
        assert agree["decision_support"]["value"] == 0

        # By-code: expected codes registered, but semantic_recall = None
        code_results = [
            {"output": {"error": "timeout"},
             "expected_output": {"reason_codes": ["R1_ENTERPRISE_PATH"]},
             "metadata": {"kind": "opportunity", "case_id": "err-1"}},
        ]
        code_metrics = {ev["name"]: ev for ev in run_eval_metrics_by_code(code_results)}
        r1 = "code_conf_R1_ENTERPRISE_PATH"
        assert code_metrics[f"{r1}_support_expected"]["value"] == 1
        assert code_metrics[f"{r1}_support_evaluable_expected"]["value"] == 0
        assert code_metrics[f"{r1}_tp"]["value"] == 0
        assert code_metrics[f"{r1}_operational_miss"]["value"] == 1
        # semantic_recall = None (no evaluable expected)
        assert code_metrics[f"{r1}_semantic_recall"]["value"] is None
        # end_to_end_recall = 0/1 = 0.0
        assert code_metrics[f"{r1}_end_to_end_recall"]["value"] == 0.0
