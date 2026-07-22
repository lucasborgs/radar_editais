"""Loader hermético para os goldens de relevância (RT00-T02).

Carrega os 5 datasets + manifest, valida tipos, unicidade, hashes e distribuição.
Sem rede, banco, LLM ou arquivos não versionados.
"""
from __future__ import annotations

import hashlib
import json
import re
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
SILVER_DIR = GOLDEN_DIR.parents[2] / "silver"

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

# Map dataset kind → silver catalog file + key path
SILVER_CATALOGS: dict[str, tuple[str, str]] = {
    "investor": ("investidores.json", "investidores"),
    "program": ("programas.json", "programas"),
}


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", str(s)).strip()


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
        self._triage: list[dict] = []
        self._silver_catalogs: dict[str, set[str]] = {}
        self._errors: list[str] = []

    def _read_json(self, filename: str) -> list[dict] | dict:
        path = self._dir / filename
        if not path.exists():
            raise FileNotFoundError(f"golden dataset not found: {path}")
        return json.loads(path.read_text(encoding="utf-8"))

    def _load_silver_ids(self, kind: str) -> set[str]:
        """Load record IDs from a silver catalog for curated_record validation."""
        if kind in self._silver_catalogs:
            return self._silver_catalogs[kind]
        catalog_info = SILVER_CATALOGS.get(kind)
        if not catalog_info:
            self._silver_catalogs[kind] = set()
            return set()
        fname, key = catalog_info
        path = SILVER_DIR / fname
        if not path.exists():
            self._silver_catalogs[kind] = set()
            return set()
        raw = json.loads(path.read_text(encoding="utf-8"))
        items = raw.get(key, raw) if isinstance(raw, dict) else raw
        ids = set()
        for item in items:
            if isinstance(item, dict):
                iid = item.get("id") or item.get("case_id")
                if iid:
                    ids.add(str(iid))
        self._silver_catalogs[kind] = ids
        return ids

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

        triage_path = self._dir.parent / "triage.json"
        if triage_path.exists():
            raw = json.loads(triage_path.read_text(encoding="utf-8"))
            if isinstance(raw, list):
                self._triage = raw

        return self._data

    def validate_all(self) -> list[str]:
        self._errors = []

        if not self._data:
            self._errors.append("no data loaded — call load_all() first")
            return self._errors

        source_ids: set[str] = set()
        source_by_id: dict[str, dict] = {}
        source_by_record: dict[str, list[dict]] = {}
        dup_source_ids: set[str] = set()

        for s in self._actor_sources:
            sid = s.get("source_id", "")
            if not sid:
                continue
            if sid in source_ids:
                dup_source_ids.add(sid)
            source_ids.add(sid)
            source_by_id[sid] = s
            rid = s.get("source_record_id", "")
            if rid:
                source_by_record.setdefault(rid, []).append(s)

        for sid in sorted(dup_source_ids):
            self._errors.append(f"duplicate source_id in actor_sources: {sid}")

        # Build set of all case_ids referenced in datasets
        all_dataset_ids: set[str] = set()

        all_ids: dict[str, set[str]] = {}
        global_ids: set[str] = set()
        total_unreviewed = 0

        triage_map: dict[str, dict] = {t["case_id"]: t for t in self._triage if "case_id" in t}

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

                if cid in global_ids:
                    self._errors.append(f"duplicate case_id across files: {cid}")
                global_ids.add(cid)
                all_dataset_ids.add(cid)

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

                item_kind = item.get("kind", "")
                expected_kind = FILE_KEY_TO_KIND.get(kind, kind)
                if item_kind != expected_kind:
                    self._errors.append(f"{cid}: kind mismatch — file kind={kind}, item kind={item_kind}")

                if src_ref and src_ref not in LEGACY_SOURCE_REFS and src_ref not in source_ids:
                    self._errors.append(f"{cid}: source_ref '{src_ref}' not found in actor_sources")
                elif src_ref in source_ids:
                    matching = source_by_id.get(src_ref)
                    if matching:
                        if matching.get("kind") != item_kind:
                            self._errors.append(f"{cid}: source_ref kind mismatch — actor_source kind={matching.get('kind')}, item kind={item_kind}")
                        if matching.get("source_record_id") != cid:
                            self._errors.append(f"{cid}: source_ref record_id mismatch — actor_source record={matching.get('source_record_id')}, item id={cid}")

                        if not matching.get("hash_sha256"):
                            self._errors.append(f"{cid}: source_ref '{src_ref}' has no hash_sha256")
                        if not matching.get("url"):
                            self._errors.append(f"{cid}: source_ref '{src_ref}' has no url")
                        if not matching.get("retrieved_at"):
                            self._errors.append(f"{cid}: source_ref '{src_ref}' has no retrieved_at")
                        if not matching.get("quote"):
                            self._errors.append(f"{cid}: source_ref '{src_ref}' has no quote")

                    # Evidence quote integrity for src:*
                    src_quote_raw = matching.get("quote", "") if matching else ""
                    if src_quote_raw:
                        srcq = _norm(src_quote_raw)
                        for ev in item.get("verdict", {}).get("evidence", []):
                            eq = _norm(ev.get("quote", ""))
                            if eq and eq not in srcq:
                                self._errors.append(f"{cid}/{ev.get('code','?')}: evidence quote not in source snapshot")

                elif src_ref == "legacy_triage_case":
                    sid = item.get("source_record_id", "")
                    if sid not in triage_map:
                        self._errors.append(f"{cid}: source_record_id '{sid}' not found in triage.json")
                    else:
                        t_entry = triage_map[sid]
                        body = _norm(f"{t_entry.get('title','')} {t_entry.get('snippet','')} {t_entry.get('content','')}")
                        for ev in item.get("verdict", {}).get("evidence", []):
                            eq = _norm(ev.get("quote", ""))
                            if eq and eq not in body:
                                self._errors.append(f"{cid}/{ev.get('code','?')}: evidence quote not found in triage entry body")

                elif src_ref == "curated_record":
                    sid = item.get("source_record_id", "")
                    silver_ids = self._load_silver_ids(item_kind)
                    if sid and silver_ids and sid not in silver_ids:
                        self._errors.append(f"{cid}: source_record_id '{sid}' not found in {item_kind} silver catalog")

                verdict = item.get("verdict", {})
                decision = verdict.get("decision", "")
                if decision not in ALLOWED_DECISIONS:
                    self._errors.append(f"{cid}: invalid decision '{decision}'")
                    continue

                try:
                    validate_verdict(item)
                except Exception as e:
                    self._errors.append(f"{cid}: verdict validation failed: {e}")

                # Every reason_code must have evidence with the same code
                evidence_codes = {ev.get("code") for ev in verdict.get("evidence", []) if ev.get("code")}
                for rc in verdict.get("reason_codes", []):
                    if rc not in evidence_codes:
                        self._errors.append(f"{cid}: reason_code '{rc}' has no matching evidence entry")

                # If missing_information starts with a known reason code,
                # that code must NOT also appear in reason_codes
                known_rc_prefixes = set()
                for rc_list in (verdict.get("reason_codes", []), verdict.get("exclusion_codes", [])):
                    for rc in rc_list:
                        known_rc_prefixes.add(rc.split("_")[0] if "_" in rc else rc)
                for mi in verdict.get("missing_information", []):
                    mi_code = mi.split(":")[0].strip() if ":" in mi else ""
                    if mi_code:
                        if mi_code in verdict.get("reason_codes", []):
                            self._errors.append(f"{cid}: reason_code '{mi_code}' also present in missing_information")
                        if mi_code in verdict.get("exclusion_codes", []):
                            self._errors.append(f"{cid}: exclusion_code '{mi_code}' also present in missing_information")

            all_ids[kind] = ids

            if expected_ids - ids:
                self._errors.append(f"{kind}: manifest expects IDs not in dataset: {expected_ids - ids}")
            if ids - expected_ids:
                self._errors.append(f"{kind}: dataset has IDs not in manifest: {ids - expected_ids}")

        # Orphaned actor_sources detection
        orphaned_sources = set()
        for s in self._actor_sources:
            rid = s.get("source_record_id", "")
            sid = s.get("source_id", "")
            if rid and rid not in all_dataset_ids:
                orphaned_sources.add((sid, rid))
        if orphaned_sources:
            manifest_justified = set(self._manifest.get("orphaned_sources_justified", []))
            for sid, rid in sorted(orphaned_sources):
                if sid not in manifest_justified:
                    self._errors.append(f"orphaned actor_source '{sid}' (record_id={rid}) not in any dataset and not justified in manifest")

        all_ids_flat = set()
        for ids in all_ids.values():
            all_ids_flat.update(ids)

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

        review_status = self._manifest.get("review_status", "")
        if total_unreviewed > 0 and review_status != "pending_owner":
            self._errors.append(f"review_status must be 'pending_owner' when {total_unreviewed} case(s) have human_reviewed=false")

        return self._errors

    def validate_actor_sources(self) -> list[str]:
        errs: list[str] = []
        if not self._actor_sources:
            errs.append("actor_sources.json not found or not a list")
            return errs

        seen_ids: set[str] = set()

        for s in self._actor_sources:
            sid = s.get("source_id", "?")
            required = ["source_id", "url", "retrieved_at", "quote", "hash_sha256", "kind", "source_record_id"]
            for field in required:
                if not s.get(field):
                    errs.append(f"{sid}: missing required field '{field}'")

            if sid != "?" and sid in seen_ids:
                errs.append(f"{sid}: duplicate source_id")
            seen_ids.add(sid)

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
