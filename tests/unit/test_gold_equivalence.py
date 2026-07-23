"""RT01-T02 — comparador de equivalência do gold + baseline congelado.

Cobre `radar.core.kg.equivalence` (comparador puro, sem I/O) e a captura real
via `tests/helpers/gold_projection.py` (harness que roda `gold.ingest_all`
real sobre `tests/fixtures/gold_equivalence/`, com os produtores de rede/LLM
stubados). Hermético: sem rede, sem banco — spec
docs/specs/radar-data-trust-01-provenance.md §9.3.

Proporcionalidade pré-beta: cada classe de divergência (escalar, array,
constraint, metadata, entidade ausente/extra, relação ausente/extra, chunk
texto/coordenada) é coberta uma vez, não em variações redundantes.
"""
from __future__ import annotations

import copy
import json

from helpers.gold_projection import DEFAULT_FIXTURES_DIR, run_capture

from radar.core.kg import equivalence

# ---------------------------------------------------------------------------
# Projeção sintética mínima para os testes do comparador (não usa o harness —
# só a forma do contrato de `equivalence.py`).
# ---------------------------------------------------------------------------


def _entity(**overrides) -> dict:
    base = dict(
        kind="edital", source="finep", native_id="finep:1",
        name="Edital Exemplo", description="descrição do edital",
        mecanismo="subvencao", formato="edital_periodico",
        setores=["Multissetorial"], tecnologias_tags=["ia"],
        status="aberta", deadline="2026-12-31", uf=None,
        ticket_min=None, ticket_max=None,
        constraints=[{"tipo": "porte", "op": "in", "valor": ["mei"]}],
        requisitos_texto=["requisito 1"], curated=False, verificado_em=None,
        metadata={"url": "https://example.org/edital"},
        embedding_input_hash="a" * 64, embedding_model="stub-embedder-v1", embedding_dim=8,
    )
    base.update(overrides)
    return equivalence.make_entity_record(**base)


def _base_projection() -> equivalence.GoldProjection:
    proj = equivalence.GoldProjection()
    ek = proj.add_entity(_entity())
    agency_key = proj.add_entity(_entity(
        kind="agencia", source="curadoria", native_id="agencia:finep",
        name="FINEP", description="FINEP", mecanismo=None, formato=None,
        setores=[], tecnologias_tags=[], status=None, deadline=None,
        constraints=[], requisitos_texto=[], curated=True, metadata={},
    ))
    proj.add_relation(equivalence.make_relation_record(
        source_entity_key=ek, target_entity_key=agency_key,
        type="operado_por", properties={},
    ))
    proj.set_match_chunks(ek, [
        equivalence.make_chunk_record(
            idx=0, section_path=["1. Objeto"], kind="paragraph", text="bloco 1",
            embedding_input_hash="b" * 64, embedding_model="stub-embedder-v1", embedding_dim=8,
        ),
        equivalence.make_chunk_record(
            idx=1, section_path=["2. Elegibilidade"], kind="paragraph", text="bloco 2",
            embedding_input_hash="c" * 64, embedding_model="stub-embedder-v1", embedding_dim=8,
        ),
    ])
    return proj


def _mutate(proj: equivalence.GoldProjection) -> dict:
    """Cópia profunda do dict serializável da projeção, pronta para mutação
    pontual num teste."""
    return copy.deepcopy(proj.to_dict())


def _only_entity_key(d: dict) -> str:
    (ek,) = [k for k in d["entities"] if d["entities"][k]["kind"] == "edital"]
    return ek


# ---------------------------------------------------------------------------
# Comparador: cada classe de divergência
# ---------------------------------------------------------------------------


