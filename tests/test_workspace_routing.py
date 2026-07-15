from __future__ import annotations

from core.services.workspace_service import _dispatch_explorer


def test_barn_nao_recebe_redirect_e_mensagem_interna_nao_e_anexada(monkeypatch):
    captured = {}

    def fake_explore(self, **kwargs):
        captured.update(kwargs)
        return "Barn factual"

    monkeypatch.setattr("core.services.explore_agent.ExploreAgent.explore", fake_explore)
    out = _dispatch_explorer(
        "qual a tese de investimentos da Barn?", [], None,
        edital_ids=None, library_items=None,
    )
    assert out == "Barn factual"
    assert "Se o usuário pedir algo FORA DO ESCOPO" not in captured["message"]
    assert captured["route_decision"].intent.value == "ENTITY_FACT"


def test_acao_de_escrita_recebe_redirect_sem_chamar_agente(monkeypatch):
    def fail(*_args, **_kwargs):
        raise AssertionError("agente não deve ser chamado")

    monkeypatch.setattr("core.services.explore_agent.ExploreAgent.explore", fail)
    out = _dispatch_explorer("escreva a seção de impacto", [], None)
    assert "/escrita" in out
