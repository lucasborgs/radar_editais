from __future__ import annotations

import pytest

from radar.core.services.workspace_service import (
    _REDIRECT_BLOCK,
    VALID_ACTIONS,
    VALID_MODES,
    _dispatch_explorer,
    dispatch,
)

pytestmark = pytest.mark.unit


def test_barn_nao_recebe_redirect_e_mensagem_interna_nao_e_anexada(monkeypatch):
    captured = {}

    def fake_explore(self, **kwargs):
        captured.update(kwargs)
        return "Barn factual"

    monkeypatch.setattr("radar.core.services.explore_agent.ExploreAgent.explore", fake_explore)
    out = _dispatch_explorer(
        "qual a tese de investimentos da Barn?", [], None,
        edital_ids=None, library_items=None,
    )
    assert out == "Barn factual"
    assert "Se o usuário pedir algo FORA DO ESCOPO" not in captured["message"]


def test_acao_de_escrita_recebe_redirect_sem_chamar_agente(monkeypatch):
    def fail(*_args, **_kwargs):
        raise AssertionError("agente não deve ser chamado")

    monkeypatch.setattr("radar.core.services.explore_agent.ExploreAgent.explore", fail)
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

    monkeypatch.setattr("radar.core.services.workspace_service._dispatch_escrita", fake_escrita)
    monkeypatch.setattr("radar.core.services.workspace_service._load_session_edital_id", lambda db, sid: None)
    monkeypatch.setattr("radar.core.services.workspace_service._mode_history", lambda db, sid, mode, window=8: [])
    monkeypatch.setattr("radar.core.services.workspace_service._save_turn", lambda *a, **kw: None)

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

    monkeypatch.setattr("radar.core.services.workspace_service._dispatch_explorer", fake_explorer)
    monkeypatch.setattr("radar.core.services.workspace_service._load_session_edital_id", lambda db, sid: None)
    monkeypatch.setattr("radar.core.services.workspace_service._mode_history", lambda db, sid, mode, window=8: [])
    monkeypatch.setattr("radar.core.services.workspace_service._save_turn", lambda *a, **kw: None)

    result = dispatch(
        db=object(), session_id="s", workspace_id="w", profile=None,
        mode="escrita", message="qual o prazo do edital?",
    )

    assert result["mode"] == "explorer"
    assert len(explorer_called) == 1
    assert "qual o prazo" in explorer_called[0]
    assert "↪" in result["response"]
    assert "troquei para /explorer" in result["response"]


# ── Task 2: /profile ─────────────────────────────────────────────────────────


def test_dispatch_profile_sem_url_nao_chama_llm(monkeypatch):
    """dispatch(profile, '') sem URL pede URL sem chamar ProfileExtractor."""
    from radar.core.ingestion.profile_extractor import ProfileExtractor

    called = []
    monkeypatch.setattr(ProfileExtractor, "extract", lambda *a, **kw: called.append(1))
    monkeypatch.setattr("radar.core.services.workspace_service._save_turn", lambda *a, **kw: None)

    result = dispatch(
        db=object(), session_id="s", workspace_id="w", profile=None,
        mode="profile", message="",
    )

    assert len(called) == 0
    assert "URL" in result["response"]
    assert result["error"] is None


def test_dispatch_profile_com_url_chama_extractor(monkeypatch):
    """dispatch(profile, 'https://acme.com') chama ProfileExtractor.extract com a URL."""
    from radar.core.ingestion.profile_extractor import ExtractResult, ProfileExtractor
    from radar.domain.user_profile import CompanyProfile

    extract_called = []

    def fake_extract(self, url, agent_enabled=False):
        extract_called.append(url)
        return ExtractResult(
            profile=CompanyProfile(
                nome="Acme Ltda",
                tipo_entidade="empresa",
                one_liner="Soluções ACME",
                solution_summary="Produtos inovadores",
                descricao_atividades="Fabricação e venda",
                uf="SP",
                ano_fundacao=2010,
                tamanho_empresa="ME",
                trl=7,
            ),
            confidence={"nome": "high", "tipo_entidade": "high", "one_liner": "high",
                        "descricao_atividades": "high"},
            source_title="Acme - Home",
            low_confidence=False,
        )

    monkeypatch.setattr(ProfileExtractor, "extract", fake_extract)
    monkeypatch.setattr("radar.core.services.workspace_service._save_turn", lambda *a, **kw: None)

    result = dispatch(
        db=object(), session_id="s", workspace_id="w", profile=None,
        mode="profile", message="https://acme.com",
    )

    assert len(extract_called) == 1
    assert "https://acme.com" in extract_called[0]
    assert "Acme Ltda" in result["response"]
    assert "Sugestão de perfil" in result["response"]
    assert "Confiança" in result["response"]
    assert "nada foi salvo" in result["response"]
    assert result["error"] is None


