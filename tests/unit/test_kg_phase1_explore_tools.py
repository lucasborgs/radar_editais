"""Contratos herméticos da exploração profile-first KG-P1C."""
from __future__ import annotations

import json

import pytest

from radar.core.kg.phase1 import store, tools
from radar.core.kg.phase1.store import Snapshot
from radar.core.llm.agent_runtime import AgentResult
from radar.core.services.explore_agent import ExploreAgent

pytestmark = pytest.mark.unit


def edge(source, target, type_, origin="phase1_deterministic", weight=1.0, props=None):
    return {"source_id": source, "target_id": target, "type": type_,
            "origin": origin, "weight": weight, "properties": props or {}}


def snapshot():
    nodes = [
        {"id": "edital:e:1", "kind": "edital", "native_id": "e:1", "name": "Edital Agro", "description": ""},
        {"id": "programa:p:1", "kind": "programa", "native_id": "p:1", "name": "Programa Verde", "description": ""},
        {"id": "agencia:a:1", "kind": "agencia", "native_id": "a:1", "name": "Agência A", "description": ""},
        {"id": "ict:i:1", "kind": "ict", "native_id": "i:1", "name": "ICT Floresta", "description": ""},
        {"id": "investidor:v:1", "kind": "investidor", "native_id": "v:1", "name": "Fundo Verde", "description": ""},
    ]
    quality = [
        {"id": "setor:agro", "family": "setor", "value": "Agro"},
        {"id": "tecnologia:ia", "family": "tecnologia", "value": "IA"},
        {"id": "uf:sc", "family": "uf", "value": "SC"},
        {"id": "estagio:seed", "family": "estagio", "value": "seed"},
        {"id": "mecanismo:subvencao", "family": "mecanismo", "value": "Subvenção"},
    ]
    edges = [
        edge("edital:e:1", "setor:agro", "tem_setor"), edge("edital:e:1", "tecnologia:ia", "tem_tecnologia"),
        edge("edital:e:1", "uf:sc", "tem_uf"), edge("edital:e:1", "mecanismo:subvencao", "usa_mecanismo"),
        edge("ict:i:1", "setor:agro", "tem_setor"), edge("ict:i:1", "tecnologia:ia", "tem_tecnologia"),
        edge("investidor:v:1", "tecnologia:ia", "tem_tecnologia"),
        edge("edital:e:1", "programa:p:1", "subordinado_a", "phase1_structural"),
        edge("edital:e:1", "agencia:a:1", "operado_por", "phase1_structural"),
        edge("edital:e:1", "investidor:v:1", "similar_a", "phase1_similarity", 0.9, {"derived": True}),
    ]
    return Snapshot(7, nodes, quality, edges, {})


def tool(monkeypatch, profile=None, snap=None):
    monkeypatch.setenv("KG_PHASE1_EXPLORE_ENABLED", "true")
    monkeypatch.setattr(store, "load_snapshot", lambda: snap or snapshot())
    return tools.build_graph_tools(profile=profile)[0]


def invoke(monkeypatch, profile=None, snap=None, requested=""):
    return json.loads(tool(monkeypatch, profile, snap).invoke({"requested_types": requested}))


def test_flag_off_is_byte_compatible_and_flag_on_is_exclusive(monkeypatch):
    monkeypatch.delenv("KG_PHASE1_EXPLORE_ENABLED", raising=False)
    before = {t.name for t in ExploreAgent()._explore_tools(profile={})}
    assert "graph_strategy" not in before
    monkeypatch.setenv("KG_PHASE1_EXPLORE_ENABLED", "true")
    after = {t.name for t in ExploreAgent()._explore_tools(profile={})}
    assert after == {"graph_strategy"}


def test_strategy_profile_is_closure_not_llm_argument(monkeypatch):
    t = tool(monkeypatch, {"uf": "SC"})
    assert "profile" not in t.args
    assert set(t.args) == {"requested_types"}


def test_profile_resolves_multiple_quality_anchors_and_no_entity_start(monkeypatch):
    out = invoke(monkeypatch, {"uf": "SC", "setores": ["Agro"], "tecnologias_tags": ["IA"]})
    assert {x["node_id"] for x in out["profile"]["recognized"]} == {"uf:sc", "setor:agro", "tecnologia:ia"}
    assert out["results_by_type"]["edital"]
    assert out["results_by_type"]["ict"]


