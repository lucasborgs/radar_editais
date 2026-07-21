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

from core.llm.agent_graph import WritingTurnOutcome  # noqa: E402
from core.llm.agent_runtime import AgentResult, TraceStep  # noqa: E402
from core.services.writing_session import WritingSession  # noqa: E402


def _outcome(result: AgentResult, *, interrupt=None, n_messages=0) -> WritingTurnOutcome:
    """Atalho para o retorno de run_writing_turn nos stubs (Etapa 3)."""
    return WritingTurnOutcome(result=result, interrupt=interrupt, n_messages=n_messages)


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
    s._source_card_context = ""
    s._programa_context = ""
    s._project_description = None
    s._pending_user_input = None
    s._plan = None
    s._plan_pending_confirmation = False
    s._tool_results = []
    s._critic_fail_open_count = 0
    s._playbook_writer_block = ""  # F5: vazio — nenhum mecanismo resolvido
    s._playbook_monitor_block = ""  # F5: vazio
    s._estilo_empresa_block = ""  # estilo de escrita — vazio por padrão (plano playbook-overlays)
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
    s = _make_session()
    trace = s._extract_tool_trace(steps)
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
    s = _make_session()
    trace = s._extract_tool_trace(steps)
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
    s = _make_session()
    assert s._extract_tool_trace(steps) == []


def test_extract_tool_trace_save_draft_exposes_saved_section_and_critic_result():
    """F3: save_draft bem-sucedido carrega `saved_section` e `critic_result`
    estruturados de _tool_results, sem regex."""
    steps = [
        TraceStep(
            kind="llm", text="vou salvar",
            tool_uses=[{"id": "tu_1", "name": "save_draft",
                        "input": {"section_title": "metodologia", "content": "..."}}],
            usage={},
        ),
        TraceStep(
            kind="tool", name="save_draft",
            input={"section_title": "metodologia", "content": "..."},
            output="Rascunho salvo em '2. Metodologia' (320 chars) (aprovado pelo critic). "
                   "Continue a conversa ou prossiga para a próxima seção.",
        ),
    ]
    s = _make_session()
    s._tool_results.append({
        "section_title": "2. Metodologia",
        "critic_result": {"approved": True, "issues": [], "feedback": ""},
    })
    trace = s._extract_tool_trace(steps)
    assert len(trace) == 1
    assert trace[0]["name"] == "save_draft"
    assert trace[0]["saved_section"] == "2. Metodologia"
    assert trace[0]["critic_result"] == {"approved": True, "issues": [], "feedback": ""}


def test_extract_tool_trace_save_draft_blocked_has_no_saved_section():
    """F3: quando o critic bloqueia, a estrutura é a mesma — `saved_section`
    e `critic_result` vêm de _tool_results (que registraram o bloqueio)."""
    steps = [
        TraceStep(
            kind="llm", text="",
            tool_uses=[{"id": "tu_1", "name": "save_draft",
                        "input": {"section_title": "2. Metodologia", "content": "x"}}],
            usage={},
        ),
        TraceStep(
            kind="tool", name="save_draft",
            input={"section_title": "2. Metodologia", "content": "x"},
            output="Critic encontrou 1 problema(s) antes de salvar:\n• Vago demais.",
        ),
    ]
    s = _make_session()
    s._tool_results.append({
        "section_title": "2. Metodologia",
        "critic_result": {"approved": False, "issues": ["Vago demais."], "feedback": ""},
    })
    trace = s._extract_tool_trace(steps)
    assert len(trace) == 1
    assert trace[0]["saved_section"] == "2. Metodologia"
    assert trace[0]["critic_result"]["approved"] is False
    assert "Vago demais." in trace[0]["critic_result"]["issues"]


def test_extract_tool_trace_save_draft_missing_tool_results():
    """_extract_tool_trace não quebra se _tool_results estiver vazio
    (fallback seguro — não savou, não tem estrutura)."""
    steps = [
        TraceStep(
            kind="tool", name="save_draft",
            input={"section_title": "x", "content": "y"},
            output="Critic encontrou 1 problema(s) antes de salvar:\n• Vago demais.",
        ),
    ]
    s = _make_session()
    trace = s._extract_tool_trace(steps)
    assert len(trace) == 1
    assert "saved_section" not in trace[0]
    assert "critic_result" not in trace[0]


