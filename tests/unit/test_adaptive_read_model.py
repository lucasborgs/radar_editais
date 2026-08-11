from __future__ import annotations

from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

from radar.core.kg.adaptive_read_model import (
    AdaptiveReadModel,
    family_values,
    project_artifact,
    resolve,
    select_compatible_artifacts,
)
from radar.core.services.consultant import GoldPathways, RelationalKnowledge
from radar.core.services.grounded_writing import GroundedWriting
from radar.domain.adaptive_extraction import (
    FIELD_VALUE_TYPES,
    ExtractedClaim,
    ExtractionArtifact,
    ExtractionStatus,
    ExtractionTarget,
)
from radar.domain.consultant import BriefProjeto, CaminhoInovacao, ConsultantState, ProjetoInovacao
from radar.domain.provenance import (
    EvidenceRef,
    FactProvenance,
    FactState,
    LocatorQuality,
    ProducerInfo,
    ProducerKind,
)
from radar.domain.source_bundle import SourceBundle
from tests.fixtures.source_bundles.fixtures import fapesc_base_amendment

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _active_adaptive_families(monkeypatch):
    monkeypatch.setenv(
        "RADAR_ADAPTIVE_ACTIVE_FAMILIES",
        "eligibility,temporal,financial,table_evidence",
    )


def _artifact(*, status: ExtractionStatus = ExtractionStatus.COMPLETE):
    ref = EvidenceRef(
        source="finep", native_id="finep:1", document="edital.pdf", page=4,
        quote="Empresas brasileiras", silver_source_hash="sha256:" + "a" * 64,
        locator_quality=LocatorQuality.EXACT,
    )
    claim = ExtractedClaim(
        subject_id="finep:1", field_path="requirements",
        value=["Empresas brasileiras"],
        provenance=FactProvenance(
            state=FactState.STATED, evidence_refs=[ref],
            producer=ProducerInfo(kind=ProducerKind.LLM, name="adaptive_textual_extractor"),
        ),
    )
    return ExtractionArtifact(
        subject_id="finep:1", asset_hash="sha256:" + "b" * 64,
        targets_requested=[ExtractionTarget(
            field_path="requirements", value_type="list[str]",
            required_for="eligibility", criticality="decision",
        )],
        claims=[claim], unresolved_targets=[], structured_blocks=[], table_fragments=[],
        route_trace=[], status=status,
        producer_versions={"adaptive_text": "text-v9", "edital_extraction_schema": "v3"},
        fingerprint="sha256:" + "c" * 64,
        created_at=datetime(2026, 8, 10, tzinfo=timezone.utc),
    )


def adaptive_artifact(artifact, **_kwargs):
    return artifact


def test_familia_inativa_nao_consulta_artifacts_ou_rt05(monkeypatch):
    monkeypatch.delenv("RADAR_ADAPTIVE_ACTIVE_FAMILIES")
    monkeypatch.setattr(
        "radar.core.kg.adaptive_read_model.document_extractions.list_for_subject",
        lambda subject_id: (_ for _ in ()).throw(AssertionError("artifacts consulted")),
    )
    monkeypatch.setattr(
        "radar.core.kg.adaptive_read_model._load_rt05_overrides",
        lambda subject_id: (_ for _ in ()).throw(AssertionError("RT05 consulted")),
    )
    artifacts = [_artifact(status=ExtractionStatus.PARTIAL), _artifact(status=ExtractionStatus.FAILED)]
    projection = resolve(
        "finep:1", artifacts=artifacts,
        legacy_values={"requirements": ["legado"]},
    )
    assert projection.source_state == "legacy"
    assert projection.claim("requirements")["value"] == ["legado"]


def test_familia_ativa_seleciona_apenas_artifact_compatível():
    compatible = _artifact()
    incompatible = compatible.model_copy(update={
        "producer_versions": {
            "adaptive_text": "text-v9",
            "edital_extraction_schema": "old-schema",
        },
    })
    assert select_compatible_artifacts([compatible, incompatible], "eligibility") == [compatible]


