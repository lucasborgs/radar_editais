"""Contratos herméticos da exploração profile-first KG-P1C."""
from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest
from pydantic import ValidationError

from radar.core.kg.phase1 import store, tools
from radar.core.kg.phase1.store import Snapshot
from radar.core.llm.agent_runtime import AgentResult
from radar.core.services.explore_agent import ExploreAgent
from radar.domain.profile_schema import CompanyProfilePayload

pytestmark = pytest.mark.unit


CANONICAL_PROFILE = {
    "nome": "iFlorestal",
    "tipo_entidade": "empresa",
    "one_liner": "Soluções para o setor agro com inteligência artificial aplicada à floresta.",
    "solution_summary": "Monitoramento e restauração florestal com inteligência artificial.",
    "descricao_atividades": "Desenvolvimento de tecnologia para manejo sustentável e recuperação de áreas.",
    "uf": "SC",
    "trl": 5,
    "estagio": "seed",
    "tipos_financiamento_interesse": ["subvencao_nao_reembolsavel", "capital_risco"],
}


def edge(source, target, type_, origin="phase1_deterministic", weight=1.0, props=None):
    return {"source_id": source, "target_id": target, "type": type_,
            "origin": origin, "weight": weight, "properties": props or {}}


def canonical_snapshot() -> Snapshot:
    nodes = [
        {"id": "edital:e:1", "kind": "edital", "native_id": "e:1", "name": "Edital Agro", "description": ""},
        {"id": "edital:e:2", "kind": "edital", "native_id": "e:2", "name": "Edital Parcial", "description": ""},
        {"id": "programa:p:1", "kind": "programa", "native_id": "p:1", "name": "Programa Verde", "description": ""},
        {"id": "agencia:a:1", "kind": "agencia", "native_id": "a:1", "name": "Agência A", "description": ""},
        {"id": "agencia:a:2", "kind": "agencia", "native_id": "a:2", "name": "Agência Parcial", "description": ""},
        {"id": "ict:i:1", "kind": "ict", "native_id": "ICT Floresta Completa", "description": "", "name": "ICT Floresta Completa"},
        {"id": "ict:i:2", "kind": "ict", "native_id": "i:2", "name": "ICT Agro Parcial", "description": ""},
        {"id": "investidor:v:1", "kind": "investidor", "native_id": "v:1", "name": "Fundo Verde", "description": ""},
    ]
    quality = [
        {"id": "setor:agro", "family": "setor", "value": "Agro"},
        {"id": "tecnologia:ia", "family": "tecnologia", "value": "Inteligência Artificial"},
        {"id": "uf:sc", "family": "uf", "value": "SC"},
        {"id": "estagio:seed", "family": "estagio", "value": "seed"},
        {"id": "mecanismo:subvencao", "family": "mecanismo", "value": "Subvenção"},
    ]
    edges = [
        edge("edital:e:1", "setor:agro", "tem_setor"),
        edge("edital:e:1", "tecnologia:ia", "tem_tecnologia"),
        edge("edital:e:1", "uf:sc", "tem_uf"),
        edge("edital:e:1", "programa:p:1", "subordinado_a", "phase1_structural"),
        edge("edital:e:1", "agencia:a:1", "operado_por", "phase1_structural"),
        edge("edital:e:2", "setor:agro", "tem_setor"),
        edge("edital:e:2", "agencia:a:2", "operado_por", "phase1_structural"),
        edge("ict:i:1", "setor:agro", "tem_setor"),
        edge("ict:i:1", "tecnologia:ia", "tem_tecnologia"),
        edge("ict:i:1", "uf:sc", "tem_uf"),
        edge("ict:i:2", "setor:agro", "tem_setor"),
        edge("investidor:v:1", "tecnologia:ia", "tem_tecnologia"),
        edge("edital:e:1", "investidor:v:1", "similar_a", "phase1_similarity", 0.9, {"derived": True}),
    ]
    return Snapshot(7, nodes, quality, edges, {})


