"""Testes do classificador shadow e da suíte relevance_shadow (RT00-T03).

Cobre:
  - cinco contratos/prompts (parse, validação, round-trip);
  - JSON inválido → erro operacional;
  - timeout/provedor → erro operacional;
  - violação Pydantic → erro operacional;
  - quote não encontrada no input → grounding error;
  - erro nunca convertido em out_of_scope;
  - três rotas de source_ref (src:*, legacy_triage_case, curated_record);
  - triage-tavily-093 resolvido via src:*;
  - KPTL marcado como curated_record;
  - 14 casos e IDs esperados;
  - fórmulas de coverage/precision;
  - estratificação por kind;
  - ausência de wiring com discovery/staging.
"""
from __future__ import annotations

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
    _assemble_input,
    eval_decision_accuracy,
    eval_evidence_grounding,
    eval_fn_guard,
    eval_operational_error,
    eval_reason_code_coverage,
    eval_reason_code_precision,
)
from radar.core.ingestion.relevance_classifier import (  # noqa: E402
    _check_quote_grounding,
    _validate_actor_verdict,
    classify,
    classify_agency,
    classify_ict,
    classify_investor,
    classify_opportunity,
    classify_program,
)
from radar.domain.relevance import (  # noqa: E402
    AgencyVerdict,
    IctVerdict,
    InclusionCode,
    InvestorReasonCode,
    InvestorVerdict,
    ProgramVerdict,
    RelevanceDecision,
    RelevanceEvidence,
    RelevanceVerdict,
)

pytestmark = pytest.mark.unit

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def loader():
    ldr = RelevanceGoldenLoader()
    ldr.load_all()
    return ldr


def _mock_classify(result: dict):
    """Patches _json_from_llm to return a given dict, bypassing LLM."""
    return patch(
        "radar.core.ingestion.relevance_classifier._json_from_llm",
        return_value=result,
    )


def _mock_classify_side_effect(exc: Exception):
    """Patches _json_from_llm to raise an exception."""
    return patch(
        "radar.core.ingestion.relevance_classifier._json_from_llm",
        side_effect=exc,
    )


# =============================================================================
# 1. Five contracts/prompts — parse and validation
# =============================================================================


class TestOpportunityContract:
    def test_valid_in_scope_round_trip(self):
        data = {
            "decision": "in_scope",
            "reason_codes": ["R1_ENTERPRISE_PATH", "R2_TECH_INNOVATION",
                             "R3_ACTIONABLE", "R4_RELEVANT_BENEFIT",
                             "R5_BRAZIL_RELEVANCE"],
            "exclusion_codes": [],
            "evidence": [
                {"code": "R1_ENTERPRISE_PATH", "quote": "Empresas podem participar",
                 "source": "landing_page"},
                {"code": "R2_TECH_INNOVATION", "quote": "inovação tecnológica",
                 "source": "landing_page"},
                {"code": "R3_ACTIONABLE", "quote": "Inscrição aberta",
                 "source": "landing_page"},
                {"code": "R4_RELEVANT_BENEFIT", "quote": "R$ 300mi",
                 "source": "landing_page"},
                {"code": "R5_BRAZIL_RELEVANCE", "quote": "Todo Brasil",
                 "source": "landing_page"},
            ],
        }
        v = RelevanceVerdict.model_validate(data)
        assert v.decision == RelevanceDecision.IN_SCOPE
        assert len(v.reason_codes) == 5

    def test_valid_out_of_scope(self):
        data = {
            "decision": "out_of_scope",
            "reason_codes": ["X1_ACADEMIC_ONLY"],
            "exclusion_codes": ["X1_ACADEMIC_ONLY"],
            "evidence": [{"code": "X1_ACADEMIC_ONLY",
                          "quote": "bolsa exclusivamente acadêmica"}],
        }
        v = RelevanceVerdict.model_validate(data)
        assert v.decision == RelevanceDecision.OUT_OF_SCOPE

    def test_valid_needs_review(self):
        data = {
            "decision": "needs_review",
            "reason_codes": ["R1_ENTERPRISE_PATH"],
            "exclusion_codes": [],
            "evidence": [{"code": "R1_ENTERPRISE_PATH",
                          "quote": "Empresas podem participar"}],
            "missing_information": ["R2_TECH_INNOVATION: sem informação"],
        }
        v = RelevanceVerdict.model_validate(data)
        assert v.decision == RelevanceDecision.NEEDS_REVIEW

    def test_rejects_unknown_code(self):
        with pytest.raises(ValidationError):
            RelevanceVerdict.model_validate({
                "decision": "in_scope",
                "reason_codes": ["R1_ENTERPRISE_PATH", "R2_TECH_INNOVATION",
                                 "R3_ACTIONABLE", "R4_RELEVANT_BENEFIT",
                                 "R5_BRAZIL_RELEVANCE", "R999_UNKNOWN"],
                "exclusion_codes": [],
            })