def test_familia_ativa_nao_faz_fallback_legado_quando_nao_tem_artifact():
    projection = resolve(
        "finep:1", artifacts=[],
        legacy_values={"requirements": ["legado"]},
    )
    assert projection.source_state != "legacy"
    assert projection.claims == []
    assert projection.needs_review is False


def test_read_model_aplica_override_rt05_sem_mudar_artifact():
    promoted = _artifact()
    projection = resolve(
        "finep:1", artifacts=[promoted],
        review_overrides={"requirements": {"decision": "mark_unknown", "rt05_open": True}},
    )
    assert projection.claims == []
    assert projection.needs_review is False


def test_read_model_rejeita_familia_e_estado_fora_do_publicavel():
    promoted = _artifact()
    promoted = promoted.model_copy(update={
        "claims": promoted.claims + [
            ExtractedClaim(
                subject_id="finep:1", field_path="title", value="não publicar",
                provenance=promoted.claims[0].provenance.model_copy(update={"state": FactState.INFERRED}),
            )
        ],
    })
    assert family_values("finep:1", artifacts=[promoted]) == {}

    wrong_family = promoted.model_copy(update={
        "producer_versions": {
            "adaptive_text": "text-v9", "edital_extraction_schema": "v3",
        }
    })
    assert select_compatible_artifacts([wrong_family], "eligibility") == [wrong_family]


def test_read_model_falha_rt05_sem_publicar_claim_original(monkeypatch):
    promoted = adaptive_artifact(_artifact())
    monkeypatch.setattr(
        "radar.core.kg.adaptive_read_model._load_rt05_overrides",
        lambda subject_id: (_ for _ in ()).throw(RuntimeError("fila indisponível")),
    )
    projection = resolve("finep:1", artifacts=[promoted])
    assert projection.needs_review is False
    assert projection.claims == []


def test_read_model_nao_injeta_correcao_textual_estruturada_invalida():
    promoted = adaptive_artifact(_artifact())
    ref = promoted.claims[0].provenance.evidence_refs[0].model_dump(mode="json")
    projection = resolve(
        "finep:1", artifacts=[promoted],
        review_overrides={"requirements": {
            "decision": "correct", "corrected_value": "não é uma lista",
            "evidence_refs": [ref],
        }},
    )
    assert projection.claims == []
    assert projection.needs_review is False


def test_composicao_rt04_aplica_precedencia_da_retificacao():
    bundle = SourceBundle.model_validate(fapesc_base_amendment())
    base, amendment = bundle.documents
    def artifact_for(document, value):
        ref = EvidenceRef(
            source="fapesc", edital_id=bundle.subject_id, document=document.doc_name,
            canonical_content_hash=document.content_hash, page=2,
            quote=value, silver_source_hash=document.content_hash,
            locator_quality=LocatorQuality.EXACT,
        )
        return adaptive_artifact(ExtractionArtifact(
            subject_id=bundle.subject_id, document=document.doc_name,
            asset_hash=document.content_hash, bundle_hash=bundle.compute_bundle_hash(),
            targets_requested=[ExtractionTarget(
                field_path="requirements", value_type="list[str]", required_for="eligibility",
            )],
            claims=[ExtractedClaim(
                subject_id=bundle.subject_id, field_path="requirements", value=[value],
                provenance=FactProvenance(
                    state=FactState.STATED, evidence_refs=[ref],
                    producer=ProducerInfo(kind=ProducerKind.LLM, name="test"),
                ),
            )], unresolved_targets=[], structured_blocks=[], table_fragments=[], route_trace=[],
            status=ExtractionStatus.COMPLETE, producer_versions={"adaptive_text": "text-v9", "edital_extraction_schema": "v3"},
            fingerprint="sha256:" + ("a" if document is base else "b") * 64,
            created_at=datetime(2026, 8, 10, tzinfo=timezone.utc),
        ))
    projection = resolve(
        bundle.subject_id,
        artifacts=[artifact_for(base, "base"), artifact_for(amendment, "amendment")],
        bundle=bundle,
    )
    assert projection.needs_review is False
    assert projection.claim("requirements")["value"] == ["amendment"]
    assert projection.claim("requirements")["provenance"]["state"] == FactState.STATED.value
    assert family_values(
        bundle.subject_id,
        artifacts=[artifact_for(base, "base"), artifact_for(amendment, "amendment")],
        bundle=bundle,
    )["requirements"] == ["amendment"]


