"""Focused tests for the cheap, fail-open ETL input gate."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

import radar.core.ingestion.early_dedup as gate
import radar.core.kg.gold as gold
import radar.core.kg.source_docs as source_docs
import radar.core.tasks as tasks
import radar.pipeline.adapters.base as adapters_base

pytestmark = pytest.mark.unit


def _write_records(root: Path, source: str, records: list[dict]) -> None:
    folder = root / f"{source}_raw"
    folder.mkdir(parents=True)
    (folder / "snapshot.json").write_text(json.dumps(records), encoding="utf-8")


def _write_silver(root: Path, source: str, native: str, *, fingerprint: str,
                  blocks: list[dict] | None = None) -> None:
    folder = root / "structured_docs" / source
    folder.mkdir(parents=True)
    values = blocks or [{"text": "ok"}]
    (folder / f"{native}.jsonl").write_text(
        "\n".join(json.dumps(value) for value in values), encoding="utf-8",
    )
    (folder / f"{native}.meta.json").write_text(json.dumps({
        "early_input_fingerprint": fingerprint,
        "early_fingerprint_version": gate.FINGERPRINT_VERSION,
        "source_hash": "canonical-hash",
        "n_blocks": len(values),
    }), encoding="utf-8")


def test_same_input_ignores_order_and_mtime_but_bundle_changes_invalidate(monkeypatch, tmp_path):
    bronze = tmp_path / "bronze"
    pdfs = tmp_path / "pdfs"
    _write_records(bronze, "finep", [{
        "chamada_id": "612",
        "titulo": "A",
        "coletado_em": "2026-01-01T00:00:00Z",
        "pdf_urls": ["https://example/a.pdf", "https://example/b.pdf"],
    }])
    pdf_dir = pdfs / "612"
    pdf_dir.mkdir(parents=True)
    (pdf_dir / "a.pdf").write_bytes(b"a")
    (pdf_dir / "b.pdf").write_bytes(b"b")
    monkeypatch.setattr(gate, "BRONZE_DIR", bronze)
    monkeypatch.setattr(gate, "FINEP_PDFS_DIR", pdfs)
    monkeypatch.setattr(gate, "_producer_material", lambda source: {"v": 1})

    first = gate.input_fingerprint("finep", "612")
    os.utime(bronze / "finep_raw" / "snapshot.json", (1, 1))
    (bronze / "finep_raw" / "snapshot.json").write_text(json.dumps([{
        "pdf_urls": ["https://example/b.pdf", "https://example/a.pdf"],
        "coletado_em": "2027-01-01T00:00:00Z",
        "titulo": "A", "chamada_id": "612",
    }]), encoding="utf-8")
    second = gate.input_fingerprint("finep", "612")
    assert first == second

    (pdf_dir / "c.pdf").write_bytes(b"c")
    assert gate.input_fingerprint("finep", "612") != second


def test_producer_version_is_part_of_fingerprint(monkeypatch, tmp_path):
    bronze = tmp_path / "bronze"
    _write_records(bronze, "web", [{"url_hash": "abc", "title": "A"}])
    monkeypatch.setattr(gate, "BRONZE_DIR", bronze)
    producer = {"v": 1}
    monkeypatch.setattr(gate, "_producer_material", lambda source: producer)
    first = gate.input_fingerprint("web", "abc")
    producer["v"] = 2
    assert gate.input_fingerprint("web", "abc") != first


def test_missing_or_corrupt_silver_fails_open(monkeypatch, tmp_path):
    monkeypatch.setattr(gate, "SILVER_DIR", tmp_path)
    assert not gate.can_skip_silver("web", "abc", "fp")
    _write_silver(tmp_path, "web", "abc", fingerprint="fp")
    assert gate.can_skip_silver("web", "abc", "fp")
    (tmp_path / "structured_docs" / "web" / "abc.jsonl").write_text("{bad", encoding="utf-8")
    assert not gate.can_skip_silver("web", "abc", "fp")


def test_gate_calls_expensive_path_only_for_changed_edital(monkeypatch):
    monkeypatch.setattr(gold, "iter_bronze_editais", lambda: [("web", "same"), ("web", "changed")])
    monkeypatch.setattr(gate, "input_fingerprint", lambda source, native: native)
    monkeypatch.setattr(gate, "can_skip_silver", lambda source, native, fingerprint: native == "same")
    monkeypatch.setattr(adapters_base, "get_adapter", lambda source: type(
        "Adapter", (), {"to_documents": lambda self, native: [{"doc_name": native, "units": ["x"]}]},
    )())
    monkeypatch.setattr(source_docs, "active_documents", lambda docs: docs)
    monkeypatch.setattr(source_docs, "save", lambda *args: None)
    monkeypatch.setattr(source_docs, "load", lambda *args: None)
    monkeypatch.setattr(tasks, "build_or_load_structured_doc", lambda *args, **kwargs: [{"text": "ok"}])
    persist_calls: list[str] = []
    monkeypatch.setattr(gate, "persist_fingerprint", lambda source, native, fp: persist_calls.append(native) or True)
    adapter_calls: list[str] = []
    monkeypatch.setattr(adapters_base, "get_adapter", lambda source: type(
        "Adapter", (), {"to_documents": lambda self, native: adapter_calls.append(native) or [{"doc_name": native, "units": ["x"]}]},
    )())

    result = tasks._build_all_silver()

    assert adapter_calls == ["changed"]
    assert result["changed_ids"] == ["web:changed"]
    assert result["unchanged"] == 1
    assert result["silver_skipped"] == 1
    assert persist_calls == ["changed"]


def test_failed_build_does_not_persist_new_fingerprint(monkeypatch):
    monkeypatch.setattr(gold, "iter_bronze_editais", lambda: [("web", "abc")])
    monkeypatch.setattr(gate, "input_fingerprint", lambda source, native: "new")
    monkeypatch.setattr(gate, "can_skip_silver", lambda *args: False)
    monkeypatch.setattr(adapters_base, "get_adapter", lambda source: type(
        "Adapter", (), {"to_documents": lambda self, native: (_ for _ in ()).throw(RuntimeError("boom"))},
    )())
    persist = []
    monkeypatch.setattr(gate, "persist_fingerprint", lambda *args: persist.append(args) or True)

    result = tasks._build_all_silver()

    assert persist == []
    assert result["step_errors"] == 1


def test_atomic_fingerprint_write_remains_valid_under_concurrent_writers(monkeypatch, tmp_path):
    monkeypatch.setattr(gate, "SILVER_DIR", tmp_path)
    _write_silver(tmp_path, "web", "abc", fingerprint="old")

    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert all(pool.map(lambda n: gate.persist_fingerprint("web", "abc", n), [f"fp-{i}" for i in range(20)]))

    meta = json.loads((tmp_path / "structured_docs" / "web" / "abc.meta.json").read_text())
    assert meta["early_input_fingerprint"].startswith("fp-")
    assert gate.can_skip_silver("web", "abc", meta["early_input_fingerprint"])


def test_finep_612_canary_second_pass_has_zero_expensive_work(monkeypatch):
    """Synthetic canary: one entity and its 34 chunks are not duplicated."""
    monkeypatch.setattr(gold, "iter_bronze_editais", lambda: [("finep", "612")])
    monkeypatch.setattr(gate, "input_fingerprint", lambda source, native: "finep-612-fp")
    gate_open = {"skip": False}
    monkeypatch.setattr(
        gate, "can_skip_silver", lambda source, native, fingerprint: gate_open["skip"],
    )
    monkeypatch.setattr(
        gate, "persist_fingerprint", lambda *args: gate_open.__setitem__("skip", True) or True,
    )
    calls = {"adapter": 0, "structurer": 0, "llm": 0, "embeddings": 0}

    class Adapter:
        def to_documents(self, native):
            calls["adapter"] += 1
            return [{"doc_name": "edital.pdf", "units": ["texto"]}]

    monkeypatch.setattr(adapters_base, "get_adapter", lambda source: Adapter())
    monkeypatch.setattr(source_docs, "active_documents", lambda docs: docs)
    monkeypatch.setattr(source_docs, "save", lambda *args: None)
    monkeypatch.setattr(source_docs, "load", lambda *args: None)
    monkeypatch.setattr(
        tasks, "build_or_load_structured_doc",
        lambda *args, **kwargs: calls.__setitem__("structurer", calls["structurer"] + 1)
        or [{"text": f"chunk-{i}"} for i in range(34)],
    )

    first = tasks._build_all_silver()
    second = tasks._build_all_silver()

    assert first["silver_built"] == 1
    assert second["unchanged"] == 1
    assert second["silver_skipped"] == 1
    assert calls == {"adapter": 1, "structurer": 1, "llm": 0, "embeddings": 0}
    assert first["changed_ids"] == ["finep:612"]
    assert second["changed_ids"] == []
