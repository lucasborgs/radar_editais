"""RT01-T08 — proveniência de investidores, programas e agências (curadoria).

Dois blocos:

(a) `provenance_writer` isolado (builders + composição): estados/produtores
    corretos, âncora `md5:` calculada a partir do JSON canônico do registro,
    todo `FactProvenance` emitido passa `model_validate`; composição
    (`build_investidor_fact_provenance`/`build_programa_fact_provenance`)
    respeita "campo sem valor -> sem entrada".

(b) captura via harness T02 (`tests/helpers/gold_projection.py`) SEM
    modificá-lo — subclasse local (mesmo padrão de
    `test_gold_provenance_dualwrite.py`/`test_gold_provenance_sources.py`)
    que também captura `f.get("provenance")` e o kwarg `provenance` de
    `stub_upsert_rel`. Prova sobre o fixture real
    (`tests/fixtures/gold_equivalence/`): investidor/programa têm provenance
    com os paths esperados (`unknown` nos copiados verbatim, `inferred` nos
    derivados); agências têm provenance mínima (`name`); a aresta
    `operado_por` de programa tem provenance; editais preservam a cobertura
    da T06; `ict` continua com provenance vazia (task irmã T07, paralela);
    nenhum campo de catálogo é marcado `stated` (adversarial); duas capturas
    produzem o mesmo resultado (determinismo).

Hermético: sem rede, sem banco (mesmos stubs do harness T02).
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from helpers.gold_projection import DEFAULT_FIXTURES_DIR, GoldCaptureHarness

from radar.core.kg import provenance_writer
from radar.core.kg.equivalence import relation_key
from radar.domain.provenance import EvidenceRef, FactProvenance

INVESTIDOR_KEY = "investidor|curadoria|investidor:indicator-capital"
PROGRAMA_KEY = "programa|curadoria|programa:centelha"
AGENCIA_MCTI_KEY = "agencia|curadoria|agencia:mcti"
ICT_KEY = "ict|embrapii|embrapii:inteligencia-artificial-ceia-ufg"
PROGRAMA_OPERADO_POR_MCTI = relation_key(PROGRAMA_KEY, AGENCIA_MCTI_KEY, "operado_por")
ICT_CREDENCIADA_POR = relation_key(ICT_KEY, "agencia|curadoria|agencia:embrapii", "credenciada_por")


# ---------------------------------------------------------------------------
# (a) Builders isolados
# ---------------------------------------------------------------------------


class TestCatalogAnchor:
    def test_anchor_is_document_only_with_md5_hash(self):
        record = {"id": "investidor:x", "name": "X", "site": "https://x.example"}
        ref = provenance_writer.build_curated_catalog_anchor(
            record, document=provenance_writer.CURATED_INVESTIDORES_DOCUMENT, source_url=record["site"]
        )
        assert ref.locator_quality == "document_only"
        assert ref.document == "data/silver/investidores.json"
        assert ref.source == "curadoria"
        assert ref.source_url == "https://x.example"
        assert ref.quote is None
        assert ref.canonical_content_hash is not None
        assert ref.canonical_content_hash.startswith("md5:")
        expected = "md5:" + hashlib.md5(
            json.dumps(record, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()
        assert ref.canonical_content_hash == expected
        EvidenceRef.model_validate(ref.model_dump(mode="json"))

    def test_anchor_hash_changes_with_record_content(self):
        r1 = {"id": "a", "name": "A"}
        r2 = {"id": "a", "name": "A changed"}
        h1 = provenance_writer.build_curated_catalog_anchor(r1, document="d").canonical_content_hash
        h2 = provenance_writer.build_curated_catalog_anchor(r2, document="d").canonical_content_hash
        assert h1 != h2

    def test_anchor_without_source_url_is_none(self):
        ref = provenance_writer.build_curated_catalog_anchor({"id": "a"}, document="d")
        assert ref.source_url is None


class TestCopiedFieldProvenance:
    def test_copied_field_is_unknown_human_with_anchor(self):
        anchor = provenance_writer.build_curated_catalog_anchor({"id": "a"}, document="d")
        fp = provenance_writer.build_catalog_copied_provenance(anchor)
        assert fp.state == "unknown"
        assert fp.producer.kind == "human"
        assert fp.producer.name == "curadoria"
        assert fp.evidence_refs == [anchor]
        FactProvenance.model_validate(fp.model_dump(mode="json"))

    def test_copied_field_never_stated(self):
        """Adversarial: mesmo com âncora resolvida (document_only), o campo
        copiado NUNCA é `stated` — curado != validado (spec §3.2)."""
        anchor = provenance_writer.build_curated_catalog_anchor({"id": "a"}, document="d")
        fp = provenance_writer.build_catalog_copied_provenance(anchor)
        assert fp.state != "stated"


class TestDerivedFieldProvenance:
    def test_derived_is_inferred_deterministic_no_refs(self):
        fp = provenance_writer.build_curated_derived_provenance(
            producer_name="_ticket_from_range", rule="_ticket_from_range:v1", inputs=["record.ticket_range"]
        )
        assert fp.state == "inferred"
        assert fp.producer.kind == "deterministic"
        assert fp.producer.name == "_ticket_from_range"
        assert fp.derivation.rule == "_ticket_from_range:v1"
        assert fp.derivation.inputs == ["record.ticket_range"]
        assert fp.evidence_refs == []
        FactProvenance.model_validate(fp.model_dump(mode="json"))


class TestProgramaRequisitoProvenance:
    def test_is_inferred_llm_never_resolves(self):
        """SEM resolução de evidência — programas não têm blocos silver
        (tabela de fatos T08)."""
        fp = provenance_writer.build_programa_requisito_provenance(model="gpt-4o-mini")
        assert fp.state == "inferred"
        assert fp.producer.kind == "llm"
        assert fp.producer.name == provenance_writer.CONSTRAINTS_PRODUCER_NAME
        assert fp.producer.model == "gpt-4o-mini"
        assert fp.evidence_refs == []
        FactProvenance.model_validate(fp.model_dump(mode="json"))


class TestProgramaOperadoPorProvenance:
    def test_is_inferred_deterministic_split_operador(self):
        fp = provenance_writer.build_programa_operado_por_provenance()
        assert fp.state == "inferred"
        assert fp.producer.kind == "deterministic"
        assert fp.derivation.rule == "_split_operador:v1"
        assert fp.derivation.inputs == ["record.operador"]
        FactProvenance.model_validate(fp.model_dump(mode="json"))


class TestAgenciaNameProvenance:
    def test_is_inferred_deterministic_canon_agency(self):
        fp = provenance_writer.build_agencia_name_provenance()
        assert fp.state == "inferred"
        assert fp.producer.kind == "deterministic"
        assert fp.derivation.rule == "_canon_agency:v1"
        assert fp.derivation.inputs == ["operador|source"]
        FactProvenance.model_validate(fp.model_dump(mode="json"))


class TestInvestidorFactProvenanceComposition:
    def _record(self, **overrides: Any) -> dict:
        base = {
            "id": "investidor:x", "name": "X Capital", "tese": "Tese de teste.",
            "site": "https://x.example",
        }
        base.update(overrides)
        return base

    def test_all_fields_present_when_record_complete(self):
        record = self._record()
        out = provenance_writer.build_investidor_fact_provenance(
            record, setores=["Multissetorial"], tecnologias_tags=["ia"],
            status="ativa", ticket_min=1.0, ticket_max=2.0,
        )
        assert set(out) == {
            "name", "description", "metadata.site", "setores", "tecnologias_tags",
            "status", "ticket_min", "ticket_max",
        }
        for path, payload in out.items():
            fp = FactProvenance.model_validate(payload)
            if path in ("name", "description", "metadata.site"):
                assert fp.state == "unknown"
                assert fp.producer.kind == "human"
            else:
                assert fp.state == "inferred"
                assert fp.producer.kind == "deterministic"

    def test_missing_ticket_omits_ticket_paths(self):
        """'Campo sem valor -> sem entrada': ticket_range null (fixture
        Indicator Capital) não gera ticket_min/ticket_max."""
        record = self._record()
        out = provenance_writer.build_investidor_fact_provenance(
            record, setores=["Multissetorial"], tecnologias_tags=[],
            status="ativa", ticket_min=None, ticket_max=None,
        )
        assert "ticket_min" not in out
        assert "ticket_max" not in out

    def test_missing_site_omits_metadata_site(self):
        record = self._record(site=None)
        out = provenance_writer.build_investidor_fact_provenance(
            record, setores=["Multissetorial"], tecnologias_tags=[],
            status="ativa", ticket_min=None, ticket_max=None,
        )
        assert "metadata.site" not in out

    def test_copied_paths_share_same_anchor_object(self):
        """'Âncora do catálogo (mesmo objeto compartilhado entre os paths)':
        name/description/metadata.site carregam o MESMO EvidenceRef."""
        record = self._record()
        out = provenance_writer.build_investidor_fact_provenance(
            record, setores=["Multissetorial"], tecnologias_tags=[],
            status="ativa", ticket_min=None, ticket_max=None,
        )
        refs = [out[p]["evidence_refs"][0] for p in ("name", "description", "metadata.site")]
        assert refs[0] == refs[1] == refs[2]


class TestProgramaFactProvenanceComposition:
    def _record(self, **overrides: Any) -> dict:
        base = {
            "id": "programa:x", "name": "Programa X", "operador": "MCTI / FINEP",
            "beneficio": "Subvenção.", "elegibilidade": "Empresas brasileiras.",
            "site": "https://x.example",
        }
        base.update(overrides)
        return base

    def test_all_fields_present_when_record_complete(self):
        record = self._record()
        out = provenance_writer.build_programa_fact_provenance(
            record, setores=["Multissetorial"], tecnologias_tags=["ia"],
            status="ativa", ticket_min=1.0, ticket_max=2.0,
            mecanismo="subvencao", formato="edital_periodico",
            constraints=[{"tipo": "porte", "op": "in", "valor": ["mei"]}],
            requisitos_texto=["Requisito A.", "Requisito B."],
            constraints_model="gpt-4o-mini",
        )
        assert set(out) == {
            "name", "metadata.operador", "metadata.beneficio", "metadata.elegibilidade",
            "setores", "tecnologias_tags", "status", "ticket_min", "ticket_max",
            "mecanismo", "formato", "constraints", "requisitos_texto.0", "requisitos_texto.1",
        }
        for path, payload in out.items():
            fp = FactProvenance.model_validate(payload)
            if path in ("name", "metadata.operador", "metadata.beneficio", "metadata.elegibilidade"):
                assert fp.state == "unknown"
            elif path == "constraints":
                assert fp.state == "inferred" and fp.producer.kind == "llm"
            elif path.startswith("requisitos_texto."):
                assert fp.state == "inferred" and fp.producer.kind == "llm"
                assert fp.evidence_refs == []
            else:
                assert fp.state == "inferred" and fp.producer.kind == "deterministic"

    def test_empty_constraints_and_requisitos_omitted(self):
        record = self._record()
        out = provenance_writer.build_programa_fact_provenance(
            record, setores=["Multissetorial"], tecnologias_tags=[],
            status="ativa", ticket_min=None, ticket_max=None,
            mecanismo=None, formato=None, constraints=[], requisitos_texto=[],
            constraints_model="gpt-4o-mini",
        )
        assert "constraints" not in out
        assert not any(k.startswith("requisitos_texto.") for k in out)
        assert "mecanismo" not in out
        assert "formato" not in out


# ---------------------------------------------------------------------------
# (b) Captura via harness T02 (subclasse LOCAL — gold_projection.py intocado)
# ---------------------------------------------------------------------------


class _CuratedProvenanceCapturingHarness(GoldCaptureHarness):
    """Subclasse local só desta suíte — mesmo padrão de
    `test_gold_provenance_dualwrite.py`/`test_gold_provenance_sources.py`:
    reusa toda a lógica de `GoldCaptureHarness` e adicionalmente guarda
    `f.get("provenance")` por chave natural de entidade e o kwarg
    `provenance` de `stub_upsert_rel` (aceito e ignorado pelo harness base;
    esta subclasse é quem de fato captura o valor)."""

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

    harness = _CuratedProvenanceCapturingHarness()
    harness.apply_patches(monkeypatch, DEFAULT_FIXTURES_DIR)
    stats = gold.ingest_all(skip_unchanged=True)
    return harness, dict(stats)


class TestCuratedCapturedProvenance:
    def test_investidor_has_expected_paths(self, monkeypatch):
        harness, _ = _run_capture(monkeypatch)
        assert INVESTIDOR_KEY in harness.entity_provenance
        prov = harness.entity_provenance[INVESTIDOR_KEY]
        assert prov, "investidor deveria ter provenance não vazia"
        # Fixture (Indicator Capital): name/tese/site presentes; ticket_range
        # null -> sem ticket_min/ticket_max.
        assert set(prov) == {"name", "description", "metadata.site", "setores", "tecnologias_tags", "status"}
        for path in ("name", "description", "metadata.site"):
            fp = FactProvenance.model_validate(prov[path])
            assert fp.state == "unknown"
            assert fp.producer.kind == "human"
            assert fp.evidence_refs[0].locator_quality == "document_only"
            assert fp.evidence_refs[0].canonical_content_hash.startswith("md5:")
            assert fp.evidence_refs[0].document == provenance_writer.CURATED_INVESTIDORES_DOCUMENT
        for path in ("setores", "tecnologias_tags", "status"):
            fp = FactProvenance.model_validate(prov[path])
            assert fp.state == "inferred"
            assert fp.producer.kind == "deterministic"

    def test_programa_has_expected_paths(self, monkeypatch):
        harness, _ = _run_capture(monkeypatch)
        assert PROGRAMA_KEY in harness.entity_provenance
        prov = harness.entity_provenance[PROGRAMA_KEY]
        assert prov, "programa deveria ter provenance não vazia"
        # Fixture (Programa Centelha): operador/beneficio/elegibilidade
        # presentes; ticket_range presente; tipo=subvencao (no enum);
        # formato=edital-periodico (mapeável) -> mecanismo/formato presentes.
        expected = {
            "name", "metadata.operador", "metadata.beneficio", "metadata.elegibilidade",
            "setores", "tecnologias_tags", "status", "ticket_min", "ticket_max",
            "mecanismo", "formato",
        }
        assert expected <= set(prov)
        for path in ("name", "metadata.operador", "metadata.beneficio", "metadata.elegibilidade"):
            fp = FactProvenance.model_validate(prov[path])
            assert fp.state == "unknown"
            assert fp.producer.kind == "human"
            assert fp.evidence_refs[0].document == provenance_writer.CURATED_PROGRAMAS_DOCUMENT
        for path in ("setores", "tecnologias_tags", "status", "ticket_min", "ticket_max", "mecanismo", "formato"):
            fp = FactProvenance.model_validate(prov[path])
            assert fp.state == "inferred"
            assert fp.producer.kind == "deterministic"

    def test_agencies_have_minimal_provenance(self, monkeypatch):
        harness, _ = _run_capture(monkeypatch)
        agencia_keys = [k for k, rec in harness.projection.entities.items() if rec["kind"] == "agencia"]
        assert agencia_keys, "fixture deveria ter agências"
        for k in agencia_keys:
            prov = harness.entity_provenance.get(k, {})
            assert set(prov) == {"name"}
            fp = FactProvenance.model_validate(prov["name"])
            assert fp.state == "inferred"
            assert fp.producer.kind == "deterministic"
            assert fp.derivation.rule == "_canon_agency:v1"

    def test_programa_operado_por_edges_have_provenance(self, monkeypatch):
        harness, _ = _run_capture(monkeypatch)
        assert PROGRAMA_OPERADO_POR_MCTI in harness.relation_provenance
        prov = harness.relation_provenance[PROGRAMA_OPERADO_POR_MCTI]
        assert prov, "aresta operado_por de programa deveria ter provenance"
        fp = FactProvenance.model_validate(prov)
        assert fp.state == "inferred"
        assert fp.producer.kind == "deterministic"
        assert fp.derivation.rule == "_split_operador:v1"

    def test_editais_preserve_t06_coverage(self, monkeypatch):
        """Nenhuma regressão nas 4 fontes de edital (T05/T06)."""
        harness, _ = _run_capture(monkeypatch)
        for key in (
            "edital|finep|finep:602", "edital|fapesp|fapesp:16466",
            "edital|fapesc|fapesc:35-2026", "edital|web|web:ce032edb720c",
        ):
            assert key in harness.entity_provenance
            prov = harness.entity_provenance[key]
            assert prov
            expected_min = {"status", "setores", "tecnologias_tags"}
            assert expected_min <= set(prov)

    # NOTA (pouso T07+T08, governança): a asserção de escopo-irmão que vivia
    # aqui ("test_ict_still_has_no_provenance") afirmava que os kinds da task PARALELA ainda não gravavam
    # provenance — supersedida quando as duas pousaram juntas. O contrato
    # positivo pós-pouso vive em test_gold_provenance_sources.py
    # (test_all_entities_have_provenance) e nos arquivos por kind.


    def test_no_catalog_field_is_stated(self, monkeypatch):
        """Adversarial: nenhum path de investidor/programa/agencia é
        `stated` — curadoria básica nunca se apresenta como fato verificado
        (spec §3.2)."""
        harness, _ = _run_capture(monkeypatch)
        curated_kinds = {"investidor", "programa", "agencia"}
        for key, rec in harness.projection.entities.items():
            if rec["kind"] not in curated_kinds:
                continue
            for path, payload in harness.entity_provenance.get(key, {}).items():
                fp = FactProvenance.model_validate(payload)
                assert fp.state != "stated", f"{key}/{path} não deveria ser stated"

    def test_two_captures_deterministic(self, monkeypatch):
        h1, _ = _run_capture(monkeypatch)
        h2, _ = _run_capture(monkeypatch)
        assert h1.entity_provenance == h2.entity_provenance
        assert h1.relation_provenance == h2.relation_provenance
