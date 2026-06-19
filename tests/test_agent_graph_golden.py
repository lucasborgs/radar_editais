"""Golden test do spike Etapa 1 (docs/specs/langgraph-migration.md).

Roda os MESMOS cenários no runtime legado (`run_agent` + `_call_anthropic`
stubbado) e no runtime LangGraph (`run_agent_graph_async` + `_build_chat_model`
stubbado) e exige `AgentResult` equivalente — prova de que o tradutor de contrato
e o loop StateGraph preservam o comportamento.

Não toca rede: ambos os LLMs são fakes scriptados a partir de uma única fonte.
"""
from __future__ import annotations

import asyncio

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import PrivateAttr

from core.llm.agent_runtime import TOOL_RESULT_CHAR_CAP, _LLMStep, run_agent, tool
import core.llm.agent_graph as ag
from core.llm.agent_graph import run_agent_graph_async


# ---------------------------------------------------------------------------
# Fakes scriptados (uma fonte → dois runtimes)
# ---------------------------------------------------------------------------

class ScriptedChatModel(BaseChatModel):
    """ChatModel fake: devolve `responses[i]` na i-ésima chamada. Registra as
    mensagens recebidas em `received` (p/ assertar injeção de reflexão)."""
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


def _legacy_step(turn: dict) -> _LLMStep:
    return _LLMStep(
        stop_reason="end_turn",
        text=turn["text"],
        tool_uses=[
            {"id": tc["id"], "name": tc["name"], "input": tc["args"]}
            for tc in turn["tool_calls"]
        ],
        assistant_message={"role": "assistant", "content": turn["text"]},
        usage=turn["usage"],
    )


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


def _make_legacy_call(turns: list[dict]):
    state = {"i": 0}

    def call(system, messages, tools_schema, model):
        step = _legacy_step(turns[state["i"]])
        state["i"] += 1
        return step

    return call


# ---------------------------------------------------------------------------
# Comparador
# ---------------------------------------------------------------------------

def _assert_equiv(legacy, graph) -> None:
    assert legacy.final_text == graph.final_text, "final_text difere"
    assert legacy.stop_reason == graph.stop_reason, "stop_reason difere"
    assert legacy.usage == graph.usage, f"usage difere: {legacy.usage} != {graph.usage}"
    assert len(legacy.steps) == len(graph.steps), (
        f"nº de steps difere: {[s.kind for s in legacy.steps]} != {[s.kind for s in graph.steps]}"
    )
    for ls, gs in zip(legacy.steps, graph.steps):
        assert ls.kind == gs.kind
        if ls.kind == "llm":
            assert ls.tool_uses == gs.tool_uses, f"tool_uses: {ls.tool_uses} != {gs.tool_uses}"
            assert ls.usage == gs.usage
        else:  # tool
            assert ls.name == gs.name
            assert ls.input == gs.input, f"tool input: {ls.input} != {gs.input}"
            assert ls.output == gs.output, f"tool output: {ls.output} != {gs.output}"


def _run_both(monkeypatch, turns, tools, *, max_steps=8):
    """Roda os mesmos `turns` nos dois runtimes e devolve (legacy, graph)."""
    # Lado legado: força o loop legado (o default agora é langgraph), senão
    # `run_agent` despacharia pro grafo e ignoraria o stub de `_call_anthropic`.
    monkeypatch.setenv("AGENT_RUNTIME", "legacy")
    monkeypatch.setattr("core.llm.agent_runtime._call_anthropic", _make_legacy_call(turns))
    legacy = run_agent(
        system="sys", initial_messages=[{"role": "user", "content": "vai"}],
        tools=tools, model="claude-sonnet-4-6", provider="anthropic", max_steps=max_steps,
    )

    scripted = ScriptedChatModel(responses=[_graph_msg(t) for t in turns])
    monkeypatch.setattr(ag, "_build_chat_model", lambda *a, **k: scripted)
    graph = asyncio.run(run_agent_graph_async(
        system="sys", initial_messages=[{"role": "user", "content": "vai"}],
        tools=tools, model="claude-sonnet-4-6", provider="anthropic", max_steps=max_steps,
    ))
    return legacy, graph


# ---------------------------------------------------------------------------
# Cenários de paridade
# ---------------------------------------------------------------------------

def test_no_tool_one_step(monkeypatch):
    @tool
    def search(q: str) -> str:
        """Busca."""
        return "x"

    turns = [{"text": "Olá!", "tool_calls": [], "usage": {"input_tokens": 30, "output_tokens": 10}}]
    legacy, graph = _run_both(monkeypatch, turns, [search])
    _assert_equiv(legacy, graph)
    assert graph.final_text == "Olá!"
    assert graph.stop_reason == "end_turn"
    assert [s.kind for s in graph.steps] == ["llm"]


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
    legacy, graph = _run_both(monkeypatch, turns, [search])
    _assert_equiv(legacy, graph)
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
    legacy, graph = _run_both(monkeypatch, turns, [flaky])
    _assert_equiv(legacy, graph)
    # degradação graciosa: erro vira string idêntica nos dois runtimes
    assert "Erro ao executar 'flaky'" in graph.steps[1].output
    assert graph.steps[1].output == legacy.steps[1].output


