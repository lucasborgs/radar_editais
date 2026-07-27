"""Testes do contrato de proveniência (RT01-T01).

Cobre:
  - construção e serialização/round-trip de EvidenceRef e FactProvenance;
  - todos os valores dos enums (FactState, LocatorQuality, ProducerKind);
  - invariantes estruturais: schema_version fixo em 1, source obrigatório e
    não vazio, page 1-based, block_idx >= 0, hash obrigatório, consistência
    semântica de locator_quality (unresolved/document_only/exact),
    producer.kind=default nunca com state=stated, stated exige EvidenceRef;
  - produtor LLM (com model/prompt_version) e não-LLM (sem eles);
  - compatibilidade: Extracted.evidence/FieldState não mudam de forma;
  - adaptador evidence_ref_from_extracted;
  - ReviewInfo como referência append-only (review_id/actor_id/reviewed_at);
  - rejeição de campos extras desconhecidos (extra=forbid);
  - ausência de score/confidence em qualquer modelo.
"""
from __future__ import annotations

from datetime import datetime

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


def _review_info(**overrides) -> ReviewInfo:
    kwargs = dict(
        review_id="rev-001",
        actor_id="lucas",
        reviewed_at=datetime(2026, 7, 23, 12, 0, 0),
        overridden=False,
    )
    kwargs.update(overrides)
    return ReviewInfo(**kwargs)


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


def test_evidence_ref_roundtrip_legado_sem_bundle_fields():
    payload = _evidence_ref().model_dump(mode="json")
    payload.pop("bundle_hash", None)
    payload.pop("content_hash", None)
    ref = EvidenceRef.model_validate(payload)
    assert ref.bundle_hash is None
    assert ref.content_hash is None


def test_evidence_ref_accepts_bundle_and_content_hash_together():
    ref = _evidence_ref(
        bundle_hash="sha256:" + "a" * 64,
        content_hash="sha256:" + "b" * 64,
    )
    assert ref.bundle_hash == "sha256:" + "a" * 64
    assert ref.content_hash == "sha256:" + "b" * 64


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
        review=_review_info(),
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


# --- schema_version fixo em 1 ------------------------------------------


def test_schema_version_default_e_1():
    ref = _evidence_ref()
    assert ref.schema_version == 1


def test_schema_version_aceita_1_explicito():
    ref = _evidence_ref(schema_version=1)
    assert ref.schema_version == 1


def test_schema_version_rejeita_outro_valor():
    with pytest.raises(ValidationError):
        _evidence_ref(schema_version=2)
    with pytest.raises(ValidationError):
        _evidence_ref(schema_version=0)


# --- source obrigatório e não vazio -------------------------------------


def test_source_obrigatorio():
    with pytest.raises(ValidationError):
        EvidenceRef(canonical_content_hash="sha256:x")


def test_source_vazio_rejeitado():
    with pytest.raises(ValidationError):
        _evidence_ref(source="")
    with pytest.raises(ValidationError):
        _evidence_ref(source="   ")


# --- invariantes estruturais: page/block_idx/hash ------------------------


def test_page_invalida_rejeitada():
    with pytest.raises(ValidationError):
        _evidence_ref(page=0)
    with pytest.raises(ValidationError):
        _evidence_ref(page=-3)


def test_page_none_permitido_html_sem_paginacao():
    ref = _evidence_ref(page=None, locator_quality=LocatorQuality.DOCUMENT_ONLY,
                        block_idx=None, quote=None)
    assert ref.page is None


def test_block_idx_negativo_rejeitado():
    with pytest.raises(ValidationError):
        _evidence_ref(page=None, block_idx=-1, section_path=[])


def test_block_idx_zero_permitido():
    ref = _evidence_ref(page=None, block_idx=0, section_path=[])
    assert ref.block_idx == 0


def test_ausencia_dos_dois_hashes_rejeitada():
    with pytest.raises(ValidationError):
        _evidence_ref(canonical_content_hash=None, silver_source_hash=None)


def test_um_hash_basta():
    ref = _evidence_ref(canonical_content_hash=None, silver_source_hash="sha256:def")
    assert ref.silver_source_hash == "sha256:def"


