"""RT01-T06 — proveniência para FAPESP, FAPESC e Web.

Captura via harness T02 (subclasse local de GoldCaptureHarness, mesmo padrão
da T05). Prova:
  - fapesp/fapesc/web têm provenance não vazia com os paths esperados;
  - finep continua com provenance (não regrediu);
  - entidades não-edital continuam com provenance vazia (não vazaram);
  - arestas operado_por/subordinado_a existentes têm provenance;
  - web NÃO tem aresta operado_por (não cria);
  - web blocks com page=1 passam pelo resolver sem fabricação;
  - nenhum `stated` sem EvidenceRef resolvido (adversarial).
"""
from __future__ import annotations

from helpers.gold_projection import DEFAULT_FIXTURES_DIR, GoldCaptureHarness

from radar.core.kg.equivalence import relation_key
from radar.domain.provenance import FactProvenance

FAPESP_HASH = "md5:329072644fcefa5cf130b051bce0365c"
FAPESC_HASH = "md5:5b1b0fbb4f62fa8d7e70ac083360aa16"
WEB_HASH = "md5:cec120ff628438e5de2052cce8c79f5c"
FINEP_HASH = "md5:e0c8a6f538aa9ab9b30f7ced0ad5edc5"


class _ProvenanceSourceCapturingHarness(GoldCaptureHarness):
    def __init__(self):
        super().__init__()
        self.entity_provenance: dict[str, dict] = {}
        self.relation_provenance: dict[str, dict] = {}
        self.chunk_coords: dict[str, list[dict]] = {}

    def stub_upsert_entity(self, cur, **f):
        eid = super().stub_upsert_entity(cur, **f)
        key = self._id_to_key[eid]
        self.entity_provenance[key] = f.get("provenance") or {}
        return eid

    def stub_upsert_rel(self, cur, source_id, target_id, rtype, properties=None, provenance=None):
        super().stub_upsert_rel(cur, source_id, target_id, rtype, properties=properties, provenance=provenance)
        sk = self._id_to_key.get(source_id, source_id)
        tk = self._id_to_key.get(target_id, target_id)
        rk = relation_key(sk, tk, rtype)
        self.relation_provenance[rk] = provenance or {}

    def stub_replace_match_chunks(self, cur, entity_id, chunks, embeddings):
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


def _run_capture(monkeypatch):
    from radar.core.kg import gold
    harness = _ProvenanceSourceCapturingHarness()
    harness.apply_patches(monkeypatch, DEFAULT_FIXTURES_DIR)
    stats = gold.ingest_all(skip_unchanged=True)
    return harness, dict(stats)


class TestSourceProvenance:
    def test_fapesp_has_provenance(self, monkeypatch):
        harness, _ = _run_capture(monkeypatch)
        key = "edital|fapesp|fapesp:16466"
        assert key in harness.entity_provenance
        prov = harness.entity_provenance[key]
        assert prov, "fapesp deveria ter provenance não vazia"
        expected_min = {"status", "setores", "tecnologias_tags"}
        assert expected_min <= set(prov)
        for payload in prov.values():
            FactProvenance.model_validate(payload)

    def test_fapesc_has_provenance(self, monkeypatch):
        harness, _ = _run_capture(monkeypatch)
        key = "edital|fapesc|fapesc:35-2026"
        assert key in harness.entity_provenance
        prov = harness.entity_provenance[key]
        assert prov
        expected_min = {"status", "setores", "tecnologias_tags"}
        assert expected_min <= set(prov)
        for payload in prov.values():
            FactProvenance.model_validate(payload)

    def test_web_has_provenance(self, monkeypatch):
        harness, _ = _run_capture(monkeypatch)
        key = "edital|web|web:ce032edb720c"
        assert key in harness.entity_provenance
        prov = harness.entity_provenance[key]
        assert prov
        expected_min = {"status", "setores", "tecnologias_tags"}
        assert expected_min <= set(prov)
        for payload in prov.values():
            FactProvenance.model_validate(payload)

    def test_finep_still_has_provenance(self, monkeypatch):
        harness, _ = _run_capture(monkeypatch)
        key = "edital|finep|finep:602"
        assert key in harness.entity_provenance
        prov = harness.entity_provenance[key]
        assert prov
        expected_min = {"status", "setores", "tecnologias_tags"}
        assert expected_min <= set(prov)
        for payload in prov.values():
            FactProvenance.model_validate(payload)

    def test_non_edital_entities_still_empty(self, monkeypatch):
        """`ict` passou a gravar provenance na RT01-T07 (spec §6.4) — excluído
        aqui e afirmado não-vazio em `test_gold_provenance_icts.py`.
        investidor/programa/agencia seguem vazios até a T08."""
        harness, _ = _run_capture(monkeypatch)
        for key, rec in harness.projection.entities.items():
            if rec["kind"] not in ("edital", "ict"):
                assert harness.entity_provenance.get(key, {}) == {}, f"{key} deveria ter provenance vazia"

    def test_web_has_no_operado_por_edge(self, monkeypatch):
        harness, _ = _run_capture(monkeypatch)
        web_op_edges = [
            rk for rk in harness.projection.relations
            if "edital|web|web:ce032edb720c" in rk and "operado_por" in rk
        ]
        assert web_op_edges == [], f"web não deveria ter operado_por mas tem: {web_op_edges}"

    def test_new_edges_have_provenance(self, monkeypatch):
        """Arestas operado_por de fapesp/fapesc têm provenance."""
        harness, _ = _run_capture(monkeypatch)
        for rk, prov in harness.relation_provenance.items():
            if "fapesp" in rk or "fapesc" in rk:
                if "operado_por" in rk:
                    assert prov, f"aresta {rk} deveria ter provenance"
                    FactProvenance.model_validate(prov)

    def test_chunk_coords_present_for_edital_sources(self, monkeypatch):
        """Editais têm chunks com source_hash; programas/investidores não têm."""
        harness, _ = _run_capture(monkeypatch)
        for ek, rec in harness.projection.entities.items():
            coords = harness.chunk_coords.get(ek, [])
            if rec["kind"] != "edital":
                continue
            assert coords, f"{ek}: edital deveria ter chunks"
            for c in coords:
                assert c["source_hash"] is not None, f"{ek}: chunk sem source_hash"

    def test_no_stated_without_evidence_ref(self, monkeypatch):
        """Adversarial: nenhum path stated sem EvidenceRef ou com locator unresolved."""
        harness, _ = _run_capture(monkeypatch)
        for ek, prov in harness.entity_provenance.items():
            for path, payload in prov.items():
                fp = FactProvenance.model_validate(payload)
                if fp.state == "stated":
                    assert fp.evidence_refs, f"{ek}/{path} stated sem EvidenceRef"
                    for ref in fp.evidence_refs:
                        assert ref.locator_quality in ("exact", "document_only"), \
                            f"{ek}/{path}: stated com locator {ref.locator_quality}"

    def test_two_captures_deterministic(self, monkeypatch):
        h1, _ = _run_capture(monkeypatch)
        h2, _ = _run_capture(monkeypatch)
        assert h1.entity_provenance == h2.entity_provenance
        assert h1.relation_provenance == h2.relation_provenance
        assert h1.chunk_coords == h2.chunk_coords
