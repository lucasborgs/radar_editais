"""Testes da facade core.llm.agent_runtime.

Pós-migração LangGraph (Etapas 1-2), o módulo é uma facade fina: o loop ReAct, o
@tool e os adapters viraram código do LangGraph/grafo. O que resta de lógica
própria aqui é `resolve_agent_provider` (fallback multi-provider por API key).
O loop/cap/reflexão/contrato são cobertos por tests/unit/test_agent_graph_golden.py.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from core.llm.agent_runtime import resolve_agent_provider  # noqa: E402

pytestmark = pytest.mark.unit

# ============================================================================
# resolve_agent_provider — fallback multi-provider por disponibilidade de key
# ============================================================================

def test_resolve_keeps_preferred_when_key_present(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-x")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-oai-x")
    provider, model = resolve_agent_provider("anthropic", "claude-sonnet-4-6")
    assert provider == "anthropic"
    assert model == "claude-sonnet-4-6"


def test_resolve_falls_back_to_openai_when_anthropic_missing(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-oai-x")
    monkeypatch.delenv("OPENAI_MODEL_AGENT", raising=False)
    monkeypatch.setenv("OPENAI_MODEL_PRO", "gpt-4o")
    provider, model = resolve_agent_provider("anthropic", "claude-sonnet-4-6")
    assert provider == "openai"
    assert model == "gpt-4o"


def test_resolve_explicit_openai_fallback_model(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-oai-x")
    provider, model = resolve_agent_provider(
        "anthropic", "claude-sonnet-4-6", openai_model="gpt-4o-2024-11-20",
    )
    assert provider == "openai"
    assert model == "gpt-4o-2024-11-20"


def test_resolve_raises_when_no_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("AGENT_OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("AGENT_OPENAI_BASE_URL", raising=False)
    with pytest.raises(RuntimeError, match="Nenhuma API key"):
        resolve_agent_provider("anthropic", "claude-sonnet-4-6")