def test_projecao_temporal_compoe_claim_e_delega_status_ao_read_model():
    bundle = SourceBundle.model_validate(fapesc_base_amendment())
    document = bundle.documents[0]
    ref = EvidenceRef(
        source="fapesc", edital_id=bundle.subject_id, document=document.doc_name,
        canonical_content_hash=document.content_hash, page=2,
        quote="Prazo final: 2026-12-31",
        locator_quality=LocatorQuality.EXACT,
    )
    targets = [
        ExtractionTarget(
            field_path="deadline", value_type="date", required_for="eligibility",
        ),
        ExtractionTarget(
            field_path="submission_window", value_type="submission_window",
            required_for="eligibility",
        ),
        ExtractionTarget(
            field_path="continuous_flow", value_type="bool", required_for="eligibility",
        ),
    ]
    claims = [
        ExtractedClaim(
            subject_id=bundle.subject_id, field_path="deadline", value="2026-12-31",
            provenance=FactProvenance(
                state=FactState.STATED, evidence_refs=[ref],
                producer=ProducerInfo(kind=ProducerKind.LLM, name="test"),
            ),
        ),
        *[
            ExtractedClaim(
                subject_id=bundle.subject_id, field_path=field, value=None,
                provenance=FactProvenance(
                    state=FactState.ABSENT, evidence_refs=[],
                    producer=ProducerInfo(kind=ProducerKind.LLM, name="test"),
                ),
            )
            for field in ("submission_window", "continuous_flow")
        ],
    ]
    artifact = adaptive_artifact(ExtractionArtifact(
        subject_id=bundle.subject_id, document=document.doc_name,
        asset_hash=document.content_hash, bundle_hash=bundle.compute_bundle_hash(),
        targets_requested=targets, claims=claims, unresolved_targets=[],
        structured_blocks=[], table_fragments=[], route_trace=[],
        status=ExtractionStatus.COMPLETE, producer_versions={"adaptive_text": "text-v9", "edital_extraction_schema": "v3"},
        fingerprint="sha256:" + "f" * 64,
        created_at=datetime(2026, 8, 10, tzinfo=timezone.utc),
    ), family="temporal")

    projection = resolve(
        bundle.subject_id, artifacts=[artifact], bundle=bundle,
        family="temporal", as_of=date(2026, 1, 1),
    )

    assert projection.claim("deadline")["value"] == "2026-12-31"
    assert projection.claim("deadline")["provenance"]["state"] == FactState.STATED.value
    assert projection.temporal_state == "active"
    assert projection.needs_review is False
    assert set(projection.gaps) == {"submission_window", "continuous_flow"}


def test_falha_nova_preserva_snapshot_saudavel_e_expoe_lacuna():
    promoted = adaptive_artifact(_artifact())
    healthy = project_artifact(promoted)
    failed = adaptive_artifact(_artifact(status=ExtractionStatus.PARTIAL))

    projection = resolve(
        "finep:1", artifacts=[promoted, failed], previous=healthy,
    )

    assert projection.claim("requirements")["value"] == ["Empresas brasileiras"]
    assert projection.needs_review is False
    assert "SourceBundle corrente indisponível; snapshot saudável preservado." in projection.gaps