def graph_tool(monkeypatch, profile=None, snap=None):
    monkeypatch.setenv("KG_PHASE1_EXPLORE_ENABLED", "true")
    actual = snap if snap is not None else canonical_snapshot()
    monkeypatch.setattr(store, "load_snapshot", lambda: actual)
    return tools.build_graph_tools(profile=profile)[0]


def invoke(monkeypatch, profile=None, snap=None, requested=None):
    raw = graph_tool(monkeypatch, profile, snap).invoke(
        {} if requested is None else {"requested_types": requested},
    )
    return json.loads(raw)


def test_golden_profile_is_canonical_and_has_no_extra_fields():
    validated = CompanyProfilePayload.model_validate(CANONICAL_PROFILE)
    assert validated.nome == "iFlorestal"
    with pytest.raises(ValidationError):
        CompanyProfilePayload.model_validate({**CANONICAL_PROFILE, "setores": ["Agro"]})


def test_text_projection_uses_existing_sector_and_tag_aliases(monkeypatch):
    out = invoke(monkeypatch, CANONICAL_PROFILE)
    projected = out["profile"]["projected"]
    assert {item["node_id"] for item in projected} >= {"setor:agro", "tecnologia:ia"}
    assert {item["source_field"] for item in projected} <= {
        "one_liner", "solution_summary", "descricao_atividades", "portfolio_projetos",
    }


def test_text_without_existing_quality_match_remains_unresolved(monkeypatch):
    profile = CompanyProfilePayload.model_validate({
        "nome": "Empresa sem taxonomia", "one_liner": "Serviços administrativos gerais.",
    }).model_dump()
    out = invoke(monkeypatch, profile)
    assert out["profile"]["projected"] == []
    assert any(item["field"] == "one_liner" for item in out["profile"]["unresolved"])
    assert out["status"] == "insufficient_profile_anchors"


def test_declared_and_projected_attributes_are_separate(monkeypatch):
    out = invoke(monkeypatch, CompanyProfilePayload.model_validate(CANONICAL_PROFILE).model_dump())
    assert any(item["field"] == "uf" for item in out["profile"]["declared"])
    assert all("source_field" in item for item in out["profile"]["projected"])


def test_three_shared_signals_are_aggregated_even_when_path_uses_one(monkeypatch):
    out = invoke(monkeypatch, CANONICAL_PROFILE)
    ict = next(item for item in out["results_by_type"]["ict"] if item["id"] == "ict:i:1")
    assert {item["node_id"] for item in ict["shared_characteristics"]} >= {
        "setor:agro", "tecnologia:ia", "uf:sc",
    }
    facts = ict["evidence"]["supporting_facts"]
    assert {fact["predicate"] for fact in facts} >= {"tem_setor", "tem_tecnologia", "tem_uf"}
    assert all(fact["via_entity_id"] == "ict:i:1" for fact in facts)


def test_structural_candidates_inherit_signals_from_intermediate_opportunity(monkeypatch):
    out = invoke(monkeypatch, CANONICAL_PROFILE, requested=["programa", "agencia"])
    program = out["results_by_type"]["programa"][0]
    agency = out["results_by_type"]["agencia"][0]
    for result in (program, agency):
        assert {item["node_id"] for item in result["shared_characteristics"]} >= {
            "setor:agro", "tecnologia:ia", "uf:sc",
        }
        assert all(item["via_entity_id"] == "edital:e:1"
                   for item in result["shared_characteristics"])


def test_agency_with_inherited_three_signals_ranks_above_partial_agency(monkeypatch):
    out = invoke(monkeypatch, CANONICAL_PROFILE, requested=["agencia"])
    assert [result["id"] for result in out["results_by_type"]["agencia"]][:2] == [
        "agencia:a:1", "agencia:a:2",
    ]


def test_more_shared_signals_rank_before_one_signal(monkeypatch):
    out = invoke(monkeypatch, CANONICAL_PROFILE, requested=["ict"])
    assert [item["id"] for item in out["results_by_type"]["ict"]][:2] == ["ict:i:1", "ict:i:2"]


