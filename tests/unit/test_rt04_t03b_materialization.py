from __future__ import annotations

import json

import pytest

from radar.core.kg.source_bundles import BundleStorageError
from radar.core.services import discovery_materializer as materializer
from radar.core.services.discovery_evidence import build_evidence_package
from radar.domain.source_bundle import AcquisitionStatus, AuthorityState, DocumentRole

pytestmark = pytest.mark.unit


def _opportunity() -> dict:
    return {
        "url": "https://portal.example/desafio-x",
        "title": "Desafio X",
        "fonte": "Web (descoberta)",
        "texto_cru": "problema específico do desafio",
    }


def _evidence(*, page_text: str = "problema específico do desafio", related: list[dict] | None = None) -> dict:
    evidence = build_evidence_package({**_opportunity(), "texto_cru": page_text})
    evidence["related_pages"] = related or []
    return evidence


def _capture_materialization(monkeypatch, tmp_path):
    monkeypatch.setattr(materializer, "BRONZE_DIR", tmp_path)
    source_docs_calls: list[tuple] = []
    bundle_calls: list = []
    monkeypatch.setattr(
        "radar.core.kg.source_docs.save",
        lambda *args: source_docs_calls.append(args) or True,
    )
    monkeypatch.setattr(
        materializer.source_bundles,
        "save",
        lambda bundle: bundle_calls.append(bundle) or True,
    )
    return source_docs_calls, bundle_calls


def test_portal_and_challenge_create_complete_bundle_and_projection(monkeypatch, tmp_path):
    source_docs_calls, bundle_calls = _capture_materialization(monkeypatch, tmp_path)
    evidence = _evidence(related=[{
        "status": "loaded",
        "label": "Portal de inovação",
        "url": "https://portal.example/desafios",
        "text": "regras gerais do portal",
    }])

    edital_id = materializer.materialize_approved_evidence(_opportunity(), evidence)

    assert edital_id == "web:" + evidence["identity"]["url_hash"]
    assert len(bundle_calls) == 1
    bundle = bundle_calls[0]
    assert bundle.acquisition_status is AcquisitionStatus.COMPLETE
    assert bundle.subject_id == edital_id
    assert [doc.role for doc in bundle.documents] == [
        DocumentRole.OPPORTUNITY_PAGE, DocumentRole.PROGRAM_PAGE,
    ]
    assert [doc.authority_state for doc in bundle.documents] == [
        AuthorityState.ACTIVE, AuthorityState.CONTEXTUAL,
    ]
    assert len(source_docs_calls) == 1
    assert len(source_docs_calls[0][2]) == 2
    projected_docs = source_docs_calls[0][2]
    assert projected_docs[0]["metadata"]["bundle_hash"] == bundle.compute_bundle_hash()
    assert projected_docs[0]["metadata"]["content_hash"] == bundle.documents[0].content_hash
    assert projected_docs[1]["metadata"]["bundle_hash"] == bundle.compute_bundle_hash()
    assert projected_docs[1]["metadata"]["content_hash"] == bundle.documents[1].content_hash


def test_isolated_challenge_creates_complete_bundle_without_context(monkeypatch, tmp_path):
    source_docs_calls, bundle_calls = _capture_materialization(monkeypatch, tmp_path)

    materializer.materialize_approved_evidence(_opportunity(), _evidence())

    assert len(bundle_calls) == 1
    bundle = bundle_calls[0]
    assert bundle.acquisition_status is AcquisitionStatus.COMPLETE
    assert len(bundle.documents) == 1
    assert bundle.documents[0].role is DocumentRole.OPPORTUNITY_PAGE
    assert len(source_docs_calls[0][2]) == 1


def test_context_without_challenge_page_creates_partial_bundle(monkeypatch, tmp_path):
    source_docs_calls, bundle_calls = _capture_materialization(monkeypatch, tmp_path)
    evidence = _evidence(page_text="")
    evidence["page"] = {"text": "", "status": "empty"}
    evidence["related_pages"] = [{
        "status": "loaded", "url": "https://portal.example/desafios",
        "text": "regras gerais do portal",
    }]

    materializer.materialize_approved_evidence(_opportunity(), evidence)

    assert len(bundle_calls) == 1
    bundle = bundle_calls[0]
    assert bundle.acquisition_status is AcquisitionStatus.PARTIAL
    assert len(bundle.documents) == 1
    assert bundle.documents[0].role is DocumentRole.PROGRAM_PAGE
    assert len(source_docs_calls[0][2]) == 1
    assert "metadata" not in source_docs_calls[0][2][0]