def test_absent_explicito_atravessa_rt04_sem_fabricar_valor():
    bundle = SourceBundle.model_validate(fapesc_base_amendment())
    fields = (
        "eligibility_constraints", "requirements", "exclusions",
        "eligible_entities", "publico_alvo",
    )
    artifacts = []
    for index, document in enumerate(bundle.documents):
        claims = [
            ExtractedClaim(
                subject_id=bundle.subject_id,
                field_path=field,
                value=None,
                provenance=FactProvenance(
                    state=FactState.ABSENT,
                    evidence_refs=[],
                    producer=ProducerInfo(kind=ProducerKind.LLM, name="test"),
                ),
            )
            for field in fields
        ]
        artifacts.append(adaptive_artifact(ExtractionArtifact(
            subject_id=bundle.subject_id,
            document=document.doc_name,
            asset_hash=document.content_hash,
            bundle_hash=bundle.compute_bundle_hash(),
            targets_requested=[
                ExtractionTarget(
                    field_path=field,
                        value_type=FIELD_VALUE_TYPES[field],
                    required_for="eligibility",
                )
                for field in fields
            ],
            claims=claims,
            unresolved_targets=[],
            structured_blocks=[],
            table_fragments=[],
            route_trace=[],
            status=ExtractionStatus.COMPLETE,
            producer_versions={"adaptive_text": "text-v9", "edital_extraction_schema": "v3"},
            fingerprint="sha256:" + chr(97 + index) * 64,
            created_at=datetime(2026, 8, 10, index + 1, tzinfo=timezone.utc),
        )))

    projection = resolve(bundle.subject_id, artifacts=artifacts, bundle=bundle)

    assert projection.needs_review is False
    assert projection.gaps == []
    assert all(claim["provenance"]["state"] == FactState.ABSENT.value for claim in projection.claims)
    assert all(claim["value"] is None for claim in projection.claims)
    assert family_values(
        bundle.subject_id, artifacts=artifacts, bundle=bundle,
    ) == {}


def test_rt05_correct_remove_gap_apos_conflito_rt04():
    bundle_data = fapesc_base_amendment()
    bundle_data["documents"][1]["amends_content_hash"] = None
    bundle = SourceBundle.model_validate(bundle_data)
    fields = (
        "eligibility_constraints", "requirements", "exclusions",
        "eligible_entities", "publico_alvo",
    )
    artifacts = []
    for index, document in enumerate(bundle.documents):
        requirement_ref = EvidenceRef(
            source="fapesc",
            edital_id=bundle.subject_id,
            document=document.doc_name,
            canonical_content_hash=document.content_hash,
            locator_quality=LocatorQuality.DOCUMENT_ONLY,
        )
        claims = [
            ExtractedClaim(
                subject_id=bundle.subject_id,
                field_path="requirements",
                value=[("base" if index == 0 else "amendment")],
                provenance=FactProvenance(
                    state=FactState.STATED,
                    evidence_refs=[requirement_ref],
                    producer=ProducerInfo(kind=ProducerKind.LLM, name="test"),
                ),
            ),
            *[
                ExtractedClaim(
                    subject_id=bundle.subject_id,
                    field_path=field,
                    value=None,
                    provenance=FactProvenance(
                        state=FactState.ABSENT,
                        evidence_refs=[],
                        producer=ProducerInfo(kind=ProducerKind.LLM, name="test"),
                    ),
                )
                for field in fields
                if field != "requirements"
            ],
        ]
        artifacts.append(adaptive_artifact(ExtractionArtifact(
            subject_id=bundle.subject_id,
            document=document.doc_name,
            asset_hash=document.content_hash,
            bundle_hash=bundle.compute_bundle_hash(),
            targets_requested=[
                ExtractionTarget(
                    field_path=field,
                        value_type=FIELD_VALUE_TYPES[field],
                    required_for="eligibility",
                )
                for field in fields
            ],
            claims=claims,
            unresolved_targets=[],
            structured_blocks=[],
            table_fragments=[],
            route_trace=[],
            status=ExtractionStatus.COMPLETE,
            producer_versions={"adaptive_text": "text-v9", "edital_extraction_schema": "v3"},
            fingerprint="sha256:" + chr(99 + index) * 64,
            created_at=datetime(2026, 8, 10, index + 1, tzinfo=timezone.utc),
        )))

    before = resolve(bundle.subject_id, artifacts=artifacts, bundle=bundle)
    correction_ref = EvidenceRef(
        source="fapesc",
        edital_id=bundle.subject_id,
        document=bundle.documents[1].doc_name,
        canonical_content_hash=bundle.documents[1].content_hash,
        locator_quality=LocatorQuality.DOCUMENT_ONLY,
    )
    after = resolve(
        bundle.subject_id,
        artifacts=artifacts,
        bundle=bundle,
        review_overrides={"requirements": {
            "decision": "correct",
            "corrected_value": ["human-value"],
            "evidence_refs": [correction_ref.model_dump(mode="json")],
        }},
    )

    assert before.claim("requirements")["provenance"]["state"] == FactState.CONFLICTING.value
    assert before.needs_review
    assert after.claim("requirements")["provenance"]["state"] == FactState.STATED.value
    assert "requirements" not in after.gaps
    assert after.gaps == []
    assert after.needs_review is False