@pytest.mark.parametrize(
    ("bundle_hash", "content_hash"),
    [
        ("sha256:" + "a" * 64, None),
        (None, "sha256:" + "b" * 64),
    ],
)
def test_bundle_hash_and_content_hash_must_appear_together(bundle_hash, content_hash):
    with pytest.raises(ValidationError, match="must appear together"):
        _evidence_ref(bundle_hash=bundle_hash, content_hash=content_hash)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("bundle_hash", "md5:abc"),
        ("content_hash", "sha256:abc"),
        ("content_hash", "sha256:" + "z" * 64),
    ],
)
def test_bundle_lineage_hashes_must_be_valid_sha256(field, value):
    kwargs = {
        "bundle_hash": "sha256:" + "a" * 64,
        "content_hash": "sha256:" + "b" * 64,
    }
    kwargs[field] = value
    with pytest.raises(ValidationError):
        _evidence_ref(**kwargs)


# --- locator_quality: unresolved -----------------------------------------


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
    capturado mesmo sem posição confirmada no documento. Interpretação
    confirmada na auditoria (não é uma coordenada posicional)."""
    ref = _evidence_ref(locator_quality=LocatorQuality.UNRESOLVED,
                        page=None, block_idx=None, section_path=[],
                        quote="trecho capturado sem posição confirmada")
    assert ref.quote == "trecho capturado sem posição confirmada"


# --- locator_quality: document_only ---------------------------------------


def test_document_only_exige_document_nao_vazio():
    with pytest.raises(ValidationError):
        _evidence_ref(locator_quality=LocatorQuality.DOCUMENT_ONLY, document=None,
                      page=None, block_idx=None, section_path=[])
    with pytest.raises(ValidationError):
        _evidence_ref(locator_quality=LocatorQuality.DOCUMENT_ONLY, document="",
                      page=None, block_idx=None, section_path=[])
    with pytest.raises(ValidationError):
        _evidence_ref(locator_quality=LocatorQuality.DOCUMENT_ONLY, document="   ",
                      page=None, block_idx=None, section_path=[])


def test_document_only_nao_pode_declarar_coordenadas_exatas():
    with pytest.raises(ValidationError):
        _evidence_ref(locator_quality=LocatorQuality.DOCUMENT_ONLY, document="Edital.pdf",
                      page=17, block_idx=None, section_path=[])
    with pytest.raises(ValidationError):
        _evidence_ref(locator_quality=LocatorQuality.DOCUMENT_ONLY, document="Edital.pdf",
                      page=None, block_idx=5, section_path=[])
    with pytest.raises(ValidationError):
        _evidence_ref(locator_quality=LocatorQuality.DOCUMENT_ONLY, document="Edital.pdf",
                      page=None, block_idx=None, section_path=["7. Elegibilidade"])


def test_document_only_valido_sem_coordenadas():
    ref = _evidence_ref(locator_quality=LocatorQuality.DOCUMENT_ONLY, document="Edital.pdf",
                        page=None, block_idx=None, section_path=[])
    assert ref.locator_quality is LocatorQuality.DOCUMENT_ONLY
    assert ref.document == "Edital.pdf"


# --- locator_quality: exact -------------------------------------------


def test_exact_exige_ao_menos_uma_coordenada():
    with pytest.raises(ValidationError):
        _evidence_ref(locator_quality=LocatorQuality.EXACT, page=None, block_idx=None,
                      section_path=[])


def test_exact_com_page_e_valido():
    ref = _evidence_ref(locator_quality=LocatorQuality.EXACT, page=17, block_idx=None,
                        section_path=[])
    assert ref.locator_quality is LocatorQuality.EXACT


def test_exact_com_block_idx_e_valido():
    ref = _evidence_ref(locator_quality=LocatorQuality.EXACT, page=None, block_idx=3,
                        section_path=[])
    assert ref.block_idx == 3


def test_exact_com_section_path_e_valido():
    ref = _evidence_ref(locator_quality=LocatorQuality.EXACT, page=None, block_idx=None,
                        section_path=["7. Elegibilidade"])
    assert ref.section_path == ["7. Elegibilidade"]


# --- FactProvenance: producer.kind=default e state=stated exige evidence ---


def test_producer_default_nunca_stated():
    with pytest.raises(ValidationError):
        FactProvenance(
            state=FactState.STATED,
            evidence_refs=[_evidence_ref()],
            producer=ProducerInfo(kind=ProducerKind.DEFAULT),
        )


def test_producer_default_com_unknown_e_valido():
    fp = FactProvenance(
        state=FactState.UNKNOWN,
        producer=ProducerInfo(kind=ProducerKind.DEFAULT),
    )
    assert fp.producer.kind is ProducerKind.DEFAULT


def test_stated_sem_evidence_refs_rejeitado():
    with pytest.raises(ValidationError):
        FactProvenance(
            state=FactState.STATED,
            producer=ProducerInfo(kind=ProducerKind.ADAPTER, name="finep_adapter"),
        )


def test_stated_com_evidence_refs_e_valido():
    fp = FactProvenance(
        state=FactState.STATED,
        evidence_refs=[_evidence_ref()],
        producer=ProducerInfo(kind=ProducerKind.ADAPTER, name="finep_adapter"),
    )
    assert fp.evidence_refs


def test_inferred_sem_evidence_refs_permitido():
    """Nenhuma regra nova foi criada para inferred além das já previstas."""
    fp = FactProvenance(
        state=FactState.INFERRED,
        producer=ProducerInfo(kind=ProducerKind.LLM, name="tagger"),
    )
    assert fp.evidence_refs == []


def test_absent_sem_evidence_refs_permitido():
    """Nenhuma regra nova foi criada para absent além das já previstas."""
    fp = FactProvenance(
        state=FactState.ABSENT,
        producer=ProducerInfo(kind=ProducerKind.ADAPTER, name="finep_adapter"),
    )
    assert fp.evidence_refs == []


def test_conflicting_sem_evidence_refs_permitido():
    fp = FactProvenance(
        state=FactState.CONFLICTING,
        producer=ProducerInfo(kind=ProducerKind.LLM, name="tagger"),
    )
    assert fp.evidence_refs == []


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


# --- ReviewInfo: referência append-only ----------------------------------


def test_review_info_campos_obrigatorios():
    with pytest.raises(ValidationError):
        ReviewInfo(actor_id="lucas", reviewed_at=datetime(2026, 7, 23))
    with pytest.raises(ValidationError):
        ReviewInfo(review_id="rev-1", reviewed_at=datetime(2026, 7, 23))
    with pytest.raises(ValidationError):
        ReviewInfo(review_id="rev-1", actor_id="lucas")


def test_review_info_ids_vazios_rejeitados():
    with pytest.raises(ValidationError):
        _review_info(review_id="")
    with pytest.raises(ValidationError):
        _review_info(review_id="   ")
    with pytest.raises(ValidationError):
        _review_info(actor_id="")
    with pytest.raises(ValidationError):
        _review_info(actor_id="  ")


def test_review_info_valido():
    ri = _review_info(overridden=True)
    assert ri.review_id == "rev-001"
    assert ri.actor_id == "lucas"
    assert ri.overridden is True


def test_review_info_sem_reviewer_nem_note():
    assert "reviewer" not in ReviewInfo.model_fields
    assert "note" not in ReviewInfo.model_fields
    assert set(ReviewInfo.model_fields) == {"review_id", "actor_id", "reviewed_at", "overridden"}


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


def test_evidence_ref_from_extracted_document_only():
    extracted = Extracted(value="x", state=FieldState.STATED, evidence="trecho")
    ref = evidence_ref_from_extracted(extracted, source="finep",
                                      canonical_content_hash="sha256:abc",
                                      document="Edital.pdf")
    assert ref is not None
    assert ref.locator_quality is LocatorQuality.DOCUMENT_ONLY


def test_evidence_ref_from_extracted_section_path_produz_exact():
    extracted = Extracted(value="x", state=FieldState.STATED, evidence="trecho")
    ref = evidence_ref_from_extracted(extracted, source="finep",
                                      canonical_content_hash="sha256:abc",
                                      section_path=["7. Elegibilidade"])
    assert ref is not None
    assert ref.locator_quality is LocatorQuality.EXACT


def test_evidence_ref_from_extracted_sem_locator_vira_unresolved():
    """Sem document/page/block_idx/section_path, o adaptador cai em
    unresolved e preserva a substring legado em quote (conteúdo, não
    coordenada) — sem fabricar uma posição que não existe."""
    extracted = Extracted(value="x", state=FieldState.STATED, evidence="trecho")
    ref = evidence_ref_from_extracted(extracted, source="finep",
                                      canonical_content_hash="sha256:abc")
    assert ref is not None
    assert ref.locator_quality is LocatorQuality.UNRESOLVED
    assert ref.page is None
    assert ref.quote == "trecho"


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
        _review_info(score=10)


# --- sem score numérico de confiança ----------------------------------------


def test_nenhum_modelo_expoe_campo_de_confianca():
    for model_cls in (EvidenceRef, ProducerInfo, DerivationInfo, ValidationResult,
                      ReviewInfo, FactProvenance):
        for field_name in model_cls.model_fields:
            assert "confidence" not in field_name.lower()
            assert "score" not in field_name.lower()