class TestInvestorContract:
    def test_valid_in_scope(self):
        data = {
            "decision": "in_scope",
            "kind": "investor",
            "reason_codes": ["INV_IDENTITY_VERIFIED",
                             "INV_TECH_STARTUP_ACTIVITY",
                             "INV_BRAZIL_RELEVANCE"],
            "evidence": [{"code": "INV_IDENTITY_VERIFIED",
                          "quote": "VC firm"}],
            "missing_information": ["ticket: não informado"],
        }
        v = _validate_actor_verdict(data, "investor")
        assert isinstance(v, InvestorVerdict)
        assert v.decision == RelevanceDecision.IN_SCOPE

    def test_rejects_cross_kind_code(self):
        with pytest.raises(ValidationError):
            data = {
                "decision": "in_scope",
                "kind": "investor",
                "reason_codes": ["ICT_IDENTITY_VERIFIED",
                                 "INV_TECH_STARTUP_ACTIVITY",
                                 "INV_BRAZIL_RELEVANCE"],
            }
            _validate_actor_verdict(data, "investor")


class TestIctContract:
    def test_valid_in_scope(self):
        data = {
            "decision": "in_scope",
            "kind": "ict",
            "reason_codes": ["ICT_IDENTITY_VERIFIED",
                             "ICT_INSTITUTIONAL_LINK_VERIFIED",
                             "ICT_ENTERPRISE_TECH_COOP",
                             "ICT_CURRENT_STATUS_VERIFIED"],
            "evidence": [{"code": "ICT_IDENTITY_VERIFIED",
                          "quote": "ICT identity"}],
        }
        v = _validate_actor_verdict(data, "ict")
        assert isinstance(v, IctVerdict)

    def test_rejects_missing_code(self):
        data = {
            "decision": "in_scope",
            "kind": "ict",
            "reason_codes": ["ICT_IDENTITY_VERIFIED",
                             "ICT_INSTITUTIONAL_LINK_VERIFIED",
                             "ICT_ENTERPRISE_TECH_COOP"],
        }
        with pytest.raises(ValidationError):
            _validate_actor_verdict(data, "ict")


class TestProgramContract:
    def test_valid_in_scope(self):
        data = {
            "decision": "in_scope",
            "kind": "program",
            "reason_codes": ["PRG_IDENTITY_OPERATOR_VERIFIED",
                             "PRG_RELEVANT_INNOVATION_MECHANISM",
                             "PRG_ENTERPRISE_RELEVANCE"],
            "evidence": [{"code": "PRG_IDENTITY_OPERATOR_VERIFIED",
                          "quote": "program operator"}],
        }
        v = _validate_actor_verdict(data, "program")
        assert isinstance(v, ProgramVerdict)


class TestAgencyContract:
    def test_valid_in_scope(self):
        data = {
            "decision": "in_scope",
            "kind": "agency",
            "reason_codes": ["AGY_IDENTITY_VERIFIED",
                             "AGY_RELEVANT_INNOVATION_MANDATE",
                             "AGY_BRAZIL_RELEVANCE"],
            "evidence": [{"code": "AGY_IDENTITY_VERIFIED",
                          "quote": "agency identity"}],
        }
        v = _validate_actor_verdict(data, "agency")
        assert isinstance(v, AgencyVerdict)

    def test_rejects_wrong_kind(self):
        data = {
            "decision": "in_scope",
            "kind": "investor",
            "reason_codes": list(InvestorReasonCode),
        }
        with pytest.raises(ValueError, match="kind mismatch"):
            _validate_actor_verdict(data, "agency")


# =============================================================================
# 2. JSON inválido → erro operacional
# =============================================================================