def test_composicao_vazia_falha_fechado_com_gaps():
    bundle = SourceBundle.model_validate(fapesc_base_amendment())
    empty = adaptive_artifact(_artifact()).model_copy(update={
        "subject_id": bundle.subject_id,
        "bundle_hash": bundle.compute_bundle_hash(),
        "claims": [],
        "unresolved_targets": ["requirements"],
    })
    projection = resolve(bundle.subject_id, artifacts=[empty], bundle=bundle)
    assert projection.needs_review is False
    assert projection.source_state == "adaptive"
    assert projection.gaps == ["Nenhum artifact adaptativo compatível e saudável."]
    assert projection.claims == []


def test_override_rt05_e_aplicado_depois_da_composicao():
    bundle = SourceBundle.model_validate(fapesc_base_amendment())
    document = bundle.documents[0]
    ref = EvidenceRef(
        source="fapesc", edital_id=bundle.subject_id, document=document.doc_name,
        canonical_content_hash=document.content_hash, quote=document.units[0],
        locator_quality=LocatorQuality.DOCUMENT_ONLY,
    )
    artifact = adaptive_artifact(ExtractionArtifact(
        subject_id=bundle.subject_id, document=document.doc_name,
        asset_hash=document.content_hash, bundle_hash=bundle.compute_bundle_hash(),
        targets_requested=[ExtractionTarget(
            field_path="requirements", value_type="list[str]", required_for="eligibility",
        )],
        claims=[ExtractedClaim(
            subject_id=bundle.subject_id, field_path="requirements", value=["base"],
            provenance=FactProvenance(
                state=FactState.STATED, evidence_refs=[ref],
                producer=ProducerInfo(kind=ProducerKind.LLM, name="test"),
            ),
        )], unresolved_targets=[], structured_blocks=[], table_fragments=[], route_trace=[],
        status=ExtractionStatus.COMPLETE, producer_versions={"adaptive_text": "text-v9", "edital_extraction_schema": "v3"},
        fingerprint="sha256:" + "e" * 64,
        created_at=datetime(2026, 8, 10, tzinfo=timezone.utc),
    ))
    projection = resolve(
        bundle.subject_id, artifacts=[artifact], bundle=bundle,
        review_overrides={"requirements": {"decision": "mark_unknown"}},
    )
    assert projection.claim("requirements")["provenance"]["state"] == "unknown"
    assert projection.claim("requirements")["value"] is None


