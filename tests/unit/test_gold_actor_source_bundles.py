from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path
from typing import Any

import pytest

from radar.core.kg import gold
from radar.core.kg.equivalence import relation_key
from radar.core.kg.source_bundles import BundleStorageError
from radar.domain.provenance import FactProvenance
from tests.helpers.gold_projection import DEFAULT_FIXTURES_DIR, GoldCaptureHarness

pytestmark = pytest.mark.unit


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _prepare_fixture_dir(
    tmp_path: Path,
    *,
    investors_last_updated: str | None = "2026-06-18",
    programs_last_updated: str | None = "2026-06-18",
    ict_data_extracao: str | None = "2026-06-03",
) -> Path:
    fixture_dir = tmp_path / "gold_fixtures"
    shutil.copytree(DEFAULT_FIXTURES_DIR, fixture_dir)

    investidores_path = fixture_dir / "silver" / "investidores.json"
    investidores = _load_json(investidores_path)
    if investors_last_updated is None:
        investidores.pop("last_updated", None)
    else:
        investidores["last_updated"] = investors_last_updated
    investidores_path.write_text(json.dumps(investidores, ensure_ascii=False, indent=2), encoding="utf-8")

    programas_path = fixture_dir / "silver" / "programas.json"
    programas = _load_json(programas_path)
    if programs_last_updated is None:
        programas.pop("last_updated", None)
    else:
        programas["last_updated"] = programs_last_updated
    programas_path.write_text(json.dumps(programas, ensure_ascii=False, indent=2), encoding="utf-8")

    ict_path = fixture_dir / "bronze" / "ict_raw" / "embrapii_fixture.json"
    icts = _load_json(ict_path)
    icts[0]["data_extracao"] = ict_data_extracao
    ict_path.write_text(json.dumps(icts, ensure_ascii=False, indent=2), encoding="utf-8")

    return fixture_dir


class _BundleCapturingHarness(GoldCaptureHarness):
    def __init__(self) -> None:
        super().__init__()
        self.entity_provenance: dict[str, dict] = {}
        self.relation_provenance: dict[str, dict] = {}

    def stub_upsert_entity(self, cur: Any, **f: Any) -> str:
        synthetic_id = super().stub_upsert_entity(cur, **f)
        key = self._id_to_key[synthetic_id]
        self.entity_provenance[key] = f.get("provenance") or {}
        return synthetic_id

    def stub_upsert_rel(
        self,
        cur: Any,
        source_id: str,
        target_id: str,
        rtype: str,
        properties: dict | None = None,
        provenance: dict | None = None,
    ) -> None:
        super().stub_upsert_rel(cur, source_id, target_id, rtype, properties=properties)
        source_key = self._id_to_key.get(source_id, source_id)
        target_key = self._id_to_key.get(target_id, target_id)
        self.relation_provenance[relation_key(source_key, target_key, rtype)] = provenance or {}


def _run_capture(monkeypatch, fixture_dir: Path, *, saver=None):
    harness = _BundleCapturingHarness()
    harness.apply_patches(monkeypatch, fixture_dir)
    bundle_calls: list = []

    def _save(bundle):
        bundle_calls.append(bundle)
        if saver is not None:
            return saver(bundle)
        return True

    monkeypatch.setattr("radar.core.kg.source_bundles.save", _save)
    stats = gold.ingest_all(skip_unchanged=True)
    return harness, bundle_calls, dict(stats)