# ── Task 3: /review ──────────────────────────────────────────────────────────


def test_dispatch_review_sem_secao_lista_outline(monkeypatch):
    """dispatch(review, '') sem seção lista outline sem chamar critic."""
    from unittest.mock import MagicMock

    session = MagicMock()
    session._doc_sections = {}
    session._proposal_outline = ["Impacto", "Metodologia", "Orçamento"]

    monkeypatch.setattr("radar.core.services.writing_session.WritingSession",
                        lambda *a, **kw: session)

    result = dispatch(
        db=object(), session_id="s", workspace_id="w", profile=None,
        mode="review", message="",
    )

    assert "Seções disponíveis" in result["response"]
    assert "Impacto" in result["response"]
    assert "Metodologia" in result["response"]
    assert result["error"] is None


def test_dispatch_review_com_secao_chama_critic(monkeypatch):
    """dispatch(review, 'Impacto') chama run_critic com o conteúdo e título."""
    from unittest.mock import MagicMock

    from radar.core.llm.agent_tools.critic_agent import CriticResult

    session = MagicMock()
    session._doc_sections = {"Impacto": "Conteúdo da seção de impacto"}
    session._proposal_outline = ["Impacto", "Metodologia"]
    session.mode = "proposal"
    session.edital_id = "edital-123"
    session._scope_edital_ids = ["edital-123"]
    session.session_id = "s"

    monkeypatch.setattr("radar.core.services.writing_session.WritingSession",
                        lambda *a, **kw: session)

    critic_called = []

    def fake_critic(draft, section_title, sess, trace_context=None):
        critic_called.append((draft, section_title, sess))
        return CriticResult(approved=True, issues=[], feedback="Tudo ok.")

    monkeypatch.setattr("radar.core.llm.agent_tools.critic_agent.run_critic", fake_critic)

    result = dispatch(
        db=object(), session_id="s", workspace_id="w", profile=None,
        mode="review", message="Impacto",
    )

    assert len(critic_called) == 1
    assert critic_called[0][0] == "Conteúdo da seção de impacto"
    assert critic_called[0][1] == "Impacto"
    assert "Aprovado" in result["response"]
    assert "nada foi alterado" in result["response"]
    assert result["error"] is None


def test_dispatch_review_nao_chama_set_section_content(monkeypatch):
    """dispatch(review, ...) nunca persiste — set_section_content não é chamado."""
    from unittest.mock import MagicMock

    from radar.core.llm.agent_tools.critic_agent import CriticResult

    session = MagicMock()
    session._doc_sections = {"Impacto": "Conteúdo da seção de impacto"}
    session._proposal_outline = ["Impacto"]
    session.mode = "proposal"
    session.edital_id = "edital-123"
    session._scope_edital_ids = ["edital-123"]
    session.session_id = "s"

    monkeypatch.setattr("radar.core.services.writing_session.WritingSession",
                        lambda *a, **kw: session)

    monkeypatch.setattr("radar.core.llm.agent_tools.critic_agent.run_critic",
                        lambda *a, **kw: CriticResult(approved=True, issues=[], feedback="ok"))

    result = dispatch(
        db=object(), session_id="s", workspace_id="w", profile=None,
        mode="review", message="Impacto",
    )

    # Zero chamadas de escrita no session mock
    assert session.set_section_content.call_count == 0
    assert result["error"] is None


# ── Validations ──────────────────────────────────────────────────────────────


def test_valid_actions_contem_profile_e_review():
    assert "profile" in VALID_ACTIONS
    assert "review" in VALID_ACTIONS


def test_valid_modes_nao_contem_actions():
    """VALID_MODES não inclui ações — são namespaces separados."""
    assert "profile" not in VALID_MODES
    assert "review" not in VALID_MODES


def test_dispatch_mode_invalido_retorna_erro(monkeypatch):
    """dispatch com mode inválido retorna erro sem exceção."""
    monkeypatch.setattr("radar.core.services.workspace_service._load_session_edital_id", lambda db, sid: None)
    monkeypatch.setattr("radar.core.services.workspace_service._mode_history", lambda db, sid, mode, window=8: [])
    monkeypatch.setattr("radar.core.services.workspace_service._save_turn", lambda *a, **kw: None)

    result = dispatch(
        db=object(), session_id="s", workspace_id="w", profile=None,
        mode="invalid", message="foo",
    )

    assert result["error"] is not None
    assert "inválido" in result["error"].lower()