def test_absent_document_does_not_fabricate_bundle(monkeypatch, tmp_path):
    source_docs_calls, bundle_calls = _capture_materialization(monkeypatch, tmp_path)
    evidence = _evidence(page_text="")
    evidence["page"] = {"text": "", "status": "empty"}

    edital_id = materializer.materialize_approved_evidence(_opportunity(), evidence)

    assert edital_id.startswith("web:")
    assert bundle_calls == []
    assert source_docs_calls[0][2] == []
    assert list((tmp_path / "web_raw").glob("*.json"))


def test_bundle_storage_failure_does_not_block_projection(monkeypatch, tmp_path):
    source_docs_calls, _ = _capture_materialization(monkeypatch, tmp_path)
    monkeypatch.setattr(
        materializer.source_bundles,
        "save",
        lambda bundle: (_ for _ in ()).throw(BundleStorageError("storage down")),
    )

    edital_id = materializer.materialize_approved_evidence(
        _opportunity(), _evidence(related=[{"status": "loaded", "text": "contexto"}]),
    )

    assert edital_id.startswith("web:")
    assert len(source_docs_calls) == 1
    assert len(source_docs_calls[0][2]) == 2
    assert all("metadata" not in doc for doc in source_docs_calls[0][2])
    bronze = next((tmp_path / "web_raw").glob("*.json"))
    assert json.loads(bronze.read_text())[0]["url_hash"]


@pytest.mark.parametrize("collected_at", [None, "não-é-um-timestamp"])
def test_missing_or_invalid_collected_at_skips_bundle_and_preserves_projection(
    monkeypatch, tmp_path, caplog, collected_at,
):
    source_docs_calls, bundle_calls = _capture_materialization(monkeypatch, tmp_path)
    evidence = _evidence()
    evidence["identity"]["collected_at"] = collected_at

    materializer.materialize_approved_evidence(_opportunity(), evidence)

    assert bundle_calls == []
    assert len(source_docs_calls) == 1
    assert len(source_docs_calls[0][2]) == 1
    assert "collected_at ausente ou inválido" in caplog.text


def test_bundle_is_saved_before_source_docs_projection(monkeypatch, tmp_path):
    monkeypatch.setattr(materializer, "BRONZE_DIR", tmp_path)
    events: list[str] = []
    monkeypatch.setattr(
        materializer.source_bundles,
        "save",
        lambda bundle: events.append("bundle") or True,
    )
    monkeypatch.setattr(
        "radar.core.kg.source_docs.save",
        lambda *args: events.append("source_docs") or True,
    )

    materializer.materialize_approved_evidence(_opportunity(), _evidence())

    assert events == ["bundle", "source_docs"]


def test_bundle_storage_failure_log_does_not_expose_exception_message(monkeypatch, tmp_path, caplog):
    _capture_materialization(monkeypatch, tmp_path)
    secret = "segredo-nao-pode-aparecer"
    monkeypatch.setattr(
        materializer.source_bundles,
        "save",
        lambda bundle: (_ for _ in ()).throw(BundleStorageError(secret)),
    )

    materializer.materialize_approved_evidence(_opportunity(), _evidence())

    assert "BundleStorageError" in caplog.text
    assert secret not in caplog.text


def test_recollecting_identical_evidence_keeps_bundle_hash_stable(monkeypatch, tmp_path):
    _, bundle_calls = _capture_materialization(monkeypatch, tmp_path)
    evidence = _evidence(related=[{"status": "loaded", "text": "contexto"}])

    materializer.materialize_approved_evidence(_opportunity(), evidence)
    materializer.materialize_approved_evidence(_opportunity(), evidence)

    assert len(bundle_calls) == 2
    assert bundle_calls[0].compute_bundle_hash() == bundle_calls[1].compute_bundle_hash()


def test_materialization_does_not_fetch(monkeypatch, tmp_path):
    _capture_materialization(monkeypatch, tmp_path)
    monkeypatch.setattr(
        "radar.core.web.fetch.fetch_and_parse",
        lambda *args, **kwargs: pytest.fail("materialização não pode fazer fetch"),
    )

    materializer.materialize_approved_evidence(
        _opportunity(), _evidence(related=[{"status": "loaded", "text": "contexto"}]),
    )