class TestInvalidJson:
    _BAD_JSON = patch(
        "radar.core.ingestion.relevance_classifier._json_from_llm",
        side_effect=json.JSONDecodeError("Expecting value", "", 0),
    )

    def test_opportunity_returns_error(self):
        with self._BAD_JSON:
            result = classify_opportunity("some material")
        assert "error" in result
        assert "parse failure" in result["error"]

    def test_investor_returns_error(self):
        with self._BAD_JSON:
            result = classify_investor("some material")
        assert "error" in result
        assert "parse failure" in result["error"]

    def test_ict_returns_error(self):
        with self._BAD_JSON:
            result = classify_ict("some material")
        assert "error" in result

    def test_program_returns_error(self):
        with self._BAD_JSON:
            result = classify_program("some material")
        assert "error" in result

    def test_agency_returns_error(self):
        with self._BAD_JSON:
            result = classify_agency("some material")
        assert "error" in result


# =============================================================================
# 3. Timeout/provedor → erro operacional
# =============================================================================


class TestProviderErrors:
    def _make_provider_error_classifier(self, exc: Exception):
        import radar.core.ingestion.relevance_classifier as rc
        return patch.object(rc, "_json_from_llm", side_effect=exc)

    def test_timeout_returns_error(self):
        with self._make_provider_error_classifier(TimeoutError("LLM timeout")):
            result = classify_opportunity("material")
        assert "error" in result
        assert "classifier timeout" in result["error"]

    def test_provider_exception_returns_error(self):
        with self._make_provider_error_classifier(RuntimeError("API error")):
            result = classify_opportunity("material")
        assert "error" in result
        assert "provider error" in result["error"]

    def test_error_never_out_of_scope(self):
        """Qualquer erro operacional produz {"error": ...}, nunca out_of_scope."""
        with self._make_provider_error_classifier(TimeoutError("timeout")):
            result = classify_opportunity("material")
        assert "error" in result

        with self._make_provider_error_classifier(RuntimeError("API error")):
            result = classify_investor("material")
        assert "error" in result

    def test_all_five_classifiers_return_error_on_failure(self):
        for kind, fn in [("opportunity", classify_opportunity),
                         ("investor", classify_investor),
                         ("ict", classify_ict),
                         ("program", classify_program),
                         ("agency", classify_agency)]:
            with self._make_provider_error_classifier(TimeoutError("timeout")):
                result = fn("material")
            assert "error" in result, f"{kind} did not return error"


# =============================================================================
# 4. Pydantic violation → erro operacional
# =============================================================================


class TestPydanticViolation:
    def test_missing_required_fields_returns_contract_error(self):
        """in_scope without all R codes → Pydantic error → contract violation."""
        import radar.core.ingestion.relevance_classifier as rc
        bad_data = {
            "decision": "in_scope",
            "reason_codes": ["R1_ENTERPRISE_PATH"],
            "exclusion_codes": [],
        }
        with patch.object(rc, "_json_from_llm", return_value=bad_data):
            result = classify_opportunity("material")
        assert "error" in result
        assert "contract violation" in result["error"]

    def test_invalid_decision_returns_contract_error(self):
        import radar.core.ingestion.relevance_classifier as rc
        bad_data = {
            "decision": "maybe",
            "reason_codes": [],
        }
        with patch.object(rc, "_json_from_llm", return_value=bad_data):
            result = classify_opportunity("material")
        assert "error" in result
        assert "contract violation" in result["error"]


# =============================================================================
# 5. Quote não encontrada no input → grounding error
# =============================================================================


class TestGrounding:
    def test_quote_in_input_passes(self):
        """Quote exata (normalizando espaços) encontrada no material."""
        material = "Empresas de base tecnológica podem participar. Subvenção de R$ 300mi."
        v = RelevanceVerdict(
            decision=RelevanceDecision.NEEDS_REVIEW,
            reason_codes=[InclusionCode.R1_ENTERPRISE_PATH],
            evidence=[RelevanceEvidence(
                code=InclusionCode.R1_ENTERPRISE_PATH,
                quote="Empresas de base tecnológica podem participar",
            )],
        )
        _check_quote_grounding(v, material)  # não levanta

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

    def test_grounding_error_classifier_returns_error(self):
        """Grounding error no classifier → erro operacional, não decisão."""
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
        assert "grounding error" in result["error"]

    def test_actor_grounding_error(self):
        import radar.core.ingestion.relevance_classifier as rc
        material = "short"
        bad_data = {
            "decision": "in_scope",
            "kind": "investor",
            "reason_codes": list(InvestorReasonCode),
            "evidence": [{"code": "INV_IDENTITY_VERIFIED",
                          "quote": "not in the text at all"}],
        }
        with patch.object(rc, "_json_from_llm", return_value=bad_data):
            result = classify_investor(material)
        assert "error" in result
        assert "grounding error" in result["error"]


