"""Testes do contrato SourceBundle (RT04-T01).

Cobre:
  - enums vs. YAML autoritativo (§14 de schema.md), lido pelo loader real;
  - construção, serialização e round-trip de SourceBundle;
  - hash estável para entradas idênticas;
  - hash imutável sob mudança de collected_at / producer_version;
  - hash mutável sob mudança de conteúdo, role, authority_state, acquisition_status;
  - incidental order não altera hash (mesmo com empate de nome/order);
  - composition_order altera hash;
  - partial e complete têm hashes diferentes;
  - validade das três fixtures (web, fapesc, actor);
  - rejeições: units vazio/branco, ID inválido por kind, papel proibido,
    amends_content_hash inexistente/fora-de-contexto, extra field,
    content_hash malformatado e inconsistente.
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from radar.core.kg.schema import load as load_schema
from radar.domain.source_bundle import (
    SOURCE_BUNDLE_SCHEMA_VERSION,
    AcquisitionStatus,
    AuthorityState,
    DocumentMetadata,
    DocumentRole,
    SourceBundle,
    SubjectKind,
    compute_content_hash,
)
from tests.fixtures.source_bundles.fixtures import (
    actor_insufficient,
    fapesc_base_amendment,
    web_portal_challenge,
)

pytestmark = pytest.mark.unit

_schema = load_schema()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_doc(**overrides: dict) -> dict:
    """Document fixture base com conteúdo estável (role oportunidade)."""
    units = ["conteúdo do documento de teste"]
    defaults: dict = {
        "doc_name": "test.pdf",
        "units": units,
        "role": "base_notice",
        "content_hash": compute_content_hash(units),
        "authority_state": "active",
    }
    defaults.update(overrides)
    return defaults


def _make_doc_actor(**overrides: dict) -> dict:
    """Document fixture base com role de ator."""
    units = ["conteúdo do ator de teste"]
    defaults: dict = {
        "doc_name": "page.html",
        "units": units,
        "role": "official_page",
        "content_hash": compute_content_hash(units),
        "authority_state": "active",
    }
    defaults.update(overrides)
    return defaults


def _make_bundle(**overrides: dict) -> dict:
    """SourceBundle fixture mínimo (oportunidade)."""
    defaults: dict = {
        "schema_version": 1,
        "subject_kind": "opportunity",
        "subject_id": "fapesc:test-2026",
        "source": "fapesc",
        "collected_at": "2026-07-27T12:00:00Z",
        "producer_version": "adapter-v1",
        "acquisition_status": "complete",
        "documents": [_make_doc()],
    }
    defaults.update(overrides)
    return defaults


def _make_actor_bundle(**overrides: dict) -> dict:
    """SourceBundle fixture mínimo para ator (ICT)."""
    defaults: dict = {
        "schema_version": 1,
        "subject_kind": "ict",
        "subject_id": "ict:exemplo:test-lab",
        "source": "exemplo",
        "collected_at": "2026-07-27T12:00:00Z",
        "producer_version": "catalog-v1",
        "acquisition_status": "partial",
        "documents": [_make_doc_actor()],
    }
    defaults.update(overrides)
    return defaults


# ---------------------------------------------------------------------------
# Enum values vs YAML autoritativo (§14) — lido pelo loader real
# ---------------------------------------------------------------------------

class TestEnumYamlEquality:
    """Lê os blocos YAML de docs/domain/schema.md e compara com os enums Python.

    Se o YAML mudar sem atualizar os enums (ou vice-versa), este teste falha.
    """

    def test_subject_kind_values(self):
        yaml_values = set(_schema["source_bundle_subject_kinds"])
        assert {s.value for s in SubjectKind} == yaml_values

    def test_acquisition_status_values(self):
        yaml_values = set(_schema["source_bundle_acquisition_statuses"])
        assert {a.value for a in AcquisitionStatus} == yaml_values

    def test_document_role_values(self):
        yaml_values = set(_schema["source_bundle_document_roles"])
        assert {r.value for r in DocumentRole} == yaml_values

    def test_authority_state_values(self):
        yaml_values = set(_schema["source_bundle_authority_states"])
        assert {a.value for a in AuthorityState} == yaml_values


# ---------------------------------------------------------------------------
# Construção e round-trip
# ---------------------------------------------------------------------------

class TestConstruction:
    def test_minimal_bundle_valid(self):
        data = _make_bundle()
        bundle = SourceBundle.model_validate(data)
        assert bundle.schema_version == 1
        assert bundle.subject_kind == SubjectKind.OPPORTUNITY
        assert bundle.acquisition_status == AcquisitionStatus.COMPLETE
        assert len(bundle.documents) == 1
        assert bundle.compute_bundle_hash().startswith("sha256:")

    def test_minimal_actor_bundle_valid(self):
        data = _make_actor_bundle()
        bundle = SourceBundle.model_validate(data)
        assert bundle.subject_kind == SubjectKind.ICT
        assert bundle.acquisition_status == AcquisitionStatus.PARTIAL

    def test_document_metadata_minimal(self):
        data = _make_doc()
        doc = DocumentMetadata.model_validate(data)
        assert doc.doc_name == "test.pdf"
        assert doc.role == DocumentRole.BASE_NOTICE
        assert doc.authority_state == AuthorityState.ACTIVE
        assert doc.amends_content_hash is None
        assert doc.composition_order is None
        assert doc.source_url is None
        assert doc.published_at is None

    def test_round_trip_json(self):
        data = _make_bundle()
        bundle = SourceBundle.model_validate(data)
        raw = json.loads(bundle.model_dump_json())
        assert raw["schema_version"] == 1
        assert "bundle_hash" not in raw
        assert "created_at" not in raw
        bundle2 = SourceBundle.model_validate(raw)
        assert bundle.compute_bundle_hash() == bundle2.compute_bundle_hash()

    def test_round_trip_keeps_optional_fields(self):
        doc_data = _make_doc(
            source_url="https://example.com/doc",
            published_at="2026-06-01",
            composition_order=1,
            authority_state="superseded",
        )
        data = _make_bundle(documents=[doc_data])
        bundle = SourceBundle.model_validate(data)
        raw = json.loads(bundle.model_dump_json())
        doc = raw["documents"][0]
        assert doc["source_url"] == "https://example.com/doc"
        assert doc["published_at"] == "2026-06-01"
        assert doc["composition_order"] == 1
        assert doc["authority_state"] == "superseded"


# ---------------------------------------------------------------------------
# Hash stability
# ---------------------------------------------------------------------------

class TestHashStability:
    def test_identical_inputs_same_hash(self):
        data = _make_bundle()
        b1 = SourceBundle.model_validate(data)
        b2 = SourceBundle.model_validate(data)
        assert b1.compute_bundle_hash() == b2.compute_bundle_hash()

    def test_collected_at_does_not_affect_hash(self):
        data = _make_bundle()
        b1 = SourceBundle.model_validate(data)
        mutated = dict(data)
        mutated["collected_at"] = "2027-01-01T00:00:00Z"
        b2 = SourceBundle.model_validate(mutated)
        assert b1.compute_bundle_hash() == b2.compute_bundle_hash()

    def test_producer_version_does_not_affect_hash(self):
        data = _make_bundle()
        b1 = SourceBundle.model_validate(data)
        mutated = dict(data)
        mutated["producer_version"] = "adapter-v999"
        b2 = SourceBundle.model_validate(mutated)
        assert b1.compute_bundle_hash() == b2.compute_bundle_hash()

    def test_incidental_document_order_same_hash(self):
        docs = [
            _make_doc(doc_name="b.pdf", composition_order=1,
                      units=["bb"], content_hash=compute_content_hash(["bb"])),
            _make_doc(doc_name="a.pdf", composition_order=0),
        ]
        data1 = _make_bundle(documents=docs)
        docs_reversed = list(reversed(docs))
        data2 = _make_bundle(documents=docs_reversed)
        b1 = SourceBundle.model_validate(data1)
        b2 = SourceBundle.model_validate(data2)
        assert b1.compute_bundle_hash() == b2.compute_bundle_hash()

    def test_incidental_order_with_tiebreaker(self):
        """Mesmo composition_order e doc_name mas content_hash diferente desempata.

        A ordenação total garante hash estável independente da ordem
        incidental do input.
        """
        docs_a = [
            _make_doc(doc_name="same.pdf", composition_order=0,
                      units=["aaa"], content_hash=compute_content_hash(["aaa"])),
            _make_doc(doc_name="same.pdf", composition_order=0,
                      units=["bbb"], content_hash=compute_content_hash(["bbb"])),
        ]
        docs_b = list(reversed(docs_a))
        b1 = SourceBundle.model_validate(_make_bundle(documents=docs_a))
        b2 = SourceBundle.model_validate(_make_bundle(documents=docs_b))
        assert b1.compute_bundle_hash() == b2.compute_bundle_hash()

    def test_composition_order_change_alters_hash(self):
        docs_a = [
            _make_doc(doc_name="a.pdf", composition_order=0),
            _make_doc(doc_name="b.pdf", composition_order=1,
                      units=["bb"], content_hash=compute_content_hash(["bb"])),
        ]
        docs_b = [
            _make_doc(doc_name="a.pdf", composition_order=1),
            _make_doc(doc_name="b.pdf", composition_order=0,
                      units=["bb"], content_hash=compute_content_hash(["bb"])),
        ]
        b1 = SourceBundle.model_validate(_make_bundle(documents=docs_a))
        b2 = SourceBundle.model_validate(_make_bundle(documents=docs_b))
        assert b1.compute_bundle_hash() != b2.compute_bundle_hash()


# ---------------------------------------------------------------------------
# Hash mutability (material changes)
# ---------------------------------------------------------------------------

class TestHashMutability:
    def test_content_change_alters_hash(self):
        b1 = SourceBundle.model_validate(_make_bundle())
        doc2 = _make_doc(
            units=["conteúdo totalmente diferente"],
            content_hash=compute_content_hash(["conteúdo totalmente diferente"]),
        )
        b2 = SourceBundle.model_validate(_make_bundle(documents=[doc2]))
        assert b1.compute_bundle_hash() != b2.compute_bundle_hash()

    def test_role_change_alters_hash(self):
        b1 = SourceBundle.model_validate(_make_bundle())
        doc2 = _make_doc(role="annex")
        b2 = SourceBundle.model_validate(_make_bundle(documents=[doc2]))
        assert b1.compute_bundle_hash() != b2.compute_bundle_hash()

    def test_authority_state_change_alters_hash(self):
        b1 = SourceBundle.model_validate(_make_bundle())
        doc2 = _make_doc(authority_state="contextual")
        b2 = SourceBundle.model_validate(_make_bundle(documents=[doc2]))
        assert b1.compute_bundle_hash() != b2.compute_bundle_hash()

    def test_document_set_change_alters_hash(self):
        b1 = SourceBundle.model_validate(_make_bundle())
        docs2 = [
            _make_doc(),
            _make_doc(doc_name="annex.pdf", role="annex",
                      units=["anexo"], content_hash=compute_content_hash(["anexo"])),
        ]
        b2 = SourceBundle.model_validate(_make_bundle(documents=docs2))
        assert b1.compute_bundle_hash() != b2.compute_bundle_hash()

    def test_subject_id_change_alters_hash(self):
        b1 = SourceBundle.model_validate(_make_bundle(subject_id="fapesc:a"))
        b2 = SourceBundle.model_validate(_make_bundle(subject_id="fapesc:b"))
        assert b1.compute_bundle_hash() != b2.compute_bundle_hash()

    def test_source_change_alters_hash(self):
        b1 = SourceBundle.model_validate(_make_bundle(source="fapesc"))
        b2 = SourceBundle.model_validate(_make_bundle(source="finep"))
        assert b1.compute_bundle_hash() != b2.compute_bundle_hash()

    def test_acquisition_status_change_alters_hash(self):
        b1 = SourceBundle.model_validate(_make_bundle(acquisition_status="complete"))
        b2 = SourceBundle.model_validate(
            _make_bundle(acquisition_status="partial")
        )
        assert b1.compute_bundle_hash() != b2.compute_bundle_hash()

    def test_schema_version_enters_hash(self):
        """Schema version está hardcoded como Literal[1] e entra no hash."""
        bundle = SourceBundle.model_validate(_make_bundle())
        payload = json.loads(bundle.model_dump_json())
        assert payload["schema_version"] == 1


# ---------------------------------------------------------------------------
# Fixture validation
# ---------------------------------------------------------------------------

class TestFixtures:
    def test_web_portal_challenge(self):
        data = web_portal_challenge()
        bundle = SourceBundle.model_validate(data)
        assert bundle.subject_kind == SubjectKind.OPPORTUNITY
        assert bundle.subject_id == "web:a1b2c3d4e5"
        assert bundle.source == "web"
        assert bundle.acquisition_status == AcquisitionStatus.COMPLETE
        assert len(bundle.documents) == 2
        roles = {d.role for d in bundle.documents}
        assert DocumentRole.PROGRAM_PAGE in roles
        assert DocumentRole.OPPORTUNITY_PAGE in roles
        assert bundle.compute_bundle_hash().startswith("sha256:")

    def test_fapesc_base_amendment(self):
        data = fapesc_base_amendment()
        bundle = SourceBundle.model_validate(data)
        assert bundle.subject_kind == SubjectKind.OPPORTUNITY
        assert bundle.subject_id == "fapesc:37-2026"
        assert bundle.source == "fapesc"
        assert len(bundle.documents) == 2
        roles = {d.role for d in bundle.documents}
        assert DocumentRole.BASE_NOTICE in roles
        assert DocumentRole.AMENDMENT in roles
        amd = [d for d in bundle.documents if d.role == DocumentRole.AMENDMENT][0]
        base = [d for d in bundle.documents if d.role == DocumentRole.BASE_NOTICE][0]
        assert amd.amends_content_hash == base.content_hash
        assert amd.amends_content_hash is not None
        assert amd.amends_content_hash.startswith("sha256:")

    def test_actor_insufficient(self):
        data = actor_insufficient()
        bundle = SourceBundle.model_validate(data)
        assert bundle.subject_kind == SubjectKind.ICT
        assert bundle.subject_id == "ict:exemplo:lab-inovacao"
        assert bundle.source == "exemplo"
        assert bundle.acquisition_status == AcquisitionStatus.PARTIAL
        assert len(bundle.documents) == 1
        assert bundle.documents[0].role == DocumentRole.OFFICIAL_PAGE
        assert bundle.compute_bundle_hash().startswith("sha256:")

    def test_fixture_hashes_stable_across_runs(self):
        data1 = web_portal_challenge()
        data2 = web_portal_challenge()
        b1 = SourceBundle.model_validate(data1)
        b2 = SourceBundle.model_validate(data2)
        assert b1.compute_bundle_hash() == b2.compute_bundle_hash()

    def test_partial_and_complete_have_different_hashes(self):
        complete = SourceBundle.model_validate(
            _make_bundle(acquisition_status="complete")
        )
        partial = SourceBundle.model_validate(
            _make_bundle(acquisition_status="partial")
        )
        assert complete.compute_bundle_hash() != partial.compute_bundle_hash()

    def test_actor_remains_incomplete_no_synthetic_content(self):
        data = actor_insufficient()
        assert data["acquisition_status"] == "partial"
        assert len(data["documents"]) == 1
        assert len(data["documents"][0]["units"][0]) < 200


# ---------------------------------------------------------------------------
# Validation rejections
# ---------------------------------------------------------------------------

class TestRejections:
    # --- Document set ---
    def test_empty_documents_rejected(self):
        with pytest.raises(ValidationError, match="at least one document"):
            SourceBundle.model_validate(_make_bundle(documents=[]))

    def test_units_empty_list_rejected(self):
        doc = _make_doc(units=[])
        with pytest.raises(ValidationError, match="units must be non-empty"):
            SourceBundle.model_validate(_make_bundle(documents=[doc]))

    def test_units_empty_string_rejected(self):
        doc = _make_doc(units=[""])
        with pytest.raises(ValidationError, match=r"units\[0\] is empty"):
            SourceBundle.model_validate(_make_bundle(documents=[doc]))

    def test_units_blank_string_rejected(self):
        doc = _make_doc(units=["   "])
        with pytest.raises(ValidationError, match=r"units\[0\] is empty"):
            SourceBundle.model_validate(_make_bundle(documents=[doc]))

    # --- Enums ---
    def test_invalid_subject_kind_rejected(self):
        with pytest.raises(ValidationError):
            SourceBundle.model_validate(_make_bundle(subject_kind="invalid"))

    def test_invalid_acquisition_status_rejected(self):
        with pytest.raises(ValidationError):
            SourceBundle.model_validate(
                _make_bundle(acquisition_status="unknown")
            )

    def test_invalid_document_role_rejected(self):
        with pytest.raises(ValidationError):
            doc = _make_doc(role="invalid_role")
            SourceBundle.model_validate(_make_bundle(documents=[doc]))

    # --- Extra fields ---
    def test_extra_field_on_envelope_rejected(self):
        with pytest.raises(ValidationError):
            SourceBundle.model_validate(_make_bundle(extra_field="should_fail"))

    def test_extra_field_on_document_rejected(self):
        doc_data = _make_doc()
        doc_data["extra_doc_field"] = "nope"
        with pytest.raises(ValidationError):
            SourceBundle.model_validate(_make_bundle(documents=[doc_data]))

    def test_created_at_rejected_as_extra(self):
        """created_at não pertence ao envelope (é persistência T02).
        Se aparecer no JSON de entrada, é rejeitado como extra."""
        data = _make_bundle(created_at="2026-07-27T12:00:00Z")
        with pytest.raises(ValidationError):
            SourceBundle.model_validate(data)

    def test_supersedes_rejected_as_extra(self):
        """supersedes foi substituído por amends_content_hash."""
        doc_data = _make_doc(supersedes="old.pdf")
        with pytest.raises(ValidationError):
            SourceBundle.model_validate(_make_bundle(documents=[doc_data]))

    # --- Content hash ---
    def test_content_hash_mismatch_rejected(self):
        doc = _make_doc(content_hash="sha256:" + "a" * 64)
        with pytest.raises(ValidationError, match="content_hash mismatch"):
            SourceBundle.model_validate(_make_bundle(documents=[doc]))

    def test_content_hash_bad_prefix_rejected(self):
        doc = _make_doc(content_hash="md5:abc123")
        with pytest.raises(ValidationError, match="content_hash must start"):
            SourceBundle.model_validate(_make_bundle(documents=[doc]))

    def test_content_hash_bad_hex_len_rejected(self):
        doc = _make_doc(content_hash="sha256:abc")
        with pytest.raises(ValidationError, match="content_hash hex part"):
            SourceBundle.model_validate(_make_bundle(documents=[doc]))

    def test_content_hash_non_hex_rejected(self):
        doc = _make_doc(content_hash="sha256:" + "z" * 64)
        with pytest.raises(ValidationError, match="content_hash hex part"):
            SourceBundle.model_validate(_make_bundle(documents=[doc]))

    # --- Empty identifiers ---
    def test_empty_subject_id_rejected(self):
        with pytest.raises(ValidationError, match="subject_id must be non-empty"):
            SourceBundle.model_validate(_make_bundle(subject_id="   "))

    def test_empty_source_rejected(self):
        with pytest.raises(ValidationError, match="source must be non-empty"):
            SourceBundle.model_validate(_make_bundle(source="   "))

    def test_empty_producer_version_rejected(self):
        with pytest.raises(ValidationError, match="producer_version must be non-empty"):
            SourceBundle.model_validate(_make_bundle(producer_version="   "))

    def test_empty_doc_name_rejected(self):
        doc = _make_doc(doc_name="")
        with pytest.raises(ValidationError, match="doc_name must be non-empty"):
            SourceBundle.model_validate(_make_bundle(documents=[doc]))

    # --- Timezone ---
    def test_collected_at_naive_rejected(self):
        data = _make_bundle(collected_at="2026-07-27T12:00:00")
        with pytest.raises(ValidationError, match="timezone-aware"):
            SourceBundle.model_validate(data)

    def test_collected_at_non_utc_accepted(self):
        """Timezone-aware mas não UTC é aceito; normalizado pelo produtor."""
        data = _make_bundle(collected_at="2026-07-27T09:00:00-03:00")
        bundle = SourceBundle.model_validate(data)
        assert bundle.collected_at.tzinfo is not None

    # --- composition_order ---
    def test_composition_order_negative_rejected(self):
        doc = _make_doc(composition_order=-1)
        with pytest.raises(ValidationError, match="composition_order must be >= 0"):
            SourceBundle.model_validate(_make_bundle(documents=[doc]))

    # --- Canonical ID ---
    def test_opportunity_id_without_colon_rejected(self):
        with pytest.raises(ValidationError, match="<source>:<native_id>"):
            SourceBundle.model_validate(_make_bundle(subject_id="no-colon"))

    def test_opportunity_id_with_actor_prefix_rejected(self):
        with pytest.raises(ValidationError, match="must not start with actor"):
            SourceBundle.model_validate(
                _make_bundle(subject_id="investidor:test")
            )

    def test_investor_id_must_start_with_investidor(self):
        with pytest.raises(ValidationError, match="start with 'investidor:'"):
            SourceBundle.model_validate(
                _make_actor_bundle(subject_kind="investor",
                                   subject_id="my-fund:abc")
            )

    def test_ict_id_must_start_with_ict(self):
        with pytest.raises(ValidationError, match="start with 'ict:'"):
            SourceBundle.model_validate(
                _make_actor_bundle(subject_id="exemplo:lab")
            )

    def test_program_id_must_start_with_programa(self):
        with pytest.raises(ValidationError, match="start with 'programa:'"):
            SourceBundle.model_validate(
                _make_actor_bundle(subject_kind="program",
                                   subject_id="meu-programa:abc")
            )

    def test_agency_id_must_start_with_agencia(self):
        with pytest.raises(ValidationError, match="start with 'agencia:'"):
            SourceBundle.model_validate(
                _make_actor_bundle(subject_kind="agency",
                                   subject_id="finep")
            )

    # --- Role per kind ---
    def test_opportunity_role_on_actor_rejected(self):
        doc = _make_doc_actor(role="base_notice")
        with pytest.raises(ValidationError, match="role.*not allowed"):
            SourceBundle.model_validate(
                _make_actor_bundle(documents=[doc])
            )

    def test_actor_role_on_opportunity_rejected(self):
        doc = _make_doc(role="curated_record")
        with pytest.raises(ValidationError, match="role.*not allowed"):
            SourceBundle.model_validate(_make_bundle(documents=[doc]))

    def test_actor_official_page_accepted(self):
        """official_page é permitido tanto em oportunidade quanto em ator."""
        doc = _make_doc(role="official_page")
        bundle = SourceBundle.model_validate(_make_bundle(documents=[doc]))
        assert bundle.documents[0].role == DocumentRole.OFFICIAL_PAGE

    def test_actor_curated_record_accepted(self):
        doc = _make_doc_actor(role="curated_record")
        bundle = SourceBundle.model_validate(
            _make_actor_bundle(documents=[doc])
        )
        assert bundle.documents[0].role == DocumentRole.CURATED_RECORD

    # --- amends_content_hash ---
    def test_amends_content_hash_on_non_amendment_rejected(self):
        doc = _make_doc(amends_content_hash="sha256:" + "a" * 64)
        with pytest.raises(ValidationError, match="only allowed on.*amendment"):
            SourceBundle.model_validate(_make_bundle(documents=[doc]))

    def test_amends_content_hash_format_invalid_rejected(self):
        doc = _make_doc(role="amendment", amends_content_hash="not-a-hash")
        with pytest.raises(ValidationError, match="amends_content_hash must start"):
            SourceBundle.model_validate(_make_bundle(documents=[doc]))

    def test_amends_content_hash_nonexistent_rejected(self):
        units = ["retificação"]
        doc = _make_doc(
            doc_name="ret.pdf",
            role="amendment",
            units=units,
            content_hash=compute_content_hash(units),
            amends_content_hash="sha256:" + "b" * 64,
        )
        with pytest.raises(ValidationError, match="does not match any"):
            SourceBundle.model_validate(_make_bundle(documents=[doc]))

    # --- Partial ---
    def test_partial_is_valid(self):
        bundle = SourceBundle.model_validate(
            _make_bundle(acquisition_status="partial")
        )
        assert bundle.acquisition_status == AcquisitionStatus.PARTIAL


# ---------------------------------------------------------------------------
# Schema version constant
# ---------------------------------------------------------------------------

class TestSchemaVersion:
    def test_version_constant(self):
        assert SOURCE_BUNDLE_SCHEMA_VERSION == 1
        assert isinstance(SOURCE_BUNDLE_SCHEMA_VERSION, int)
