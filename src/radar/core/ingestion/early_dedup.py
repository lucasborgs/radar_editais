"""Cheap, fail-open input gate for the daily edital ETL.

The gate fingerprints the raw artifacts that can affect one edital before the
source adapter is called.  It deliberately does not open PDFs, clean HTML, or
build a CanonicalDoc; those remain the changed-input path.
"""
from __future__ import annotations

import hashlib
import importlib
import json
import logging
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from radar.core.config import BRONZE_DIR, FINEP_PDFS_DIR, SILVER_DIR
from radar.core.kg import schema

logger = logging.getLogger(__name__)

FINGERPRINT_VERSION = "early-input-fingerprint-v1"
_VOLATILE_KEYS = {
    "collected_at", "coletado_em", "data_extracao", "discovered_at",
    "downloaded_at", "fetched_at", "mtime", "scraped_at", "scrape_at",
    "timestamp", "updated_at",
}
_URL_ID_RE = re.compile(r"/(\d+)(?:/|$)")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _stable(value: Any, *, key: str | None = None) -> Any:
    """Normalize JSON values while excluding collection-only volatility."""
    if isinstance(value, dict):
        return {
            str(k): _stable(v, key=str(k))
            for k, v in sorted(value.items(), key=lambda item: str(item[0]))
            if str(k).lower() not in _VOLATILE_KEYS
        }
    if isinstance(value, list):
        normalized = [_stable(v) for v in value]
        return sorted(normalized, key=lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True))
    return value


def _json_files(source: str) -> list[Path]:
    return sorted((BRONZE_DIR / f"{source}_raw").glob("*.json"))


def _records(source: str) -> list[dict]:
    files = _json_files(source)
    if not files:
        return []
    if source != "web":
        try:
            value = json.loads(files[-1].read_text(encoding="utf-8"))
            return value if isinstance(value, list) else []
        except Exception:
            return []

    # Web bronze is additive; later files replace the same url_hash.
    by_hash: dict[str, dict] = {}
    for path in files:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            for item in value if isinstance(value, list) else []:
                if item.get("url_hash"):
                    by_hash[item["url_hash"]] = item
        except Exception:
            continue
    return list(by_hash.values())


def _native(source: str, record: dict) -> str | None:
    if source == "finep":
        value = record.get("chamada_id")
        return str(value) if value is not None else None
    if source == "fapesc":
        return record.get("native_id") or None
    if source == "web":
        return record.get("url_hash") or None
    if source == "fapesp":
        match = _URL_ID_RE.search((record.get("url") or "").rstrip("/"))
        return match.group(1) if match else None
    return None


def _record_for(source: str, native_id: str) -> dict | None:
    seen: set[str] = set()
    for record in _records(source):
        if source in {"fapesp", "fapesc"}:
            url = (record.get("url") or "").replace("http://", "https://").rstrip("/")
            if url and url in seen:
                continue
            if url:
                seen.add(url)
        if _native(source, record) == native_id:
            return record
    return None


def _producer_material(source: str) -> dict[str, Any]:
    """Hash code/config that changes the meaning of the input."""
    registry_entry = schema.source_adapters().get(source) or {}
    module = importlib.import_module(registry_entry["module"])
    paths = [Path(module.__file__)]
    base_module = importlib.import_module("radar.pipeline.adapters.base")
    paths.append(Path(base_module.__file__))
    structurer_module = importlib.import_module("radar.core.ingestion.structurer")
    paths.append(Path(structurer_module.__file__))
    code_hashes = {}
    for path in sorted(set(paths)):
        code_hashes[str(path.name)] = _sha256_bytes(path.read_bytes())
    return {
        "fingerprint_version": FINGERPRINT_VERSION,
        "code": code_hashes,
        "structured_doc_schema": _stable(schema.structured_doc_schema()),
        "structurer_params": _stable(schema.structurer_params()),
        "source_adapter": _stable(registry_entry),
    }


def _finep_artifacts(native_id: str) -> list[dict[str, Any]]:
    from radar.pipeline.adapters import finep

    pdf_dir = FINEP_PDFS_DIR / native_id
    candidates = [
        path for path in sorted(pdf_dir.glob("*.pdf"))
        if not any(finep._fold(keyword) in finep._fold(path.stem) for keyword in finep._skip_keywords())
    ]
    artifacts = []
    for path, metadata in finep._versioned_documents(candidates):
        artifacts.append({
            "name": path.name,
            "metadata": _stable(metadata),
            "content_sha256": _sha256_bytes(path.read_bytes()),
        })
    return artifacts


def input_fingerprint(source: str, native_id: str) -> str | None:
    """Return the deterministic raw-input fingerprint, or None to fail open."""
    try:
        record = _record_for(source, native_id)
        if record is None:
            return None
        payload: dict[str, Any] = {
            "source": source,
            "native_id": native_id,
            "producer": _producer_material(source),
            "record": _stable(record),
        }
        if source == "finep":
            payload["documents"] = _finep_artifacts(native_id)
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        return _sha256_bytes(encoded)
    except Exception as exc:  # fail-open: uncertainty must rebuild safely
        logger.warning("early dedup: fingerprint indisponível para %s:%s (%s)", source, native_id, exc)
        return None


def _silver_paths(source: str, native_id: str) -> tuple[Path, Path]:
    base = SILVER_DIR / "structured_docs" / source
    return base / f"{native_id}.jsonl", base / f"{native_id}.meta.json"


def _read_jsonl(path: Path) -> list[dict] | None:
    try:
        lines = [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        values = [json.loads(line) for line in lines]
        return values if all(isinstance(value, dict) for value in values) else None
    except Exception:
        return None


def can_skip_silver(source: str, native_id: str, fingerprint: str) -> bool:
    """Check sidecar + JSONL integrity before taking the early exit."""
    jsonl_path, meta_path = _silver_paths(source, native_id)
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        blocks = _read_jsonl(jsonl_path)
        if blocks is None:
            return False
        return (
            meta.get("early_input_fingerprint") == fingerprint
            and meta.get("early_fingerprint_version") == FINGERPRINT_VERSION
            and meta.get("source_hash")
            and meta.get("n_blocks") == len(blocks)
        )
    except Exception:
        return False


def persist_fingerprint(source: str, native_id: str, fingerprint: str) -> bool:
    """Atomically add the fingerprint to an already successful sidecar."""
    _, meta_path = _silver_paths(source, native_id)
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta["early_input_fingerprint"] = fingerprint
        meta["early_fingerprint_version"] = FINGERPRINT_VERSION
        meta_path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=f".{meta_path.name}.", suffix=".tmp", dir=meta_path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(meta, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, meta_path)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)
        return True
    except Exception as exc:
        logger.warning("early dedup: fingerprint não persistido para %s:%s (%s)", source, native_id, exc)
        return False