def test_profile_route_is_derived_but_internal_attributes_are_facts(monkeypatch):
    out = invoke(monkeypatch, CANONICAL_PROFILE, requested=["ict"])
    ict = next(item for item in out["results_by_type"]["ict"] if item["id"] == "ict:i:1")
    assert ict["evidence"]["route_relation"] == {
        "classification": "derived_profile_route", "confirmed": False,
    }
    assert any(fact["origin"] == "phase1_deterministic" and fact["confirmed"]
               for fact in ict["evidence"]["supporting_facts"])


def test_profile_to_opportunity_to_agency_keeps_structural_supporting_fact(monkeypatch):
    out = invoke(monkeypatch, CANONICAL_PROFILE, requested=["agencia"])
    agency = out["results_by_type"]["agencia"][0]
    assert agency["evidence"]["route_relation"]["confirmed"] is False
    fact = next(f for f in agency["evidence"]["supporting_facts"] if f["predicate"] == "operado_por")
    assert fact["origin"] == "phase1_structural"
    assert fact["confirmed"] is True


def test_inverse_traversal_preserves_factual_edge_direction():
    snap = canonical_snapshot()
    edge_index = tools._edge_index(snap.edges)
    step = tools._path_entry(
        [("setor:agro", "tem_setor", "edital:e:1")], edge_index,
    )[0]
    assert step["traversal_from"] == "setor:agro"
    assert step["traversal_to"] == "edital:e:1"
    assert step["source"] == "edital:e:1"
    assert step["target"] == "setor:agro"


def test_no_quality_fact_is_emitted_in_traversal_direction(monkeypatch):
    out = invoke(monkeypatch, CANONICAL_PROFILE, requested=["programa"])
    for fact in out["results_by_type"]["programa"][0]["evidence"]["supporting_facts"]:
        if fact["predicate"] == "tem_setor":
            assert fact["source"] == "edital:e:1"
            assert fact["target"] == "setor:agro"


def test_derived_graph_step_is_never_confirmed(monkeypatch):
    out = invoke(monkeypatch, CANONICAL_PROFILE, requested=["investidor"])
    investor = out["results_by_type"]["investidor"][0]
    assert all(not step["confirmed"] for step in investor["evidence"]["derived_steps"])


def test_requested_kinds_are_structured_and_invalid_is_rejected(monkeypatch):
    out = invoke(monkeypatch, CANONICAL_PROFILE, requested=["agencia", "ict"])
    assert out["coverage"]["agencia"]["queried"] is True
    assert out["coverage"]["edital"]["queried"] is False
    monkeypatch.setenv("KG_PHASE1_EXPLORE_ENABLED", "true")
    monkeypatch.setattr(store, "load_snapshot", canonical_snapshot)
    raw = tools.build_graph_tools(profile=CANONICAL_PROFILE)[0].invoke({"requested_types": ["agências"]})
    assert "Kind inválido" in raw or "kind não suportado" in raw


def test_coverage_never_turns_unqueried_into_negative_claim(monkeypatch):
    out = invoke(monkeypatch, CANONICAL_PROFILE, requested=["ict"])
    assert out["coverage"]["programa"]["status"] == "not_queried"
    assert "inexistência" in " ".join(out["limitations"])


def test_routes_are_identical_under_input_shuffle(monkeypatch):
    first = invoke(monkeypatch, CANONICAL_PROFILE)
    original = canonical_snapshot()
    shuffled = Snapshot(original.generation_id, list(reversed(original.nodes)),
                        list(reversed(original.quality_nodes)), list(reversed(original.edges)), {})
    assert first == invoke(monkeypatch, CANONICAL_PROFILE, shuffled)


def test_routes_are_identical_under_different_pythonhashseeds():
    code = """
import json
from tests.unit.test_kg_phase1_explore_tools import CANONICAL_PROFILE, canonical_snapshot
from radar.core.kg.phase1.tools import strategy_payload
print(json.dumps(strategy_payload(CANONICAL_PROFILE, canonical_snapshot()).payload, ensure_ascii=False, sort_keys=True))
"""
    outputs = []
    for seed in ("1", "987654"):
        env = {**os.environ, "PYTHONHASHSEED": seed, "PYTHONPATH": "src"}
        result = subprocess.run([sys.executable, "-c", code], capture_output=True,
                                text=True, cwd=os.getcwd(), env=env, check=True)
        outputs.append(result.stdout)
    assert outputs[0] == outputs[1]


