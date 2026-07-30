"""Comparação reproduzível de snapshots do catálogo e índices derivados."""
from __future__ import annotations

import hashlib
import json
from typing import Any

TABLES = ("entities", "entity_relationships", "match_chunks", "edital_chunks", "discovered_opportunities")


def canonical_hash(rows: list[dict[str, Any]]) -> str:
    payload = json.dumps(rows, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def compare_snapshots(before: dict[str, list[dict]], after: dict[str, list[dict]]) -> dict:
    result = {}
    for table in TABLES:
        left, right = before.get(table, []), after.get(table, [])
        result[table] = {"before": len(left), "after": len(right), "equal": canonical_hash(left) == canonical_hash(right)}
    return result
