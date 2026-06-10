"""Coerência interna do critic: rascunho cruzado com as demais seções.

Cobre o helper que monta o contexto das outras seções (exclui a que está
sendo salva, respeita orçamento, lida com vazio) e a injeção desse contexto
no prompt do critic — sem bater em rede (retriever e LLM mockados).
"""
from __future__ import annotations

from types import SimpleNamespace

from core.llm.agent_tools.critic_agent import _build_proposal_context, run_critic


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


def test_run_critic_injects_siblings_into_prompt(monkeypatch):
    captured = {}

    def fake_create(**kw):
        captured["messages"] = kw["messages"]
        content = '{"approved": false, "issues": ["A conclusão cita 5 pesquisadores, mas a seção Equipe diz 3"], "feedback": "conflito entre seções"}'
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
        )

    fake_client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=fake_create))
    )
    monkeypatch.setattr("core.llm.llm_client.make_client", lambda **kw: fake_client)
    monkeypatch.setattr("core.retrieval.retriever.retrieve_chunks", lambda *a, **k: [])

    s = _StubSession(
        ["1. Equipe", "2. Conclusão"],
        {"1. Equipe": "A equipe é composta por 3 pesquisadores."},
    )
    res = run_critic(
        "Concluímos que nossos 5 pesquisadores garantem a execução.",
        "2. Conclusão",
        s,
    )

    user_msg = captured["messages"][1]["content"]
    assert "OUTRAS SEÇÕES JÁ REDIGIDAS" in user_msg
    assert "3 pesquisadores" in user_msg  # conteúdo da seção irmã entrou no prompt
    assert res.approved is False
    assert res.issues


def test_run_critic_pitch_uses_fund_node_not_edital_chunks(monkeypatch):
    """No mode=pitch, o critic cruza contra o nó do fundo (context-stuffing) e
    NÃO chama retrieve_chunks (edital_chunks). Substrato e prompt mudam."""
    captured = {}

    def fake_create(**kw):
        captured["messages"] = kw["messages"]
        content = '{"approved": false, "issues": ["O pitch diz que o fundo investe em série B, mas o estágio alvo é seed"], "feedback": "contradiz o fundo"}'
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
        )

    fake_client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=fake_create))
    )
    monkeypatch.setattr("core.llm.llm_client.make_client", lambda **kw: fake_client)

    # Se o critic tentar retrieve_chunks no pitch, o teste falha (não deve tocar edital_chunks).
    def _boom(*a, **k):
        raise AssertionError("retrieve_chunks não deve ser chamado no mode=pitch")
    monkeypatch.setattr("core.retrieval.retriever.retrieve_chunks", _boom)

    s = _StubSession(["1. Time", "2. Fit com a tese do fundo"], {})
    s.mode = "pitch"
    s._pitch_target_context = (
        "FUNDO-ALVO: KPTL\nTese: deep-tech early-stage.\nEstágio alvo: seed"
    )

    res = run_critic(
        "Seu fundo investe em série B, e é por isso que encaixamos.",
        "2. Fit com a tese do fundo",
        s,
    )

    system_msg = captured["messages"][0]["content"]
    user_msg = captured["messages"][1]["content"]
    assert "pitches de captação" in system_msg          # prompt de pitch, não de edital
    assert "DADOS DO FUNDO-ALVO" in user_msg
    assert "Estágio alvo: seed" in user_msg             # nó do fundo entrou no prompt
    assert res.approved is False
