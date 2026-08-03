from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from radar.core.llm.agent_runtime import AgentResult, TraceStep
from radar.core.services import grounded_strategy
from radar.core.services.explore_agent import ExploreAgent

pytestmark = pytest.mark.unit


def _payload(*, name: str = "Edital Atual", kind: str = "edital") -> dict:
    return {
        "status": "ok",
        "generation_id": 9,
        "results_by_type": {
            "edital": [{
                "id": "edital:finep:1", "name": name, "kind": kind,
                "evidence": {"supporting_facts": [{
                    "source": "edital:finep:1", "target": "setor:agro",
                    "predicate": "tem_setor", "origin": "phase1_deterministic",
                }], "derived_steps": []},
            }],
            "programa": [], "agencia": [], "ict": [], "investidor": [],
        },
        "coverage": {}, "truncated": False,
    }


def test_synthesis_rejects_unknown_id_kind_and_facts():
    with pytest.raises((ValidationError, ValueError)):
        grounded_strategy.validate_synthesis({"selections": [{
            "id": "edital:inventado", "kind": "edital",
            "action": "evaluate_opportunity", "fact_refs": [],
        }]}, _payload())
    with pytest.raises(ValueError):
        grounded_strategy.validate_synthesis({"selections": [{
            "id": "edital:finep:1", "kind": "ict",
            "action": "contact_ict", "fact_refs": [],
        }]}, _payload())
    with pytest.raises(ValueError):
        grounded_strategy.validate_synthesis({"selections": [{
            "id": "edital:finep:1", "kind": "edital",
            "action": "evaluate_opportunity", "fact_refs": ["unknown"],
        }]}, _payload())


def test_grounded_response_uses_current_payload_not_history_or_invented_names(monkeypatch):
    monkeypatch.setattr(
        grounded_strategy, "resolve_temporal",
        lambda payload, db=None: ({"edital:finep:1": grounded_strategy.ValidityState.ACTIVE}, {"active": 1}),
    )
    answer, meta = grounded_strategy.grounded_response(json.dumps(_payload()))
    assert "Edital Atual" in answer
    assert "UFSC" not in answer and "INPE" not in answer
    assert meta["called_tools"] == ["graph_strategy"]
    assert "payload" not in meta


def test_invalid_payload_falls_back_without_facts():
    answer, meta = grounded_strategy.grounded_response("not-json")
    assert "Falha ao consultar" in answer
    assert meta["deterministic_fallback"] is True


def test_derived_step_is_not_a_supporting_fact():
    payload = _payload()
    payload["results_by_type"]["edital"][0]["evidence"] = {
        "supporting_facts": [],
        "derived_steps": [{"source": "edital:finep:1", "target": "empresa:efemera",
                            "predicate": "similar_a", "origin": "phase1_similarity"}],
    }
    answer, _meta = grounded_strategy.grounded_response(json.dumps(payload))
    assert "Edital Atual" in answer
    assert "relação derivada" in answer
    assert "Fatos confirmados" in answer


def test_structured_failure_statuses_are_not_empty_results():
    for status, expected in {
        "unavailable": "indisponível",
        "error": "Falha ao consultar",
        "insufficient_profile_anchors": "âncoras suficientes",
        "invalid_request": "inválido",
    }.items():
        answer, meta = grounded_strategy.grounded_response(json.dumps({"status": status}))
        assert expected in answer
        assert meta["status"] == status


def test_temporal_states_render_active_review_and_exclude_closed(monkeypatch):
    payload = _payload()
    payload["results_by_type"]["edital"].append({
        "id": "edital:finep:2", "name": "Edital Encerrado", "kind": "edital",
        "evidence": payload["results_by_type"]["edital"][0]["evidence"],
    })
    payload["status"] = "ok"
    states = {
        "edital:finep:1": grounded_strategy.ValidityState.NEEDS_REVIEW,
        "edital:finep:2": grounded_strategy.ValidityState.CLOSED,
    }
    monkeypatch.setattr(grounded_strategy, "resolve_temporal", lambda payload, db=None: (states, {}))
    answer, _meta = grounded_strategy.grounded_response(json.dumps(payload))
    assert "validade a confirmar" in answer
    assert "Edital Atual" in answer
    assert "Edital Encerrado" not in answer


def test_temporal_filter_applies_to_structural_graph_payloads(monkeypatch):
    monkeypatch.setattr(grounded_strategy, "resolve_temporal", lambda payload, db=None: ({
        "edital:finep:closed": grounded_strategy.ValidityState.CLOSED,
        "edital:finep:review": grounded_strategy.ValidityState.NEEDS_REVIEW,
    }, {}))
    raw = json.dumps({
        "nodes": [
            {"id": "edital:finep:closed", "kind": "edital"},
            {"id": "edital:finep:review", "kind": "edital"},
        ],
        "edges": [{"source": "edital:finep:closed", "target": "ict:x"}],
    })
    out = json.loads(grounded_strategy.temporalize_tool_payload(raw))
    assert [node["id"] for node in out["nodes"]] == ["edital:finep:review"]
    assert out["nodes"][0]["temporal_note"] == "validade a confirmar"
    assert out["edges"] == []


