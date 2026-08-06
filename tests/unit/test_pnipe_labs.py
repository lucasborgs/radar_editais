"""PNIPE labs como capacidades (spec docs/specs/ict-pnipe-capabilities.md).

Cobre:
(a) contrato/normalizador — `parse_pnipe_record` mapeia o dump curado para o
    contrato bronze (docs/domain/sources/pnipe.md); `PnipeScraper.extract` não
    toca a rede (sem dump → []) e normaliza o dump quando presente; `_save`
    grava bronze `pnipe_*.json`.
(b) proveniência — âncora `document_only` com `source='pnipe'`; tabela de
    fatos dos paths de capacidade (stated/adapter `pnipe_lab_index`, data de
    verificação); regressão EMBRAPII intacta (paths inalterados).
(c) ingest gold via harness T02 — labs materializam `entities(kind=ict)`
    `source='pnipe'` com `metadata.capacidades` e `verificado_em`, SEM aresta
    `credenciada_por` (não há credenciamento EMBRAPII), SEM editais.
(d) consumidores — `_ict_capacity_payload` do catálogo; `find_ict_partners`
    expõe capacidades/url no caminho; `build_explanation` confirma capacidades
    declaradas sem claim de disponibilidade.

Hermético: sem rede, sem banco (mesmos stubs do harness T02).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from radar.core.kg import provenance_writer
from radar.core.services import domain_paths, match_v3
from radar.domain.provenance import FactProvenance
from radar.pipeline.extractors.pnipe import PnipeScraper, parse_pnipe_record
from tests.helpers.gold_projection import GoldCaptureHarness, run_capture

PNIPE_FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "pnipe_gold"
BRONZE_FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "pnipe_gold" / "bronze" / "ict_raw" / "pnipe_fixture.json"

PNIPE_RECORD_KEY = "ict|pnipe|pnipe:lab-ia-robotica"


def _pnipe_records() -> list[dict]:
    return json.loads(BRONZE_FIXTURE.read_text(encoding="utf-8"))


def _record() -> dict:
    return _pnipe_records()[0]


# ---------------------------------------------------------------------------
# (a) Contrato do normalizador
# ---------------------------------------------------------------------------


class TestParsePnipeRecord:
    def test_maps_raw_dump_to_bronze_contract(self):
        out = parse_pnipe_record({
            "nome": "Lab IA",
            "url": "https://pnipe.mcti.gov.br/laboratorio/lab-ia",
            "verificado_em": "2026-08-04",
            "instituicao": "UFSC",
            "tipo_instituicao": "Universidade Federal",
            "endereco": "Rua A",
            "municipio": "Florianópolis",
            "uf": "SC",
            "competencias": ["Visão computacional"],
            "equipamentos": ["Cluster GPU"],
            "condicoes_acesso": "mediante proposta",
            "contato_email": "lab@ufsc.br",
            "contato_site": "https://lab.ufsc.br",
            "areas": ["Inteligência artificial"],
        })
        assert out["name"] == "Lab IA"
        assert out["slug"] == "lab-ia"
        assert out["source"] == "pnipe"
        assert out["kind"] == "laboratorio"
        assert out["institution"] == "UFSC"
        assert out["municipio"] == "Florianópolis"
        assert out["address"].endswith(", Florianópolis, SC")
        assert out["competencias"] == ["Visão computacional"]
        assert out["equipamentos"] == ["Cluster GPU"]
        assert out["condicoes_acesso"] == "mediante proposta"
        assert out["contact"]["email"] == "lab@ufsc.br"
        assert out["areas_raw"] == ["Inteligência artificial"]
        assert out["data_extracao"] == "2026-08-04"

    def test_slug_derived_when_absent(self):
        out = parse_pnipe_record({
            "name": "Laboratório de Materiais Avançados",
            "url": "https://pnipe.mcti.gov.br/laboratorio/x",
            "verificado_em": "2026-08-04",
        })
        assert out["slug"] == "laboratorio-de-materiais-avancados"

    def test_missing_fields_default_to_empty_lists(self):
        out = parse_pnipe_record({
            "name": "Lab", "url": "https://x/lab", "verificado_em": "2026-08-04",
        })
        assert out["competencias"] == []
        assert out["equipamentos"] == []
        assert out["areas_raw"] == []
        assert out["condicoes_acesso"] == ""
        assert out["municipio"] == ""

    def test_requires_name_url_and_verification_date(self):
        with pytest.raises(ValueError):
            parse_pnipe_record({"url": "https://x", "verificado_em": "2026-08-04"})
        with pytest.raises(ValueError):
            parse_pnipe_record({"name": "Lab", "verificado_em": "2026-08-04"})
        with pytest.raises(ValueError):
            parse_pnipe_record({"name": "Lab", "url": "https://x"})

    def test_bronze_fixture_conforms_to_contract(self):
        """O bronze `pnipe_*.json` carrega os campos que `_ingest_icts` consome
        (name/url/data_extracao obrigatórios + campos de capacidade)."""
        for rec in _pnipe_records():
            assert rec["name"] and rec["url"] and rec["data_extracao"]
            assert rec["source"] == "pnipe"
            assert rec["kind"] == "laboratorio"
            assert isinstance(rec["competencias"], list)
            assert isinstance(rec["equipamentos"], list)
            assert "institution" in rec and "municipio" in rec


class TestPnipeScraper:
    def test_extract_without_dump_returns_empty(self, tmp_path):
        scraper = PnipeScraper(dump_path=tmp_path / "ausente.json")
        assert scraper.extract() == []

    def test_extract_normalizes_curated_dump(self, tmp_path):
        dump = tmp_path / "dump.json"
        dump.write_text(json.dumps([
            {"nome": "Lab A", "url": "https://x/a", "verificado_em": "2026-08-04",
             "competencias": ["IA"]},
        ]), encoding="utf-8")
        scraper = PnipeScraper(dump_path=dump)
        records = scraper.extract()
        assert len(records) == 1
        assert records[0]["name"] == "Lab A"
        assert records[0]["kind"] == "laboratorio"
        assert records[0]["data_extracao"] == "2026-08-04"

    def test_save_writes_pnipe_bronze_file(self, tmp_path, monkeypatch):
        scraper = PnipeScraper(dump_path=tmp_path / "dump.json")
        scraper.output_dir = str(tmp_path)
        records = _pnipe_records()
        path = scraper._save(records, prefix="pnipe")
        written = json.loads(Path(path).read_text(encoding="utf-8"))
        assert written == records
        assert Path(path).name.startswith("pnipe_")

    def test_not_in_scraper_registry(self):
        from radar.pipeline.extractors import SCRAPER_REGISTRY

        assert "pnipe" not in SCRAPER_REGISTRY


# ---------------------------------------------------------------------------
# (b) Proveniência
# ---------------------------------------------------------------------------


class TestPnipeProvenance:
    def test_anchor_source_is_pnipe(self):
        record = _record()
        anchor = provenance_writer.build_ict_record_anchor(
            record=record, document="pnipe_fixture.json",
            source_url=record.get("url"), native_id="pnipe:lab-ia-robotica",
            source="pnipe",
        )
        assert anchor.locator_quality == "document_only"
        assert anchor.source == "pnipe"
        assert anchor.document == "pnipe_fixture.json"
        assert anchor.canonical_content_hash.startswith("md5:")
        assert anchor.quote is None

    def test_fact_provenance_capacity_paths_stated_adapter(self):
        record = _record()
        anchor = provenance_writer.build_ict_record_anchor(
            record=record, document="pnipe_fixture.json",
            source_url=record.get("url"), native_id="pnipe:lab-ia-robotica",
            source="pnipe",
        )
        out = provenance_writer.build_ict_fact_provenance(
            record=record, anchor=anchor, uf="SC",
            source="pnipe", producer_name="pnipe_lab_index",
        )
        expected = {
            "name", "metadata.url", "metadata.institution", "metadata.municipio",
            "metadata.competencias", "metadata.equipamentos",
            "metadata.condicoes_acesso", "metadata.verificado_em",
            "uf", "setores", "tecnologias_tags",
        }
        assert set(out) == expected
        for path in ("name", "metadata.url", "metadata.institution",
                     "metadata.competencias", "metadata.verificado_em"):
            fp = FactProvenance.model_validate(out[path])
            assert fp.state == "stated"
            assert fp.producer.kind == "adapter"
            assert fp.producer.name == "pnipe_lab_index"
            assert fp.evidence_refs and fp.evidence_refs[0].source == "pnipe"
        for path in ("uf", "setores", "tecnologias_tags"):
            fp = FactProvenance.model_validate(out[path])
            assert fp.state == "inferred"
            assert fp.producer.kind == "deterministic"

    def test_empty_capacity_field_omitted(self):
        record = dict(_record())
        record["condicoes_acesso"] = ""
        record["competencias"] = []
        anchor = provenance_writer.build_ict_record_anchor(
            record=record, document="pnipe_fixture.json",
            source_url=record.get("url"), source="pnipe",
        )
        out = provenance_writer.build_ict_fact_provenance(
            record=record, anchor=anchor, uf="SP",
            source="pnipe", producer_name="pnipe_lab_index",
        )
        assert "metadata.condicoes_acesso" not in out
        assert "metadata.competencias" not in out

    def test_embrapii_fact_table_unchanged(self):
        """Regressão RT01-T07: sem `source`, o contrato EMBRAPII permanece
        exatamente com os 5 paths e produtor `embrapii_scraper`."""
        record = {
            "name": "CEIA", "url": "https://embrapii.org.br/x",
            "areas_raw": ["IA"], "address": "Goiânia, GO",
        }
        anchor = provenance_writer.build_ict_record_anchor(
            record=record, document="embrapii_fixture.json",
            source_url=record["url"], native_id="embrapii:ceia",
        )
        out = provenance_writer.build_ict_fact_provenance(record=record, anchor=anchor, uf="GO")
        assert set(out) == {"name", "metadata.url", "uf", "setores", "tecnologias_tags"}
        assert FactProvenance.model_validate(out["name"]).producer.name == "embrapii_scraper"


# ---------------------------------------------------------------------------
# (c) Ingest gold via harness T02
# ---------------------------------------------------------------------------


class TestGoldCapturePnipeLabs:
    def test_labs_ingested_as_ict_capabilities(self, monkeypatch):
        projection, stats = run_capture(monkeypatch, PNIPE_FIXTURES_DIR, sources=["ict"])
        rec = projection.entities.get(PNIPE_RECORD_KEY)
        assert rec is not None
        assert rec["kind"] == "ict"
        assert rec["source"] == "pnipe"
        assert rec["native_id"] == "pnipe:lab-ia-robotica"
        assert rec["name"] == "Laboratório de Inteligência Artificial e Robótica"
        assert rec["uf"] == "SC"
        assert rec["verificado_em"] == "2026-08-04"
        assert rec["curated"] is True
        assert "Aprendizado de máquina" in rec["description"]
        meta = rec["metadata"]
        assert meta["institution"] == "UFSC"
        assert meta["municipio"] == "Florianópolis"
        assert "Cluster GPU" in meta["equipamentos"]
        assert "Visão computacional" in meta["competencias"]
        assert meta["condicoes_acesso"]
        assert meta["verificado_em"] == "2026-08-04"
        assert stats["ict"] == 2

    def test_no_credenciada_por_edge_for_pnipe(self, monkeypatch):
        projection, _ = run_capture(monkeypatch, PNIPE_FIXTURES_DIR, sources=["ict"])
        for rk, rel in projection.relations.items():
            assert rel["type"] != "credenciada_por", f"PNIPE não é credenciado pela EMBRAPII ({rk})"

    def test_labs_never_become_editais(self, monkeypatch):
        projection, _ = run_capture(monkeypatch, PNIPE_FIXTURES_DIR, sources=["ict"])
        assert all(rec["kind"] == "ict" for rec in projection.entities.values())


class _PnipeProvenanceHarness(GoldCaptureHarness):
    def __init__(self) -> None:
        super().__init__()
        self.entity_provenance: dict[str, dict] = {}

    def stub_upsert_entity(self, cur, **f):
        synthetic_id = super().stub_upsert_entity(cur, **f)
        key = self._id_to_key[synthetic_id]
        self.entity_provenance[key] = f.get("provenance") or {}
        return synthetic_id


class TestGoldCapturePnipeProvenance:
    def test_captured_lab_provenance_has_capacity_paths(self, monkeypatch):
        from radar.core.kg import gold

        harness = _PnipeProvenanceHarness()
        harness.apply_patches(monkeypatch, PNIPE_FIXTURES_DIR)
        gold.ingest_all(sources=["ict"], skip_unchanged=True)
        prov = harness.entity_provenance.get(PNIPE_RECORD_KEY)
        assert prov, "lab PNIPE deveria ter provenance não vazia"
        assert "metadata.institution" in prov
        assert "metadata.competencias" in prov
        assert "metadata.equipamentos" in prov
        assert "metadata.verificado_em" in prov
        fp = FactProvenance.model_validate(prov["metadata.verificado_em"])
        assert fp.state == "stated"
        assert fp.producer.name == "pnipe_lab_index"
        assert len(fp.evidence_refs) == 1
        ref = fp.evidence_refs[0]
        assert ref.source == "pnipe"
        assert ref.locator_quality == "document_only"


# ---------------------------------------------------------------------------
# (d) Consumidores
# ---------------------------------------------------------------------------


class TestCatalogCapacityPayload:
    def test_payload_extracts_capacidades(self):
        from radar.core.kg import entity_catalog

        row = {
            "source": "pnipe", "uf": "SC",
            "metadata": {
                "url": "https://pnipe.mcti.gov.br/laboratorio/x",
                "institution": "UFSC", "municipio": "Florianópolis",
                "competencias": ["IA"], "equipamentos": ["Cluster"],
                "condicoes_acesso": "proposta", "verificado_em": "2026-08-04",
            },
        }
        out = entity_catalog._ict_capacity_payload(row)
        assert out["source"] == "pnipe"
        assert out["uf"] == "SC"
        assert out["url"] == "https://pnipe.mcti.gov.br/laboratorio/x"
        assert out["capacidades"]["institution"] == "UFSC"
        assert out["capacidades"]["competencias"] == ["IA"]
        assert out["capacidades"]["verificado_em"] == "2026-08-04"

    def test_payload_empty_when_no_metadata(self):
        from radar.core.kg import entity_catalog

        out = entity_catalog._ict_capacity_payload({})
        assert out["capacidades"] == {
            "institution": "", "municipio": "", "competencias": [],
            "equipamentos": [], "condicoes_acesso": "", "verificado_em": "",
        }


class TestFindIctPartnersCapacity:
    def test_capacities_and_url_flow_into_caminho(self, monkeypatch):
        from radar.core.kg import entity_catalog

        item = {
            "id": "pnipe:lab-ia", "name": "Lab IA", "type": "Ator",
            "description": "P&D em IA",
            "themes": ["Saúde"],
            "source": "pnipe", "uf": "SC",
            "url": "https://pnipe.mcti.gov.br/laboratorio/lab-ia",
            "capacidades": {
                "institution": "UFSC", "municipio": "Florianópolis",
                "competencias": ["Visão computacional"], "equipamentos": ["Cluster GPU"],
                "condicoes_acesso": "mediante proposta", "verificado_em": "2026-08-04",
            },
        }
        monkeypatch.setattr(
            entity_catalog, "list_entity_catalog",
            lambda key, *, tema, limit: [item],
        )
        partners = match_v3.find_ict_partners(
            {"nome": "ACME", "one_liner": "diagnóstico por visão computacional em saúde"},
        )
        assert len(partners) == 1
        p = partners[0]
        assert p["kind"] == "ict"
        assert p["caminho"]["canal_de_acesso"] == "https://pnipe.mcti.gov.br/laboratorio/lab-ia"
        confirmados = p["explicacao"]["confirmados"]
        joined = " ".join(confirmados)
        assert "Visão computacional" in joined
        assert "Cluster GPU" in joined
        assert "UFSC" in joined
        # Não-claim: disponibilidade/parceria nunca é confirmada — só o declarado
        assert "parceria confirmada" not in joined.lower()

    def test_explanation_capacity_absent_is_safe(self):
        expl = domain_paths.build_explanation(
            domain_paths.PATH_TIPO_ICT, e={"setores": ["Saúde"], "status": None},
            eleg=None, profile={"one_liner": "projeto X"}, has_project=True,
            shared_themes={"Saúde"},
        )
        assert any("competência" in i.lower() for i in expl["inferidos"])
