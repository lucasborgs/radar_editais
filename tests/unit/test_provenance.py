"""Testes do contrato de proveniência (RT01-T01).

Cobre:
  - construção e serialização/round-trip de EvidenceRef e FactProvenance;
  - todos os valores dos enums (FactState, LocatorQuality, ProducerKind);
  - invariantes estruturais: page 1-based, hash obrigatório, unresolved sem
    coordenadas fabricadas, producer.kind=default nunca com state=stated;
  - produtor LLM (com model/prompt_version) e não-LLM (sem eles);
  - compatibilidade: Extracted.evidence/FieldState não mudam de forma;
  - adaptador evidence_ref_from_extracted;
  - rejeição de campos extras desconhecidos (extra=forbid).
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from radar.domain.edital_extraction import EditalExtraction, Extracted, FieldState
from radar.domain.provenance import (
    PROVENANCE_SCHEMA_VERSION,
    DerivationInfo,
    EvidenceRef,
    FactProvenance,
    FactState,
    LocatorQuality,
    ProducerInfo,
    ProducerKind,
    ReviewInfo,
    ValidationResult,
    evidence_ref_from_extracted,
)

pytestmark = pytest.mark.unit


def _evidence_ref(**overrides) -> EvidenceRef:
    kwargs = dict(
        source="finep",
        native_id="745",
        document="Edital.pdf",
        page=17,
        quote="Poderão participar empresas brasileiras...",
        canonical_content_hash="sha256:abc",
        locator_quality=LocatorQuality.EXACT,
    )
    kwargs.update(overrides)
    return EvidenceRef(**kwargs)


# --- construção e round-trip ------------------------------------------------


def test_evidence_ref_constroi_com_defaults():
    ref = _evidence_ref()
    assert ref.schema_version == PROVENANCE_SCHEMA_VERSION
    assert ref.locator_quality is LocatorQuality.EXACT
    assert ref.section_path == []


def test_evidence_ref_roundtrip():
    ref = _evidence_ref(section_path=["7. Elegibilidade", "7.2 Proponentes"])
    again = EvidenceRef.model_validate(ref.model_dump())
    assert again == ref
    again_json = EvidenceRef.model_validate_json(ref.model_dump_json())
    assert again_json == ref


def test_fact_provenance_roundtrip():
    fp = FactProvenance(
        state=FactState.STATED,
        evidence_refs=[_evidence_ref()],
        producer=ProducerInfo(kind=ProducerKind.LLM, name="edital_extractor",
                              version="2", model="gpt-4o-mini",
                              prompt_version="extraction-v2"),
        derivation=DerivationInfo(inputs=["eligible_entities"],
                                  rule="canonicalize_eligible_entities:v1"),
        validations=[ValidationResult(name="quote_is_verbatim", status="passed")],
        review=ReviewInfo(reviewer="lucas", note="ok", overridden=False),
    )
    again = FactProvenance.model_validate(fp.model_dump())
    assert again == fp


# --- enums -------------------------------------------------------------


def test_fact_state_valores():
    assert {s.value for s in FactState} == {
        "stated", "inferred", "absent", "conflicting", "unknown",
    }


def test_locator_quality_valores():
    assert {q.value for q in LocatorQuality} == {
        "exact", "document_only", "unresolved",
    }


def test_producer_kind_valores():
    assert {k.value for k in ProducerKind} == {
        "adapter", "deterministic", "llm", "human", "default", "backfill",
    }


# --- invariantes estruturais --------------------------------------------


def test_page_invalida_rejeitada():
    with pytest.raises(ValidationError):
        _evidence_ref(page=0)
    with pytest.raises(ValidationError):
        _evidence_ref(page=-3)


def test_page_none_permitido_html_sem_paginacao():
    ref = _evidence_ref(page=None, locator_quality=LocatorQuality.DOCUMENT_ONLY,
                        block_idx=None, quote=None)
    assert ref.page is None


def test_ausencia_dos_dois_hashes_rejeitada():
    with pytest.raises(ValidationError):
        _evidence_ref(canonical_content_hash=None, silver_source_hash=None)


def test_um_hash_basta():
    ref = _evidence_ref(canonical_content_hash=None, silver_source_hash="sha256:def")
    assert ref.silver_source_hash == "sha256:def"


def test_unresolved_com_coordenadas_fabricadas_rejeitado():
    """unresolved não pode carregar page/block_idx/section_path (posição
    fabricada), isolando cada campo dos demais overrides do fixture base."""
    with pytest.raises(ValidationError):
        _evidence_ref(locator_quality=LocatorQuality.UNRESOLVED, page=17,
                      block_idx=None, section_path=[])
    with pytest.raises(ValidationError):
        _evidence_ref(locator_quality=LocatorQuality.UNRESOLVED, page=None,
                      block_idx=5, section_path=[])
    with pytest.raises(ValidationError):
        _evidence_ref(locator_quality=LocatorQuality.UNRESOLVED, page=None,
                      block_idx=None, section_path=["7. Elegibilidade"])


def test_unresolved_sem_coordenadas_e_valido():
    ref = _evidence_ref(locator_quality=LocatorQuality.UNRESOLVED,
                        page=None, block_idx=None, quote=None, section_path=[])
    assert ref.locator_quality is LocatorQuality.UNRESOLVED


def test_unresolved_com_quote_mas_sem_posicao_e_valido():
    """quote é conteúdo, não coordenada: unresolved pode guardar o texto
    capturado mesmo sem posição confirmada no documento."""
    ref = _evidence_ref(locator_quality=LocatorQuality.UNRESOLVED,
                        page=None, block_idx=None, section_path=[],
                        quote="trecho capturado sem posição confirmada")
    assert ref.quote == "trecho capturado sem posição confirmada"


def test_producer_default_nunca_stated():
    with pytest.raises(ValidationError):
        FactProvenance(
            state=FactState.STATED,
            producer=ProducerInfo(kind=ProducerKind.DEFAULT),
        )


def test_producer_default_com_unknown_e_valido():
    fp = FactProvenance(
        state=FactState.UNKNOWN,
        producer=ProducerInfo(kind=ProducerKind.DEFAULT),
    )
    assert fp.producer.kind is ProducerKind.DEFAULT


# --- produtor LLM vs não-LLM ---------------------------------------------


def test_producer_llm_com_model_e_prompt_version():
    p = ProducerInfo(kind=ProducerKind.LLM, name="tagger", version="1",
                     model="gpt-4o-mini", prompt_version="tag-v3")
    assert p.model == "gpt-4o-mini"
    assert p.prompt_version == "tag-v3"


def test_producer_nao_llm_sem_model_nem_prompt_version():
    p = ProducerInfo(kind=ProducerKind.ADAPTER, name="finep_adapter", version="1")
    assert p.model is None
    assert p.prompt_version is None


def test_producer_deterministic_sem_model():
    p = ProducerInfo(kind=ProducerKind.DETERMINISTIC, name="canonicalize_eligible_entities")
    assert p.model is None


def test_producer_human_sem_model():
    p = ProducerInfo(kind=ProducerKind.HUMAN, name="curador")
    assert p.model is None


def test_producer_backfill_sem_model():
    p = ProducerInfo(kind=ProducerKind.BACKFILL, name="backfill_2026_07")
    assert p.model is None


# --- compatibilidade com Extracted.evidence --------------------------------


def test_extracted_shape_nao_muda():
    """Extracted[T] continua com exatamente value/state/evidence — nenhum
    campo novo de proveniência foi injetado no schema legado."""
    assert set(Extracted.model_fields) == {"value", "state", "evidence"}


def test_edital_extraction_defaults_inalterados():
    e = EditalExtraction(source="finep", native_id="612")
    assert e.eligible_entities.state is FieldState.ABSENT
    assert e.eligible_entities.evidence is None
    assert set(e.model_dump().keys()) == set(EditalExtraction.model_fields)


def test_extracted_roundtrip_legado_preservado():
    legacy_payload = {"value": ["bioeconomia"], "state": "stated",
                      "evidence": "temáticas: bioeconomia"}
    extracted = Extracted[list[str]].model_validate(legacy_payload)
    assert extracted.model_dump() == legacy_payload


def test_evidence_ref_from_extracted_none_quando_sem_evidence():
    extracted: Extracted[str] = Extracted(state=FieldState.ABSENT)
    assert evidence_ref_from_extracted(extracted, source="finep",
                                       canonical_content_hash="sha256:x") is None


def test_evidence_ref_from_extracted_converte_substring():
    extracted = Extracted(value=["bioeconomia"], state=FieldState.STATED,
                          evidence="temáticas: bioeconomia")
    ref = evidence_ref_from_extracted(
        extracted, source="finep", native_id="612",
        canonical_content_hash="sha256:abc", document="Edital.pdf", page=3,
    )
    assert ref is not None
    assert ref.quote == "temáticas: bioeconomia"
    assert ref.locator_quality is LocatorQuality.EXACT
    assert ref.page == 3


def test_evidence_ref_from_extracted_sem_locator_vira_unresolved():
    extracted = Extracted(value="x", state=FieldState.STATED, evidence="trecho")
    ref = evidence_ref_from_extracted(extracted, source="finep",
                                      canonical_content_hash="sha256:abc")
    assert ref is not None
    assert ref.locator_quality is LocatorQuality.UNRESOLVED
    assert ref.page is None


def test_evidence_ref_from_extracted_sem_hash_rejeitado():
    extracted = Extracted(value="x", state=FieldState.STATED, evidence="trecho")
    with pytest.raises(ValidationError):
        evidence_ref_from_extracted(extracted, source="finep")


# --- rigidez: campos extras desconhecidos ----------------------------------


def test_evidence_ref_rejeita_campo_extra():
    with pytest.raises(ValidationError):
        EvidenceRef(source="finep", canonical_content_hash="sha256:x",
                   confidence=0.9)


def test_fact_provenance_rejeita_campo_extra():
    with pytest.raises(ValidationError):
        FactProvenance(
            state=FactState.UNKNOWN,
            producer=ProducerInfo(kind=ProducerKind.DEFAULT),
            confidence_score=0.5,
        )


def test_producer_info_rejeita_campo_extra():
    with pytest.raises(ValidationError):
        ProducerInfo(kind=ProducerKind.LLM, api_key="sk-secret")


def test_review_info_rejeita_campo_extra():
    with pytest.raises(ValidationError):
        ReviewInfo(reviewer="lucas", score=10)


# --- sem score numérico de confiança ----------------------------------------


def test_nenhum_modelo_expoe_campo_de_confianca():
    for model_cls in (EvidenceRef, ProducerInfo, DerivationInfo, ValidationResult,
                      ReviewInfo, FactProvenance):
        for field_name in model_cls.model_fields:
            assert "confidence" not in field_name.lower()
            assert "score" not in field_name.lower()