def test_all_requested_types_are_grouped_and_explainable(monkeypatch):
    out = invoke(monkeypatch, {"uf": "SC", "setores": ["Agro"], "tecnologias_tags": ["IA"]})
    assert set(out["results_by_type"]) == {"edital", "programa", "agencia", "ict", "investidor"}
    for results in out["results_by_type"].values():
        for result in results:
            assert result["path"]
            assert result["relation"]["classification"]


def test_routes_are_deterministic_under_input_shuffle(monkeypatch):
    first = invoke(monkeypatch, {"uf": "SC", "setores": ["Agro"], "tecnologias_tags": ["IA"]})
    shuffled = snapshot()
    shuffled = Snapshot(7, list(reversed(shuffled.nodes)), list(reversed(shuffled.quality_nodes)),
                        list(reversed(shuffled.edges)), {})
    second = invoke(monkeypatch, {"uf": "SC", "setores": ["Agro"], "tecnologias_tags": ["IA"]}, shuffled)
    assert first == second


def test_facts_attributes_and_derived_relations_are_distinct(monkeypatch):
    out = invoke(monkeypatch, {"setores": ["Agro"], "tecnologias_tags": ["IA"]})
    assert out["results_by_type"]["programa"][0]["relation"]["classification"] == "catalog_structural_fact"
    assert out["results_by_type"]["ict"][0]["relation"]["classification"] == "cataloged_attribute"
    assert out["results_by_type"]["investidor"][0]["relation"]["classification"] in {
        "cataloged_attribute", "derived_relation"
    }


def test_unknown_attribute_is_unresolved_and_never_fuzzy_matched(monkeypatch):
    out = invoke(monkeypatch, {"setores": ["Agroflorestal desconhecido"], "solution_summary": "IA para floresta"})
    assert any(x["field"] == "setores" for x in out["profile"]["unresolved"])
    assert any(x["field"] == "solution_summary" for x in out["profile"]["unresolved"])
    assert out["status"] == "insufficient_profile_anchors"
    assert all(not values for values in out["results_by_type"].values())


def test_coverage_distinguishes_absence_from_not_queried(monkeypatch):
    out = invoke(monkeypatch, {"setores": ["Agro"]}, requested="ict,agencias")
    assert out["coverage"]["ict"]["queried"] is True
    assert out["coverage"]["programa"]["queried"] is False
    assert "inexistência" in out["limitations"][0] or any("inexistência" in x for x in out["limitations"])


def test_unavailable_snapshot_is_honest_and_has_no_legacy_fallback(monkeypatch):
    monkeypatch.setenv("KG_PHASE1_EXPLORE_ENABLED", "true")
    monkeypatch.setattr(store, "load_snapshot", lambda: None)
    out = tools.build_graph_tools(profile={"uf": "SC"})[0].invoke({})
    assert "indisponível" in out
    assert "get_edital" not in out and "search_entities" not in out


def test_payload_is_utf8_bounded_and_explicitly_truncated(monkeypatch):
    snap = snapshot()
    huge = {**snap.nodes[0], "name": "é" * 50_000}
    snap = Snapshot(snap.generation_id, [huge, *snap.nodes[1:]], snap.quality_nodes, snap.edges, {})
    out = tool(monkeypatch, {"setores": ["Agro"]}, snap).invoke({})
    assert len(out.encode("utf-8")) <= tools.MAX_PAYLOAD_BYTES
    assert json.loads(out)


def test_agent_active_mode_does_not_inject_match_or_catalog(monkeypatch):
    monkeypatch.setenv("KG_PHASE1_EXPLORE_ENABLED", "true")
    captured = {}
    monkeypatch.setattr("radar.core.llm.agent_runtime.run_agent",
                        lambda **kw: captured.update(kw) or AgentResult("ok", [], "end_turn", {}))
    ExploreAgent().explore_with_meta("quais caminhos?", profile_text="perfil", profile={"uf": "SC"})
    assert [t.name for t in captured["tools"]] == ["graph_strategy"]
    assert "graph_strategy" in captured["system"]
    assert "find_matching_editais" not in captured["system"]


def test_agent_active_mode_with_missing_profile_does_not_fabricate(monkeypatch):
    captured = {}
    monkeypatch.setenv("KG_PHASE1_EXPLORE_ENABLED", "true")
    monkeypatch.setattr("radar.core.llm.agent_runtime.run_agent",
                        lambda **kw: captured.update(kw) or AgentResult("ok", [], "end_turn", {}))
    ExploreAgent().explore_with_meta("o que existe?", profile=None)
    assert [t.name for t in captured["tools"]] == ["graph_strategy"]