def test_excecao_aberta_bloqueia_campo_mesmo_com_revisao_de_outra():
    import radar.core.services.data_quality_exceptions as exceptions

    review = SimpleNamespace(
        decision="confirm", corrected_value=None, evidence_refs=[],
        review=SimpleNamespace(model_dump=lambda mode="json": {}),
    )
    monkeypatch_rows = [
        {"id": "open", "subject_id": "finep:1", "field_path": "requirements", "issue_code": "fact_conflict", "status": "open"},
        {"id": "resolved", "subject_id": "finep:1", "field_path": "requirements", "issue_code": "fact_conflict", "status": "resolved"},
    ]
    original_list = exceptions.list_exceptions_for_subjects
    original_reviews = exceptions.load_current_temporal_reviews
    exceptions.list_exceptions_for_subjects = lambda subject_ids: {"finep:1": monkeypatch_rows}
    exceptions.load_current_temporal_reviews = lambda ids: {"resolved": review}
    try:
        from radar.core.kg.adaptive_read_model import _load_rt05_overrides_many

        overrides = _load_rt05_overrides_many(["finep:1"])
    finally:
        exceptions.list_exceptions_for_subjects = original_list
        exceptions.load_current_temporal_reviews = original_reviews
    assert overrides["finep:1"]["requirements"]["decision"] == "mark_unknown"


def test_knowledge_pathway_e_writing_recebem_a_mesma_projecao():
    promoted = adaptive_artifact(_artifact())

    class ReadModelDouble:
        def resolve(self, subject_id, **kwargs):
            return resolve(subject_id, artifacts=[promoted], **kwargs)

    knowledge = RelationalKnowledge(ReadModelDouble())
    entity = knowledge._from_card({
        "id": "finep:1", "kind": "edital", "title": "Edital", "objective": "P&D",
        "key_requirements": ["legado"], "constraints": [], "exclusoes": [],
        "eligible_entities": [], "publico_alvo": [],
    })
    assert entity["adaptive_claims"] == []
    assert entity["card"]["key_requirements"] == []

    brief = BriefProjeto(original_intention="P&D")
    project = ProjetoInovacao(workspace_id="w1", brief_id=brief.id, profile_snapshot={})
    path = CaminhoInovacao(
        status="selected", tipo="financiamento", project_id=project.id,
        entity_ref="finep:1", opportunity_ref="finep:1", recommendation="R",
        next_step="N", claims=entity["adaptive_claims"], claim_gaps=entity["adaptive_gaps"],
    )
    state = ConsultantState(
        conversation_id="c", workspace_id="w1", project=project,
        project_id=project.id, paths=[path], path_ids=[path.id], selected_path_id=path.id,
    )
    context = GroundedWriting.build_context(state, path, "proposta_tecnica", [])
    assert context.claims == path.claims


def test_payload_comum_preserva_estados_mas_oculta_valores_nao_publicaveis():
    projection = AdaptiveReadModel(
        subject_id="finep:1",
        artifact_fingerprint="sha256:" + "d" * 64,
        claims=[
            {
                "field_path": "requirements",
                "value": ["Empresa brasileira"],
                "provenance": {
                    "state": "stated",
                    "evidence_refs": [{
                        "locator_quality": "document_only",
                        "document": "edital.pdf",
                        "silver_source_hash": "sha256:" + "a" * 64,
                    }],
                },
            },
            *[
                {
                    "field_path": field,
                    "value": ["não publicar"],
                    "provenance": {"state": state, "evidence_refs": []},
                }
                for field, state in (
                    ("eligible_entities", "absent"),
                    ("publico_alvo", "unknown"),
                    ("exclusions", "inferred"),
                    ("eligibility_constraints", "conflicting"),
                )
            ],
        ],
        gaps=["publico_alvo", "exclusions", "eligibility_constraints"],
        needs_review=True,
        source_state="needs_review",
    )

    payload = projection.consumer_payload()

    assert [claim["field_path"] for claim in payload["claims"]] == [
        "requirements", "eligible_entities", "publico_alvo", "exclusions",
        "eligibility_constraints",
    ]
    assert payload["claims"][0]["value"] == ["Empresa brasileira"]
    assert all(claim["value"] is None for claim in payload["claims"][1:])
    assert payload["gaps"] == projection.gaps
    assert projection.has_effective_projection is True