def test_max_steps_stop_reason(monkeypatch):
    """Modelo insiste em tool além do teto → stop_reason=max_steps nos dois."""
    @tool
    def search(q: str) -> str:
        """Busca."""
        return "mais"

    # 2 turns, ambos pedindo tool; max_steps=2 → as tools da 2ª rodada SÃO
    # executadas (paridade com o legado) e então o teto corta.
    turns = [
        {"text": "t1", "tool_calls": [{"id": "a", "name": "search", "args": {"q": "1"}}],
         "usage": {"input_tokens": 10, "output_tokens": 2}},
        {"text": "t2", "tool_calls": [{"id": "b", "name": "search", "args": {"q": "2"}}],
         "usage": {"input_tokens": 10, "output_tokens": 2}},
    ]
    legacy, graph = _run_both(monkeypatch, turns, [search], max_steps=2)
    _assert_equiv(legacy, graph)
    assert graph.stop_reason == "max_steps"
    # trace completo: llm, tool, llm, tool (a última rodada de tools roda)
    assert [s.kind for s in graph.steps] == ["llm", "tool", "llm", "tool"]


# ---------------------------------------------------------------------------
# Reflexão (graph-only): com reflect_every=1, o nó reflect injeta o prompt
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
    scripted = ScriptedChatModel(responses=[_graph_msg(t) for t in turns])
    monkeypatch.setattr(ag, "_build_chat_model", lambda *a, **k: scripted)
    graph = asyncio.run(run_agent_graph_async(
        system="sys", initial_messages=[{"role": "user", "content": "vai"}],
        tools=[search], model="claude-sonnet-4-6", provider="anthropic", reflect_every=1,
    ))
    # 2ª chamada ao modelo deve conter o prompt de reflexão injetado pelo nó reflect.
    second_call_msgs = scripted._received[1]
    assert any(
        isinstance(m, HumanMessage) and "Reflexão interna" in str(m.content)
        for m in second_call_msgs
    ), "nó reflect não injetou o prompt de reflexão"
    assert graph.final_text == "pronto"


# ---------------------------------------------------------------------------
# Cobertura de runtime do grafo (espelha garantias dos testes de adapter legado)
# ---------------------------------------------------------------------------

class RaisingChatModel(BaseChatModel):
    """Modelo que sempre levanta — exercita a degradação graciosa do grafo."""
    @property
    def _llm_type(self) -> str:
        return "raising"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        raise RuntimeError("modelo explodiu")

    def bind_tools(self, tools, **kwargs):  # noqa: ANN001
        return self


def test_graph_caps_tool_result(monkeypatch):
    """Tool que devolve 50k chars → output capado no trace (espelha test_context_budget)."""
    @tool
    def big_tool() -> str:
        """Devolve um output enorme."""
        return "y" * 50_000

    turns = [
        {"text": "", "tool_calls": [{"id": "t1", "name": "big_tool", "args": {}}],
         "usage": {"input_tokens": 5, "output_tokens": 1}},
        {"text": "pronto", "tool_calls": [], "usage": {"input_tokens": 5, "output_tokens": 1}},
    ]
    scripted = ScriptedChatModel(responses=[_graph_msg(t) for t in turns])
    monkeypatch.setattr(ag, "_build_chat_model", lambda *a, **k: scripted)
    graph = asyncio.run(run_agent_graph_async(
        system="sys", initial_messages=[{"role": "user", "content": "vai"}],
        tools=[big_tool], model="m", provider="anthropic",
    ))
    tool_step = next(s for s in graph.steps if s.kind == "tool")
    assert len(tool_step.output) <= TOOL_RESULT_CHAR_CAP + 80
    assert "…[truncado:" in tool_step.output
    assert graph.final_text == "pronto"


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

    turns = [{"text": "ok", "tool_calls": [], "usage": {"input_tokens": 1, "output_tokens": 1}}]
    scripted = ScriptedChatModel(responses=[_graph_msg(t) for t in turns])
    monkeypatch.setattr(ag, "_build_chat_model", lambda *a, **k: scripted)

    @tool
    def noop(x: str) -> str:
        """noop."""
        return x

    asyncio.run(run_agent_graph_async(
        system="sys", initial_messages=[{"role": "user", "content": "x"}],
        tools=[noop], model="claude-sonnet-4-6", provider="anthropic",
        span_name="subagent.deep_research",
    ))
    assert captured == ["subagent.deep_research"]
