from __future__ import annotations

import json
from pathlib import Path

import pytest

from radar.core import tasks
from radar.core.kg import gold, source_bundles, source_docs
from radar.core.kg.source_bundles import BundleStorageError
from radar.domain.source_bundle import AcquisitionStatus, AuthorityState, DocumentRole
from radar.pipeline.adapters import fapesc

pytestmark = pytest.mark.unit


def _record(*, data_extracao: str | None = "2026-07-19T03:06:41+00:00", normative: list[dict] | None = None) -> dict:
    record = {
        "native_id": "37-2026",
        "url": "https://fapesc.sc.gov.br/chamada-37-2026",
        "texto_cru": "resumo HTML ou texto normativo",
        "content_source": "pdf",
        "documentos_normativos": normative or [],
    }
    if data_extracao is not None:
        record["data_extracao"] = data_extracao
    return record


def _normative() -> list[dict]:
    return [
        {
            "doc_name": "Edital_37_2026.pdf",
            "url": "https://fapesc.sc.gov.br/uploads/edital-37.pdf",
            "family": "edital-base",
            "text": "texto integral do edital-base",
        },
        {
            "doc_name": "Retificacao_01.pdf",
            "url": "https://fapesc.sc.gov.br/uploads/retificacao-01.pdf",
            "family": "emenda",
            "text": "texto integral da retificação",
        },
    ]


def _write_bronze(monkeypatch, tmp_path: Path, record: dict) -> None:
    bronze_dir = tmp_path / "fapesc_raw"
    bronze_dir.mkdir()
    (bronze_dir / "scan.json").write_text(json.dumps([record]), encoding="utf-8")
    monkeypatch.setattr(fapesc, "_BRONZE_DIR", bronze_dir)


def test_base_and_amendment_map_without_invented_links_or_order(monkeypatch, tmp_path):
    _write_bronze(monkeypatch, tmp_path, _record(normative=_normative()))

    bundle = fapesc.build_source_bundle("37-2026")

    assert bundle is not None
    assert bundle.subject_id == "fapesc:37-2026"
    assert bundle.acquisition_status is AcquisitionStatus.COMPLETE
    assert [doc.role for doc in bundle.documents] == [
        DocumentRole.BASE_NOTICE, DocumentRole.AMENDMENT,
    ]
    assert all(doc.authority_state is AuthorityState.ACTIVE for doc in bundle.documents)
    assert all(doc.composition_order is None for doc in bundle.documents)
    assert all(doc.amends_content_hash is None for doc in bundle.documents)
    assert all(doc.source_url for doc in bundle.documents)
    assert all(doc.content_hash.startswith("sha256:") for doc in bundle.documents)


def test_identical_recollection_keeps_bundle_hash(monkeypatch, tmp_path):
    _write_bronze(monkeypatch, tmp_path, _record(normative=_normative()))

    first = fapesc.build_source_bundle("37-2026")
    second = fapesc.build_source_bundle("37-2026")

    assert first is not None and second is not None
    assert first.compute_bundle_hash() == second.compute_bundle_hash()


@pytest.mark.parametrize("timestamp", [None, "", "not-a-timestamp", "2026-07-19 03:06:41"])
def test_missing_or_invalid_collection_timestamp_does_not_create_bundle(monkeypatch, tmp_path, timestamp):
    _write_bronze(monkeypatch, tmp_path, _record(data_extracao=timestamp, normative=_normative()))

    assert fapesc.build_source_bundle("37-2026") is None


def test_html_fallback_does_not_create_normative_bundle(monkeypatch, tmp_path):
    record = _record(normative=[])
    record["content_source"] = "html"
    _write_bronze(monkeypatch, tmp_path, record)

    assert fapesc.build_source_bundle("37-2026") is None


def test_source_bundle_attempt_precedes_source_docs_in_silver_path(monkeypatch):
    events: list[str] = []
    monkeypatch.setattr(gold, "iter_bronze_editais", lambda: [("fapesc", "37-2026")])
    monkeypatch.setattr(
        tasks, "build_or_load_structured_doc", lambda source, native, docs: [{"text": "ok"}],
    )
    monkeypatch.setattr(source_docs, "active_documents", lambda docs: docs)
    monkeypatch.setattr(source_docs, "load", lambda edital_id: [])
    monkeypatch.setattr(
        tasks, "_save_fapesc_bundle_if_available", lambda source, native: events.append("bundle"),
    )
    monkeypatch.setattr(
        source_docs, "save", lambda edital_id, source, docs: events.append("source_docs"),
    )
    monkeypatch.setattr(
        "radar.pipeline.adapters.base.get_adapter", lambda source: type("Adapter", (), {
            "to_documents": lambda self, native: [{"doc_name": "base", "units": ["texto"]}],
        })(),
    )

    assert tasks._build_all_silver() == 1
    assert events == ["bundle", "source_docs"]


def test_bundle_storage_failure_is_sanitized_and_does_not_block_projection(monkeypatch, caplog):
    bundle = object()
    monkeypatch.setattr(
        "radar.pipeline.adapters.fapesc.build_source_bundle", lambda native: bundle,
    )
    monkeypatch.setattr(
        source_bundles, "save",
        lambda value: (_ for _ in ()).throw(BundleStorageError("secret database payload")),
    )

    tasks._save_fapesc_bundle_if_available("fapesc", "37-2026")

    assert "secret database payload" not in caplog.text
    assert "BundleStorageError" in caplog.text


def _patch_persistence_path(monkeypatch, events: list[str]) -> None:
    adapter = type("Adapter", (), {
        "to_documents": lambda self, native: [{"doc_name": "base", "units": ["texto"]}],
    })()
    monkeypatch.setattr(
        tasks, "_save_fapesc_bundle_if_available", lambda source, native: events.append("bundle"),
    )
    monkeypatch.setattr(
        source_docs, "save", lambda edital_id, source, docs: events.append("source_docs"),
    )
    monkeypatch.setattr(source_docs, "active_documents", lambda docs: docs)
    monkeypatch.setattr(source_docs, "load", lambda edital_id: [])
    monkeypatch.setattr(tasks, "get_adapter", lambda source: adapter)
    monkeypatch.setattr(
        "radar.pipeline.adapters.base.get_adapter", lambda source: adapter,
    )


def test_chunking_path_executes_bundle_before_source_docs(monkeypatch):
    events: list[str] = []
    _patch_persistence_path(monkeypatch, events)
    monkeypatch.setattr(tasks, "build_or_load_structured_doc", lambda source, native, docs: [])

    assert tasks._build_chunks_for_edital("fapesc:37-2026") == []
    assert events == ["bundle", "source_docs"]


def test_promoted_ingestion_path_executes_bundle_before_source_docs(monkeypatch):
    import asyncio

    events: list[str] = []
    _patch_persistence_path(monkeypatch, events)
    monkeypatch.setattr(tasks, "build_or_load_structured_doc", lambda source, native, docs: [])
    monkeypatch.setattr(
        "radar.core.services.discovery_promotion.mark_by_edital",
        lambda edital_id, step, status, **kwargs: None,
    )

    asyncio.run(tasks.ingest_promoted_edital_task.func("fapesc:37-2026"))
    assert events == ["bundle", "source_docs"]
