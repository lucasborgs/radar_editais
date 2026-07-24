"""RT01-T05 — dual-write de proveniência do vertical slice FINEP.

Dois blocos:

(a) `provenance_writer` isolado (sem gold.py/harness): a tabela de fatos da
    task — estados/produtores corretos, todo `FactProvenance` emitido passa
    `model_validate`; `requisitos_texto` resolve `stated` com `EvidenceRef`
    exato quando o trecho existe verbatim no silver real da fixture
    (`tests/fixtures/gold_equivalence/.../finep/602.jsonl`), e cai em
    `inferred` (adversarial: NUNCA `stated`) quando não existe.

(b) captura via harness T02 (`tests/helpers/gold_projection.py`) SEM
    modificá-lo — uma subclasse local de `GoldCaptureHarness` que também
    captura `f.get("provenance")` (entidades) e as 4 coordenadas novas de
    `match_chunks`, delegando toda a lógica de correlação/projeção à classe
    base. Prova: finep tem proveniência nos paths esperados; coordenadas de
    chunk só existem nos chunks finep. NOTA: fapesp/fapesc/web (T06) e ict
    (T07) também passaram a gravar provenance não vazia — afirmado em
    test_gold_provenance_sources.py e test_gold_provenance_icts.py,
    respectivamente; investidor/programa/agência seguem vazios até T08.

Hermético: sem rede, sem banco (mesmos stubs do harness T02).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from helpers.gold_projection import DEFAULT_FIXTURES_DIR, GoldCaptureHarness

from radar.core.kg import provenance_writer
from radar.core.kg.equivalence import relation_key
from radar.domain.provenance import FactProvenance

FINEP_SILVER = DEFAULT_FIXTURES_DIR / "silver" / "structured_docs" / "finep" / "602.jsonl"
FINEP_HASH = "md5:e0c8a6f538aa9ab9b30f7ced0ad5edc5"  # tests/fixtures/.../finep/602.meta.json::source_hash


def _load_finep_blocks() -> list[dict]:
    return [json.loads(line) for line in FINEP_SILVER.read_text(encoding="utf-8").splitlines() if line.strip()]


# ---------------------------------------------------------------------------
# (a) Tabela de fatos — builders isolados
# ---------------------------------------------------------------------------


class TestFactTableBuilders:
    def test_status_is_inferred_deterministic_no_refs(self):
        fp = provenance_writer.build_status_provenance("aberta")
        assert fp.state == "stated" or fp.state == "inferred"  # sanity: campo existe
        assert fp.state == "inferred"
        assert fp.producer.kind == "deterministic"
        assert fp.evidence_refs == []
        assert fp.derivation.rule == "_normalize_status:v1"
        assert fp.derivation.inputs == ["bronze.status", "deadline"]
        FactProvenance.model_validate(fp.model_dump(mode="json"))

    def test_mecanismo_is_inferred_deterministic_no_refs(self):
        fp = provenance_writer.build_mecanismo_provenance()
        assert fp.state == "inferred"
        assert fp.producer.kind == "deterministic"
        assert fp.derivation.rule == "_infer_mecanismo_from_text:v1"
        assert fp.evidence_refs == []
        FactProvenance.model_validate(fp.model_dump(mode="json"))

    def test_tags_provenance_is_inferred_llm_no_refs(self):
        fp = provenance_writer.build_tags_provenance(model="gpt-4o-mini")
        assert fp.state == "inferred"
        assert fp.producer.kind == "llm"
        assert fp.producer.name == "gold_tagger"
        assert fp.producer.model == "gpt-4o-mini"
        assert fp.producer.prompt_version == provenance_writer.GOLD_TAGGER_PROMPT_VERSION
        assert fp.derivation.inputs == ["silver.thematic_sections"]
        assert fp.evidence_refs == []
        FactProvenance.model_validate(fp.model_dump(mode="json"))

    def test_constraints_provenance_is_inferred_llm_no_refs(self):
        fp = provenance_writer.build_constraints_provenance(model="gpt-4o-mini")
        assert fp.state == "inferred"
        assert fp.producer.kind == "llm"
        assert fp.producer.name == "constraints_producer"
        assert fp.producer.version == provenance_writer.CONSTRAINTS_PRODUCER_VERSION
        assert fp.derivation.inputs == ["silver.eligibility_sections"]
        assert fp.evidence_refs == []
        FactProvenance.model_validate(fp.model_dump(mode="json"))

    def test_operado_por_edge_is_inferred_deterministic(self):
        fp = provenance_writer.build_operado_por_provenance()
        assert fp.state == "inferred"
        assert fp.producer.kind == "deterministic"
        assert fp.derivation.rule == "_SOURCE_AGENCY:v1"
        FactProvenance.model_validate(fp.model_dump(mode="json"))

    def test_subordinado_a_edge_is_inferred_deterministic_with_name_input(self):
        fp = provenance_writer.build_subordinado_a_provenance()
        assert fp.state == "inferred"
        assert fp.producer.kind == "deterministic"
        assert fp.derivation.rule == "_detect_programa:v1"
        assert fp.derivation.inputs == ["name"]
        FactProvenance.model_validate(fp.model_dump(mode="json"))


class TestRequisitoResolution:
    """`requisitos_texto.<i>` contra o silver REAL da fixture finep/602."""

    def test_exact_quote_resolves_to_stated_with_exact_evidence_ref(self):
        blocks = _load_finep_blocks()
        quote = "A duração máxima de cada projeto será de 2 (dois) anos."
        fp = provenance_writer.build_requisito_provenance(
            quote, blocks=blocks, source="finep", native_id="602", edital_id="finep:602",
            silver_source_hash=FINEP_HASH, model="gpt-4o-mini",
        )
        assert fp.state == "stated"
        assert len(fp.evidence_refs) == 1
        ref = fp.evidence_refs[0]
        assert ref.locator_quality == "exact"
        assert ref.document == "Edital.pdf"
        assert ref.page == 5
        assert ref.block_idx == 47
        assert ref.quote == quote
        assert ref.silver_source_hash == FINEP_HASH
        FactProvenance.model_validate(fp.model_dump(mode="json"))

    def test_non_matching_quote_is_inferred_never_stated(self):
        blocks = _load_finep_blocks()
        quote = "Este trecho não existe em nenhum bloco deste edital de teste."
        fp = provenance_writer.build_requisito_provenance(
            quote, blocks=blocks, source="finep", native_id="602", edital_id="finep:602",
            silver_source_hash=FINEP_HASH, model="gpt-4o-mini",
        )
        assert fp.state == "inferred"
        FactProvenance.model_validate(fp.model_dump(mode="json"))

    def test_missing_hash_never_produces_evidence_ref_still_inferred(self):
        """Adversarial extra: sem hash, o resolver não constrói EvidenceRef
        nenhum (T03) — build_requisito_provenance nunca promove a `stated`
        sem referência, mesmo que o texto exista verbatim no bloco."""
        blocks = _load_finep_blocks()
        quote = "A duração máxima de cada projeto será de 2 (dois) anos."
        fp = provenance_writer.build_requisito_provenance(
            quote, blocks=blocks, source="finep", native_id="602", edital_id="finep:602",
            silver_source_hash=None, model="gpt-4o-mini",
        )
        assert fp.state == "inferred"
        assert fp.evidence_refs == []
        FactProvenance.model_validate(fp.model_dump(mode="json"))


class TestEditalFactProvenanceComposition:
    def test_composes_expected_paths_and_all_validate(self):
        blocks = _load_finep_blocks()
        out = provenance_writer.build_edital_fact_provenance(
            status="aberta", mecanismo="subvencao",
            constraints=[{"tipo": "porte", "op": "in", "valor": ["mei"]}],
            requisitos_texto=[
                "A duração máxima de cada projeto será de 2 (dois) anos.",
                "Texto que não existe no documento.",
            ],
            blocks=blocks, source="finep", native_id="602", edital_id="finep:602",
            silver_source_hash=FINEP_HASH, source_url="https://example.org/edital",
            tagger_model="gpt-4o-mini", constraints_model="gpt-4o-mini",
        )
        assert set(out) == {
            "status", "setores", "tecnologias_tags", "mecanismo", "constraints",
            "requisitos_texto.0", "requisitos_texto.1",
        }
        for path, payload in out.items():
            fp = FactProvenance.model_validate(payload)
            if path == "requisitos_texto.0":
                assert fp.state == "stated"
            elif path == "requisitos_texto.1":
                assert fp.state == "inferred"
            else:
                assert fp.state == "inferred"

    def test_mecanismo_and_constraints_omitted_when_absent_empty(self):
        out = provenance_writer.build_edital_fact_provenance(
            status="aberta", mecanismo=None, constraints=[], requisitos_texto=[],
            blocks=[], source="finep", native_id="602", edital_id="finep:602",
            silver_source_hash=None, tagger_model="gpt-4o-mini", constraints_model="gpt-4o-mini",
        )
        assert "mecanismo" not in out
        assert "constraints" not in out
        assert set(out) == {"status", "setores", "tecnologias_tags"}


class TestChunkStorageCoords:
    def test_returns_first_block_coords_when_present(self):
        chunk = {"src_doc": "Edital.pdf", "src_page": 5, "src_idx": 47, "text": "..."}
        coords = provenance_writer.chunk_storage_coords(chunk, FINEP_HASH)
        assert coords == {
            "document": "Edital.pdf", "page": 5, "silver_block_idx": 47, "source_hash": FINEP_HASH,
        }

    def test_returns_empty_dict_when_no_source_block(self):
        chunk = {"src_doc": None, "src_page": None, "src_idx": None, "text": "..."}
        assert provenance_writer.chunk_storage_coords(chunk, FINEP_HASH) == {}


# ---------------------------------------------------------------------------
# (b) Captura via harness T02 (subclasse LOCAL — gold_projection.py intocado)
# ---------------------------------------------------------------------------


class _ProvenanceCapturingHarness(GoldCaptureHarness):
    """Subclasse local só desta suíte: reusa TODA a lógica de
    `GoldCaptureHarness` (correlação FIFO produtor→entidade, projeção
    canônica) e adicionalmente guarda `f.get("provenance")` por chave natural
    de entidade, o kwarg `provenance` de `stub_upsert_rel` (RT01-T05: emenda
    autorizada pela governança em 2026-07-24 — `stub_upsert_rel` do harness
    T02 agora ACEITA e IGNORA `provenance`; esta subclasse é quem de fato
    captura o valor para os testes de T05, sem alterar a projeção que o
    gate T02 compara) e as 4 coordenadas novas por chunk. Não modifica
    `tests/helpers/gold_projection.py` além da emenda mínima autorizada."""

    def __init__(self) -> None:
        super().__init__()
        self.entity_provenance: dict[str, dict] = {}
        self.relation_provenance: dict[str, dict] = {}
        self.chunk_coords: dict[str, list[dict]] = {}

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

    def stub_replace_match_chunks(
        self, cur: Any, entity_id: str, chunks: list[dict], embeddings: list[list[float]]
    ) -> int:
        n = super().stub_replace_match_chunks(cur, entity_id, chunks, embeddings)
        ek = self._id_to_key.get(entity_id, entity_id)
        self.chunk_coords[ek] = [
            {
                "document": c.get("document"),
                "page": c.get("page"),
                "silver_block_idx": c.get("silver_block_idx"),
                "source_hash": c.get("source_hash"),
            }
            for c in chunks
        ]
        return n


def _run_provenance_capture(monkeypatch, fixtures_dir: Path | None = None):
    from radar.core.kg import gold

    fixtures_dir = fixtures_dir or DEFAULT_FIXTURES_DIR
    harness = _ProvenanceCapturingHarness()
    harness.apply_patches(monkeypatch, fixtures_dir)
    stats = gold.ingest_all(skip_unchanged=True)
    return harness, dict(stats)


class TestHarnessCapturedProvenance:
    def test_finep_edital_has_expected_provenance_paths(self, monkeypatch):
        harness, _stats = _run_provenance_capture(monkeypatch)
        key = "edital|finep|finep:602"
        assert key in harness.entity_provenance
        prov = harness.entity_provenance[key]
        assert prov, "finep edital deveria ter provenance não vazia"
        expected_min = {"status", "setores", "tecnologias_tags"}
        assert expected_min <= set(prov)
        for _path, payload in prov.items():
            FactProvenance.model_validate(payload)

    # NOTA (RT01-T06, governança): os testes `test_non_finep_entities_have_
    # empty_provenance`, `test_chunk_coords_only_present_for_finep` e
    # `test_non_finep_edges_have_empty_provenance` (escopo era-T05: "só finep
    # grava") foram REMOVIDOS ao pousar a T06, que estendeu o dual-write a
    # todas as fontes de edital. O contrato vigente é afirmado em
    # tests/unit/test_gold_provenance_sources.py (fontes de edital com
    # provenance/coords; não-edital ainda vazio até T07/T08).

    def test_finep_chunk_coords_are_anchored(self, monkeypatch):
        harness, _stats = _run_provenance_capture(monkeypatch)
        finep_key = "edital|finep|finep:602"
        assert finep_key in harness.chunk_coords
        finep_coords = harness.chunk_coords[finep_key]
        assert finep_coords, "edital finep deveria ter chunks"
        assert all(c["document"] == "Edital.pdf" for c in finep_coords)
        assert all(c["source_hash"] == FINEP_HASH for c in finep_coords)
        assert all(c["silver_block_idx"] is not None for c in finep_coords)

    def test_two_consecutive_captures_agree_on_provenance(self, monkeypatch):
        """Determinismo (espelha `TestBaselineCapture` do T02): a captura de
        proveniência não introduz não-determinismo entre execuções."""
        harness1, _ = _run_provenance_capture(monkeypatch)
        harness2, _ = _run_provenance_capture(monkeypatch)
        assert harness1.entity_provenance == harness2.entity_provenance
        assert harness1.chunk_coords == harness2.chunk_coords
        assert harness1.relation_provenance == harness2.relation_provenance

    def test_finep_operado_por_edge_has_valid_deterministic_provenance(self, monkeypatch):
        """RT01-T05, emenda autorizada pela governança (2026-07-24): a aresta
        `operado_por` do edital finep grava provenance (inferred/deterministic,
        rule `_SOURCE_AGENCY:v1`) — capturada via o kwarg agora aceito (e
        ignorado pela projeção comparada) por `stub_upsert_rel`."""
        harness, _stats = _run_provenance_capture(monkeypatch)
        rk = "edital|finep|finep:602->agencia|curadoria|agencia:finep|operado_por"
        assert rk in harness.relation_provenance
        prov = harness.relation_provenance[rk]
        assert prov, "aresta operado_por do edital finep deveria ter provenance não vazia"
        fp = FactProvenance.model_validate(prov)
        assert fp.state == "inferred"
        assert fp.producer.kind == "deterministic"
        assert fp.derivation.rule == "_SOURCE_AGENCY:v1"

    def test_programa_edges_have_empty_provenance_until_t08(self, monkeypatch):
        """Arestas de programa (`operado_por`) seguem sem provenance até
        T08. RT01-T07: a aresta `credenciada_por` de ICT (EMBRAPII) passou a
        gravar provenance (âncora document_only do registro, spec §6.4) —
        removida deste teste "ainda vazio" e afirmada em
        test_gold_provenance_icts.py; as fontes de EDITAL passaram a gravar
        na T06 e são afirmadas em test_gold_provenance_sources.py."""
        harness, _stats = _run_provenance_capture(monkeypatch)
        programa_rks = [rk for rk in harness.projection.relations if rk.startswith("programa|")]
        assert programa_rks, "fixture deveria ter arestas de programa"
        for rk in programa_rks:
            assert harness.relation_provenance.get(rk, {}) == {}, f"{rk} deveria ter provenance vazia"
