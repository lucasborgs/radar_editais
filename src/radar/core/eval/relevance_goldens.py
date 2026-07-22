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

FILE_KEY_TO_KIND = {
    "opportunities": "opportunity",
    "investors": "investor",
    "icts": "ict",
    "programs": "program",
    "agencies": "agency",
}

VALID_ACTOR_SOURCE_KINDS = {"investor", "program", "ict", "agency", "opportunity"}
LEGACY_SOURCE_REFS = {"legacy_triage_case", "curated_record"}

ALLOWED_DECISIONS = {"in_scope", "out_of_scope", "needs_review"}


def validate_verdict(item: dict) -> None:
    kind = item.get("kind", "")
    verdict_cls = KIND_VERDICT_CLASS.get(kind)
    if verdict_cls is None:
        raise ValueError(f"unknown kind: {kind}")
    if kind == "opportunity":
        RelevanceVerdict.model_validate(item["verdict"])
    else:
        actor_verdict_adapter.validate_python(item["verdict"])


class RelevanceGoldenLoader:
    """Loader que valida todos os datasets do seam de goldens de relevância."""

    def __init__(self, golden_dir: Path | None = None):
        self._dir = golden_dir or GOLDEN_DIR
        self._data: dict[str, list[dict]] = {}
        self._manifest: dict[str, Any] = {}
        self._actor_sources: list[dict] = []
        self._errors: list[str] = []

    def _read_json(self, filename: str) -> list[dict] | dict:
        path = self._dir / filename
        if not path.exists():
            raise FileNotFoundError(f"golden dataset not found: {path}")
        return json.loads(path.read_text(encoding="utf-8"))

    def load_all(self) -> dict[str, list[dict]]:
        self._manifest = self._read_json("manifest.json")

        for kind, fname in DATASET_FILES.items():
            raw = self._read_json(fname)
            if not isinstance(raw, list):
                raise ValueError(f"{fname}: expected list, got {type(raw).__name__}")
            self._data[kind] = raw

        try:
            raw = self._read_json("actor_sources.json")
            if isinstance(raw, list):
                self._actor_sources = raw
        except FileNotFoundError:
            self._actor_sources = []

        return self._data

    def validate_all(self) -> list[str]:
        self._errors = []

        if not self._data:
            self._errors.append("no data loaded — call load_all() first")
            return self._errors

        # Build index of actor sources
        source_ids = {s["source_id"] for s in self._actor_sources if "source_id" in s}
        source_by_record: dict[str, list[dict]] = {}
        for s in self._actor_sources:
            rid = s.get("source_record_id", "")
            if rid:
                source_by_record.setdefault(rid, []).append(s)

        all_ids: dict[str, set[str]] = {}
        global_ids: set[str] = set()
        total_unreviewed = 0

        for kind, items in self._data.items():
            ids = set()
            expected_ids = set(self._manifest.get("dataset_ids", {}).get(kind, []))

            for item in items:
                cid = item.get("case_id", "")
                if not cid:
                    self._errors.append(f"{kind}: item without case_id")
                    continue

                # Duplicate within file
                if cid in ids:
                    self._errors.append(f"duplicate case_id in {kind}: {cid}")
                ids.add(cid)

                # Global duplicate
                if cid in global_ids:
                    self._errors.append(f"duplicate case_id across files: {cid}")
                global_ids.add(cid)

                # Required fields
                hr = item.get("human_reviewed")
                if not isinstance(hr, bool):
                    self._errors.append(f"{cid}: human_reviewed must be bool, got {type(hr).__name__}")
                if hr is False:
                    total_unreviewed += 1

                src_ref = item.get("source_ref", "")
                if not src_ref:
                    self._errors.append(f"{cid}: missing source_ref")

                as_of = item.get("as_of", "")
                if not as_of:
                    self._errors.append(f"{cid}: missing as_of date")

                # Kind match
                item_kind = item.get("kind", "")
                expected_kind = FILE_KEY_TO_KIND.get(kind, kind)
                if item_kind != expected_kind:
                    self._errors.append(f"{cid}: kind mismatch — file kind={kind}, item kind={item_kind}")

                # Source reference validation
                if src_ref and src_ref not in LEGACY_SOURCE_REFS and src_ref not in source_ids:
                    self._errors.append(f"{cid}: source_ref '{src_ref}' not found in actor_sources")
                elif src_ref in source_ids:
                    # Verify kind and source_record_id match
                    matching = [s for s in self._actor_sources if s["source_id"] == src_ref]
                    if matching:
                        ms = matching[0]
                        if ms.get("kind") != item_kind:
                            self._errors.append(f"{cid}: source_ref kind mismatch — actor_source kind={ms.get('kind')}, item kind={item_kind}")
                        if ms.get("source_record_id") != cid:
                            self._errors.append(f"{cid}: source_ref record_id mismatch — actor_source record={ms.get('source_record_id')}, item id={cid}")

                    # Check hash is present
                    ms = matching[0] if matching else {}
                    if not ms.get("hash_sha256"):
                        self._errors.append(f"{cid}: source_ref '{src_ref}' has no hash_sha256")
                    if not ms.get("url"):
                        self._errors.append(f"{cid}: source_ref '{src_ref}' has no url")
                    if not ms.get("retrieved_at"):
                        self._errors.append(f"{cid}: source_ref '{src_ref}' has no retrieved_at")
                    if not ms.get("quote"):
                        self._errors.append(f"{cid}: source_ref '{src_ref}' has no quote")

                # Verdict validation
                verdict = item.get("verdict", {})
                decision = verdict.get("decision", "")
                if decision not in ALLOWED_DECISIONS:
                    self._errors.append(f"{cid}: invalid decision '{decision}'")
                    continue

                try:
                    validate_verdict(item)
                except Exception as e:
                    self._errors.append(f"{cid}: verdict validation failed: {e}")

            all_ids[kind] = ids

            # Manifest completeness
            if expected_ids - ids:
                self._errors.append(f"{kind}: manifest expects IDs not in dataset: {expected_ids - ids}")
            if ids - expected_ids:
                self._errors.append(f"{kind}: dataset has IDs not in manifest: {ids - expected_ids}")

        # Global uniqueness count
        all_ids_flat = set()
        for ids in all_ids.values():
            all_ids_flat.update(ids)

        # Compare corpus_stats
        manifest_total = self._manifest.get("corpus_stats", {}).get("total_cases", 0)
        if manifest_total != len(all_ids_flat):
            self._errors.append(f"manifest total_cases={manifest_total} != actual unique cases={len(all_ids_flat)}")

        manifest_by_kind = self._manifest.get("corpus_stats", {}).get("by_kind", {})
        for kind, ids in all_ids.items():
            expected_count = manifest_by_kind.get(kind, 0)
            if expected_count != len(ids):
                self._errors.append(f"manifest by_kind[{kind}]={expected_count} != actual={len(ids)}")

        manifest_by_dec = self._manifest.get("corpus_stats", {}).get("by_decision", {})
        actual_by_dec = self.distribution()
        for dec, count in actual_by_dec.items():
            expected_count = manifest_by_dec.get(dec, 0)
            if expected_count != count:
                self._errors.append(f"manifest by_decision[{dec}]={expected_count} != actual={count}")

        # Review status check
        review_status = self._manifest.get("review_status", "")
        if total_unreviewed > 0 and review_status != "pending_owner":
            self._errors.append(f"review_status must be 'pending_owner' when {total_unreviewed} case(s) have human_reviewed=false")

        return self._errors

    def validate_actor_sources(self) -> list[str]:
        errs: list[str] = []
        if not self._actor_sources:
            errs.append("actor_sources.json not found or not a list")
            return errs

        for s in self._actor_sources:
            sid = s.get("source_id", "?")
            required = ["source_id", "url", "retrieved_at", "quote", "hash_sha256", "kind", "source_record_id"]
            for field in required:
                if not s.get(field):
                    errs.append(f"{sid}: missing required field '{field}'")

            if "hash_sha256" in s and s["hash_sha256"]:
                expected = s["hash_sha256"]
                actual = hashlib.sha256((s.get("quote") or "").encode("utf-8")).hexdigest()
                if expected != actual:
                    errs.append(f"{sid}: hash mismatch — expected {expected}, got {actual}")

            kind = s.get("kind", "")
            if kind and kind not in VALID_ACTOR_SOURCE_KINDS:
                errs.append(f"{sid}: unknown kind '{kind}' in actor_source")

        return errs

    def distribution(self) -> dict[str, int]:
        dist: dict[str, int] = {}
        for _kind, items in self._data.items():
            for item in items:
                d = item.get("verdict", {}).get("decision", "unknown")
                k = item.get("kind", "?")
                key = f"{k}:{d}"
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
