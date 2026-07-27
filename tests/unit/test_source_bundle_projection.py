from __future__ import annotations

import json

import pytest

from radar.core.kg import source_bundles
from radar.core.kg.source_bundle_projection import (
    ExplicitClaim,
    attach_bundle_lineage,
    find_current_document,
    project_current_documents,
    resolve_field,
)
from radar.domain.provenance import EvidenceRef, FactState, LocatorQuality
from radar.domain.source_bundle import SourceBundle, compute_content_hash
from tests.fixtures.source_bundles.fixtures import fapesc_base_amendment, web_portal_challenge

pytestmark = pytest.mark.unit


def _bundle(data: dict) -> SourceBundle:
    return SourceBundle.model_validate(data)


def _claim(field: str, value, content_hash: str, *, supersedes_content_hash: str | None = None) -> ExplicitClaim:
    return ExplicitClaim(
        field=field,
        value=value,
        content_hash=content_hash,
        supersedes_content_hash=supersedes_content_hash,
    )


class _Result:
    def __init__(self, data):
        self.data = data


class _SelectChain:
    def __init__(self, rows):
        self._rows = rows
        self._filters: dict[str, object] = {}

    def select(self, *_args, **_kwargs):
        return self

    def eq(self, col, val):
        self._filters[col] = val
        return self

    def order(self, *_args, **_kwargs):
        return self

    def limit(self, _n):
        return self

    def execute(self):
        rows = self._rows
        for col, val in self._filters.items():
            rows = [row for row in rows if row.get(col) == val]
        return _Result(rows[:1])


class _FakeDB:
    def __init__(self, rows):
        self._rows = rows

    def table(self, _name):
        return _SelectChain(self._rows)


def test_superseded_excluded_from_projection():
    data = fapesc_base_amendment()
    data["documents"][0]["authority_state"] = "superseded"
    bundle = _bundle(data)

    projection = project_current_documents(bundle)

    assert [doc.doc_name for doc in projection.documents] == ["Retificacao_01_37_2026.pdf"]


def test_active_contextual_and_annex_preserved():
    data = web_portal_challenge()
    annex_units = ["Anexo tecnico do desafio."]
    data["documents"].append({
        "doc_name": "anexo.pdf",
        "units": annex_units,
        "role": "annex",
        "source_url": "https://tupy.example.com/anexo.pdf",
        "content_hash": compute_content_hash(annex_units),
        "authority_state": "active",
        "composition_order": 2,
    })
    bundle = _bundle(data)

    projection = project_current_documents(bundle)

    assert [doc.role.value for doc in projection.documents] == [
        "program_page", "opportunity_page", "annex",
    ]


def test_partial_posterior_does_not_replace_last_complete(monkeypatch):
    complete = _bundle(fapesc_base_amendment())
    partial_data = fapesc_base_amendment()
    partial_data["collected_at"] = "2026-07-28T12:00:00Z"
    partial_data["acquisition_status"] = "partial"
    partial = _bundle(partial_data)
    rows = [
        {
            "subject_kind": "opportunity",
            "subject_id": "fapesc:37-2026",
            "acquisition_status": "partial",
            "bundle": json.loads(partial.model_dump_json()),
        },
        {
            "subject_kind": "opportunity",
            "subject_id": "fapesc:37-2026",
            "acquisition_status": "complete",
            "bundle": json.loads(complete.model_dump_json()),
        },
    ]
    monkeypatch.setattr(source_bundles, "_pg_configured", lambda: True)
    monkeypatch.setattr("radar.core.infra.db.get_supabase_service", lambda: _FakeDB(rows))

    loaded = source_bundles.load("opportunity", "fapesc:37-2026")

    assert loaded is not None
    assert loaded.acquisition_status.value == "complete"
    assert loaded.collected_at == complete.collected_at


def test_equal_claims_do_not_conflict():
    bundle = _bundle(fapesc_base_amendment())
    base, amendment = bundle.documents

    resolution = resolve_field(bundle, [
        _claim("deadline", "2026-09-30", base.content_hash),
        _claim("deadline", "2026-09-30", amendment.content_hash),
    ], "deadline")

    assert resolution.state is FactState.STATED
    assert resolution.value == "2026-09-30"
    assert len(resolution.evidence_refs) == 2


def test_amendment_explicitly_wins_only_referenced_field():
    bundle = _bundle(fapesc_base_amendment())
    base, amendment = bundle.documents

    resolution = resolve_field(bundle, [
        _claim("deadline", "2026-08-31", base.content_hash),
        _claim(
            "deadline",
            "2026-09-30",
            amendment.content_hash,
            supersedes_content_hash=base.content_hash,
        ),
    ], "deadline")

    assert resolution.state is FactState.STATED
    assert resolution.value == "2026-09-30"
    assert [ref.document for ref in resolution.evidence_refs] == [amendment.doc_name]


def test_amendment_does_not_win_different_field():
    bundle = _bundle(fapesc_base_amendment())
    base, amendment = bundle.documents

    resolution = resolve_field(bundle, [
        _claim("publico_alvo", "MPE", base.content_hash),
        _claim("publico_alvo", "Medias empresas", amendment.content_hash),
    ], "publico_alvo")

    assert resolution.state is FactState.CONFLICTING
    assert resolution.value is None


def test_opportunity_page_wins_program_page_on_same_field():
    bundle = _bundle(web_portal_challenge())
    program_page, opportunity_page = bundle.documents

    resolution = resolve_field(bundle, [
        _claim("beneficio", "Aporte de ate R$ 200 mil", program_page.content_hash),
        _claim("beneficio", "R$ 150 mil em subvencao", opportunity_page.content_hash),
    ], "beneficio")

    assert resolution.state is FactState.STATED
    assert resolution.value == "R$ 150 mil em subvencao"
    assert [ref.document for ref in resolution.evidence_refs] == [opportunity_page.doc_name]


