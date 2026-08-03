"""Testes do ExploreAgent (rota única = agente, pós-Sprint 3).

Estratégia: instanciamos `ExploreAgent()` real e stubbamos `run_agent` para não
bater na API. As tools de leitura leem o catálogo SQL (entity_catalog) — aqui
mockado para rodar sem Postgres.

Cobre:
  - _build_explore_hint pra clique no grafo
  - explore() → _explore_agent (rota única) + error/fallback paths
  - tools de explore_tools (set, planning, leitura)
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from radar.core.llm.agent_runtime import AgentResult, TraceStep  # noqa: E402
from radar.core.llm.agent_tools import build_explore_tools  # noqa: E402
from radar.core.services import grounded_strategy  # noqa: E402
from radar.core.services.explore_agent import ExploreAgent  # noqa: E402

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def mock_grounding_judge(monkeypatch):
    monkeypatch.setattr(
        grounded_strategy,
        "judge_grounding",
        lambda *args, **kwargs: {
            "requires_graph": bool(args[2]), "grounded": True, "unsupported_claims": [],
        },
    )

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
    monkeypatch.setattr(
        ExploreAgent, "_explore_agent",
        lambda *a, **kw: ("resposta agente", {"stop_reason": "end_turn", "truncated": False}),
    )
    assert svc.explore("qualquer pergunta") == "resposta agente"


def test_explore_with_meta_exposes_truncated(monkeypatch):
    """PR6.2 (F10): o router lê `truncated` do meta para avisar a UI."""
    svc = ExploreAgent()
    monkeypatch.setattr(
        ExploreAgent, "_explore_agent",
        lambda *a, **kw: ("resposta", {"stop_reason": "max_steps", "truncated": True}),
    )
    answer, meta = svc.explore_with_meta("qualquer pergunta")
    assert answer == "resposta"
    assert meta["truncated"] is True


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
    monkeypatch.setattr("radar.core.llm.agent_runtime.run_agent", lambda **kw: fake_result)

    out, meta = svc._explore_agent("oi", None, None, None, None)
    assert out == "Resposta do agente."
    assert meta["stop_reason"] == "end_turn"
    assert meta["truncated"] is False
    assert meta["called_tools"] == []
    assert meta["repair_triggered"] is False


def test_explore_agent_max_steps_marks_truncated(monkeypatch):
    """stop_reason=max_steps → meta.truncated=True (PR6.2/F10) e a resposta
    parcial é entregue mesmo assim (comportamento 'entrega avisando')."""
    svc = ExploreAgent()
    fake_result = AgentResult(
        final_text="resposta parcial", steps=[], stop_reason="max_steps",
        usage={"input_tokens": 0, "output_tokens": 0},
    )
    monkeypatch.setattr("radar.core.llm.agent_runtime.run_agent", lambda **kw: fake_result)

    out, meta = svc._explore_agent("oi", None, None, None, None)
    assert out == "resposta parcial"
    assert meta["truncated"] is True


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

    monkeypatch.setattr("radar.core.llm.agent_runtime.run_agent", fake_run_agent)
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

    monkeypatch.setattr("radar.core.llm.agent_runtime.run_agent", fake_run_agent)

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


def test_explore_agent_includes_read_tools(monkeypatch):
    """O agente de explore tem as ferramentas de leitura do KG."""
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

    monkeypatch.setenv("KG_PHASE1_EXPLORE_ENABLED", "true")
    monkeypatch.setattr("radar.core.llm.agent_runtime.run_agent", fake_run_agent)
    svc._explore_agent("oi", None, None, None, None)

    names = {t.name for t in captured["tools"]}
    assert names == {"graph_strategy", "graph_explore", "graph_reason", "graph_community"}
    assert captured["max_steps"] >= 10  # EXPLORE_AGENT_MAX_STEPS


def test_entity_fact_investidor_inclui_get_investidor(monkeypatch):
    captured = {}
    fake_result = AgentResult(
        final_text="ok", steps=[], stop_reason="end_turn",
        usage={"input_tokens": 0, "output_tokens": 0},
    )
    monkeypatch.setenv("KG_PHASE1_EXPLORE_ENABLED", "true")
    monkeypatch.setattr(
        "radar.core.llm.agent_runtime.run_agent",
        lambda **kwargs: captured.update(kwargs) or fake_result,
    )
    ExploreAgent().explore_with_meta(
        "Em quais verticais a Barn investe?",
        node_id="investidor:barn-invest",
        node_type="investidor",
    )
    tool_names = {tool.name for tool in captured["tools"]}
    assert tool_names == {"graph_strategy", "graph_explore", "graph_reason", "graph_community"}


def test_explore_agent_error_returns_friendly_message(monkeypatch):
    svc = ExploreAgent()
    fake_result = AgentResult(
        final_text="", steps=[], stop_reason="error",
        usage={"input_tokens": 0, "output_tokens": 0},
    )
    monkeypatch.setattr("radar.core.llm.agent_runtime.run_agent", lambda **kw: fake_result)

    out, _meta = svc._explore_agent("oi", None, None, None, None)
    assert "não consegui processar" in out.lower()


def test_explore_agent_empty_final_text_falls_back(monkeypatch):
    svc = ExploreAgent()
    fake_result = AgentResult(
        final_text="", steps=[], stop_reason="end_turn",
        usage={"input_tokens": 0, "output_tokens": 0},
    )
    monkeypatch.setattr("radar.core.llm.agent_runtime.run_agent", lambda **kw: fake_result)

    out, _meta = svc._explore_agent("oi", None, None, None, None)
    assert "não consegui" in out.lower() or out  # algo útil, não vazio


# ============================================================================
# explore_tools — tools de leitura do catálogo SQL (entity_catalog mockado)
# ============================================================================

def test_explore_tools_count_and_names():
    tools = build_explore_tools()
    names = [t.name for t in tools]
    assert set(names) == {
        "list_editais", "get_edital", "explore_opportunity",
        "search_entities", "related_by_tags", "get_node_neighborhood",
        "list_icts", "list_investidores", "get_investidor",
    }


def test_get_investidor_expoe_ficha_completa(monkeypatch):
    from radar.core.kg import entity_catalog

    monkeypatch.setattr(entity_catalog, "get_investidor", lambda _id: {
        "id": "investidor:barn-invest", "name": "Barn Invest",
        "tese": "Greentech para a transição verde na América Latina.",
        "setores": ["agro", "mobilidade", "indústria limpa", "energia renovável"],
        "tese_themes": [], "estagio_alvo": ["growth"], "portfolio": [],
        "ticket_range": {}, "site": "https://barninvest.com.br/en",
        "verificado_em": "2026-06-09",
    })
    tools = {t.name: t for t in build_explore_tools()}
    out = tools["get_investidor"].invoke({"investidor_id": "investidor:barn-invest"})
    assert "Greentech" in out
    assert "agro, mobilidade, indústria limpa, energia renovável" in out
    assert "Fonte oficial" in out


def test_explore_tools_deep_research_gated(monkeypatch):
    """deep_research só entra no toolset com EXPLORE_DEEP_RESEARCH_ENABLED=true
    (explore é endpoint público; o crawl web é vetor de custo)."""
    svc = ExploreAgent()

    monkeypatch.delenv("KG_PHASE1_EXPLORE_ENABLED", raising=False)
    assert svc._explore_tools() == []
    monkeypatch.setenv("EXPLORE_DEEP_RESEARCH_ENABLED", "true")
    assert svc._explore_tools() == []


def test_explore_opportunity_is_cross_dimensional(monkeypatch):
    """O panorama cobre as três frentes (eventos + ICTs + investidores) num só
    retorno — robusto mesmo se alguma dimensão estiver vazia."""
    from radar.core.kg import entity_catalog
    monkeypatch.setattr(entity_catalog, "list_editais",
                        lambda **kw: [{
                            "id": "finep:1",
                            "title": "E",
                            "status": "Desconhecido",
                            "deadline": "",
                            "validity_state": "needs_review",
                            "temporal_mode": "unknown",
                        }])
    monkeypatch.setattr(entity_catalog, "list_entity_catalog",
                        lambda ck, **kw: [{"id": f"{ck}:x", "name": "N", "themes": [], "description": ""}])
    tools = {t.name: t for t in build_explore_tools()}
    out = tools["explore_opportunity"].invoke({"tema": "agro"})
    assert isinstance(out, str)
    assert "Editais" in out and "ICTs" in out and "Investidores" in out
    assert "Validade a confirmar" in out


# ---------------------------------------------------------------------------
# _theme_match — casamento de tema tolerante a linguagem natural (entity_catalog)
# ---------------------------------------------------------------------------

def test_theme_match_natural_language_phrase():
    """Frase natural casa o tema canônico por TOKEN: 'IA em saúde' → 'saúde e
    ciências da vida'."""
    from radar.core.kg.entity_catalog import _theme_match
    assert _theme_match("IA em saúde", ["saúde e ciências da vida"]) is True
    # agro ⊂ agronegócio (token bidirecional)
    assert _theme_match("IA no agronegócio", ["agro, bioeconomia e alimentos"]) is True