def test_extract_tool_trace_multi_save_draft_fifo_invariant():
    """F3-B1: dois save_draft no mesmo turno, 1º com título inválido.
    O 1º (falho) não recebe saved_section do 2º (sucesso). A invariante 1:1
    é mantida por todos os caminhos de retorno da tool."""
    s = _make_session()
    s._tool_results = [
        {"section_title": None, "critic_result": None},  # sentinela do 1º (título inválido)
        {"section_title": "2. Descrição",
         "critic_result": {"approved": True, "issues": [], "feedback": ""}},
    ]
    steps = [
        TraceStep(kind="llm", text="", usage={}, tool_uses=[
            {"id": "tu_1", "name": "save_draft",
             "input": {"section_title": "Secao Inexistente", "content": "x"}},
            {"id": "tu_2", "name": "save_draft",
             "input": {"section_title": "2. Descrição", "content": "real"}},
        ]),
        TraceStep(kind="tool", name="save_draft",
                  input={"section_title": "Secao Inexistente", "content": "x"},
                  output="Título 'Secao Inexistente' não está no outline..."),
        TraceStep(kind="tool", name="save_draft",
                  input={"section_title": "2. Descrição", "content": "real"},
                  output="Rascunho salvo em '2. Descrição' (4 chars) (aprovado pelo critic)"),
    ]
    trace = s._extract_tool_trace(steps)
    assert len(trace) == 2
    # 1º save_draft: falhou (título inválido) — sem saved_section
    assert trace[0]["name"] == "save_draft"
    assert "saved_section" not in trace[0], "1º save (falho) não deve ter saved_section"
    # 2º save_draft: sucesso — saved_section e critic_result presentes
    assert trace[1]["name"] == "save_draft"
    assert trace[1]["saved_section"] == "2. Descrição"
    assert trace[1]["critic_result"]["approved"] is True


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

    def fake_run_writing_turn(**kwargs):
        return _outcome(fake_result)

    monkeypatch.setattr("core.llm.agent_graph.run_writing_turn", fake_run_writing_turn)

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
    monkeypatch.setattr(
        "core.llm.agent_graph.run_writing_turn", lambda **kw: _outcome(fake_result),
    )

    s._turn_count = 1
    result = s._turn_agent("qual o prazo?", section_hint=None, user_turn_index=1)

    assert result["success"] is True
    assert result["assistant_message"] == "Prazo é 30/06."
    assert len(result["tool_trace"]) == 1
    assert result["tool_trace"][0]["name"] == "search_edital"
    assert result["tool_trace"][0]["output"] == "trecho: 30/06"

    # A row assistant grava tokens = input+output de todo o
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
    monkeypatch.setattr(
        "core.llm.agent_graph.run_writing_turn", lambda **kw: _outcome(fake_result),
    )

    s._turn_count = 1
    result = s._turn_agent("oi", section_hint=None, user_turn_index=1)
    assert result["success"] is False
    assert result["error_type"] == "AGENT_ERROR"
    # turn_count voltou pra 0 (rollback)
    assert s._turn_count == 0


def test_turn_agent_interrupt_surfaces_pending_and_persists_question(monkeypatch):
    """Etapa 3: request_user_info → interrupt(). O outcome traz `interrupt`; a
    PERGUNTA vira a msg do assistente, o response carrega {field, prompt}, e o
    estado interno guarda thread_id + n_msgs para a retomada."""
    s = _make_session()

    # Trace parcial: o agente chamou search antes de pedir info (turno interrompido).
    partial = AgentResult(
        final_text="",
        steps=[
            TraceStep(kind="llm", text="vou pedir",
                      tool_uses=[{"id": "tu_1", "name": "request_user_info",
                                  "input": {"field": "cnpj", "prompt": "Qual o CNPJ?"}}],
                      usage={"input_tokens": 100, "output_tokens": 10}),
        ],
        stop_reason="end_turn",
        usage={"input_tokens": 100, "output_tokens": 10},
    )
    monkeypatch.setattr(
        "core.llm.agent_graph.run_writing_turn",
        lambda **kw: _outcome(
            partial, interrupt={"field": "cnpj", "prompt": "Qual o CNPJ?"}, n_messages=3,
        ),
    )
    # Item 3: thread-por-sessão — helpers de checkpointer mockados (unit hermético).
    monkeypatch.setattr("core.llm.agent_graph.get_thread_message_count", lambda *a, **k: 0)
    monkeypatch.setattr("core.llm.agent_graph.trim_thread_history", lambda *a, **k: 0)

    s._turn_count = 1
    result = s._turn_agent("escreva a identificação", section_hint=None, user_turn_index=1)

    # Response expõe só {field, prompt} (sem thread_id interno).
    assert result["pending_user_input"] == {"field": "cnpj", "prompt": "Qual o CNPJ?"}
    # A pergunta é a msg do assistente persistida (espelha o chat).
    assert result["assistant_message"] == "Qual o CNPJ?"
    # Item 3: thread da SESSÃO (sem :turn); discriminador de resume preservado.
    assert s._pending_user_input["thread_id"] == "ws_1:sess_1"
    assert s._pending_user_input["n_msgs"] == 3