def test_snapshot_unavailable_is_honest_without_legacy_fallback(monkeypatch):
    monkeypatch.setenv("KG_PHASE1_EXPLORE_ENABLED", "true")
    monkeypatch.setattr(store, "load_snapshot", lambda: None)
    raw = tools.build_graph_tools(profile=CANONICAL_PROFILE)[0].invoke({})
    assert "indisponível" in raw
    assert "search_entities" not in raw


def test_database_failure_is_sanitized(monkeypatch, caplog):
    import psycopg
    monkeypatch.setenv("KG_PHASE1_EXPLORE_ENABLED", "true")
    monkeypatch.setattr(store, "load_snapshot", lambda: (_ for _ in ()).throw(
        psycopg.OperationalError("postgresql://secret SELECT password"),
    ))
    raw = tools.build_graph_tools(profile=CANONICAL_PROFILE)[0].invoke({})
    assert "secret" not in raw
    assert "postgresql://" not in caplog.text


def test_payload_is_valid_utf8_and_bounded(monkeypatch):
    snap = canonical_snapshot()
    huge = {**snap.nodes[0], "name": "é" * 50_000}
    snap = Snapshot(snap.generation_id, [huge, *snap.nodes[1:]], snap.quality_nodes, snap.edges, {})
    raw = graph_tool(monkeypatch, CANONICAL_PROFILE, snap).invoke({})
    assert len(raw.encode("utf-8")) <= tools.MAX_PAYLOAD_BYTES
    json.loads(raw)


def test_hub_weight_does_not_expand_strategy(monkeypatch):
    snap = canonical_snapshot()
    snap = Snapshot(snap.generation_id, snap.nodes,
                    [*snap.quality_nodes, {"id": "setor:multissetorial", "family": "setor", "value": "Multissetorial"}],
                    [*snap.edges, edge("ict:i:2", "setor:multissetorial", "tem_setor", weight=0.1)], {})
    out = invoke(monkeypatch, CANONICAL_PROFILE, snap, ["ict"])
    assert all(item["id"] != "setor:multissetorial" for item in out["results_by_type"]["ict"])


def test_flag_off_preserves_previous_tools_and_system(monkeypatch):
    monkeypatch.delenv("KG_PHASE1_EXPLORE_ENABLED", raising=False)
    monkeypatch.delenv("EXPLORE_DEEP_RESEARCH_ENABLED", raising=False)
    captured = {}
    monkeypatch.setattr("radar.core.llm.agent_runtime.run_agent",
                        lambda **kw: captured.update(kw) or AgentResult("ok", [], "end_turn", {}))
    ExploreAgent().explore_with_meta(
        "quais caminhos?", profile_text="perfil", profile=CANONICAL_PROFILE,
        workspace_id="workspace", db=object(),
    )
    assert captured["tools"] == []
    assert "find_matching_editais" not in captured["system"]
    assert "graph_strategy" not in captured["tools"]


def test_flag_on_is_exclusive(monkeypatch):
    captured = {}
    monkeypatch.setenv("KG_PHASE1_EXPLORE_ENABLED", "true")
    def fake_run(**kw):
        captured.setdefault("agent", kw)
        return AgentResult("ok", [], "end_turn", {})
    monkeypatch.setattr("radar.core.llm.agent_runtime.run_agent", fake_run)
    answer, meta = ExploreAgent().explore_with_meta(
        "quais oportunidades existem?", profile_text="perfil", profile=CANONICAL_PROFILE,
    )
    assert {tool.name for tool in captured["agent"]["tools"]} == {
        "graph_strategy", "graph_explore", "graph_reason", "graph_community",
    }
    assert captured["agent"]["system"] == ExploreAgent._explore_system()
    assert "explore_opportunity" not in captured["agent"]["system"]
    assert "supporting_facts" in captured["agent"]["system"]
    assert "find_matching_editais" not in meta["called_tools"]
    assert answer