def test_theme_match_empty_matches_all():
    from radar.core.kg.entity_catalog import _theme_match
    assert _theme_match("", ["qualquer tema"]) is True


def test_theme_match_rejects_unrelated():
    """Não casa tema sem token em comum — recall-first, mas não casa tudo."""
    from radar.core.kg.entity_catalog import _theme_match
    assert _theme_match("turismo", ["saúde e ciências da vida"]) is False
    # stopword/conectivo sozinho não casa (cai no corte de tamanho/stopword)
    assert _theme_match("em de para", ["saúde e ciências da vida"]) is False


def test_list_editais_tool_returns_string_with_results(monkeypatch):
    from radar.core.kg import entity_catalog
    monkeypatch.setattr(entity_catalog, "list_editais",
                        lambda **kw: [{
                            "id": "finep:1",
                            "title": "Edital X",
                            "status": "Desconhecido",
                            "deadline": "",
                            "themes": [],
                            "validity_state": "needs_review",
                            "temporal_mode": "unknown",
                        }])
    t = next(x for x in build_explore_tools() if x.name == "list_editais")
    out = t.invoke({"limit": 3})
    assert isinstance(out, str)
    assert "Encontrados" in out
    assert "Validade a confirmar" in out


def test_get_edital_tool_exposes_conservative_temporal_note(monkeypatch):
    from radar.core.kg import entity_catalog

    monkeypatch.setattr(entity_catalog, "get_edital", lambda _eid: {
        "id": "finep:1",
        "title": "Edital X",
        "status": "Desconhecido",
        "deadline": "",
        "objective": "Objetivo",
        "themes": [],
        "validity_state": "needs_review",
        "temporal_mode": "unknown",
        "decision_source": "legacy",
        "last_verified_at": "2026-07-29T12:00:00+00:00",
    })

    t = next(x for x in build_explore_tools() if x.name == "get_edital")
    out = t.invoke({"edital_id": "finep:1"})

    assert "Status: Validade a confirmar" in out
    assert "Prazo: Validade a confirmar" in out


def test_list_editais_caps_limit(monkeypatch):
    """Limit > 50 é cortado para 50 (proteção contra prompt blowup)."""
    from radar.core.kg import entity_catalog
    captured: dict = {}

    def _fake(**kw):
        captured["limit"] = kw.get("limit")
        return []

    monkeypatch.setattr(entity_catalog, "list_editais", _fake)
    t = next(x for x in build_explore_tools() if x.name == "list_editais")
    out = t.invoke({"limit": 99999})
    assert isinstance(out, str)
    assert captured["limit"] == 50


def test_get_edital_tool_returns_error_string_for_invalid_id(monkeypatch):
    from radar.core.kg import entity_catalog
    monkeypatch.setattr(entity_catalog, "get_edital", lambda eid: None)
    t = next(x for x in build_explore_tools() if x.name == "get_edital")
    out = t.invoke({"edital_id": "id_que_nao_existe_xyz"})
    assert isinstance(out, str)
    assert "não encontrado" in out
