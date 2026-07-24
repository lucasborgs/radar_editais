"""RT01-T07 — ICTs EMBRAPII: proveniência do registro versionado do scraper.

Três blocos:

(a) `provenance_writer` isolado (sem gold.py/harness): a tabela de fatos da
    task (spec §3.2/§3.3/§6.4) — estados/produtores corretos, todo
    `FactProvenance` emitido passa `model_validate`; a âncora `document_only`
    do registro (`build_ict_record_anchor`) tem `canonical_content_hash`
    prefixado `md5:` real (hash do JSON do REGISTRO individual, não do
    arquivo inteiro); `build_ict_fact_provenance` omite path cujo campo não
    tem valor no registro (nada de `unknown` artificial).

(b) captura via harness T02 (`tests/helpers/gold_projection.py`) SEM
    modificá-lo — subclasse local (mesmo padrão de T05/T06). Prova: a ICT do
    fixture (`embrapii_fixture.json`, CEIA-UFG) tem provenance nos paths
    esperados; a aresta `credenciada_por` tem provenance `stated` com a
    âncora do registro; editais preservam o comportamento T06; investidor/
    programa/agência seguem vazios até T08.

(c) adversarial + determinismo: nenhum `stated` sem `EvidenceRef` com
    locator `exact`/`document_only`; duas capturas produzem o mesmo
    resultado.

Hermético: sem rede, sem banco (mesmos stubs do harness T02).
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from helpers.gold_projection import DEFAULT_FIXTURES_DIR, GoldCaptureHarness

from radar.core.kg import provenance_writer
from radar.core.kg.equivalence import relation_key
from radar.domain.provenance import FactProvenance

ICT_FIXTURE = DEFAULT_FIXTURES_DIR / "bronze" / "ict_raw" / "embrapii_fixture.json"


def _load_ict_records() -> list[dict]:
    return json.loads(ICT_FIXTURE.read_text(encoding="utf-8"))


def _fixture_record() -> dict:
    return _load_ict_records()[0]


def _expected_hash(record: dict) -> str:
    canonical = json.dumps(record, sort_keys=True, ensure_ascii=False)
    return "md5:" + hashlib.md5(canonical.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# (a) Tabela de fatos — builders isolados
# ---------------------------------------------------------------------------


class TestICTRecordAnchor:
    def test_anchor_is_document_only_with_real_md5_prefix(self):
        record = _fixture_record()
        anchor = provenance_writer.build_ict_record_anchor(
            record=record, document="embrapii_fixture.json",
            source_url=record.get("url"), native_id="embrapii:inteligencia-artificial-ceia-ufg",
        )
        assert anchor.locator_quality == "document_only"
        assert anchor.document == "embrapii_fixture.json"
        assert anchor.source_url == record["url"]
        assert anchor.quote is None
        assert anchor.page is None
        assert anchor.block_idx is None
        assert anchor.section_path == []
        assert anchor.canonical_content_hash == _expected_hash(record)
        assert anchor.canonical_content_hash.startswith("md5:")

    def test_hash_covers_only_the_individual_record_not_the_whole_file(self):
        """Duas ICTs do mesmo arquivo têm hashes DISTINTOS — a âncora nunca
        hasheia o array inteiro do arquivo versionado."""
        rec_a = {"name": "A", "slug": "a", "url": "https://x/a"}
        rec_b = {"name": "B", "slug": "b", "url": "https://x/b"}
        anchor_a = provenance_writer.build_ict_record_anchor(
            record=rec_a, document="embrapii_x.json", source_url=rec_a["url"],
        )
        anchor_b = provenance_writer.build_ict_record_anchor(
            record=rec_b, document="embrapii_x.json", source_url=rec_b["url"],
        )
        assert anchor_a.canonical_content_hash != anchor_b.canonical_content_hash
        assert anchor_a.document == anchor_b.document == "embrapii_x.json"

    def test_hash_is_key_order_independent(self):
        """`json.dumps(..., sort_keys=True)` — ordem das chaves do dict de
        entrada não muda o hash (mesmo registro, ordens distintas)."""
        rec1 = {"name": "A", "slug": "a", "url": "https://x/a"}
        rec2 = {"url": "https://x/a", "slug": "a", "name": "A"}
        h1 = provenance_writer.build_ict_record_anchor(record=rec1, document="d.json", source_url=None)
        h2 = provenance_writer.build_ict_record_anchor(record=rec2, document="d.json", source_url=None)
        assert h1.canonical_content_hash == h2.canonical_content_hash


class TestICTFactTableBuilders:
    def _anchor(self) -> Any:
        record = _fixture_record()
        return provenance_writer.build_ict_record_anchor(
            record=record, document="embrapii_fixture.json", source_url=record.get("url"),
        )

    def test_identity_is_stated_adapter_with_anchor(self):
        anchor = self._anchor()
        fp = provenance_writer.build_ict_identity_provenance(anchor)
        assert fp.state == "stated"
        assert fp.producer.kind == "adapter"
        assert fp.producer.name == "embrapii_scraper"
        assert fp.evidence_refs == [anchor]
        FactProvenance.model_validate(fp.model_dump(mode="json"))

    def test_uf_is_inferred_deterministic_no_refs(self):
        fp = provenance_writer.build_ict_uf_provenance()
        assert fp.state == "inferred"
        assert fp.producer.kind == "deterministic"
        assert fp.producer.name == "_uf_from_address"
        assert fp.derivation.rule == "_uf_from_address:v1"
        assert fp.derivation.inputs == ["record.address"]
        assert fp.evidence_refs == []
        FactProvenance.model_validate(fp.model_dump(mode="json"))

    def test_tags_is_inferred_deterministic_no_refs(self):
        fp = provenance_writer.build_ict_tags_provenance()
        assert fp.state == "inferred"
        assert fp.producer.kind == "deterministic"
        assert fp.producer.name == "normalize_tags"
        assert fp.derivation.rule == "normalize_tags:v1"
        assert fp.derivation.inputs == ["record.areas_raw"]
        assert fp.evidence_refs == []
        FactProvenance.model_validate(fp.model_dump(mode="json"))

    def test_credenciada_por_is_stated_adapter_with_anchor(self):
        anchor = self._anchor()
        fp = provenance_writer.build_ict_credenciada_por_provenance(anchor)
        assert fp.state == "stated"
        assert fp.producer.kind == "adapter"
        assert fp.producer.name == "embrapii_scraper"
        assert fp.evidence_refs == [anchor]
        FactProvenance.model_validate(fp.model_dump(mode="json"))


class TestICTFactProvenanceComposition:
    def test_composes_expected_paths_for_fixture_record(self):
        record = _fixture_record()
        anchor = provenance_writer.build_ict_record_anchor(
            record=record, document="embrapii_fixture.json", source_url=record.get("url"),
        )
        out = provenance_writer.build_ict_fact_provenance(record=record, anchor=anchor, uf="GO")
        assert set(out) == {"name", "metadata.url", "uf", "setores", "tecnologias_tags"}
        for path, payload in out.items():
            fp = FactProvenance.model_validate(payload)
            if path in ("name", "metadata.url"):
                assert fp.state == "stated"
            else:
                assert fp.state == "inferred"

    def test_missing_name_omits_name_path(self):
        record = {"url": "https://x/a", "areas_raw": ["IA"]}
        anchor = provenance_writer.build_ict_record_anchor(record=record, document="d.json", source_url=record["url"])
        out = provenance_writer.build_ict_fact_provenance(record=record, anchor=anchor, uf=None)
        assert "name" not in out

    def test_missing_url_omits_metadata_url_path(self):
        record = {"name": "X", "areas_raw": ["IA"]}
        anchor = provenance_writer.build_ict_record_anchor(record=record, document="d.json", source_url=None)
        out = provenance_writer.build_ict_fact_provenance(record=record, anchor=anchor, uf=None)
        assert "metadata.url" not in out

    def test_uf_none_omits_uf_path(self):
        record = {"name": "X", "url": "https://x/a"}
        anchor = provenance_writer.build_ict_record_anchor(record=record, document="d.json", source_url=record["url"])
        out = provenance_writer.build_ict_fact_provenance(record=record, anchor=anchor, uf=None)
        assert "uf" not in out

    def test_missing_areas_omits_tags_paths(self):
        record = {"name": "X", "url": "https://x/a"}
        anchor = provenance_writer.build_ict_record_anchor(record=record, document="d.json", source_url=record["url"])
        out = provenance_writer.build_ict_fact_provenance(record=record, anchor=anchor, uf=None)
        assert "setores" not in out
        assert "tecnologias_tags" not in out

    def test_no_value_no_entry_never_fabricates_unknown(self):
        """Registro vazio → dict de provenance vazio; nunca `unknown`
        fabricado para preencher paths sem valor (contrato da task)."""
        record = {}
        anchor = provenance_writer.build_ict_record_anchor(record=record, document="d.json", source_url=None)
        out = provenance_writer.build_ict_fact_provenance(record=record, anchor=anchor, uf=None)
        assert out == {}


# ---------------------------------------------------------------------------
# (b) Captura via harness T02 (subclasse LOCAL — gold_projection.py intocado)
# ---------------------------------------------------------------------------


class _ICTProvenanceCapturingHarness(GoldCaptureHarness):
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
        self, cur: Any, source_id: str, target_id: str, rtype: str,
        properties: dict | None = None, provenance: dict | None = None,
    ) -> None:
        super().stub_upsert_rel(cur, source_id, target_id, rtype, properties=properties)
        source_key = self._id_to_key.get(source_id, source_id)
        target_key = self._id_to_key.get(target_id, target_id)
        rk = relation_key(source_key, target_key, rtype)
        self.relation_provenance[rk] = provenance or {}


def _run_capture(monkeypatch):
    from radar.core.kg import gold
    harness = _ICTProvenanceCapturingHarness()
    harness.apply_patches(monkeypatch, DEFAULT_FIXTURES_DIR)
    stats = gold.ingest_all(skip_unchanged=True)
    return harness, dict(stats)


ICT_KEY = "ict|embrapii|embrapii:inteligencia-artificial-ceia-ufg"
AGENCY_KEY = "agencia|curadoria|agencia:embrapii"
CREDENCIADA_POR_RK = f"{ICT_KEY}->{AGENCY_KEY}|credenciada_por"


class TestHarnessCapturedICTProvenance:
    def test_ict_has_expected_provenance_paths(self, monkeypatch):
        harness, _stats = _run_capture(monkeypatch)
        assert ICT_KEY in harness.entity_provenance
        prov = harness.entity_provenance[ICT_KEY]
        assert prov, "ICT deveria ter provenance não vazia"
        assert set(prov) == {"name", "metadata.url", "uf", "setores", "tecnologias_tags"}
        for path, payload in prov.items():
            fp = FactProvenance.model_validate(payload)
            if path in ("name", "metadata.url"):
                assert fp.state == "stated"
            else:
                assert fp.state == "inferred"

    def test_ict_identity_paths_anchor_to_the_record(self, monkeypatch):
        harness, _stats = _run_capture(monkeypatch)
        record = _fixture_record()
        expected_hash = _expected_hash(record)
        for path in ("name", "metadata.url"):
            fp = FactProvenance.model_validate(harness.entity_provenance[ICT_KEY][path])
            assert len(fp.evidence_refs) == 1
            ref = fp.evidence_refs[0]
            assert ref.source == "embrapii"
            assert ref.document == "embrapii_fixture.json"
            assert ref.canonical_content_hash == expected_hash
            assert ref.source_url == record["url"]
            assert ref.locator_quality == "document_only"
            assert ref.quote is None

    def test_credenciada_por_edge_is_stated_with_record_anchor(self, monkeypatch):
        harness, _stats = _run_capture(monkeypatch)
        assert CREDENCIADA_POR_RK in harness.relation_provenance
        prov = harness.relation_provenance[CREDENCIADA_POR_RK]
        assert prov, "aresta credenciada_por deveria ter provenance não vazia"
        fp = FactProvenance.model_validate(prov)
        assert fp.state == "stated"
        assert fp.producer.kind == "adapter"
        assert fp.producer.name == "embrapii_scraper"
        assert len(fp.evidence_refs) == 1
        ref = fp.evidence_refs[0]
        assert ref.locator_quality == "document_only"
        assert ref.canonical_content_hash == _expected_hash(_fixture_record())

    def test_edital_provenance_still_behaves_like_t06(self, monkeypatch):
        """Não-regressão: editais (todas as 4 fontes) continuam com
        provenance não vazia após a mudança de T07."""
        harness, _stats = _run_capture(monkeypatch)
        for key, rec in harness.projection.entities.items():
            if rec["kind"] == "edital":
                assert harness.entity_provenance.get(key), f"{key} deveria ter provenance não vazia"

    def test_investor_programa_agencia_still_empty(self, monkeypatch):
        """investidor/programa/agência seguem sem provenance até T08."""
        harness, _stats = _run_capture(monkeypatch)
        checked = 0
        for key, rec in harness.projection.entities.items():
            if rec["kind"] in ("investidor", "programa", "agencia"):
                checked += 1
                assert harness.entity_provenance.get(key, {}) == {}, f"{key} deveria ter provenance vazia"
        assert checked, "fixture deveria ter investidor/programa/agência"


# ---------------------------------------------------------------------------
# (c) Adversarial + determinismo
# ---------------------------------------------------------------------------


class TestAdversarialAndDeterminism:
    def test_no_stated_without_resolved_evidence_ref(self, monkeypatch):
        harness, _stats = _run_capture(monkeypatch)
        for ek, prov in harness.entity_provenance.items():
            for path, payload in prov.items():
                fp = FactProvenance.model_validate(payload)
                if fp.state == "stated":
                    assert fp.evidence_refs, f"{ek}/{path} stated sem EvidenceRef"
                    for ref in fp.evidence_refs:
                        assert ref.locator_quality in ("exact", "document_only"), \
                            f"{ek}/{path}: stated com locator {ref.locator_quality}"
        for rk, prov in harness.relation_provenance.items():
            if not prov:
                continue
            fp = FactProvenance.model_validate(prov)
            if fp.state == "stated":
                assert fp.evidence_refs, f"{rk} stated sem EvidenceRef"
                for ref in fp.evidence_refs:
                    assert ref.locator_quality in ("exact", "document_only"), \
                        f"{rk}: stated com locator {ref.locator_quality}"

    def test_two_captures_deterministic(self, monkeypatch):
        h1, _ = _run_capture(monkeypatch)
        h2, _ = _run_capture(monkeypatch)
        assert h1.entity_provenance == h2.entity_provenance
        assert h1.relation_provenance == h2.relation_provenance
