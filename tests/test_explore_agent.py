"""Testes do path agente de KGMatchService.explore (Sprint 3 do Cenário B).

Estratégia: instanciamos `KGMatchService()` real (lê o índice JSON de
knowledge_graph/index.json, que existe no repo) mas stubbamos `run_agent` para
não bater na API Anthropic.

Cobre:
  - dispatcher explore() → _explore_agent vs _explore_legacy
  - _build_explore_hint pra clique no grafo
  - _explore_agent error path
  - tools de explore_tools (list_editais, get_edital, find_analogues,
    get_graph_neighbors) — wrappers leves sobre KGMatchService já testado
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.llm.agent_runtime import AgentResult, TraceStep  # noqa: E402
from core.llm.agent_tools import build_explore_tools  # noqa: E402
from core.services.kg_match_service import KGMatchService  # noqa: E402

# ============================================================================
# _build_explore_hint
# ============================================================================

def test_build_explore_hint_empty_when_no_click():
    assert KGMatchService._build_explore_hint(None, None, None) == ""
    assert KGMatchService._build_explore_hint([], None, None) == ""


def test_build_explore_hint_node_only():
    h = KGMatchService._build_explore_hint(None, "radar-editais/temas/bio", "tema")
    assert "radar-editais/temas/bio" in h
    assert "tipo=tema" in h
    assert "get_graph_neighbors" in h


def test_build_explore_hint_edital_ids_only():
    h = KGMatchService._build_explore_hint(["finep:589", "finep:613"], None, None)
    assert "finep:589" in h
    assert "finep:613" in h
    assert "get_edital" in h or "find_analogues" in h


def test_build_explore_hint_node_and_ids():
    h = KGMatchService._build_explore_hint(
        ["finep:589"], "radar-editais/temas/bio", "tema",
    )
    assert "radar-editais/temas/bio" in h
    assert "finep:589" in h


def test_build_explore_hint_caps_at_3_ids():
    """Mais que 3 IDs no clique não inunda o prompt."""
    ids = [f"finep:{i}" for i in range(10)]
    h = KGMatchService._build_explore_hint(ids, None, None)
    assert "finep:0" in h
    assert "finep:9" not in h  # cap em 3


# ============================================================================
# explore() dispatcher
# ============================================================================

def test_explore_dispatches_to_agent_when_flag_true(monkeypatch):
    svc = KGMatchService()
    called = {"agent": False, "legacy": False}

    def fake_agent(self, *a, **kw):
        called["agent"] = True
        return "agent response"

    def fake_legacy(self, *a, **kw):
        called["legacy"] = True
        return "legacy response"

    monkeypatch.setattr(KGMatchService, "_explore_agent", fake_agent)
    monkeypatch.setattr(KGMatchService, "_explore_legacy", fake_legacy)

    out = svc.explore("oi", agent_enabled=True)
    assert called["agent"] is True
    assert called["legacy"] is False
    assert out == "agent response"


def test_explore_dispatches_to_legacy_by_default(monkeypatch):
    svc = KGMatchService()
    called = {"agent": False, "legacy": False}

    monkeypatch.setattr(
        KGMatchService, "_explore_agent",
        lambda self, *a, **kw: called.__setitem__("agent", True) or "x",
    )
    monkeypatch.setattr(
        KGMatchService, "_explore_legacy",
        lambda self, *a, **kw: called.__setitem__("legacy", True) or "y",
    )

    svc.explore("oi")  # agent_enabled default False
    assert called["agent"] is False
    assert called["legacy"] is True


# ============================================================================
# _explore_agent (run_agent stubbed)
# ============================================================================

def test_explore_agent_happy_path(monkeypatch):
    svc = KGMatchService()

    fake_result = AgentResult(
        final_text="Resposta do agente.",
        steps=[
            TraceStep(kind="llm", text="Resposta do agente.",
                      tool_uses=[], usage={"input_tokens": 50, "output_tokens": 10}),
        ],
        stop_reason="end_turn",
        usage={"input_tokens": 50, "output_tokens": 10},
    )
    monkeypatch.setattr("core.llm.agent_runtime.run_agent", lambda **kw: fake_result)

    out = svc._explore_agent("oi", None, None, None, None)
    assert out == "Resposta do agente."


def test_explore_agent_with_hint_passes_to_messages(monkeypatch):
    """Quando há clique, hint vai como user message antes da pergunta."""
    svc = KGMatchService()
    captured: dict = {}

    fake_result = AgentResult(
        final_text="ok", steps=[], stop_reason="end_turn",
        usage={"input_tokens": 0, "output_tokens": 0},
    )

    def fake_run_agent(**kw):
        captured["initial_messages"] = kw["initial_messages"]
        return fake_result

    monkeypatch.setattr("core.llm.agent_runtime.run_agent", fake_run_agent)
    svc._explore_agent(
        "qual o prazo?", history=None,
        edital_ids=None,
        node_id="radar-editais/temas/bio", node_type="tema",
    )

    msgs = captured["initial_messages"]
    # hint vem antes da pergunta atual
    hint_msg = next((m for m in msgs if "radar-editais/temas/bio" in m["content"]), None)
    assert hint_msg is not None
    last_msg = msgs[-1]
    assert last_msg["content"] == "qual o prazo?"


def test_explore_agent_passes_history_window(monkeypatch):
    """Apenas os últimos 8 turnos vão pro agente (igual ao legacy)."""
    svc = KGMatchService()
    captured: dict = {}

    fake_result = AgentResult(
        final_text="ok", steps=[], stop_reason="end_turn",
        usage={"input_tokens": 0, "output_tokens": 0},
    )

    def fake_run_agent(**kw):
        captured["msgs"] = kw["initial_messages"]
        return fake_result

    monkeypatch.setattr("core.llm.agent_runtime.run_agent", fake_run_agent)

    # 12 turnos no histórico
    history = [
        {"role": "user" if i % 2 == 0 else "assistant", "content": f"msg{i}"}
        for i in range(12)
    ]
    svc._explore_agent("nova pergunta", history=history,
                       edital_ids=None, node_id=None, node_type=None)

    msgs = captured["msgs"]
    # Últimos 8 turnos do history + a nova pergunta = 9 messages
    assert len(msgs) == 9
    # primeiro turno do history que entrou foi msg4
    assert msgs[0]["content"] == "msg4"
    assert msgs[-1]["content"] == "nova pergunta"


def test_explore_agent_includes_planning_tool(monkeypatch):
    """O agente de explore ganha write_todos (Opção A) além das 8 tools de
    leitura — habilita planejamento multi-etapa no chat de Descoberta."""
    svc = KGMatchService()
    captured: dict = {}

    fake_result = AgentResult(
        final_text="ok", steps=[], stop_reason="end_turn",
        usage={"input_tokens": 0, "output_tokens": 0},
    )

    def fake_run_agent(**kw):
        captured["tools"] = kw["tools"]
        captured["max_steps"] = kw["max_steps"]
        return fake_result

    monkeypatch.setattr("core.llm.agent_runtime.run_agent", fake_run_agent)
    svc._explore_agent("oi", None, None, None, None)

    names = {t.name for t in captured["tools"]}
    assert "write_todos" in names
    # as 8 de leitura continuam presentes
    assert {"list_editais", "oportunidades_por_tema", "list_icts"} <= names
    assert captured["max_steps"] >= 8  # espaço pra planejamento + multi-task


def test_explore_agent_error_returns_friendly_message(monkeypatch):
    svc = KGMatchService()
    fake_result = AgentResult(
        final_text="", steps=[], stop_reason="error",
        usage={"input_tokens": 0, "output_tokens": 0},
    )
    monkeypatch.setattr("core.llm.agent_runtime.run_agent", lambda **kw: fake_result)

    out = svc._explore_agent("oi", None, None, None, None)
    assert "não consegui processar" in out.lower()


def test_explore_agent_empty_final_text_falls_back(monkeypatch):
    svc = KGMatchService()
    fake_result = AgentResult(
        final_text="", steps=[], stop_reason="end_turn",
        usage={"input_tokens": 0, "output_tokens": 0},
    )
    monkeypatch.setattr("core.llm.agent_runtime.run_agent", lambda **kw: fake_result)

    out = svc._explore_agent("oi", None, None, None, None)
    assert "não consegui" in out.lower() or out  # algo útil, não vazio


# ============================================================================
# explore_tools — wrappers leves
# ============================================================================

def test_explore_tools_count_and_names():
    svc = KGMatchService()
    tools = build_explore_tools(svc)
    names = [t.name for t in tools]
    assert set(names) == {
        "list_editais", "get_edital", "find_analogues", "get_graph_neighbors",
        "find_ict_partners",
        # Cross-dimensionais (Fase 2 — chat de Descoberta sobre todas as dimensões)
        "list_icts", "list_investidores", "oportunidades_por_tema",
    }


def test_oportunidades_por_tema_is_cross_dimensional():
    """O panorama cobre as três frentes (eventos + ICTs + investidores) num só
    retorno — robusto mesmo se alguma dimensão estiver vazia no ambiente."""
    svc = KGMatchService()
    tools = {t.name: t for t in build_explore_tools(svc)}
    fn = getattr(tools["oportunidades_por_tema"], "func", tools["oportunidades_por_tema"])
    out = fn(tema="agro")
    assert isinstance(out, str)
    assert "Editais" in out and "ICTs" in out and "Investidores" in out


# ---------------------------------------------------------------------------
# _theme_match — casamento de tema tolerante a linguagem natural
# ---------------------------------------------------------------------------

def test_theme_match_natural_language_phrase():
    """Frase natural casa o tema canônico por TOKEN (o bug que devolvia vazio no
    chat cross-dim): 'IA em saúde' → 'saúde e ciências da vida'."""
    from core.llm.agent_tools.explore_tools import _theme_match
    assert _theme_match("IA em saúde", ["saúde e ciências da vida"]) is True
    # agro ⊂ agronegócio (token bidirecional)
    assert _theme_match("IA no agronegócio", ["agro, bioeconomia e alimentos"]) is True


def test_theme_match_empty_matches_all():
    from core.llm.agent_tools.explore_tools import _theme_match
    assert _theme_match("", ["qualquer tema"]) is True


def test_theme_match_rejects_unrelated():
    """Não casa tema sem token em comum — recall-first, mas não casa tudo."""
    from core.llm.agent_tools.explore_tools import _theme_match
    assert _theme_match("turismo", ["saúde e ciências da vida"]) is False
    # stopword/conectivo sozinho não casa (cai no corte de tamanho/stopword)
    assert _theme_match("em de para", ["saúde e ciências da vida"]) is False


def test_list_editais_tool_returns_string_with_results():
    svc = KGMatchService()
    tools = build_explore_tools(svc)
    t = next(x for x in tools if x.name == "list_editais")
    out = t.call({"limit": 3})
    assert isinstance(out, str)
    # Sem assert sobre conteúdo específico — depende do índice em disco —
    # mas deve mencionar quantidade ou ausência.
    assert ("Encontrados" in out) or ("Nenhum edital" in out)


def test_list_editais_caps_limit():
    """Limit > 50 é cortado para 50 (proteção contra prompt blowup)."""
    svc = KGMatchService()
    tools = build_explore_tools(svc)
    t = next(x for x in tools if x.name == "list_editais")
    # Não dá pra inspecionar contagem direto; só garante que não levanta erro
    out = t.call({"limit": 99999})
    assert isinstance(out, str)


def test_get_edital_tool_returns_error_string_for_invalid_id():
    svc = KGMatchService()
    tools = build_explore_tools(svc)
    t = next(x for x in tools if x.name == "get_edital")
    out = t.call({"edital_id": "id_que_nao_existe_xyz"})
    assert isinstance(out, str)
    assert "Erro" in out or "não encontrado" in out


def test_find_analogues_tool_handles_missing_id():
    svc = KGMatchService()
    tools = build_explore_tools(svc)
    t = next(x for x in tools if x.name == "find_analogues")
    out = t.call({"edital_id": "id_invalido"})
    assert isinstance(out, str)
    assert "análogo" in out.lower() or "Nenhum" in out


def test_get_graph_neighbors_tool_handles_missing_node():
    svc = KGMatchService()
    tools = build_explore_tools(svc)
    t = next(x for x in tools if x.name == "get_graph_neighbors")
    out = t.call({"node_id": "no/que/nao/existe"})
    assert isinstance(out, str)
    assert "não tem" in out.lower() or "não existe" in out.lower()