def test_consumidores_compartilham_projecao_parcial_sem_fallback_entre_publicos():
    ref = {
        "locator_quality": "document_only",
        "document": "edital.pdf",
        "silver_source_hash": "sha256:" + "e" * 64,
    }
    table = {
        "document": "anexo-financeiro.pdf",
        "title": "Tabela de contrapartida",
        "page": 8,
        "purpose": "sustenta a contrapartida",
    }
    projection = AdaptiveReadModel(
        subject_id="finep:1",
        artifact_fingerprint="sha256:" + "f" * 64,
        claims=[
            {"field_path": "requirements", "value": ["novo"],
             "provenance": {"state": "stated", "evidence_refs": [ref]}},
            {"field_path": "eligible_entities", "value": None,
             "provenance": {"state": "absent", "evidence_refs": []}},
            {"field_path": "publico_alvo", "value": None,
             "provenance": {"state": "unknown", "evidence_refs": []}},
            {"field_path": "table_references", "value": [table],
             "provenance": {"state": "stated", "evidence_refs": [ref]}},
        ],
        gaps=["publico_alvo", "eligibility_constraints", "exclusions"],
        needs_review=True,
        source_state="needs_review",
    )

    class ReadModelDouble:
        def resolve(self, subject_id, **_kwargs):
            assert subject_id == "finep:1"
            return projection

    knowledge = RelationalKnowledge(ReadModelDouble())
    entity = knowledge._from_card({
        "id": "finep:1", "kind": "edital", "title": "Edital", "objective": "P&D",
        "key_requirements": ["legado"], "constraints": [{"tipo": "legado"}],
        "exclusoes": ["legado"], "eligible_entities": ["legado"],
        "publico_alvo": ["legado"],
    })

    assert entity["adaptive_claims"] == projection.public_claims()
    assert entity["adaptive_gaps"] == projection.gaps
    assert entity["card"]["key_requirements"] == ["novo"]
    assert entity["card"]["eligible_entities"] == []
    assert entity["card"]["publico_alvo"] == []
    assert entity["card"]["constraints"] == []
    assert entity["card"]["exclusoes"] == []
    table_claim = next(
        claim for claim in entity["adaptive_claims"]
        if claim["field_path"] == "table_references"
    )
    assert table_claim["value"] == [table]
    adaptive_evidence = knowledge._evidence_for(entity)
    assert adaptive_evidence == GoldPathways._adaptive_evidence(entity)
    assert any(item.kind == "table_reference" for item in adaptive_evidence)

    brief = BriefProjeto(original_intention="P&D")
    project = ProjetoInovacao(workspace_id="w1", brief_id=brief.id)
    path = CaminhoInovacao(
        status="selected", tipo="financiamento", project_id=project.id,
        entity_ref="finep:1", opportunity_ref="finep:1", recommendation="R",
        next_step="N", claims=entity["adaptive_claims"], claim_gaps=entity["adaptive_gaps"],
    )
    state = ConsultantState(
        conversation_id="c", workspace_id="w1", project=project,
        project_id=project.id, paths=[path], path_ids=[path.id], selected_path_id=path.id,
    )
    context = GroundedWriting.build_context(state, path, "proposta_tecnica", [])
    assert context.claims == entity["adaptive_claims"]
    assert any(claim["field_path"] == "table_references" for claim in context.claims)


def test_falha_ao_ler_projecao_vira_lacuna_sem_interromper_knowledge():
    class FailingReadModel:
        def resolve(self, subject_id, **_kwargs):
            raise RuntimeError("read model indisponível")

    knowledge = RelationalKnowledge(FailingReadModel())
    entity = knowledge._from_card({
        "id": "finep:1", "kind": "edital", "title": "Edital", "objective": "P&D",
        "key_requirements": ["legado"],
    })

    assert entity["adaptive_claims"] == []
    assert entity["adaptive_gaps"] == ["Projeção adaptativa indisponível; fatos adaptativos não publicados."]
    assert entity["card"]["key_requirements"] == []
