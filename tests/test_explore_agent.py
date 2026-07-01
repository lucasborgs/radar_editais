"""Testes do ExploreAgent (rota única = agente, pós-Sprint 3).

Estratégia: instanciamos `ExploreAgent()` real e stubbamos `run_agent` para não
bater na API. As tools de leitura leem o hipergrado (hypergraph_catalog/kg_store)
direto — sem index.json/wiki/GraphService.

Cobre:
  - _build_explore_hint pra clique no grafo
  - explore() → _explore_agent (rota única) + error/fallback paths
  - tools de explore_tools (set, planning, leitura)
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.llm.agent_runtime import AgentResult, TraceStep  # noqa: E402
from core.llm.agent_tools import build_explore_tools  # noqa: E402
from core.services.explore_agent import ExploreAgent  # noqa: E402

# ============================================================================
# _build_explore_hint
# ============================================================================

def test_build_explore_hint_empty_when_no_click():
    assert ExploreAgent._build_explore_hint(None, None, None) == ""
    assert ExploreAgent._build_explore_hint([], None, None) == ""


def test_build_explore_hint_node_only():
    h = ExploreAgent._build_explore_hint(None, "bioeconomia", "tema")
    assert "bioeconomia" in h
    assert "tipo=tema" in h
    assert "get_node_neighborhood" in h


def test_build_explore_hint_edital_ids_only():
    h = ExploreAgent._build_explore_hint(["finep:589", "finep:613"], None, None)
    assert "finep:589" in h
    assert "finep:613" in h
    assert "get_edital" in h or "get_node_neighborhood" in h


def test_build_explore_hint_node_and_ids():
    h = ExploreAgent._build_explore_hint(["finep:589"], "bioeconomia", "tema")
    assert "bioeconomia" in h
    assert "finep:589" in h


def test_build_explore_hint_caps_at_3_ids():
    """Mais que 3 IDs no clique não inunda o prompt."""
    ids = [f"finep:{i}" for i in range(10)]
    h = ExploreAgent._build_explore_hint(ids, None, None)
    assert "finep:0" in h
    assert "finep:9" not in h  # cap em 3


# ============================================================================
# explore() — rota única (sempre _explore_agent)
# ============================================================================

def test_explore_routes_to_agent(monkeypatch):
    svc = ExploreAgent()
    monkeypatch.setattr(ExploreAgent, "_explore_agent", lambda *a, **kw: "resposta agente")
    assert svc.explore("qualquer pergunta") == "resposta agente"


# ============================================================================
# _explore_agent (run_agent stubbed)
# ============================================================================

def test_explore_agent_happy_path(monkeypatch):
    svc = ExploreAgent()

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
    svc = ExploreAgent()
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
        node_id="bioeconomia", node_type="tema",
    )

    msgs = captured["initial_messages"]
    # hint vem antes da pergunta atual
    hint_msg = next((m for m in msgs if "bioeconomia" in m["content"]), None)
    assert hint_msg is not None
    last_msg = msgs[-1]
    assert last_msg["content"] == "qual o prazo?"


def test_explore_agent_passes_history_window(monkeypatch):
    """Apenas os últimos 8 turnos vão pro agente."""
    svc = ExploreAgent()
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
    """O agente de explore ganha write_todos além das tools de leitura."""
    svc = ExploreAgent()
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
    # as de leitura continuam presentes
    assert {"list_editais", "explore_opportunity", "list_icts", "get_node_neighborhood"} <= names
    assert captured["max_steps"] >= 8  # espaço pra planejamento + multi-task


def test_explore_agent_error_returns_friendly_message(monkeypatch):
    svc = ExploreAgent()
    fake_result = AgentResult(
        final_text="", steps=[], stop_reason="error",
        usage={"input_tokens": 0, "output_tokens": 0},
    )
    monkeypatch.setattr("core.llm.agent_runtime.run_agent", lambda **kw: fake_result)

    out = svc._explore_agent("oi", None, None, None, None)
    assert "não consegui processar" in out.lower()


def test_explore_agent_empty_final_text_falls_back(monkeypatch):
    svc = ExploreAgent()
    fake_result = AgentResult(
        final_text="", steps=[], stop_reason="end_turn",
        usage={"input_tokens": 0, "output_tokens": 0},
    )
    monkeypatch.setattr("core.llm.agent_runtime.run_agent", lambda **kw: fake_result)

    out = svc._explore_agent("oi", None, None, None, None)
    assert "não consegui" in out.lower() or out  # algo útil, não vazio


# ============================================================================
# explore_tools — tools de leitura do hipergrado
# ============================================================================

def test_explore_tools_count_and_names():
    tools = build_explore_tools()
    names = [t.name for t in tools]
    assert set(names) == {
        "list_editais", "get_edital",
        "get_node_neighborhood",
        "list_icts", "list_investidores", "explore_opportunity",
    }


def test_explore_tools_deep_research_gated(monkeypatch):
    """deep_research só entra no toolset com EXPLORE_DEEP_RESEARCH_ENABLED=true
    (explore é endpoint público; o crawl web é vetor de custo)."""
    svc = ExploreAgent()

    monkeypatch.delenv("EXPLORE_DEEP_RESEARCH_ENABLED", raising=False)
    off = {t.name for t in svc._explore_tools()}
    assert "deep_research" not in off
    # write_todos (planning) segue presente independentemente da flag
    assert "write_todos" in off

    monkeypatch.setenv("EXPLORE_DEEP_RESEARCH_ENABLED", "true")
    on = {t.name for t in svc._explore_tools()}
    assert "deep_research" in on


def test_explore_opportunity_is_cross_dimensional():
    """O panorama cobre as três frentes (eventos + ICTs + investidores) num só
    retorno — robusto mesmo se alguma dimensão estiver vazia no ambiente."""
    tools = {t.name: t for t in build_explore_tools()}
    fn = getattr(tools["explore_opportunity"], "func", tools["explore_opportunity"])
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
    tools = build_explore_tools()
    t = next(x for x in tools if x.name == "list_editais")
    out = t.invoke({"limit": 3})
    assert isinstance(out, str)
    # Sem assert sobre conteúdo específico — depende do hipergrado em disco —
    # mas deve mencionar quantidade ou ausência.
    assert ("Encontrados" in out) or ("Nenhum edital" in out)


def test_list_editais_caps_limit():
    """Limit > 50 é cortado para 50 (proteção contra prompt blowup)."""
    tools = build_explore_tools()
    t = next(x for x in tools if x.name == "list_editais")
    # Não dá pra inspecionar contagem direto; só garante que não levanta erro
    out = t.invoke({"limit": 99999})
    assert isinstance(out, str)


def test_get_edital_tool_returns_error_string_for_invalid_id():
    tools = build_explore_tools()
    t = next(x for x in tools if x.name == "get_edital")
    out = t.invoke({"edital_id": "id_que_nao_existe_xyz"})
    assert isinstance(out, str)
    assert "Erro" in out or "não encontrado" in out