def test_curated_record_does_not_beat_official_record():
    data = {
        "schema_version": 1,
        "subject_kind": "program",
        "subject_id": "programa:teste",
        "source": "manual",
        "collected_at": "2026-07-27T12:00:00Z",
        "producer_version": "test-v1",
        "acquisition_status": "complete",
        "documents": [
            {
                "doc_name": "registro_oficial.json",
                "units": ["registro oficial"],
                "role": "official_record",
                "source_url": "https://programa.example/oficial",
                "content_hash": compute_content_hash(["registro oficial"]),
                "authority_state": "active",
            },
            {
                "doc_name": "catalogo.json",
                "units": ["registro curado"],
                "role": "curated_record",
                "source_url": "https://programa.example/catalogo",
                "content_hash": compute_content_hash(["registro curado"]),
                "authority_state": "active",
            },
        ],
    }
    bundle = _bundle(data)
    official, curated = bundle.documents

    resolution = resolve_field(bundle, [
        _claim("name", "Programa Oficial", official.content_hash),
        _claim("name", "Programa Curado", curated.content_hash),
    ], "name")

    assert resolution.state is FactState.STATED
    assert resolution.value == "Programa Oficial"
    assert [ref.document for ref in resolution.evidence_refs] == [official.doc_name]


def test_incompatible_values_without_precedence_become_conflicting():
    bundle = _bundle(fapesc_base_amendment())
    base, amendment = bundle.documents

    resolution = resolve_field(bundle, [
        _claim("ticket", 100000, base.content_hash),
        _claim("ticket", 200000, amendment.content_hash),
    ], "ticket")

    assert resolution.state is FactState.CONFLICTING
    assert resolution.value is None
    assert len(resolution.evidence_refs) == 2


def test_claim_for_absent_or_superseded_document_is_ignored_safely():
    data = fapesc_base_amendment()
    data["documents"][0]["authority_state"] = "superseded"
    bundle = _bundle(data)
    base, amendment = bundle.documents

    resolution = resolve_field(bundle, [
        _claim("deadline", "2026-08-31", base.content_hash),
        _claim("deadline", "2026-09-30", amendment.content_hash),
        _claim("deadline", "2026-10-01", "sha256:" + "f" * 64),
    ], "deadline")

    assert resolution.state is FactState.STATED
    assert resolution.value == "2026-09-30"
    assert len(resolution.limitations) == 2
    assert all("Claim ignorado" in item for item in resolution.limitations)


def test_attach_bundle_lineage_adds_bundle_and_content_hash():
    bundle = _bundle(web_portal_challenge())
    document = bundle.documents[0]
    ref = attach_bundle_lineage(
        resolve_field(
            bundle,
            [_claim("beneficio", "A", document.content_hash)],
            "beneficio",
        ).evidence_refs[0],
        bundle=bundle,
        document=document,
    )

    assert ref.bundle_hash == bundle.compute_bundle_hash()
    assert ref.content_hash == document.content_hash


def test_attach_bundle_lineage_rejects_superseded_document():
    data = fapesc_base_amendment()
    data["documents"][0]["authority_state"] = "superseded"
    bundle = _bundle(data)
    superseded_doc = bundle.documents[0]
    ref = EvidenceRef(
        source="fapesc",
        edital_id=bundle.subject_id,
        document=superseded_doc.doc_name,
        canonical_content_hash=superseded_doc.content_hash,
        locator_quality=LocatorQuality.DOCUMENT_ONLY,
    )

    with pytest.raises(ValueError, match="superseded"):
        attach_bundle_lineage(ref, bundle=bundle, document=superseded_doc)


def test_find_current_document_returns_none_for_superseded_or_ambiguous():
    data = web_portal_challenge()
    first = data["documents"][0]
    data["documents"].append({**first})
    bundle = _bundle(data)
    assert find_current_document(bundle, content_hash=first["content_hash"], doc_name=first["doc_name"]) is None


def test_absence_of_claims_does_not_fabricate_value_or_conflict():
    bundle = _bundle(web_portal_challenge())

    resolution = resolve_field(bundle, [], "deadline")

    assert resolution.state is FactState.UNKNOWN
    assert resolution.value is None
    assert resolution.evidence_refs == []
    assert resolution.limitations == ["Sem claims explicitos para o campo."]


def test_no_precedence_is_inferred_from_published_at_name_or_order():
    data = {
        "schema_version": 1,
        "subject_kind": "opportunity",
        "subject_id": "web:teste",
        "source": "web",
        "collected_at": "2026-07-27T12:00:00Z",
        "producer_version": "test-v1",
        "acquisition_status": "complete",
        "documents": [
            {
                "doc_name": "a-documento-novo.html",
                "units": ["contexto antigo"],
                "role": "official_page",
                "source_url": "https://example.com/a",
                "published_at": "2026-07-10",
                "content_hash": compute_content_hash(["contexto antigo"]),
                "authority_state": "active",
                "composition_order": 0,
            },
            {
                "doc_name": "z-documento-antigo.html",
                "units": ["contexto concorrente"],
                "role": "official_page",
                "source_url": "https://example.com/z",
                "published_at": "2026-01-01",
                "content_hash": compute_content_hash(["contexto concorrente"]),
                "authority_state": "active",
                "composition_order": 99,
            },
        ],
    }
    bundle = _bundle(data)
    first, second = bundle.documents

    resolution = resolve_field(bundle, [
        _claim("beneficio", "valor-a", first.content_hash),
        _claim("beneficio", "valor-b", second.content_hash),
    ], "beneficio")

    assert resolution.state is FactState.CONFLICTING
    assert resolution.value is None