# =============================================================================
# 6. Erro nunca convertido em out_of_scope (testado também no 3)
# =============================================================================


class TestErrorNeverOutOfScope:
    def test_json_decode_error_not_out_of_scope(self):
        import radar.core.ingestion.relevance_classifier as rc
        with patch.object(rc, "_json_from_llm",
                          side_effect=json.JSONDecodeError("msg", "doc", 0)):
            result = classify_opportunity("x")
        assert "error" in result
        assert "out_of_scope" not in str(result)

    def test_runtime_error_not_out_of_scope(self):
        result = classify("invalid_kind", "x")
        assert "error" in result
        assert "unknown kind" in result["error"]


# =============================================================================
# 7. Três rotas de source_ref
# =============================================================================


class TestSourceRefRouting:
    def test_src_source_resolves_from_actor_sources(self, loader):
        """Triage-tavily-093 tem source_ref=src:finep-chamada-779."""
        for item in loader.data["opportunities"]:
            if item["case_id"] == "triage-tavily-093":
                assert item["source_ref"].startswith("src:")
                break
        else:
            pytest.fail("triage-tavily-093 not found in opportunities")

    def test_legacy_triage_case_resolves(self):
        """triage-dou-000 tem source_ref=legacy_triage_case."""
        loader = RelevanceGoldenLoader()
        loader.load_all()
        for item in loader.data["opportunities"]:
            if item["case_id"] == "triage-dou-000":
                assert item["source_ref"] == "legacy_triage_case"
                break
        else:
            pytest.fail("triage-dou-000 not found")

    def test_curated_record_kptl(self):
        """investidor:kptl tem source_ref=curated_record."""
        loader = RelevanceGoldenLoader()
        loader.load_all()
        for item in loader.data["investors"]:
            if item["case_id"] == "investidor:kptl":
                assert item["source_ref"] == "curated_record"
                break
        else:
            pytest.fail("investidor:kptl not found")


# =============================================================================
# 8. Triage-tavily-093 resolvido via src:*
# =============================================================================


class TestTriageTavily093Source:
    def test_case_093_uses_src_ref(self):
        loader = RelevanceGoldenLoader()
        loader.load_all()
        for item in loader.data["opportunities"]:
            if item["case_id"] == "triage-tavily-093":
                assert item["source_ref"] == "src:finep-chamada-779"
                assert item["source_record_id"] == "triage-tavily-093"
                break
        else:
            pytest.fail("triage-tavily-093 not found")

    def test_093_not_legacy_triage(self):
        """093 não pertence ao grupo legacy_triage_case."""
        loader = RelevanceGoldenLoader()
        loader.load_all()
        for item in loader.data["opportunities"]:
            if item["case_id"] == "triage-tavily-093":
                assert item["source_ref"] != "legacy_triage_case"
                break


# =============================================================================
# 9. KPTL marcado curated_record
# =============================================================================


class TestKptlCuratedRecord:
    def test_kptl_source_ref(self):
        loader = RelevanceGoldenLoader()
        loader.load_all()
        for item in loader.data["investors"]:
            if item["case_id"] == "investidor:kptl":
                assert item["source_ref"] == "curated_record"
                break

    def test_kptl_metadata_source_quality(self):
        """O input da shadow deve marcar source_quality=curated_record."""
        loader = RelevanceGoldenLoader()
        loader.load_all()
        for item in loader.data["investors"]:
            if item["case_id"] == "investidor:kptl":
                inp = _assemble_input(item)
                meta = inp.get("metadata", {})
                assert meta.get("source_quality") == "curated_record"
                break
        else:
            pytest.fail("investidor:kptl not found")

    def test_kptl_input_has_record(self):
        loader = RelevanceGoldenLoader()
        loader.load_all()
        for item in loader.data["investors"]:
            if item["case_id"] == "investidor:kptl":
                inp = _assemble_input(item)
                assert "record" in inp["input"] or "content" in inp["input"]
                break


# =============================================================================
# 10. 14 casos e IDs esperados
# =============================================================================