class TestDiffProjections:
    def test_identical_projections_have_no_divergence(self):
        old = _base_projection()
        new = equivalence.GoldProjection.from_dict(json.loads(old.to_json()))
        assert equivalence.diff_projections(old, new) == []

    def test_detects_scalar_field_change(self):
        old = _base_projection()
        new_d = _mutate(old)
        new_d["entities"][_only_entity_key(new_d)]["status"] = "encerrada"
        divs = equivalence.diff_projections(old, equivalence.GoldProjection.from_dict(new_d))
        assert any(d.category == "entity_field" and d.field == "status" for d in divs)

    def test_detects_array_field_change(self):
        old = _base_projection()
        new_d = _mutate(old)
        new_d["entities"][_only_entity_key(new_d)]["tecnologias_tags"] = ["ia", "biotecnologia"]
        divs = equivalence.diff_projections(old, equivalence.GoldProjection.from_dict(new_d))
        assert any(d.category == "entity_field" and d.field == "tecnologias_tags" for d in divs)

    def test_detects_constraint_change(self):
        old = _base_projection()
        new_d = _mutate(old)
        new_d["entities"][_only_entity_key(new_d)]["constraints"] = [
            {"tipo": "porte", "op": "in", "valor": ["mei", "me"]}
        ]
        divs = equivalence.diff_projections(old, equivalence.GoldProjection.from_dict(new_d))
        assert any(d.category == "entity_field" and d.field == "constraints" for d in divs)

    def test_detects_metadata_change(self):
        old = _base_projection()
        new_d = _mutate(old)
        new_d["entities"][_only_entity_key(new_d)]["metadata"] = {"url": "https://example.org/outro"}
        divs = equivalence.diff_projections(old, equivalence.GoldProjection.from_dict(new_d))
        assert any(d.category == "entity_field" and d.field == "metadata" for d in divs)

    def test_detects_missing_entity(self):
        old = _base_projection()
        new_d = _mutate(old)
        del new_d["entities"][_only_entity_key(new_d)]
        divs = equivalence.diff_projections(old, equivalence.GoldProjection.from_dict(new_d))
        assert any(d.category == "entity_missing" for d in divs)

    def test_detects_extra_entity(self):
        old = _base_projection()
        new_d = _mutate(old)
        extra = _entity(kind="edital", source="finep", native_id="finep:2", name="Outro edital")
        new_d["entities"][equivalence.entity_key("edital", "finep", "finep:2")] = extra
        divs = equivalence.diff_projections(old, equivalence.GoldProjection.from_dict(new_d))
        assert any(d.category == "entity_extra" for d in divs)

    def test_detects_missing_relation(self):
        old = _base_projection()
        new_d = _mutate(old)
        new_d["relations"] = {}
        divs = equivalence.diff_projections(old, equivalence.GoldProjection.from_dict(new_d))
        assert any(d.category == "relation_missing" for d in divs)

    def test_detects_extra_relation(self):
        old = _base_projection()
        new_d = _mutate(old)
        ek = _only_entity_key(new_d)
        agency_key = equivalence.entity_key("agencia", "curadoria", "agencia:finep")
        extra_rel = equivalence.make_relation_record(
            source_entity_key=ek, target_entity_key=agency_key,
            type="subordinado_a", properties={},
        )
        rk = equivalence.relation_key(ek, agency_key, "subordinado_a")
        new_d["relations"][rk] = extra_rel
        divs = equivalence.diff_projections(old, equivalence.GoldProjection.from_dict(new_d))
        assert any(d.category == "relation_extra" for d in divs)

    def test_detects_chunk_text_change(self):
        old = _base_projection()
        new_d = _mutate(old)
        ek = _only_entity_key(new_d)
        new_d["match_chunks"][ek][0]["text"] = "bloco 1 alterado"
        divs = equivalence.diff_projections(old, equivalence.GoldProjection.from_dict(new_d))
        assert any(d.category == "chunk_field" and d.field == "text" for d in divs)

    def test_detects_chunk_coordinate_change(self):
        old = _base_projection()
        new_d = _mutate(old)
        ek = _only_entity_key(new_d)
        new_d["match_chunks"][ek][1]["section_path"] = ["3. Outra seção"]
        divs = equivalence.diff_projections(old, equivalence.GoldProjection.from_dict(new_d))
        assert any(d.category == "chunk_field" and d.field == "section_path" for d in divs)

    def test_ignore_list_entity_fields_do_not_trigger_divergence(self):
        old = _base_projection()
        new_d = _mutate(old)
        ek = _only_entity_key(new_d)
        assert equivalence.IGNORED_ENTITY_FIELDS == {"id", "created_at", "updated_at", "provenance"}
        new_d["entities"][ek]["id"] = "11111111-1111-1111-1111-111111111111"
        new_d["entities"][ek]["created_at"] = "2026-07-23T00:00:00Z"
        new_d["entities"][ek]["updated_at"] = "2026-07-23T01:00:00Z"
        new_d["entities"][ek]["provenance"] = {"deadline": {"state": "stated"}}
        divs = equivalence.diff_projections(old, equivalence.GoldProjection.from_dict(new_d))
        assert divs == []

    def test_ignore_list_relation_fields_do_not_trigger_divergence(self):
        old = _base_projection()
        new_d = _mutate(old)
        (rk,) = new_d["relations"].keys()
        new_d["relations"][rk]["id"] = "some-physical-id"
        new_d["relations"][rk]["provenance"] = {"operado_por": {"state": "stated"}}
        divs = equivalence.diff_projections(old, equivalence.GoldProjection.from_dict(new_d))
        assert divs == []

    def test_ignore_list_chunk_fields_do_not_trigger_divergence(self):
        old = _base_projection()
        new_d = _mutate(old)
        ek = _only_entity_key(new_d)
        new_d["match_chunks"][ek][0]["id"] = "chunk-physical-id"
        new_d["match_chunks"][ek][0]["entity_id"] = "entity-physical-id"
        divs = equivalence.diff_projections(old, equivalence.GoldProjection.from_dict(new_d))
        assert divs == []


# ---------------------------------------------------------------------------
# Baseline congelado: captura real (harness) == snapshot commitado
# ---------------------------------------------------------------------------


class TestBaselineCapture:
    def test_capture_matches_committed_baseline(self, monkeypatch):
        projection, _stats = run_capture(monkeypatch)
        baseline_path = DEFAULT_FIXTURES_DIR / "baseline_projection.json"
        baseline = equivalence.GoldProjection.from_dict(
            json.loads(baseline_path.read_text(encoding="utf-8"))
        )
        divs = equivalence.diff_projections(baseline, projection)
        assert divs == [], equivalence.render_divergences(divs)
        # Confere que a captura de fato populou algo — um diff vazio por
        # projeção vazia dos dois lados esconderia um harness quebrado.
        assert projection.entities
        assert projection.relations
        assert projection.match_chunks

    def test_two_consecutive_captures_are_deterministic(self, monkeypatch):
        proj1, _ = run_capture(monkeypatch)
        proj2, _ = run_capture(monkeypatch)
        assert proj1.to_json() == proj2.to_json()
        assert equivalence.diff_projections(proj1, proj2) == []
