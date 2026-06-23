"""Testes de runtime do grafo LangGraph (core.llm.agent_graph).

Pós-Etapa 2 o grafo é o único runtime; estes testes exercitam o loop + tradutor
de contrato com um chat model scriptado (zero rede): no-tool, 1-tool, tool-error,
max_steps (com trace completo), reflexão, cap central, degradação por erro de
modelo, span_name. Equivalência ao runtime legado foi provada na Etapa 1 (antes
de o legado ser aposentado) e está congelada nas asserções diretas abaixo.
"""
from __future__ import annotations

import asyncio

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import tool
from pydantic import PrivateAttr

import core.llm.agent_graph as ag
from core.llm.agent_graph import run_agent_graph_async
from core.llm.agent_runtime import TOOL_RESULT_CHAR_CAP


# ---------------------------------------------------------------------------
# Chat model scriptado (zero rede)
# ---------------------------------------------------------------------------

class ScriptedChatModel(BaseChatModel):
    """Devolve `responses[i]` na i-ésima chamada; registra as mensagens recebidas."""
    responses: list
    _idx: int = PrivateAttr(default=0)
    _received: list = PrivateAttr(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        self._received.append(list(messages))
        msg = self.responses[self._idx]
        self._idx += 1
        return ChatResult(generations=[ChatGeneration(message=msg)])

    def bind_tools(self, tools, **kwargs):  # noqa: ANN001
        return self


def _graph_msg(turn: dict) -> AIMessage:
    return AIMessage(
        content=turn["text"],
        tool_calls=[
            {"id": tc["id"], "name": tc["name"], "args": tc["args"], "type": "tool_call"}
            for tc in turn["tool_calls"]
        ],
        usage_metadata={
            "input_tokens": turn["usage"]["input_tokens"],
            "output_tokens": turn["usage"]["output_tokens"],
            "total_tokens": turn["usage"]["input_tokens"] + turn["usage"]["output_tokens"],
        },
    )


def _run_graph(monkeypatch, turns, tools, *, max_steps=8, reflect_every=None):
    scripted = ScriptedChatModel(responses=[_graph_msg(t) for t in turns])
    monkeypatch.setattr(ag, "_build_chat_model", lambda *a, **k: scripted)
    result = asyncio.run(run_agent_graph_async(
        system="sys", initial_messages=[{"role": "user", "content": "vai"}],
        tools=tools, model="m", provider="anthropic",
        max_steps=max_steps, reflect_every=reflect_every,
    ))
    return result, scripted


# ---------------------------------------------------------------------------
# Loop + tradutor de contrato
# ---------------------------------------------------------------------------

def test_no_tool_one_step(monkeypatch):
    @tool
    def search(q: str) -> str:
        """Busca."""
        return "x"

    turns = [{"text": "Olá!", "tool_calls": [], "usage": {"input_tokens": 30, "output_tokens": 10}}]
    graph, _ = _run_graph(monkeypatch, turns, [search])
    assert graph.final_text == "Olá!"
    assert graph.stop_reason == "end_turn"
    assert [s.kind for s in graph.steps] == ["llm"]
    assert graph.usage == {"input_tokens": 30, "output_tokens": 10}


def test_one_tool_then_response(monkeypatch):
    @tool
    def search(q: str) -> str:
        """Busca."""
        return f"resultado para {q}"

    turns = [
        {"text": "Vou buscar.",
         "tool_calls": [{"id": "tu_1", "name": "search", "args": {"q": "prazo"}}],
         "usage": {"input_tokens": 100, "output_tokens": 20}},
        {"text": "O prazo é 30/06.", "tool_calls": [],
         "usage": {"input_tokens": 150, "output_tokens": 15}},
    ]
    graph, _ = _run_graph(monkeypatch, turns, [search])
    assert graph.final_text == "O prazo é 30/06."
    assert [s.kind for s in graph.steps] == ["llm", "tool", "llm"]
    assert graph.steps[1].output == "resultado para prazo"
    assert graph.steps[1].input == {"q": "prazo"}
    assert graph.usage == {"input_tokens": 250, "output_tokens": 35}


def test_tool_error_recovers(monkeypatch):
    @tool
    def flaky(q: str) -> str:
        """Tool que falha."""
        raise ValueError("boom")

    turns = [
        {"text": "", "tool_calls": [{"id": "tu_1", "name": "flaky", "args": {"q": "z"}}],
         "usage": {"input_tokens": 50, "output_tokens": 5}},
        {"text": "Não consegui, mas sigo.", "tool_calls": [],
         "usage": {"input_tokens": 60, "output_tokens": 8}},
    ]
    graph, _ = _run_graph(monkeypatch, turns, [flaky])
    # degradação graciosa: tool que levanta vira string de erro (ToolNode handler)
    assert "Erro ao executar a tool" in graph.steps[1].output
    assert graph.final_text == "Não consegui, mas sigo."


def test_max_steps_stop_reason(monkeypatch):
    """Modelo insiste em tool além do teto → stop_reason=max_steps; tools da última
    rodada SÃO executadas (trace llm,tool,llm,tool)."""
    @tool
    def search(q: str) -> str:
        """Busca."""
        return "mais"

    turns = [
        {"text": "t1", "tool_calls": [{"id": "a", "name": "search", "args": {"q": "1"}}],
         "usage": {"input_tokens": 10, "output_tokens": 2}},
        {"text": "t2", "tool_calls": [{"id": "b", "name": "search", "args": {"q": "2"}}],
         "usage": {"input_tokens": 10, "output_tokens": 2}},
    ]
    graph, _ = _run_graph(monkeypatch, turns, [search], max_steps=2)
    assert graph.stop_reason == "max_steps"
    assert [s.kind for s in graph.steps] == ["llm", "tool", "llm", "tool"]


# ---------------------------------------------------------------------------
# Reflexão / cap / degradação / span_name
# ---------------------------------------------------------------------------

def test_reflection_node_injects_prompt(monkeypatch):
    @tool
    def search(q: str) -> str:
        """Busca."""
        return "ok"

    turns = [
        {"text": "buscando", "tool_calls": [{"id": "a", "name": "search", "args": {"q": "x"}}],
         "usage": {"input_tokens": 10, "output_tokens": 2}},
        {"text": "pronto", "tool_calls": [], "usage": {"input_tokens": 10, "output_tokens": 2}},
    ]
    graph, scripted = _run_graph(monkeypatch, turns, [search], reflect_every=1)
    # 2ª chamada ao modelo deve conter o prompt de reflexão injetado pelo nó reflect.
    second_call_msgs = scripted._received[1]
    assert any(
        isinstance(m, HumanMessage) and "Reflexão interna" in str(m.content)
        for m in second_call_msgs
    ), "nó reflect não injetou o prompt de reflexão"
    assert graph.final_text == "pronto"


def test_graph_caps_tool_result(monkeypatch):
    """Tool que devolve 50k chars → output capado no trace (cap central no nó tools)."""
    @tool
    def big_tool(_: str = "") -> str:
        """Devolve um output enorme."""
        return "y" * 50_000

    turns = [
        {"text": "", "tool_calls": [{"id": "t1", "name": "big_tool", "args": {}}],
         "usage": {"input_tokens": 5, "output_tokens": 1}},
        {"text": "pronto", "tool_calls": [], "usage": {"input_tokens": 5, "output_tokens": 1}},
    ]
    graph, _ = _run_graph(monkeypatch, turns, [big_tool])
    tool_step = next(s for s in graph.steps if s.kind == "tool")
    assert len(tool_step.output) <= TOOL_RESULT_CHAR_CAP + 80
    assert "…[truncado:" in tool_step.output
    assert graph.final_text == "pronto"


class RaisingChatModel(BaseChatModel):
    """Modelo que sempre levanta — exercita a degradação graciosa do grafo."""
    @property
    def _llm_type(self) -> str:
        return "raising"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        raise RuntimeError("modelo explodiu")

    def bind_tools(self, tools, **kwargs):  # noqa: ANN001
        return self


def test_graph_degrades_on_model_error(monkeypatch):
    """Modelo levanta no nó agent → AgentResult error, sem propagar exceção."""
    @tool
    def search(q: str) -> str:
        """Busca."""
        return "x"

    monkeypatch.setattr(ag, "_build_chat_model", lambda *a, **k: RaisingChatModel())
    graph = asyncio.run(run_agent_graph_async(
        system="sys", initial_messages=[{"role": "user", "content": "vai"}],
        tools=[search], model="m", provider="anthropic",
    ))
    assert graph.stop_reason == "error"
    assert graph.final_text == "" and graph.steps == []


def test_graph_honors_span_name(monkeypatch):
    """span_name chega na telemetria (igual ao legado → nesting de subagente)."""
    import contextlib

    import core.telemetry as telemetry

    captured: list[str] = []

    @contextlib.contextmanager
    def fake_agent_run(name, **kw):
        captured.append(name)
        yield None

    monkeypatch.setattr(telemetry, "agent_run", fake_agent_run)

    @tool
    def noop(x: str) -> str:
        """noop."""
        return x

    turns = [{"text": "ok", "tool_calls": [], "usage": {"input_tokens": 1, "output_tokens": 1}}]
    scripted = ScriptedChatModel(responses=[_graph_msg(t) for t in turns])
    monkeypatch.setattr(ag, "_build_chat_model", lambda *a, **k: scripted)

    asyncio.run(run_agent_graph_async(
        system="sys", initial_messages=[{"role": "user", "content": "x"}],
        tools=[noop], model="claude-sonnet-4-6", provider="anthropic",
        span_name="subagent.deep_research",
    ))
    assert captured == ["subagent.deep_research"]