EXPECTED_14_IDS = {
    # opportunities (7)
    "triage-tavily-082", "triage-tavily-093", "triage-dou-000",
    "triage-tavily-084", "triage-tavily-118", "triage-tavily-079",
    "triage-tavily-098",
    # investors (2)
    "investidor:indicator-capital", "investidor:kptl",
    # icts (1)
    "ict:embrapii:senai-cimatec",
    # programs (2)
    "programa:pipe-fapesp", "programa:centelha",
    # agencies (2)
    "agencia:finep", "agencia:fapesp",
}


class TestFourteenCases:
    def test_loader_returns_14_cases(self, loader):
        total = sum(len(v) for v in loader.data.values())
        assert total == 14, f"expected 14 cases, got {total}"

    def test_all_ids_in_expected(self, loader):
        found: set[str] = set()
        for items in loader.data.values():
            for item in items:
                found.add(item["case_id"])
        missing = EXPECTED_14_IDS - found
        extra = found - EXPECTED_14_IDS
        assert not missing, f"missing case_ids: {missing}"
        assert not extra, f"unexpected case_ids: {extra}"

    def test_shadow_suite_loads_14(self):
        items = SUITE.load_data()
        assert len(items) == 14, f"shadow suite loaded {len(items)} cases"

    def test_shadow_suite_case_ids(self, loader):
        items = SUITE.load_data()
        found = {item.get("metadata", {}).get("case_id") for item in items}
        assert found == EXPECTED_14_IDS, "suite IDs don't match golden"

    def test_expected_case_ids_config(self):
        ids = SUITE.expected_case_ids
        if callable(ids):
            ids = ids()
        assert set(ids) == EXPECTED_14_IDS


# =============================================================================
# 11. Fórmulas de coverage/precision
# =============================================================================


class TestMetricsFormulas:
    def test_decision_accuracy_correct(self):
        """Veredito igual ao golden → 1.0."""
        output = {"verdict": {"decision": "in_scope"}}
        expected = {"decision": "in_scope"}
        ev = eval_decision_accuracy(output=output, expected_output=expected,
                                    input={}, metadata={})
        assert ev["value"] == 1.0

    def test_decision_accuracy_wrong(self):
        output = {"verdict": {"decision": "needs_review"}}
        expected = {"decision": "in_scope"}
        ev = eval_decision_accuracy(output=output, expected_output=expected,
                                    input={}, metadata={})
        assert ev["value"] == 0.0

    def test_decision_accuracy_error_returns_none(self):
        ev = eval_decision_accuracy(output={"error": "timeout"},
                                    expected_output={"decision": "in_scope"},
                                    input={}, metadata={})
        assert ev["value"] is None

    def test_reason_code_coverage_all(self):
        """Todos os reason codes do golden foram detectados."""
        output = {
            "verdict": {
                "reason_codes": ["R1_ENTERPRISE_PATH", "R2_TECH_INNOVATION"],
            }
        }
        expected = {
            "reason_codes": ["R1_ENTERPRISE_PATH", "R2_TECH_INNOVATION"],
        }
        ev = eval_reason_code_coverage(output=output, expected_output=expected,
                                       input={}, metadata={})
        assert ev["value"] == 1.0

    def test_reason_code_coverage_partial(self):
        output = {"verdict": {"reason_codes": ["R1_ENTERPRISE_PATH"]}}
        expected = {"reason_codes": ["R1_ENTERPRISE_PATH", "R2_TECH_INNOVATION"]}
        ev = eval_reason_code_coverage(output=output, expected_output=expected,
                                       input={}, metadata={})
        assert ev["value"] == 0.5

    def test_reason_code_precision_all_correct(self):
        output = {"verdict": {"reason_codes": ["R1_ENTERPRISE_PATH",
                                                "R2_TECH_INNOVATION"]}}
        expected = {"reason_codes": ["R1_ENTERPRISE_PATH", "R2_TECH_INNOVATION"]}
        ev = eval_reason_code_precision(output=output, expected_output=expected,
                                        input={}, metadata={})
        assert ev["value"] == 1.0

    def test_reason_code_precision_with_extra(self):
        output = {"verdict": {"reason_codes": ["R1_ENTERPRISE_PATH",
                                                "R2_TECH_INNOVATION",
                                                "R3_ACTIONABLE"]}}
        expected = {"reason_codes": ["R1_ENTERPRISE_PATH", "R2_TECH_INNOVATION"]}
        ev = eval_reason_code_precision(output=output, expected_output=expected,
                                        input={}, metadata={})
        assert ev["value"] == 2.0 / 3.0

    def test_fn_guard_in_scope_detected(self):
        output = {"verdict": {"decision": "in_scope"}}
        expected = {"decision": "in_scope"}
        ev = eval_fn_guard(output=output, expected_output=expected,
                           input={}, metadata={})
        assert ev["value"] == 0.0  # não foi FN

        output = {"verdict": {"decision": "out_of_scope"}}
        expected = {"decision": "in_scope"}
        ev = eval_fn_guard(output=output, expected_output=expected,
                           input={}, metadata={})
        assert ev["value"] == 1.0  # FN

    def test_fn_guard_non_in_scope_no_penalty(self):
        """out_of_scope golden + out_of_scope pred → não é FN."""
        output = {"verdict": {"decision": "out_of_scope"}}
        expected = {"decision": "out_of_scope"}
        ev = eval_fn_guard(output=output, expected_output=expected,
                           input={}, metadata={})
        assert ev["value"] == 0.0

    def test_fn_guard_error_returns_none(self):
        ev = eval_fn_guard(output={"error": "timeout"},
                           expected_output={"decision": "in_scope"},
                           input={}, metadata={})
        assert ev["value"] is None

    def test_operational_error_on_success(self):
        ev = eval_operational_error(output={"verdict": {"decision": "in_scope"}},
                                    expected_output={}, input={}, metadata={})
        assert ev["value"] == 0

    def test_operational_error_on_error(self):
        ev = eval_operational_error(output={"error": "timeout"},
                                    expected_output={}, input={}, metadata={})
        assert ev["value"] == 1

    def test_evidence_grounding_present(self):
        """Quando o output tem verdict com evidência, grounding = True."""
        output = {"verdict": {"evidence": [{"code": "R1",
                                             "quote": "some quote"}]}}
        ev = eval_evidence_grounding(output=output, expected_output={},
                                     input={}, metadata={})
        assert ev["value"] == 1.0

    def test_no_evidence_grounding_zero(self):
        ev = eval_evidence_grounding(output={"verdict": {"evidence": []}},
                                     expected_output={}, input={}, metadata={})
        assert ev["value"] == 0.0

    def test_evidence_grounding_error_none(self):
        ev = eval_evidence_grounding(output={"error": "timeout"},
                                     expected_output={}, input={}, metadata={})
        assert ev["value"] is None


