from __future__ import annotations

import asyncio
import json

import pytest
from pydantic import ValidationError

from radar.core.services import grounded_strategy
from radar.core.services.explore_agent import ExploreAgent
from radar.core.services.explore_routing import ProfileStrategyRoute, profile_strategy_route

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


def test_greeting_and_conceptual_do_not_use_strategy():
    assert profile_strategy_route("oi", has_profile=True) is ProfileStrategyRoute.GREETING
    assert profile_strategy_route("o que é subvenção?", has_profile=True) is ProfileStrategyRoute.CONCEPTUAL


def test_discovery_requires_profile():
    assert profile_strategy_route("quais oportunidades existem?", has_profile=False) is ProfileStrategyRoute.NO_PROFILE_ORIENTATION
    assert profile_strategy_route("quais oportunidades existem?", has_profile=True) is ProfileStrategyRoute.PROFILE_STRATEGY


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
    assert "não indica inexistência" in answer
    assert meta["deterministic_fallback"] is True


def test_derived_step_is_not_a_supporting_fact():
    payload = _payload()
    payload["results_by_type"]["edital"][0]["evidence"] = {
        "supporting_facts": [],
        "derived_steps": [{"source": "edital:finep:1", "target": "empresa:efemera",
                            "predicate": "similar_a", "origin": "phase1_similarity"}],
    }
    answer, _meta = grounded_strategy.grounded_response(json.dumps(payload))
    assert "Edital Atual" not in answer


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
    events = asyncio.run(_collect(ExploreAgent().explore_stream(
        "quais oportunidades existem?", profile={"nome": "iFlorestal"},
    )))
    assert [event.kind for event in events] == ["final"]
    assert events[0].answer == ExploreAgent().explore_with_meta(
        "quais oportunidades existem?", profile={"nome": "iFlorestal"},
    )[0]


async def _collect(iterator):
    return [event async for event in iterator]
