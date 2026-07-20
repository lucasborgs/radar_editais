from __future__ import annotations

from core.services.workspace_service import (
    _REDIRECT_BLOCK,
    _dispatch_explorer,
    dispatch,
)


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


def test_redirect_block_nao_contem_recusa():
    """_REDIRECT_BLOCK não contém mais instrução de recusa (Task 1)."""
    assert "recuse" not in _REDIRECT_BLOCK
    assert "NÃO tente executar" not in _REDIRECT_BLOCK
    assert "troca de contexto" in _REDIRECT_BLOCK
    assert "automaticamente" in _REDIRECT_BLOCK


def test_dispatch_handoff_explorer_para_escrita(monkeypatch):
    """dispatch(explorer, 'escreva...') invoca escrita e retorna mode=escrita."""
    escrita_called = []

    def fake_escrita(db, session_id, workspace_id, profile, message, library_items=None):
        escrita_called.append(message)
        return "conteúdo da seção de impacto"

    monkeypatch.setattr("core.services.workspace_service._dispatch_escrita", fake_escrita)
    monkeypatch.setattr("core.services.workspace_service._load_session_edital_id", lambda db, sid: None)
    monkeypatch.setattr("core.services.workspace_service._mode_history", lambda db, sid, mode, window=8: [])
    monkeypatch.setattr("core.services.workspace_service._save_turn", lambda *a, **kw: None)

    result = dispatch(
        db=object(), session_id="s", workspace_id="w", profile=None,
        mode="explorer", message="escreva a seção de impacto",
    )

    assert result["mode"] == "escrita"
    assert len(escrita_called) == 1
    assert "escreva a seção de impacto" in escrita_called[0]
    assert "↪" in result["response"]
    assert "troquei para /escrita" in result["response"]


def test_dispatch_handoff_escrita_para_explorer(monkeypatch):
    """dispatch(escrita, 'qual o prazo?') invoca explorer e retorna mode=explorer."""
    explorer_called = []

    def fake_explorer(message, history, profile, edital_ids=None, library_items=None, decision=None):
        explorer_called.append(message)
        return "O prazo é 30 dias úteis."

    monkeypatch.setattr("core.services.workspace_service._dispatch_explorer", fake_explorer)
    monkeypatch.setattr("core.services.workspace_service._load_session_edital_id", lambda db, sid: None)
    monkeypatch.setattr("core.services.workspace_service._mode_history", lambda db, sid, mode, window=8: [])
    monkeypatch.setattr("core.services.workspace_service._save_turn", lambda *a, **kw: None)

    result = dispatch(
        db=object(), session_id="s", workspace_id="w", profile=None,
        mode="escrita", message="qual o prazo do edital?",
    )

    assert result["mode"] == "explorer"
    assert len(explorer_called) == 1
    assert "qual o prazo" in explorer_called[0]
    assert "↪" in result["response"]
    assert "troquei para /explorer" in result["response"]
