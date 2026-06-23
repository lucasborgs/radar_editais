"""Testes do DeepResearch (core/deep_research + research_tools — Fase A)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import deep_research as dr
from core import web_search as ws
from core.llm.agent_runtime import AgentResult
from core.llm.agent_tools.research_tools import build_research_tools


def _fake_hits():
    return [
        ws.SearchHit("Mercado X", "http://a", "cresce 10%", "conteúdo a"),
        ws.SearchHit("Dado Y", "http://b", "vale 5bi", "conteúdo b"),
        ws.SearchHit("Mercado X dup", "http://a", "dup", "dup"),  # URL duplicada
    ]


def test_run_deep_research_collects_and_dedups_sources(monkeypatch):
    # web_search devolve hits fixos; run_subagent finge que o subagente buscou.
    monkeypatch.setattr(ws, "web_search", lambda q, k=5: _fake_hits())

    def fake_run_subagent(**kw):
        tools = {t.name: t for t in kw["tools"]}
        tools["web_search"].invoke({"query": "tamanho mercado X", "k": 3})
        return AgentResult(final_text="O mercado X cresce 10% [http://a].",
                           steps=[], stop_reason="end_turn", usage={})

    monkeypatch.setattr(dr, "run_subagent", fake_run_subagent)

    res = dr.run_deep_research("qual o tamanho do mercado X?")
    assert res.answer.startswith("O mercado X cresce")
    # dedup por URL: a (dup colapsa) + b = 2 fontes
    assert [s.url for s in res.sources] == ["http://a", "http://b"]
    assert res.stop_reason == "end_turn"


def test_run_deep_research_agent_error_degrades(monkeypatch):
    # run_subagent já absorve a exceção interna e devolve stop_reason="error";
    # run_deep_research só propaga esse resultado degradado.
    def degraded(**kw):
        return AgentResult(final_text="", steps=[], stop_reason="error", usage={})

    monkeypatch.setattr(dr, "run_subagent", degraded)
    res = dr.run_deep_research("x")
    assert res.answer == "" and res.sources == [] and res.stop_reason == "error"


def test_web_search_tool_handles_no_key(monkeypatch):
    """A tool interna web_search converte WebSearchError em string (não quebra)."""
    def fake_run_subagent(**kw):
        tools = {t.name: t for t in kw["tools"]}
        out = tools["web_search"].invoke({"query": "q"})
        assert "indispon" in out.lower()  # mensagem amigável, não exceção
        return AgentResult(final_text="não encontrei", steps=[],
                           stop_reason="end_turn", usage={})

    def raise_no_key(q, k=5):
        raise ws.WebSearchError("TAVILY_API_KEY não configurada")

    monkeypatch.setattr(ws, "web_search", raise_no_key)
    monkeypatch.setattr(dr, "run_subagent", fake_run_subagent)
    res = dr.run_deep_research("q")
    assert res.answer == "não encontrei"
    assert res.sources == []  # nada coletado


def test_deep_research_tool_formats_with_sources(monkeypatch):
    result = dr.DeepResearchResult(
        answer="Resposta sintetizada.",
        sources=[dr.Source("Fonte A", "http://a"), dr.Source("", "http://b")],
        stop_reason="end_turn",
    )
    monkeypatch.setattr("core.llm.agent_tools.research_tools.run_deep_research",
                        lambda q, **kwargs: result)
    tool = build_research_tools()[0]
    assert tool.name == "deep_research"
    out = tool.invoke({"question": "qualquer"})
    assert "Resposta sintetizada." in out
    assert "Fontes encontradas:" in out
    assert "http://a" in out and "http://b" in out


def test_deep_research_tool_graceful_when_empty(monkeypatch):
    monkeypatch.setattr(
        "core.llm.agent_tools.research_tools.run_deep_research",
        lambda q, **kwargs: dr.DeepResearchResult(answer="", sources=[], stop_reason="error"),
    )
    tool = build_research_tools()[0]
    out = tool.invoke({"question": "x"})
    assert "não consegui pesquisar" in out.lower()


def test_redator_inclui_deep_research():
    """Guard-rail inverso da Fase C: aqui a tool DEVE estar no Redator."""
    import inspect

    from core.llm.agent_tools import writing_tools
    src = inspect.getsource(writing_tools.build_writing_tools)
    assert "build_research_tools" in src
