"""Testes da tool write_todos (core/llm/agent_tools/planning_tools).

Chama Tool.call direto (sem LLM), padrão de tests/unit/test_agent_runtime.py. Cobre:
render, replace integral, status default, shape inválido → erro-string, contagem.
Inclui guard-rails de wiring (writing/extractor).
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

import pydantic
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from core.llm.agent_tools.planning_tools import PlanState, build_planning_tools

pytestmark = pytest.mark.unit


def _write_todos(state: PlanState):
    tools = build_planning_tools(state)
    assert len(tools) == 1
    assert tools[0].name == "write_todos"
    return tools[0]


def test_render_with_markers_and_count():
    state = PlanState()
    out = _write_todos(state).invoke({"todos": [
        {"content": "Buscar requisitos", "status": "completed"},
        {"content": "Redigir metodologia", "status": "in_progress"},
        {"content": "Salvar rascunho", "status": "pending"},
    ]})
    assert "✓ Buscar requisitos" in out
    assert "▶ Redigir metodologia" in out
    assert "☐ Salvar rascunho" in out
    assert "(1/3 concluídas)" in out


def test_replace_is_total_not_merge():
    state = PlanState()
    tool = _write_todos(state)
    tool.invoke({"todos": [{"content": "A"}, {"content": "B"}]})
    out = tool.invoke({"todos": [{"content": "C", "status": "completed"}]})
    # A e B somem — substituição integral, não merge.
    assert [t["content"] for t in state.todos] == ["C"]
    assert "A" not in out and "B" not in out
    assert "✓ C" in out and "(1/1 concluídas)" in out


def test_status_defaults_to_pending():
    state = PlanState()
    out = _write_todos(state).invoke({"todos": [{"content": "Sem status"}]})
    assert state.todos == [{"content": "Sem status", "status": "pending"}]
    assert "☐ Sem status" in out


def test_invalid_shape_returns_error_string_not_exception():
    """Validação SEMÂNTICA (content vazio, status inválido) volta como string de
    erro — o tool não quebra. Shape inválido a nível de SCHEMA (item não-dict) é
    barrado pelo pydantic do LangChain e convertido em string pelo ToolNode no
    loop (ver test_agent_graph_golden::test_tool_error_recovers)."""
    state = PlanState()
    tool = _write_todos(state)

    # content vazio (shape válido; validação semântica do tool)
    out = tool.invoke({"todos": [{"content": "   "}]})
    assert "content" in out and out.startswith("Erro")
    assert state.todos == []  # nada gravado em erro

    # status inválido
    out = tool.invoke({"todos": [{"content": "ok", "status": "doing"}]})
    assert "status" in out.lower() and "Erro" in out

    # shape inválido (item não-dict): barrado pelo schema LangChain (pydantic).
    with pytest.raises(pydantic.ValidationError):
        tool.invoke({"todos": ["string solta"]})
    assert state.todos == []


def test_content_trimmed_and_stored():
    state = PlanState()
    _write_todos(state).invoke({"todos": [{"content": "  espaços  "}]})
    assert state.todos[0]["content"] == "espaços"


def test_empty_list_renders_vazio():
    state = PlanState()
    out = _write_todos(state).invoke({"todos": []})
    assert "vazio" in out.lower()


# ---------------------------------------------------------------------------
# Guard-rails de wiring
# ---------------------------------------------------------------------------

def test_build_writing_tools_inclui_write_todos():
    """Mesmo padrão de test_redator_inclui_deep_research: a tool DEVE estar lá."""
    from core.llm.agent_tools import writing_tools
    src = inspect.getsource(writing_tools.build_writing_tools)
    assert "build_planning_tools" in src


def test_extractor_tools_incluem_write_todos_e_write_note():
    """As tools do agente de extração somam planning + scratchpad."""
    import core.ingestion.profile_extractor as pe
    src = inspect.getsource(pe.ProfileExtractor._extract_agent)
    assert "build_planning_tools" in src
    assert "build_scratchpad_tools" in src
