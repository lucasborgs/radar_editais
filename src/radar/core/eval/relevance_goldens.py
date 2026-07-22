"""Loader hermético para os goldens de relevância (RT00-T02).

Carrega os 5 datasets + manifest, valida tipos, unicidade, hashes e distribuição.
Sem rede, banco, LLM ou arquivos não versionados.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from radar.domain.relevance import (
    AgencyVerdict,
    IctVerdict,
    InvestorVerdict,
    ProgramVerdict,
    RelevanceVerdict,
    actor_verdict_adapter,
)

GOLDEN_DIR = Path(__file__).resolve().parents[4] / "data" / "evaluation" / "golden" / "relevance"

DATASET_FILES = {
    "opportunities": "opportunities.json",
    "investors": "investors.json",
    "icts": "icts.json",
    "programs": "programs.json",
    "agencies": "agencies.json",
}

KIND_VERDICT_CLASS = {
    "opportunity": RelevanceVerdict,
    "investor": InvestorVerdict,
    "ict": IctVerdict,
    "program": ProgramVerdict,
    "agency": AgencyVerdict,
}

# Filename key (plural) → item kind (singular)
FILE_KEY_TO_KIND = {
    "opportunities": "opportunity",
    "investors": "investor",
    "icts": "ict",
    "programs": "program",
    "agencies": "agency",
}

VALID_ACTOR_SOURCE_KINDS = {"investor", "program", "ict", "agency", "opportunity"}


def load_json(filename: str) -> list[dict] | dict:
    path = GOLDEN_DIR / filename
    if not path.exists():
        raise FileNotFoundError(f"golden dataset not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def validate_verdict(item: dict) -> None:
    kind = item.get("kind", "")
    verdict_cls = KIND_VERDICT_CLASS.get(kind)
    if verdict_cls is None:
        raise ValueError(f"unknown kind: {kind}")
    if kind == "opportunity":
        RelevanceVerdict.model_validate(item["verdict"])
    else:
        actor_verdict_adapter.validate_python(item["verdict"])


ALLOWED_DECISIONS = {"in_scope", "out_of_scope", "needs_review"}


class RelevanceGoldenLoader:
    """Loader que valida todos os datasets do seam de goldens de relevância."""

    def __init__(self, golden_dir: Path | None = None):
        self._dir = golden_dir or GOLDEN_DIR
        self._data: dict[str, list[dict]] = {}
        self._manifest: dict[str, Any] = {}
        self._errors: list[str] = []

    def load_all(self) -> dict[str, list[dict]]:
        self._manifest = load_json("manifest.json")

        for kind, fname in DATASET_FILES.items():
            raw = load_json(fname)
            if not isinstance(raw, list):
                raise ValueError(f"{fname}: expected list, got {type(raw).__name__}")
            self._data[kind] = raw

        return self._data

    def validate_all(self) -> list[str]:
        self._errors = []

        if not self._data:
            self._errors.append("no data loaded — call load_all() first")
            return self._errors

        all_ids: dict[str, set[str]] = {}
        file_kind_map = FILE_KEY_TO_KIND

        for kind, items in self._data.items():
            ids = set()
            expected_ids = set(self._manifest.get("dataset_ids", {}).get(kind, []))

            for item in items:
                cid = item.get("case_id", "")
                if not cid:
                    self._errors.append(f"{kind}: item without case_id")
                    continue
                if cid in ids:
                    self._errors.append(f"duplicate case_id in {kind}: {cid}")
                ids.add(cid)

                item_kind = item.get("kind", "")
                expected_kind = file_kind_map.get(kind, kind)
                if item_kind != expected_kind:
                    self._errors.append(
                        f"{cid}: kind mismatch — file kind={kind}, item kind={item_kind}"
                    )

                verdict = item.get("verdict", {})
                decision = verdict.get("decision", "")
                if decision not in ALLOWED_DECISIONS:
                    self._errors.append(f"{cid}: invalid decision '{decision}'")
                    continue

                try:
                    validate_verdict(item)
                except Exception as e:
                    self._errors.append(f"{cid}: verdict validation failed: {e}")

                as_of = item.get("as_of", "")
                if not as_of:
                    self._errors.append(f"{cid}: missing as_of date")

            all_ids[kind] = ids

            if expected_ids - ids:
                self._errors.append(
                    f"{kind}: manifest expects IDs not in dataset: {expected_ids - ids}"
                )
            if ids - expected_ids:
                self._errors.append(
                    f"{kind}: dataset has IDs not in manifest: {ids - expected_ids}"
                )

        all_ids_flat = set()
        for ids in all_ids.values():
            all_ids_flat.update(ids)

        total_manifest = self._manifest.get("corpus_stats", {}).get("total_cases", 0)
        if total_manifest != len(all_ids_flat):
            self._errors.append(
                f"manifest total_cases={total_manifest} != actual unique cases={len(all_ids_flat)}"
            )

        return self._errors

    def validate_actor_sources(self) -> list[str]:
        errs: list[str] = []
        try:
            sources = load_json("actor_sources.json")
        except FileNotFoundError:
            errs.append("actor_sources.json not found")
            return errs

        if not isinstance(sources, list):
            errs.append("actor_sources.json: expected list")
            return errs

        for s in sources:
            sid = s.get("source_id", "?")
            if "hash_sha256" in s:
                expected = s["hash_sha256"]
                actual = hashlib.sha256(
                    (s.get("quote") or "").encode("utf-8")
                ).hexdigest()
                if expected != actual:
                    errs.append(
                        f"{sid}: hash mismatch — expected {expected}, got {actual}"
                    )
            kind = s.get("kind", "")
            if kind and kind not in VALID_ACTOR_SOURCE_KINDS:
                errs.append(f"{sid}: unknown kind '{kind}' in actor_source")

        return errs

    def distribution(self) -> dict[str, int]:
        dist: dict[str, int] = {}
        for _kind, items in self._data.items():
            for item in items:
                d = item.get("verdict", {}).get("decision", "unknown")
                key = f"{d}"
                dist[key] = dist.get(key, 0) + 1
        return dist

    @property
    def errors(self) -> list[str]:
        return list(self._errors)

    @property
    def manifest(self) -> dict[str, Any]:
        return dict(self._manifest)

    @property
    def data(self) -> dict[str, list[dict]]:
        return dict(self._data)