class TestActorBundleBuilders:
    def test_build_ict_bundle_uses_official_record(self):
        record = _load_json(
            Path("tests/fixtures/gold_equivalence/bronze/ict_raw/embrapii_fixture.json")
        )[0]
        bundle = gold._build_actor_source_bundle(
            subject_kind="ict",
            subject_id="ict:embrapii:inteligencia-artificial-ceia-ufg",
            source="embrapii",
            collected_at_raw=record["data_extracao"],
            document_name="embrapii_fixture.json",
            document_role="official_record",
            source_url=record["url"],
            record=record,
        )

        assert bundle is not None
        assert bundle.subject_id == "ict:embrapii:inteligencia-artificial-ceia-ufg"
        assert bundle.documents[0].role.value == "official_record"
        assert bundle.documents[0].source_url == record["url"]
        assert bundle.documents[0].units == [json.dumps(record, sort_keys=True, ensure_ascii=False)]
        assert bundle.collected_at.isoformat() == "2026-06-03T00:00:00+00:00"
        assert bundle.acquisition_status.value == "complete"
        assert bundle.compute_bundle_hash() == gold._build_actor_source_bundle(
            subject_kind="ict",
            subject_id="ict:embrapii:inteligencia-artificial-ceia-ufg",
            source="embrapii",
            collected_at_raw=record["data_extracao"],
            document_name="embrapii_fixture.json",
            document_role="official_record",
            source_url=record["url"],
            record=record,
        ).compute_bundle_hash()

    def test_build_curated_investor_bundle_uses_curated_record(self):
        record = _load_json(Path("data/silver/investidores.json"))["investidores"][0]
        bundle = gold._build_actor_source_bundle(
            subject_kind="investor",
            subject_id=record["id"],
            source="curadoria",
            collected_at_raw="2026-06-18",
            document_name="investidores.json",
            document_role="curated_record",
            source_url=record["site"],
            record=record,
        )

        assert bundle is not None
        assert bundle.subject_id == "investidor:indicator-capital"
        assert bundle.documents[0].role.value == "curated_record"
        assert bundle.documents[0].source_url == record["site"]
        assert bundle.acquisition_status.value == "complete"
        assert bundle.collected_at.isoformat() == "2026-06-18T00:00:00+00:00"

    def test_build_curated_program_bundle_uses_curated_record(self):
        record = _load_json(Path("data/silver/programas.json"))["programas"][0]
        bundle = gold._build_actor_source_bundle(
            subject_kind="program",
            subject_id=record["id"],
            source="curadoria",
            collected_at_raw="2026-06-18",
            document_name="programas.json",
            document_role="curated_record",
            source_url=record["site"],
            record=record,
        )

        assert bundle is not None
        assert bundle.subject_id == "programa:centelha"
        assert bundle.documents[0].role.value == "curated_record"
        assert bundle.documents[0].source_url == record["site"]
        assert bundle.acquisition_status.value == "complete"

    def test_incomplete_actor_bundle_stays_partial(self):
        record = {
            "id": "investidor:minimo",
            "name": "Investidor Minimo",
            "site": "https://minimo.example",
            "source_urls": ["https://minimo.example"],
        }
        bundle = gold._build_actor_source_bundle(
            subject_kind="investor",
            subject_id="investidor:minimo",
            source="curadoria",
            collected_at_raw="2026-06-18",
            document_name="investidores.json",
            document_role="curated_record",
            source_url=record["site"],
            record=record,
        )

        assert bundle is not None
        assert bundle.acquisition_status.value == "partial"

    def test_partial_actor_bundle_is_persisted_but_not_used_as_current_lineage(self, monkeypatch, tmp_path):
        fixture_dir = _prepare_fixture_dir(tmp_path)
        investidores_path = fixture_dir / "silver" / "investidores.json"
        investidores = _load_json(investidores_path)
        investidores["investidores"][0] = {
            "id": "investidor:indicator-capital",
            "name": "Indicator Capital",
            "site": "https://indicatorcapital.com.br",
            "source_urls": ["https://indicatorcapital.com.br"],
        }
        investidores_path.write_text(
            json.dumps(investidores, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        harness, bundle_calls, stats = _run_capture(monkeypatch, fixture_dir)

        assert stats["investidor"] == 1
        investor_bundle = next(bundle for bundle in bundle_calls if bundle.subject_id == "investidor:indicator-capital")
        assert investor_bundle.acquisition_status.value == "partial"
        investor_name_ref = FactProvenance.model_validate(
            harness.entity_provenance["investidor|curadoria|investidor:indicator-capital"]["name"]
        ).evidence_refs[0]
        assert investor_name_ref.bundle_hash is None
        assert investor_name_ref.content_hash is None

    def test_date_only_timestamp_normalizes_to_midnight_utc(self):
        bundle = gold._build_actor_source_bundle(
            subject_kind="program",
            subject_id="programa:teste",
            source="curadoria",
            collected_at_raw="2026-06-18",
            document_name="programas.json",
            document_role="curated_record",
            source_url="https://programa.example",
            record={"id": "programa:teste", "name": "Programa Teste", "descricao": "descricao"},
        )
        assert bundle is not None
        assert bundle.collected_at.isoformat() == "2026-06-18T00:00:00+00:00"

    @pytest.mark.parametrize("collected_at_raw", [None, "invalido", "2026-06-18T12:30:00"])
    def test_missing_or_invalid_timestamp_skips_bundle(self, collected_at_raw):
        bundle = gold._build_actor_source_bundle(
            subject_kind="program",
            subject_id="programa:teste",
            source="curadoria",
            collected_at_raw=collected_at_raw,
            document_name="programas.json",
            document_role="curated_record",
            source_url="https://programa.example",
            record={"id": "programa:teste", "name": "Programa Teste", "descricao": "descricao"},
        )
        assert bundle is None


class TestActorBundleIngest:
    def test_ingest_persists_known_actor_bundles(self, monkeypatch, tmp_path):
        fixture_dir = _prepare_fixture_dir(tmp_path)

        harness, bundle_calls, stats = _run_capture(monkeypatch, fixture_dir)

        bundles_by_subject = {bundle.subject_id: bundle for bundle in bundle_calls}
        assert stats["ict"] == 1
        assert stats["investidor"] == 1
        assert stats["programa"] == 1
        assert "ict:embrapii:inteligencia-artificial-ceia-ufg" in bundles_by_subject
        assert "investidor:indicator-capital" in bundles_by_subject
        assert "programa:centelha" in bundles_by_subject
        assert not any(bundle.subject_kind.value == "agency" for bundle in bundle_calls)

        ict_bundle = bundles_by_subject["ict:embrapii:inteligencia-artificial-ceia-ufg"]
        assert ict_bundle.documents[0].role.value == "official_record"
        assert ict_bundle.collected_at.isoformat() == "2026-06-03T00:00:00+00:00"

        investor_bundle = bundles_by_subject["investidor:indicator-capital"]
        assert investor_bundle.documents[0].role.value == "curated_record"
        assert investor_bundle.collected_at.isoformat() == "2026-06-18T00:00:00+00:00"

        program_bundle = bundles_by_subject["programa:centelha"]
        assert program_bundle.documents[0].role.value == "curated_record"
        assert program_bundle.collected_at.isoformat() == "2026-06-18T00:00:00+00:00"

        ict_name_ref = FactProvenance.model_validate(
            harness.entity_provenance["ict|embrapii|embrapii:inteligencia-artificial-ceia-ufg"]["name"]
        ).evidence_refs[0]
        assert ict_name_ref.bundle_hash == ict_bundle.compute_bundle_hash()
        assert ict_name_ref.content_hash == ict_bundle.documents[0].content_hash

        investor_name_ref = FactProvenance.model_validate(
            harness.entity_provenance["investidor|curadoria|investidor:indicator-capital"]["name"]
        ).evidence_refs[0]
        assert investor_name_ref.bundle_hash == investor_bundle.compute_bundle_hash()
        assert investor_name_ref.content_hash == investor_bundle.documents[0].content_hash

        program_name_ref = FactProvenance.model_validate(
            harness.entity_provenance["programa|curadoria|programa:centelha"]["name"]
        ).evidence_refs[0]
        assert program_name_ref.bundle_hash == program_bundle.compute_bundle_hash()
        assert program_name_ref.content_hash == program_bundle.documents[0].content_hash

    def test_agency_bundle_is_not_applicable(self, monkeypatch, tmp_path, caplog):
        fixture_dir = _prepare_fixture_dir(tmp_path)
        caplog.set_level(logging.INFO)

        _, bundle_calls, _ = _run_capture(monkeypatch, fixture_dir)

        assert not any(bundle.subject_kind.value == "agency" for bundle in bundle_calls)
        assert "não aplicável para agência derivada" in caplog.text

    def test_recollect_is_idempotent_for_actor_bundle_hashes(self, monkeypatch, tmp_path):
        fixture_dir = _prepare_fixture_dir(tmp_path)

        _, bundle_calls_1, _ = _run_capture(monkeypatch, fixture_dir)
        _, bundle_calls_2, _ = _run_capture(monkeypatch, fixture_dir)

        hashes_1 = {bundle.subject_id: bundle.compute_bundle_hash() for bundle in bundle_calls_1}
        hashes_2 = {bundle.subject_id: bundle.compute_bundle_hash() for bundle in bundle_calls_2}
        assert hashes_1 == hashes_2

    @pytest.mark.parametrize("last_updated", [None, "nao-e-data"])
    def test_missing_or_invalid_catalog_timestamp_does_not_block_gold(self, monkeypatch, tmp_path, last_updated, caplog):
        fixture_dir = _prepare_fixture_dir(
            tmp_path,
            investors_last_updated=last_updated,
            programs_last_updated=last_updated,
        )

        _, bundle_calls, stats = _run_capture(monkeypatch, fixture_dir)

        assert stats["investidor"] == 1
        assert stats["programa"] == 1
        assert stats["ict"] == 1
        subject_ids = {bundle.subject_id for bundle in bundle_calls}
        assert "ict:embrapii:inteligencia-artificial-ceia-ufg" in subject_ids
        assert "investidor:indicator-capital" not in subject_ids
        assert "programa:centelha" not in subject_ids
        assert "bundle não persistido" in caplog.text

    def test_bundle_storage_failure_is_best_effort_and_sanitized(self, monkeypatch, tmp_path, caplog):
        fixture_dir = _prepare_fixture_dir(tmp_path)
        secret = "segredo-nao-pode-aparecer"

        def _failing_save(bundle):
            raise BundleStorageError(secret)

        harness, _, stats = _run_capture(monkeypatch, fixture_dir, saver=_failing_save)

        assert stats["investidor"] == 1
        assert stats["programa"] == 1
        assert stats["ict"] == 1
        assert harness.projection.entities
        assert "BundleStorageError" in caplog.text
        assert secret not in caplog.text
        investor_name_ref = FactProvenance.model_validate(
            harness.entity_provenance["investidor|curadoria|investidor:indicator-capital"]["name"]
        ).evidence_refs[0]
        assert investor_name_ref.bundle_hash is None
        assert investor_name_ref.content_hash is None

    def test_bundle_validation_failure_is_best_effort_and_keeps_stats(self, monkeypatch, tmp_path, caplog):
        fixture_dir = _prepare_fixture_dir(tmp_path, ict_data_extracao="2026-06-03T12:30:00")

        harness, bundle_calls, stats = _run_capture(monkeypatch, fixture_dir)

        assert stats["investidor"] == 1
        assert stats["programa"] == 1
        assert stats["ict"] == 1
        assert harness.projection.entities
        assert {bundle.subject_id for bundle in bundle_calls} == {
            "investidor:indicator-capital",
            "programa:centelha",
        }
        assert "bundle não persistido" in caplog.text

    def test_existing_provenance_states_remain_equal_when_bundle_path_changes(self, tmp_path):
        fixture_dir_ok = _prepare_fixture_dir(tmp_path / "ok")
        fixture_dir_fail = _prepare_fixture_dir(tmp_path / "fail")

        with pytest.MonkeyPatch.context() as monkeypatch:
            ok_harness, _, _ = _run_capture(monkeypatch, fixture_dir_ok)

        def _failing_save(bundle):
            raise BundleStorageError("segredo")

        with pytest.MonkeyPatch.context() as monkeypatch:
            fail_harness, _, _ = _run_capture(monkeypatch, fixture_dir_fail, saver=_failing_save)

        assert FactProvenance.model_validate(
            ok_harness.entity_provenance["investidor|curadoria|investidor:indicator-capital"]["name"]
        ).state == "unknown"
        assert FactProvenance.model_validate(
            ok_harness.entity_provenance["programa|curadoria|programa:centelha"]["name"]
        ).state == "unknown"
        assert FactProvenance.model_validate(
            ok_harness.entity_provenance["ict|embrapii|embrapii:inteligencia-artificial-ceia-ufg"]["name"]
        ).state == "stated"
        assert FactProvenance.model_validate(
            fail_harness.entity_provenance["investidor|curadoria|investidor:indicator-capital"]["name"]
        ).state == "unknown"
        ok_ict_ref = FactProvenance.model_validate(
            ok_harness.entity_provenance["ict|embrapii|embrapii:inteligencia-artificial-ceia-ufg"]["name"]
        ).evidence_refs[0]
        fail_ict_ref = FactProvenance.model_validate(
            fail_harness.entity_provenance["ict|embrapii|embrapii:inteligencia-artificial-ceia-ufg"]["name"]
        ).evidence_refs[0]
        assert ok_ict_ref.bundle_hash is not None
        assert ok_ict_ref.content_hash is not None
        assert fail_ict_ref.bundle_hash is None
        assert fail_ict_ref.content_hash is None
