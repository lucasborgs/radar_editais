"""Testes do path agente da WritingSession (Sprint 2 do Cenário B).

Estratégia: instanciamos `WritingSession.__new__(WritingSession)` e setamos os
atributos manualmente — pulamos o `__init__` real porque ele exige Supabase,
edital, perfil, etc. O Supabase client vira MagicMock chained.

`run_agent` é stubbado: testes não batem em Anthropic API.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.agent_runtime import AgentResult, TraceStep  # noqa: E402
from core.writing_session import WritingSession  # noqa: E402


def _make_session() -> WritingSession:
    """Cria uma WritingSession sem chamar __init__ (que exige DB real).

    Atributos mínimos necessários para o path agente funcionar. Pós-Front 1
    não há mais feature flag: turn() sempre roda o agente.
    """
    s = WritingSession.__new__(WritingSession)
    s.session_id = "sess_1"
    s.workspace_id = "ws_1"
    s.edital_id = "ed_1"

    db = MagicMock()
    db.table.return_value.insert.return_value.execute.return_value = None
    db.table.return_value.update.return_value.eq.return_value.execute.return_value = None
    s._db = db

    s._scope_edital_ids = ["ed_1"]
    s._doc_sections = {}
    s._proposal_outline = ["1. Identificação", "2. Objeto"]
    s._library_item_ids = set()
    s._history = []
    s._history_summary = ""
    s._turn_count = 0
    s._profile_context = "Empresa: ACME Bio. Setor: bioeconomia."
    s._library_context = ""
    s._reflection_insights_context = ""
    s._temporal_block = ""
    s.mode = "proposal"
    s._pitch_target_context = ""
    s._pending_user_input = None
    s.backend = "anthropic"
    s.model = "claude-sonnet-4-6"
    return s


# ============================================================================
# _extract_tool_trace
# ============================================================================

def test_extract_tool_trace_pairs_use_with_result():
    steps = [
        TraceStep(
            kind="llm",
            text="vou buscar",
            tool_uses=[
                {"id": "tu_1", "name": "search_edital", "input": {"query": "prazo"}},
            ],
            usage={"input_tokens": 100, "output_tokens": 10},
        ),
        TraceStep(
            kind="tool",
            name="search_edital",
            input={"query": "prazo"},
            output="Trecho: prazo é 30/06.",
        ),
        TraceStep(
            kind="llm",
            text="O prazo é 30/06.",
            tool_uses=[],
            usage={"input_tokens": 150, "output_tokens": 8},
        ),
    ]
    trace = WritingSession._extract_tool_trace(steps)
    assert len(trace) == 1
    assert trace[0]["id"] == "tu_1"
    assert trace[0]["name"] == "search_edital"
    assert trace[0]["input"] == {"query": "prazo"}
    assert "30/06" in trace[0]["output"]


def test_extract_tool_trace_multiple_tools_same_name():
    """Quando o agente chama search_edital 2x num turn, ambas viram trace
    com IDs corretos pareados em ordem."""
    steps = [
        TraceStep(
            kind="llm",
            text="",
            tool_uses=[
                {"id": "tu_1", "name": "search_edital", "input": {"query": "prazo"}},
                {"id": "tu_2", "name": "search_edital", "input": {"query": "TRL"}},
            ],
            usage={},
        ),
        TraceStep(
            kind="tool", name="search_edital",
            input={"query": "prazo"}, output="30/06",
        ),
        TraceStep(
            kind="tool", name="search_edital",
            input={"query": "TRL"}, output="TRL 5-9",
        ),
    ]
    trace = WritingSession._extract_tool_trace(steps)
    assert len(trace) == 2
    assert trace[0]["id"] == "tu_1"
    assert trace[1]["id"] == "tu_2"


def test_extract_tool_trace_empty_when_no_tools():
    steps = [
        TraceStep(
            kind="llm",
            text="resposta sem tool",
            tool_uses=[],
            usage={},
        ),
    ]
    assert WritingSession._extract_tool_trace(steps) == []


# ============================================================================
# turn() — sempre roda o agente (Front 1: legacy aposentado)
# ============================================================================

def test_turn_always_runs_agent(monkeypatch):
    """Sem feature flag: todo turn roda _turn_agent. Não existe mais legacy."""
    s = _make_session()
    called = {"agent": False}

    def fake_agent(self, *a, **kw):
        called["agent"] = True
        return {"session_id": self.session_id, "assistant_message": "ok",
                "draft_content": None, "pending_user_input": None,
                "turn_number": 1, "success": True, "error": None,
                "tool_trace": []}

    monkeypatch.setattr(WritingSession, "_turn_agent", fake_agent)
    s.turn("oi")
    assert called["agent"] is True


def test_no_legacy_dispatch_attributes():
    """Garante que o path legacy foi removido (sem _turn_legacy nem _use_agent)."""
    assert not hasattr(WritingSession, "_turn_legacy")
    assert not hasattr(WritingSession, "_use_agent")
    assert not hasattr(WritingSession, "_build_messages")


def test_turn_consumes_pending_user_input(monkeypatch):
    """Se há pending_user_input setado, o próximo turn limpa antes de processar."""
    s = _make_session()
    s._pending_user_input = {"field": "cnpj", "prompt": "Qual o CNPJ?"}

    monkeypatch.setattr(
        WritingSession,
        "_turn_agent",
        lambda self, *a, **kw: {
            "session_id": self.session_id, "assistant_message": "ok",
            "draft_content": None, "pending_user_input": None,
            "turn_number": 1, "success": True, "error": None, "tool_trace": [],
        },
    )

    s.turn("12345678901234")
    # Após o turn, deve ter sido limpo e UPDATE no DB foi chamado
    assert s._pending_user_input is None
    s._db.table.assert_any_call("writing_sessions")


# ============================================================================
# _turn_agent — happy path via stub de run_agent
# ============================================================================

def test_turn_agent_happy_path_no_tools(monkeypatch):
    s = _make_session()

    fake_result = AgentResult(
        final_text="Resposta direta sem tools.",
        steps=[
            TraceStep(kind="llm", text="Resposta direta sem tools.",
                      tool_uses=[], usage={"input_tokens": 50, "output_tokens": 10}),
        ],
        stop_reason="end_turn",
        usage={"input_tokens": 50, "output_tokens": 10},
    )

    def fake_run_agent(**kwargs):
        return fake_result

    monkeypatch.setattr("core.agent_runtime.run_agent", fake_run_agent)

    s._turn_count = 1
    result = s._turn_agent("oi", section_hint=None, user_turn_index=1)
    assert result["success"] is True
    assert result["assistant_message"] == "Resposta direta sem tools."
    assert result["tool_trace"] == []
    assert result["pending_user_input"] is None
    assert len(s._history) == 2  # user + assistant


def test_turn_agent_with_tools_persists_trace(monkeypatch):
    s = _make_session()

    fake_result = AgentResult(
        final_text="Prazo é 30/06.",
        steps=[
            TraceStep(
                kind="llm", text="vou buscar",
                tool_uses=[{"id": "tu_1", "name": "search_edital",
                            "input": {"query": "prazo"}}],
                usage={"input_tokens": 100, "output_tokens": 10},
            ),
            TraceStep(
                kind="tool", name="search_edital",
                input={"query": "prazo"}, output="trecho: 30/06",
            ),
            TraceStep(
                kind="llm", text="Prazo é 30/06.",
                tool_uses=[],
                usage={"input_tokens": 150, "output_tokens": 8},
            ),
        ],
        stop_reason="end_turn",
        usage={"input_tokens": 250, "output_tokens": 18},
    )
    monkeypatch.setattr("core.agent_runtime.run_agent", lambda **kw: fake_result)

    s._turn_count = 1
    result = s._turn_agent("qual o prazo?", section_hint=None, user_turn_index=1)

    assert result["success"] is True
    assert result["assistant_message"] == "Prazo é 30/06."
    assert len(result["tool_trace"]) == 1
    assert result["tool_trace"][0]["name"] == "search_edital"
    assert result["tool_trace"][0]["output"] == "trecho: 30/06"

    # Custo/turno (#12): a row assistant grava tokens = input+output de todo o
    # loop do agente (250+18); a row user fica sem tokens (não há custo nela).
    inserts = [c.args[0] for c in s._db.table.return_value.insert.call_args_list]
    turns = {p["role"]: p for p in inserts if isinstance(p, dict) and "role" in p}
    assert turns["assistant"]["tokens"] == 268
    assert "tokens" not in turns["user"]


def test_turn_agent_error_returns_error_dict(monkeypatch):
    s = _make_session()

    fake_result = AgentResult(
        final_text="",
        steps=[],
        stop_reason="error",
        usage={"input_tokens": 0, "output_tokens": 0},
    )
    monkeypatch.setattr("core.agent_runtime.run_agent", lambda **kw: fake_result)

    s._turn_count = 1
    result = s._turn_agent("oi", section_hint=None, user_turn_index=1)
    assert result["success"] is False
    assert result["error_type"] == "AGENT_ERROR"
    # turn_count voltou pra 0 (rollback)
    assert s._turn_count == 0


def test_turn_agent_pending_user_input_propagates(monkeypatch):
    """Quando uma tool seta session._pending_user_input, o response carrega."""
    s = _make_session()

    # Simula a tool request_user_info mutando state durante run_agent
    fake_result = AgentResult(
        final_text="Encaminhei a pergunta.",
        steps=[
            TraceStep(
                kind="llm", text="",
                tool_uses=[{"id": "tu_1", "name": "request_user_info",
                            "input": {"field": "cnpj", "prompt": "Qual o CNPJ?"}}],
                usage={},
            ),
            TraceStep(
                kind="tool", name="request_user_info",
                input={"field": "cnpj", "prompt": "Qual o CNPJ?"},
                output="Pergunta encaminhada.",
            ),
            TraceStep(kind="llm", text="Encaminhei a pergunta.", tool_uses=[], usage={}),
        ],
        stop_reason="end_turn",
        usage={"input_tokens": 100, "output_tokens": 10},
    )

    def fake_run_agent(**kw):
        # Tool factory ainda não roda dentro do stub; simulamos o side effect aqui
        s._pending_user_input = {"field": "cnpj", "prompt": "Qual o CNPJ?"}
        return fake_result

    monkeypatch.setattr("core.agent_runtime.run_agent", fake_run_agent)

    s._turn_count = 1
    result = s._turn_agent("preciso preencher o CNPJ", section_hint=None, user_turn_index=1)
    assert result["pending_user_input"] == {"field": "cnpj", "prompt": "Qual o CNPJ?"}


# ============================================================================
# Modo PITCH (investidor, kind_class=entidade) — Fatia 2 multi-quadrante
# ============================================================================

def _make_pitch_session() -> WritingSession:
    """Sessão de pitch (alvo = fundo investidor). Mesmo padrão __new__ do fixture
    de proposta, com o nó do fundo injetado como contexto (context-stuffing)."""
    s = _make_session()
    s.edital_id = "investidor:kptl"
    s.mode = "pitch"
    s._pitch_target_context = (
        "FUNDO-ALVO (use para ancorar o fit; não invente tese):\n"
        "FUNDO-ALVO: KPTL\nTese: deep-tech early-stage.\nEstágio alvo: seed"
    )
    return s


def test_pitch_writer_system_is_outbound():
    """mode=pitch seleciona o prompt de captação, não o de proposta de edital."""
    s = _make_pitch_session()
    assert "captação" in s._writer_system().lower()
    # proposta segue no prompt de edital
    assert "edital" in _make_session()._writer_system().lower()


def test_pitch_scope_has_no_edital_analogues():
    """Entidade não resolve análogos de edital — escopo é só o próprio id."""
    s = _make_pitch_session()
    assert s._resolve_edital_scope() == ["investidor:kptl"]


def test_pitch_outline_is_capture_genre():
    s = _make_pitch_session()
    outline = s._default_pitch_outline()
    assert any("Ask" in sec for sec in outline)
    assert any("Tração" in sec for sec in outline)


def test_search_edital_returns_fund_node_in_pitch():
    """No pitch, search_edital devolve o nó do fundo, não chunks de edital_chunks."""
    from core.agent_tools import build_writing_tools

    s = _make_pitch_session()
    tools = {t.name: t for t in build_writing_tools(s)}
    out = tools["search_edital"].func(query="qual a tese do fundo?")
    assert "FUNDO-ALVO" in out
    assert "KPTL" in out


def test_pitch_context_injected_in_initial_messages():
    s = _make_pitch_session()
    msgs = s._build_agent_initial_messages("escreva o problema", None, "")
    assert any("FUNDO-ALVO" in m["content"] for m in msgs)
