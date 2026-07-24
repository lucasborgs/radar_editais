"""Teste de carga do golden de proveniência (RT02-T01).

Confirma que `data/evaluation/golden/provenance/` carrega, casa com o
formato esperado e que cada caso é fiel ao caminho real de resolução
(`radar.core.kg.evidence_resolver.resolve_quote`) — sem rodar a suíte
diagnóstica `provenance` (essa é a task RT02-T02, ainda não existe).

Este arquivo é SÓ um teste de carga/fidelidade da fixture. Não registra
nenhuma `Suite` em `core/eval/registry.py` e não introduz nenhum threshold.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from radar.core.kg.evidence_resolver import resolve_quote
from radar.domain.provenance import EvidenceRef, FactState, LocatorQuality

GOLDEN_DIR = Path(__file__).resolve().parents[2] / "data" / "evaluation" / "golden" / "provenance"

REQUIRED_CASE_TYPES = {
    "unique_exact_quote",
    "repeated_two_pages",
    "html_no_page",
    "normalized_value",
    "absent_field",
    "legacy_no_silver",
}


@pytest.fixture(scope="module")
def manifest() -> dict:
    return json.loads((GOLDEN_DIR / "manifest.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def cases() -> list[dict]:
    return json.loads((GOLDEN_DIR / "provenance.json").read_text(encoding="utf-8"))


# ── Estrutura do golden ──────────────────────────────────────────────────


class TestGoldenStructure:
    def test_files_exist(self):
        assert (GOLDEN_DIR / "manifest.json").exists()
        assert (GOLDEN_DIR / "provenance.json").exists()

    def test_six_cases_total(self, cases):
        assert len(cases) == 6

    def test_case_ids_unique(self, cases):
        ids = [c["case_id"] for c in cases]
        assert len(ids) == len(set(ids)), f"duplicate case_id: {ids}"

    def test_manifest_case_ids_match_file(self, manifest, cases):
        assert manifest["case_ids"] == [c["case_id"] for c in cases]

    def test_manifest_total_matches(self, manifest, cases):
        assert manifest["corpus_stats"]["total_cases"] == len(cases)

    def test_manifest_review_status_pending(self, manifest):
        # governança/proprietário ainda não revisou este golden (RT02-T01).
        assert manifest["review_status"] == "pending"

    def test_required_top_level_keys(self, cases):
        for c in cases:
            for key in ("case_id", "case_type", "description", "input", "expected_output", "metadata"):
                assert key in c, f"case {c.get('case_id')} missing key {key}"

    def test_one_case_per_required_type(self, cases):
        # spec radar-data-trust-02-quality-gates.md §7.1: exatamente um caso
        # por tipo obrigatório, nada além (conflicting/retificação ficam fora).
        types = [c["case_type"] for c in cases]
        assert set(types) == REQUIRED_CASE_TYPES
        assert len(types) == len(REQUIRED_CASE_TYPES), "no redundant/extra case per type allowed"

    def test_input_has_minimum_fields_for_determinism(self, cases):
        for c in cases:
            inp = c["input"]
            for key in ("quote", "source", "native_id", "blocks"):
                assert key in inp, f"case {c['case_id']} input missing {key}"
            assert isinstance(inp["blocks"], list)


# ── Fidelidade ao caminho real (resolve_quote) ───────────────────────────


class TestResolveQuoteFidelity:
    """O golden é o insumo/gabarito do caminho `resolve_quote`; cada caso
    deve produzir, hoje, exatamente o `expected_output` gravado — sem
    tolerância, sem fuzz. Se o resolver mudar de comportamento, este teste
    quebra (o que é o ponto: a golden é o contrato observável)."""

    @staticmethod
    def _run(case: dict):
        inp = case["input"]
        return resolve_quote(
            inp["quote"],
            inp["blocks"],
            source=inp["source"],
            native_id=inp.get("native_id"),
            edital_id=inp.get("edital_id"),
            silver_source_hash=inp.get("silver_source_hash"),
            canonical_content_hash=inp.get("canonical_content_hash"),
        )

    def test_candidates_match(self, cases):
        for c in cases:
            result = self._run(c)
            actual = [
                {"doc": cand.doc, "page": cand.page, "block_idx": cand.block_idx, "section_path": list(cand.section_path)}
                for cand in result.candidates
            ]
            assert actual == c["expected_output"]["candidates"], c["case_id"]

    def test_ambiguous_flag_matches(self, cases):
        for c in cases:
            result = self._run(c)
            assert result.ambiguous == c["expected_output"]["ambiguous"], c["case_id"]

    def test_missing_hash_flag_matches(self, cases):
        for c in cases:
            result = self._run(c)
            assert result.missing_hash == c["expected_output"]["missing_hash"], c["case_id"]

    def test_evidence_ref_presence_matches(self, cases):
        for c in cases:
            result = self._run(c)
            expected_ref = c["expected_output"]["evidence_ref"]
            if expected_ref is None:
                assert result.evidence_ref is None, c["case_id"]
            else:
                assert result.evidence_ref is not None, c["case_id"]

    def test_evidence_ref_fields_match(self, cases):
        for c in cases:
            result = self._run(c)
            expected_ref = c["expected_output"]["evidence_ref"]
            if expected_ref is None:
                continue
            ref = result.evidence_ref
            assert ref.source == expected_ref["source"], c["case_id"]
            assert ref.native_id == expected_ref["native_id"], c["case_id"]
            assert ref.edital_id == expected_ref["edital_id"], c["case_id"]
            assert ref.document == expected_ref["document"], c["case_id"]
            assert ref.page == expected_ref["page"], c["case_id"]
            assert ref.block_idx == expected_ref["block_idx"], c["case_id"]
            assert list(ref.section_path) == expected_ref["section_path"], c["case_id"]
            assert ref.quote == expected_ref["quote"], c["case_id"]
            assert ref.canonical_content_hash == expected_ref["canonical_content_hash"], c["case_id"]
            assert ref.silver_source_hash == expected_ref["silver_source_hash"], c["case_id"]
            assert ref.locator_quality.value == expected_ref["locator_quality"], c["case_id"]

    def test_locator_quality_matches(self, cases):
        for c in cases:
            result = self._run(c)
            expected_lq = c["expected_output"]["locator_quality"]
            if result.evidence_ref is not None:
                assert result.evidence_ref.locator_quality.value == expected_lq, c["case_id"]
            else:
                # missing_hash=True (case 6): não há evidence_ref, mas o
                # golden ainda declara a locator_quality que a resolução
                # textual pura teria produzido (unresolved, aqui: 0
                # candidatos).
                assert expected_lq == LocatorQuality.UNRESOLVED.value, c["case_id"]


# ── Invariantes de EvidenceRef (schema real, não um clone) ───────────────


class TestEvidenceRefInvariants:
    """Cada evidence_ref esperado no golden deve ser, ele mesmo, um
    `EvidenceRef` pydantic válido — reusa o schema real, não reimplementa
    suas regras aqui."""

    def test_expected_evidence_ref_constructs_validly(self, cases):
        for c in cases:
            expected_ref = c["expected_output"]["evidence_ref"]
            if expected_ref is None:
                continue
            EvidenceRef(
                source=expected_ref["source"],
                native_id=expected_ref["native_id"],
                edital_id=expected_ref["edital_id"],
                document=expected_ref["document"],
                page=expected_ref["page"],
                block_idx=expected_ref["block_idx"],
                section_path=expected_ref["section_path"],
                quote=expected_ref["quote"],
                canonical_content_hash=expected_ref["canonical_content_hash"],
                silver_source_hash=expected_ref["silver_source_hash"],
                locator_quality=LocatorQuality(expected_ref["locator_quality"]),
            )

    def test_fact_state_is_valid_enum_member(self, cases):
        for c in cases:
            state = c["expected_output"]["fact_state"]
            FactState(state)  # raises ValueError if invalid


# ── Regras "pare" do plano (sem coordenada/citação fabricada) ────────────


class TestNoFabrication:
    def test_legacy_case_has_no_evidence_ref_and_no_hash(self, cases):
        legacy = next(c for c in cases if c["case_type"] == "legacy_no_silver")
        assert legacy["input"]["silver_source_hash"] is None
        assert legacy["input"]["canonical_content_hash"] is None
        assert legacy["input"]["blocks"] == []
        assert legacy["expected_output"]["evidence_ref"] is None
        assert legacy["expected_output"]["missing_hash"] is True
        assert legacy["expected_output"]["fact_state"] == "unknown"

    def test_absent_field_case_has_no_fabricated_coordinate(self, cases):
        absent = next(c for c in cases if c["case_type"] == "absent_field")
        ref = absent["expected_output"]["evidence_ref"]
        assert ref is not None  # hash+blocks exist; only the search failed
        assert ref["page"] is None
        assert ref["block_idx"] is None
        assert ref["document"] is None
        assert ref["locator_quality"] == "unresolved"
        assert absent["expected_output"]["fact_state"] == "absent"

    def test_html_no_page_case_never_reports_exact(self, cases):
        html_case = next(c for c in cases if c["case_type"] == "html_no_page")
        assert html_case["expected_output"]["locator_quality"] == "document_only"
        ref = html_case["expected_output"]["evidence_ref"]
        assert ref["page"] is None
        assert ref["block_idx"] is None

    def test_normalized_value_quote_is_the_raw_block_text_not_the_number(self, cases):
        normalized = next(c for c in cases if c["case_type"] == "normalized_value")
        quote = normalized["expected_output"]["evidence_ref"]["quote"]
        assert "R$600.000,00" in quote
        # o número normalizado não deve aparecer como se fosse o quote
        assert quote != "600000.00"
        assert normalized["metadata"]["normalized_value"]["value"] == 600000.0


# ── Faithfulness: quote é substring verbatim de algum bloco do input ─────


class TestFaithfulness:
    def test_quote_verbatim_in_some_input_block_when_resolved(self, cases):
        for c in cases:
            ref = c["expected_output"]["evidence_ref"]
            if ref is None or ref["locator_quality"] == "unresolved":
                continue
            texts = [b["text"] for b in c["input"]["blocks"]]
            assert any(ref["quote"] in t for t in texts), (
                f"{c['case_id']}: quote não é substring verbatim de nenhum bloco de input"
            )
