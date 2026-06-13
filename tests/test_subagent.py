"""Testes de run_subagent (core/llm/agent_runtime).

Cobre: happy path (monkeypatch _call_anthropic com _make_fake_call),
degradação (run_agent interno lança → stop_reason="error" sem exceção),
span_name propagado (f"subagent.{name}" chega na telemetria).

Reusa o padrão _make_fake_call/_LLMStep de tests/test_agent_runtime.py.
"""
from __future__ import annotations

import sys
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import core.llm.agent_runtime as ar
from core.llm.agent_runtime import _LLMStep, run_subagent, tool


def _make_fake_call(sequence: list[_LLMStep]):
    """Stub: devolve sequence[i] na i-ésima invocação (cópia de test_agent_runtime)."""
    state = {"i": 0}

    def call(system, messages, tools_schema, model):
        step = sequence[state["i"]]
        state["i"] += 1
        return step

    return call


@tool
def _noop(x: str) -> str:
    """Tool de teste."""
    return x


def test_run_subagent_happy_path(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-x")
    fake = _make_fake_call([
        _LLMStep(
            stop_reason="end_turn",
            text="resposta do subagente",
            tool_uses=[],
            assistant_message={"role": "assistant", "content": "resposta do subagente"},
            usage={"input_tokens": 20, "output_tokens": 5},
        ),
    ])
    monkeypatch.setattr(ar, "_call_anthropic", fake)

    res = run_subagent(
        name="deep_research",
        system="sys",
        user_message="pergunta",
        tools=[_noop],
        provider="anthropic",
        model="claude-sonnet-4-6",
    )
    assert res.final_text == "resposta do subagente"
    assert res.stop_reason == "end_turn"


def test_run_subagent_degrades_on_internal_error(monkeypatch):
    """run_agent interno lança → run_subagent devolve stop_reason='error' sem propagar."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-x")

    def boom(**kw):
        raise RuntimeError("loop explodiu")

    monkeypatch.setattr(ar, "run_agent", boom)

    res = run_subagent(
        name="deep_research",
        system="sys",
        user_message="x",
        tools=[_noop],
    )
    assert res.final_text == "" and res.steps == [] and res.stop_reason == "error"
    assert res.usage == {}


def test_run_subagent_degrades_when_no_api_key(monkeypatch):
    """Sem nenhuma API key, resolve_agent_provider levanta → degrada, não propaga."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    res = run_subagent(
        name="deep_research", system="s", user_message="x", tools=[_noop],
    )
    assert res.stop_reason == "error"


def test_run_subagent_propagates_span_name(monkeypatch):
    """O span raiz vira f'subagent.{name}', distinguindo de agente top-level."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-x")
    fake = _make_fake_call([
        _LLMStep(
            stop_reason="end_turn",
            text="ok",
            tool_uses=[],
            assistant_message={"role": "assistant", "content": "ok"},
            usage={"input_tokens": 1, "output_tokens": 1},
        ),
    ])
    monkeypatch.setattr(ar, "_call_anthropic", fake)

    captured: list[str] = []

    @contextmanager
    def fake_agent_run(name, **kw):
        captured.append(name)
        yield None

    # run_agent importa telemetry lazy via `from core import telemetry`.
    import core.telemetry as telemetry
    monkeypatch.setattr(telemetry, "agent_run", fake_agent_run)

    run_subagent(
        name="deep_research",
        system="sys",
        user_message="x",
        tools=[_noop],
        model="claude-sonnet-4-6",
    )
    assert captured == ["subagent.deep_research"]