def test_turn_agent_resume_routes_command_and_clears_pending(monkeypatch):
    """Quando há interrupt pendente (com thread_id), turn() retoma o thread via
    resume= e fecha a pergunta: pending limpo, resposta final no chat."""
    s = _make_session()
    # Estado de uma sessão recarregada com interrupt em aberto (thread da sessão).
    s._pending_user_input = {
        "field": "cnpj", "prompt": "Qual o CNPJ?",
        "thread_id": "ws_1:sess_1", "n_msgs": 3,
    }

    captured: dict = {}

    final = AgentResult(
        final_text="CNPJ registrado. Seção concluída.",
        steps=[
            TraceStep(kind="tool", name="request_user_info",
                      input={}, output="O usuário respondeu (campo 'cnpj'): 12.345"),
            TraceStep(kind="llm", text="CNPJ registrado. Seção concluída.",
                      tool_uses=[], usage={"input_tokens": 80, "output_tokens": 12}),
        ],
        stop_reason="end_turn",
        usage={"input_tokens": 80, "output_tokens": 12},
    )

    def fake(**kw):
        captured.update(kw)
        return _outcome(final, interrupt=None, n_messages=5)

    monkeypatch.setattr("core.llm.agent_graph.run_writing_turn", fake)
    # Item 3: prior_n_msgs vem do checkpointer (não de resume_ctx["n_msgs"]).
    monkeypatch.setattr("core.llm.agent_graph.get_thread_message_count", lambda *a, **k: 3)
    monkeypatch.setattr("core.llm.agent_graph.trim_thread_history", lambda *a, **k: 0)

    result = s.turn("12.345.678/0001-90")

    # Retomou o MESMO thread da sessão com resume = a resposta do usuário.
    assert captured["resume"] == "12.345.678/0001-90"
    assert captured["thread_id"] == "ws_1:sess_1"
    # prior_n_msgs sourced do checkpointer (mock=3), não mais de resume_ctx.
    assert captured["prior_n_msgs"] == 3
    # Pergunta fechada: pending limpo e resposta final no chat.
    assert s._pending_user_input is None
    assert result["pending_user_input"] is None
    assert result["assistant_message"] == "CNPJ registrado. Seção concluída."


def test_turn_agent_gates_trim_to_fresh_turns_only(monkeypatch):
    """Item 3 (regressão da revisão T4): `_trim_thread_history` roda em turno
    FRESCO mas NUNCA no resume — podar uma thread pausada num interrupt via
    update_state descarta o estado pendente e quebra o Command(resume)."""
    s = _make_session()
    trim_calls: list = []
    monkeypatch.setattr(s, "_trim_thread_history", lambda tid: trim_calls.append(tid))
    monkeypatch.setattr("core.llm.agent_graph.get_thread_message_count", lambda *a, **k: 0)

    final = AgentResult(
        final_text="ok", steps=[], stop_reason="end_turn",
        usage={"input_tokens": 10, "output_tokens": 5},
    )
    monkeypatch.setattr(
        "core.llm.agent_graph.run_writing_turn",
        lambda **kw: _outcome(final, interrupt=None, n_messages=4),
    )

    # Turno FRESCO → poda roda.
    s._turn_count = 1
    s._turn_agent("primeiro turno", section_hint=None, user_turn_index=1, resume_ctx=None)
    assert trim_calls == ["ws_1:sess_1"], "turno fresco deve podar"

    # RESUME → poda NÃO roda (thread pausada).
    trim_calls.clear()
    s._turn_count = 2
    s._turn_agent(
        "resposta ao interrupt", section_hint=None, user_turn_index=2,
        resume_ctx={"thread_id": "ws_1:sess_1", "n_msgs": 3},
    )
    assert trim_calls == [], "resume NÃO pode podar a thread pausada"