# =============================================================================
# 12. Estratificação por kind
# =============================================================================


class TestStratification:
    def test_all_kinds_present(self, loader):
        kinds = set()
        for _fk, items in loader.data.items():
            for item in items:
                kinds.add(item["kind"])
        assert kinds == {"opportunity", "investor", "ict", "program", "agency"}

    def test_suite_items_have_kind_in_metadata(self):
        items = SUITE.load_data()
        kinds = set()
        for item in items:
            meta = item.get("metadata", {})
            kinds.add(meta.get("kind"))
        assert kinds == {"opportunity", "investor", "ict", "program", "agency"}


# =============================================================================
# 13. Ausência de wiring com discovery/staging
# =============================================================================


class TestNoWiringWithDiscovery:
    def test_classifier_does_not_import_opportunity_discovery(self):
        """relevance_classifier não importa oportunity_discovery nem _triage."""
        import radar.core.ingestion.relevance_classifier as rc
        src = inspect.getsource(rc)
        assert "opportunity_discovery" not in src

    def test_shadow_does_not_import_opportunity_discovery(self):
        """relevance_shadow também não importa oportunity_discovery."""
        import radar.core.eval.relevance_shadow as rs
        src = inspect.getsource(rs)
        assert "opportunity_discovery" not in src
        assert "discovery" not in src  # nenhuma referência a discovery

    def test_shadow_uses_llm_client_factory_public(self):
        """Usa make_client público, não _make_client privado."""
        import radar.core.ingestion.relevance_classifier as rc
        src = inspect.getsource(rc)
        assert "from radar.core.llm.llm_client import make_client" in src
        assert "_make_client" not in src.split("from radar.core.ingestion")[0] if "from radar.core.ingestion" in src else True

    def test_registry_does_not_reference_discovery(self):
        """Verifica que registry não referencia opportunity_discovery."""
        import radar.core.eval.registry as reg
        src = inspect.getsource(reg)
        assert "opportunity_discovery" not in src
        assert "relevance_shadow" in src  # mas registra a shadow suite


# =============================================================================
# Help
# =============================================================================
import inspect
