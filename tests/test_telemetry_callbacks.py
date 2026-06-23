"""Etapa 6 — telemetria via CallbackHandler nativo + nesting cross-thread (zero rede).

Não exercita o Langfuse real (desabilitado nos testes: LANGFUSE_* = "" no conftest).
Prova o WIRING: a CallbackHandler entra no `config["callbacks"]` de cada invocação do
grafo, o `trace_context` do pai é capturado e propagado ao subagente (nesting do critic),
e tudo vira no-op gracioso quando o Langfuse está desligado.
"""
from __future__ import annotations

import asyncio

import core.llm.agent_graph as ag
import core.llm.agent_runtime as art
import core.telemetry as telemetry
from langchain_core.messages import AIMessage


class _FakeGraph:
    """Captura o config passado ao ainvoke e devolve um estado final mínimo."""
    def __init__(self):
        self.captured_config: dict | None = None

    async def ainvoke(self, init, config=None):  # noqa: ANN001
        self.captured_config = config
        return {"messages": [AIMessage(content="ok", usage_metadata={
            "input_tokens": 1, "output_tokens": 1, "total_tokens": 2})]}


# ---------------------------------------------------------------------------
# Wiring do handler no config do grafo
# ---------------------------------------------------------------------------

def test_callback_handler_injected_into_graph_config(monkeypatch):
    sentinel = object()
    monkeypatch.setattr(telemetry, "make_callback_handler", lambda *a, **k: sentinel)
    fake = _FakeGraph()
    monkeypatch.setattr(ag, "_build_chat_model", lambda *a, **k: object())
    monkeypatch.setattr(ag, "_build_graph", lambda *a, **k: fake)

    asyncio.run(ag.run_agent_graph_async(
        system="sys", initial_messages=[{"role": "user", "content": "x"}],
        tools=[], model="m", provider="anthropic",
    ))
    assert fake.captured_config["callbacks"] == [sentinel]


def test_no_callbacks_key_when_handler_none(monkeypatch):
    """Langfuse off → make_callback_handler None → config sem 'callbacks' (sem overhead)."""
    monkeypatch.setattr(telemetry, "make_callback_handler", lambda *a, **k: None)
    fake = _FakeGraph()
    monkeypatch.setattr(ag, "_build_chat_model", lambda *a, **k: object())
    monkeypatch.setattr(ag, "_build_graph", lambda *a, **k: fake)

    asyncio.run(ag.run_agent_graph_async(
        system="sys", initial_messages=[{"role": "user", "content": "x"}],
        tools=[], model="m", provider="anthropic",
    ))
    assert "callbacks" not in fake.captured_config


def test_trace_context_forwarded_to_agent_run(monkeypatch):
    """run_agent_graph_async repassa trace_context (parent remoto) ao agent_run."""
    import contextlib

    captured: dict = {}

    @contextlib.contextmanager
    def fake_agent_run(name, **kw):
        captured["trace_context"] = kw.get("trace_context")
        yield None

    monkeypatch.setattr(telemetry, "agent_run", fake_agent_run)
    monkeypatch.setattr(telemetry, "make_callback_handler", lambda *a, **k: None)
    monkeypatch.setattr(ag, "_build_chat_model", lambda *a, **k: object())
    monkeypatch.setattr(ag, "_build_graph", lambda *a, **k: _FakeGraph())

    ctx = {"trace_id": "T123", "parent_span_id": "P456"}
    asyncio.run(ag.run_agent_graph_async(
        system="sys", initial_messages=[{"role": "user", "content": "x"}],
        tools=[], model="m", provider="anthropic", trace_context=ctx,
    ))
    assert captured["trace_context"] == ctx


# ---------------------------------------------------------------------------
# Nesting cross-thread: run_subagent captura e propaga o trace do pai
# ---------------------------------------------------------------------------

def test_run_subagent_propagates_parent_trace_context(monkeypatch):
    parent_ctx = {"trace_id": "Tparent", "parent_span_id": "Sparent"}
    monkeypatch.setattr(telemetry, "current_trace_context", lambda: parent_ctx)

    captured: dict = {}

    def fake_run_agent(**kwargs):
        captured.update(kwargs)
        return art.AgentResult(final_text="ok", steps=[], stop_reason="end_turn", usage={})

    monkeypatch.setattr(art, "run_agent", fake_run_agent)

    art.run_subagent(
        name="critic", system="revise", user_message="revise",
        tools=[], provider="anthropic", model="m", max_steps=2,
    )
    assert captured["trace_context"] == parent_ctx
    assert captured["span_name"] == "subagent.critic"


# ---------------------------------------------------------------------------
# Degradação graciosa quando Langfuse está desabilitado (default nos testes)
# ---------------------------------------------------------------------------

def test_factories_noop_when_disabled():
    assert telemetry.is_enabled() is False           # conftest zera as keys
    assert telemetry.make_callback_handler() is None
    assert telemetry.current_trace_context() is None