def test_flag_off_factual_question_uses_safe_prompt_without_legacy_tools(monkeypatch):
    monkeypatch.delenv("KG_PHASE1_EXPLORE_ENABLED", raising=False)
    captured = {}

    def fake_run(**kwargs):
        captured.update(kwargs)
        return AgentResult("O grafo está indisponível.", [], "end_turn", {})

    monkeypatch.setattr(
        "radar.core.llm.agent_runtime.run_agent",
        fake_run,
    )
    answer, meta = ExploreAgent().explore_with_meta("quais ICTs existem?", profile={"nome": "iFlorestal"})
    assert answer == "O grafo está indisponível."
    assert meta["called_tools"] == []
    assert captured["tools"] == []
    assert "não invente fatos atuais" in captured["system"]
    assert "explore_opportunity" not in captured["system"]


def test_conceptual_and_greeting_never_call_graph(monkeypatch):
    monkeypatch.setenv("KG_PHASE1_EXPLORE_ENABLED", "true")
    def fake_run(**kwargs):
        text = "Olá!" if kwargs["initial_messages"][-1]["content"] == "oi" else "Subvenção é um recurso público não reembolsável."
        return AgentResult(text, [], "end_turn", {})
    monkeypatch.setattr("radar.core.llm.agent_runtime.run_agent", fake_run)
    service = ExploreAgent()
    answer, meta = service.explore_with_meta("o que é subvenção?", profile={"nome": "iFlorestal"})
    assert "recurso público" in answer
    assert meta["called_tools"] == []
    greeting, greeting_meta = service.explore_with_meta("oi", profile={"nome": "iFlorestal"})
    assert greeting.startswith("Olá!")
    assert greeting_meta["called_tools"] == []


def test_explore_active_calls_graph_once_and_ignores_history(monkeypatch):
    calls = []

    class FakeTool:
        name = "graph_strategy"

        def invoke(self, args):
            calls.append(args)
            return json.dumps(_payload())

    monkeypatch.setenv("KG_PHASE1_EXPLORE_ENABLED", "true")
    monkeypatch.setattr("radar.core.kg.phase1.tools.build_graph_tools", lambda **_: [FakeTool()])
    monkeypatch.setattr(
        grounded_strategy, "resolve_temporal",
        lambda payload, db=None: ({"edital:finep:1": grounded_strategy.ValidityState.ACTIVE}, {"active": 1}),
    )
    def fake_run(**kwargs):
        output = kwargs["tools"][0].invoke({"requested_types": ["edital"]})
        return AgentResult(
            "Edital Atual [edital:finep:1]", [TraceStep(kind="tool", name="graph_strategy", output=output)],
            "end_turn", {},
        )
    monkeypatch.setattr("radar.core.llm.agent_runtime.run_agent", fake_run)
    answer, meta = ExploreAgent().explore_with_meta(
        "quais oportunidades existem?", history=[{"role": "assistant", "content": "INPE"}],
        profile={"nome": "iFlorestal"},
    )
    assert calls == [{}]
    assert "INPE" not in answer
    assert meta["called_tools"] == ["graph_strategy"]


def test_explore_stream_has_no_tokens_before_safe_final(monkeypatch):
    class FakeTool:
        name = "graph_strategy"

        def invoke(self, args):
            return json.dumps(_payload())

    monkeypatch.setenv("KG_PHASE1_EXPLORE_ENABLED", "true")
    monkeypatch.setattr("radar.core.kg.phase1.tools.build_graph_tools", lambda **_: [FakeTool()])
    monkeypatch.setattr(
        grounded_strategy, "resolve_temporal",
        lambda payload, db=None: ({"edital:finep:1": grounded_strategy.ValidityState.ACTIVE}, {"active": 1}),
    )
    async def fake_stream(**kwargs):
        output = kwargs["tools"][0].invoke({"requested_types": ["edital"]})
        yield SimpleNamespace(kind="done", result=AgentResult(
            "Edital Atual [edital:finep:1]",
            [TraceStep(kind="tool", name="graph_strategy", output=output)],
            "end_turn", {},
        ))
    monkeypatch.setattr("radar.core.llm.agent_runtime.run_agent_streaming_async", fake_stream)
    events = asyncio.run(_collect(ExploreAgent().explore_stream(
        "quais oportunidades existem?", profile={"nome": "iFlorestal"},
    )))
    assert [event.kind for event in events] == ["token", "final"]
    assert "Edital Atual" in events[0].text
    assert events[1].answer == events[0].text


async def _collect(iterator):
    return [event async for event in iterator]