def _bridge_session(monkeypatch, *, thread_count):
    """Sessão com _history não-vazio (pós plan-first) + captura do run_writing_turn."""
    s = _make_session()
    s._history = [
        {"role": "user", "content": "Redija a descrição. Depois substitua a primeira frase por 'NOSSO PROJETO X'."},
        {"role": "assistant", "content": "## Plano da Proposta — 11 seções."},
    ]
    monkeypatch.setattr("core.llm.agent_graph.get_thread_message_count", lambda *a, **k: thread_count)
    monkeypatch.setattr(s, "_trim_thread_history", lambda tid: None)
    captured: dict = {}
    final = AgentResult(final_text="ok", steps=[], stop_reason="end_turn",
                        usage={"input_tokens": 10, "output_tokens": 5})
    monkeypatch.setattr(
        "core.llm.agent_graph.run_writing_turn",
        lambda **kw: (captured.update(kw), _outcome(final))[1],
    )
    return s, captured


def test_turn_agent_bridges_history_into_empty_thread(monkeypatch):
    """PONTE (fix do NO-GO): 1º turno `_turn_agent` com thread VAZIA + _history
    não-vazio (o plan-first não escreveu na thread) → semeia o histórico no
    payload, para o agente ver a instrução de edição do usuário. Sem isso a
    edição se perdia (user_edit_preserved 1.0→0.0)."""
    s, captured = _bridge_session(monkeypatch, thread_count=0)  # thread vazia
    n_hist = len(s._history)  # ANTES do turno (_turn_agent appenda ao _history)
    s._turn_count = 1
    s._turn_agent("Finalize e salve.", section_hint="2. Descrição do projeto",
                  user_turn_index=1, resume_ctx=None)
    texts = [m["content"] for m in captured["initial_messages"]]
    assert any("substitua a primeira frase" in t for t in texts), \
        "histórico (com a edição) deve ser semeado no payload"
    # delta pula o histórico semeado: system(1) + prefixo(1) + len(history original)
    assert captured["prior_n_msgs"] == 2 + n_hist


def test_turn_agent_bridge_idempotent_when_thread_populated(monkeypatch):
    """Idempotência da ponte: thread JÁ com conteúdo → NÃO re-semeia o histórico
    (o checkpointer já o tem); prior_n_msgs vem do checkpointer, não do 2+len."""
    s, captured = _bridge_session(monkeypatch, thread_count=7)  # thread não-vazia
    s._turn_count = 3
    s._turn_agent("Continue.", section_hint="2. Descrição do projeto",
                  user_turn_index=3, resume_ctx=None)
    texts = [m["content"] for m in captured["initial_messages"]]
    assert not any("substitua a primeira frase" in t for t in texts), \
        "thread não-vazia: NÃO re-semeia (evita duplicar histórico)"
    assert captured["prior_n_msgs"] == 7


# ============================================================================
# Modo PITCH (investidor, kind_class=entidade) — Fatia 2 multi-quadrante
# ============================================================================

def _make_pitch_session() -> WritingSession:
    """Sessão de pitch (alvo = fundo investidor). Mesmo padrão __new__ do fixture
    de proposta, com o nó do fundo injetado como contexto (context-stuffing)."""
    s = _make_session()
    s.edital_id = "investidor:kptl"
    s.mode = "pitch"
    s._source_card_context = (
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
    from core.llm.agent_tools import build_writing_tools

    s = _make_pitch_session()
    tools = {t.name: t for t in build_writing_tools(s)}
    out = tools["search_edital"].func(query="qual a tese do fundo?")
    assert "FUNDO-ALVO" in out
    assert "KPTL" in out


def test_pitch_context_injected_in_initial_messages():
    s = _make_pitch_session()
    msgs = s._build_thread_initial_messages("escreva o problema", None, "")
    assert any("FUNDO-ALVO" in m["content"] for m in msgs)
