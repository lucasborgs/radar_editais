"""Coerência interna do critic: rascunho cruzado com as demais seções.

Cobre o helper que monta o contexto das outras seções (exclui a que está
sendo salva, respeita orçamento, lida com vazio) e a injeção desse contexto
no prompt do critic — sem bater em rede (retriever e LLM mockados).
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from radar.core.llm.agent_tools.critic_agent import _build_proposal_context, run_critic

pytestmark = pytest.mark.unit


class _StubSession:
    def __init__(self, outline, sections):
        self._proposal_outline = outline
        self._doc_sections = sections
        self._db = object()
        self._scope_edital_ids = ["finep:612"]
        self.session_id = "sess-coher"


def test_build_context_excludes_current_and_empty():
    s = _StubSession(
        ["1. Equipe", "2. Metodologia", "3. Conclusão"],
        {"1. Equipe": "Equipe de 3 pesquisadores.", "2. Metodologia": "", "3. Conclusão": "..."},
    )
    ctx = _build_proposal_context(s, "3. Conclusão")
    assert "1. Equipe" in ctx
    assert "Equipe de 3 pesquisadores." in ctx
    assert "2. Metodologia" not in ctx   # vazia, ignorada
    assert "3. Conclusão" not in ctx     # é a seção sendo salva


def test_build_context_empty_when_no_siblings():
    s = _StubSession(["1. Equipe"], {"1. Equipe": "conteúdo"})
    ctx = _build_proposal_context(s, "1. Equipe")
    assert "Nenhuma outra seção" in ctx


def test_build_context_respects_budget():
    big = "x" * 10_000
    s = _StubSession(["A", "B"], {"A": big, "B": "alvo"})
    ctx = _build_proposal_context(s, "B", budget=500)
    assert len(ctx) <= 600  # ~budget + cabeçalho


def test_build_context_defensive_without_attrs():
    # sessão que não expõe _doc_sections/_proposal_outline não deve quebrar
    bare = SimpleNamespace()
    ctx = _build_proposal_context(bare, "Qualquer")
    assert "Nenhuma outra seção" in ctx


# ── Critic-como-sub-agente (Item 5 da spec) ──
# run_critic agora roda via run_subagent: o substrato (edital/fundo, perfil,
# outras seções) chega por TOOLS, não mais context-stuffed num único prompt.
# Mockamos run_subagent para (a) capturar as tools e os params de invocação e
# (b) exercitar as tools, verificando o roteamento de substrato por gênero.
from radar.core.llm.agent_runtime import AgentResult, TraceStep  # noqa: E402


def _canned_result(content: str, n_steps: int = 1) -> AgentResult:
    steps = [TraceStep(kind="llm", text=content) for _ in range(n_steps)]
    return AgentResult(final_text=content, steps=steps, stop_reason="end_turn", usage={})


def test_run_critic_subagent_proposal_siblings_via_tool(monkeypatch):
    """Gênero proposta: o critic usa run_subagent (provider=openai, gpt-4o,
    temperature=0.05); a seção irmã chega via a tool read_proposal_sections, e o
    edital via read_target_context (que faz o retrieve)."""
    import radar.core.llm.agent_tools.critic_agent as ca
    captured = {}

    monkeypatch.setattr(
        "radar.core.retrieval.retriever.retrieve_chunks", lambda *a, **k: []
    )

    def fake_run_subagent(**kw):
        captured.update(kw)
        # Exercita as tools para provar o roteamento de substrato.
        tools_by_name = {t.name: t for t in kw["tools"]}
        captured["tool_names"] = set(tools_by_name)
        captured["siblings"] = tools_by_name["read_proposal_sections"].invoke({})
        captured["target"] = tools_by_name["read_target_context"].invoke({"query": "equipe"})
        return _canned_result(
            '{"approved": false, "issues": ["A conclusão cita 5 pesquisadores, '
            'mas a seção Equipe diz 3"], "feedback": "conflito entre seções"}'
        )

    monkeypatch.setattr(ca, "run_subagent", fake_run_subagent)

    s = _StubSession(
        ["1. Equipe", "2. Conclusão"],
        {"1. Equipe": "A equipe é composta por 3 pesquisadores."},
    )
    res = run_critic(
        "Concluímos que nossos 5 pesquisadores garantem a execução.",
        "2. Conclusão",
        s,
    )

    assert captured["provider"] == "openai"
    assert captured["model"] in ("gpt-4o",) or captured["model"]
    assert captured["temperature"] == 0.05
    assert captured["max_steps"] == 3
    assert captured["tool_names"] == {
        "read_target_context", "read_company_profile", "read_proposal_sections",
    }
    assert "3 pesquisadores" in captured["siblings"]  # seção irmã via tool
    assert res.approved is False
    assert res.issues


def test_run_critic_subagent_pitch_target_is_fund_node(monkeypatch):
    """mode=pitch: read_target_context retorna o nó do fundo (context-stuffing) e
    NÃO chama retrieve_chunks (edital_chunks)."""
    import radar.core.llm.agent_tools.critic_agent as ca
    captured = {}

    def _boom(*a, **k):
        raise AssertionError("retrieve_chunks não deve ser chamado no mode=pitch")
    monkeypatch.setattr("radar.core.retrieval.retriever.retrieve_chunks", _boom)

    def fake_run_subagent(**kw):
        captured.update(kw)
        tools_by_name = {t.name: t for t in kw["tools"]}
        captured["target"] = tools_by_name["read_target_context"].invoke({"query": "estágio"})
        return _canned_result(
            '{"approved": false, "issues": ["O pitch diz que o fundo investe em '
            'série B, mas o estágio alvo é seed"], "feedback": "contradiz o fundo"}'
        )

    monkeypatch.setattr(ca, "run_subagent", fake_run_subagent)

    s = _StubSession(["1. Time", "2. Fit com a tese do fundo"], {})
    s.mode = "pitch"
    s._source_card_context = (
        "FUNDO-ALVO: KPTL\nTese: deep-tech early-stage.\nEstágio alvo: seed"
    )

    res = run_critic(
        "Seu fundo investe em série B, e é por isso que encaixamos.",
        "2. Fit com a tese do fundo",
        s,
    )

    # prompt de pitch, não de edital
    assert "pitches de captação" in captured["system"]
    # nó do fundo retornado pela tool (sem tocar edital_chunks)
    assert "Estágio alvo: seed" in captured["target"]
    assert res.approved is False


def test_run_critic_degrades_on_subagent_error(monkeypatch):
    """stop_reason=='error' do sub-agente → approved=True (save nunca bloqueia)."""
    import radar.core.llm.agent_tools.critic_agent as ca

    monkeypatch.setattr(
        ca, "run_subagent",
        lambda **kw: AgentResult(final_text="", steps=[], stop_reason="error", usage={}),
    )
    s = _StubSession(["1. Equipe"], {"1. Equipe": "x"})
    res = run_critic("qualquer rascunho", "1. Equipe", s)
    assert res.approved is True
    assert "indisponível" in res.feedback.lower()


def test_run_critic_degrades_on_unparseable_verdict(monkeypatch):
    """final_text não-JSON → approved=True por fallback."""
    import radar.core.llm.agent_tools.critic_agent as ca

    monkeypatch.setattr(
        ca, "run_subagent",
        lambda **kw: _canned_result("não consegui decidir, desculpe"),
    )
    s = _StubSession(["1. Equipe"], {"1. Equipe": "x"})
    res = run_critic("qualquer rascunho", "1. Equipe", s)
    assert res.approved is True
