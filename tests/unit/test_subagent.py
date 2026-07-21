"""Testes de run_subagent (core/llm/agent_runtime).

Pós-migração LangGraph: o subagente roda pelo grafo (agent_graph). Cobre:
  - happy path (chat model scriptado via _build_chat_model);
  - degradação (run_agent interno lança → stop_reason="error", sem propagar);
  - degradação sem API key (resolve_agent_provider levanta → error);
  - span_name propagado (f"subagent.{name}" chega na telemetria).
"""
from __future__ import annotations

import contextlib
import sys
from pathlib import Path

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import tool
from pydantic import PrivateAttr

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import core.llm.agent_graph as ag
import core.llm.agent_runtime as ar
from core.llm.agent_runtime import run_subagent

pytestmark = pytest.mark.unit


class _ScriptedChatModel(BaseChatModel):
    responses: list
    _idx: int = PrivateAttr(default=0)

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        msg = self.responses[self._idx]
        self._idx += 1
        return ChatResult(generations=[ChatGeneration(message=msg)])

    def bind_tools(self, tools, **kwargs):  # noqa: ANN001
        return self


@tool
def _noop(x: str) -> str:
    """Tool de teste."""
    return x


def test_run_subagent_happy_path(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-x")
    scripted = _ScriptedChatModel(responses=[
        AIMessage(content="resposta do subagente",
                  usage_metadata={"input_tokens": 20, "output_tokens": 5, "total_tokens": 25}),
    ])
    monkeypatch.setattr(ag, "_build_chat_model", lambda *a, **k: scripted)

    res = run_subagent(
        name="deep_research", system="sys", user_message="pergunta",
        tools=[_noop], provider="anthropic", model="claude-sonnet-4-6",
    )
    assert res.final_text == "resposta do subagente"
    assert res.stop_reason == "end_turn"


def test_run_subagent_degrades_on_internal_error(monkeypatch):
    """run_agent interno lança → run_subagent devolve stop_reason='error' sem propagar."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-x")

    def boom(**kw):
        raise RuntimeError("loop explodiu")

    monkeypatch.setattr(ar, "run_agent", boom)

    res = run_subagent(name="deep_research", system="sys", user_message="x", tools=[_noop])
    assert res.final_text == "" and res.steps == [] and res.stop_reason == "error"
    assert res.usage == {}


def test_run_subagent_degrades_when_no_api_key(monkeypatch):
    """Sem nenhuma API key, resolve_agent_provider levanta → degrada, não propaga."""
    for var in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "AGENT_OPENAI_API_KEY", "AGENT_OPENAI_BASE_URL"):
        monkeypatch.delenv(var, raising=False)

    res = run_subagent(name="deep_research", system="s", user_message="x", tools=[_noop])
    assert res.stop_reason == "error"


def test_run_subagent_propagates_span_name(monkeypatch):
    """O span raiz vira f'subagent.{name}', distinguindo de agente top-level."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-x")
    scripted = _ScriptedChatModel(responses=[
        AIMessage(content="ok",
                  usage_metadata={"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}),
    ])
    monkeypatch.setattr(ag, "_build_chat_model", lambda *a, **k: scripted)

    captured: list[str] = []

    @contextlib.contextmanager
    def fake_agent_run(name, **kw):
        captured.append(name)
        yield None

    import core.infra.telemetry as telemetry
    monkeypatch.setattr(telemetry, "agent_run", fake_agent_run)

    run_subagent(
        name="deep_research", system="sys", user_message="x",
        tools=[_noop], model="claude-sonnet-4-6",
    )
    assert captured == ["subagent.deep_research"]
