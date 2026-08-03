from __future__ import annotations

import json

import pytest

from radar.core.llm.agent_runtime import AgentResult
from radar.core.services import grounded_strategy

pytestmark = pytest.mark.unit


def _stub_runtime(captured: dict[str, object]):
    def run_agent(**kwargs):
        captured.update(kwargs)
        return AgentResult(
            json.dumps({
                "requires_graph": True,
                "grounded": True,
                "unsupported_claims": [],
            }),
            [],
            "end_turn",
            {},
        )

    return run_agent


def test_judge_reuses_anthropic_provider_and_model(monkeypatch):
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        "radar.core.llm.agent_runtime.run_agent", _stub_runtime(captured),
    )

    result = grounded_strategy.judge_grounding(
        "Quais ICTs são aderentes?", "A ICT é válida.", [],
        provider="anthropic", model="claude-sonnet-4-6",
    )

    assert result == {
        "requires_graph": True, "grounded": True, "unsupported_claims": [],
    }
    assert captured["provider"] == "anthropic"
    assert captured["model"] == "claude-sonnet-4-6"
    assert captured["tools"] == []
    assert captured["max_steps"] == 1
    assert captured["temperature"] == 0


def test_judge_reuses_openai_compatible_provider_and_model(monkeypatch):
    captured: dict[str, object] = {}
    monkeypatch.setattr(
        "radar.core.llm.agent_runtime.run_agent", _stub_runtime(captured),
    )

    result = grounded_strategy.judge_grounding(
        "Quais investidores são aderentes?", "Há um investidor.", [],
        provider="openai", model="deepseek-chat",
    )

    assert result == {
        "requires_graph": True, "grounded": True, "unsupported_claims": [],
    }
    assert captured["provider"] == "openai"
    assert captured["model"] == "deepseek-chat"
    assert captured["tools"] == []
    assert captured["max_steps"] == 1
    assert captured["temperature"] == 0


def test_judge_fails_closed_on_invalid_json(monkeypatch):
    monkeypatch.setattr(
        "radar.core.llm.agent_runtime.run_agent",
        lambda **_kwargs: AgentResult("not json", [], "end_turn", {}),
    )

    assert grounded_strategy.judge_grounding(
        "Pergunta factual", "Resposta", [], provider="openai", model="gpt-4o-mini",
    ) is None
