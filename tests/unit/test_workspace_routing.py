from __future__ import annotations

import pytest

from radar.core.services.workspace_service import VALID_ACTIONS, dispatch

pytestmark = pytest.mark.unit


# ── /profile ─────────────────────────────────────────────────────────────────


def test_dispatch_profile_sem_url_nao_chama_llm(monkeypatch):
    """dispatch(profile, '') sem URL pede URL sem chamar ProfileExtractor."""
    from radar.core.ingestion.profile_extractor import ProfileExtractor

    called = []
    monkeypatch.setattr(ProfileExtractor, "extract", lambda *a, **kw: called.append(1))

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


# ── /review ──────────────────────────────────────────────────────────────────


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


# ── Validações ───────────────────────────────────────────────────────────────


def test_valid_actions_contem_profile_e_review():
    assert "profile" in VALID_ACTIONS
    assert "review" in VALID_ACTIONS


def test_dispatch_acao_invalida_retorna_erro():
    """dispatch com mode fora de VALID_ACTIONS retorna erro sem exceção."""
    result = dispatch(
        db=object(), session_id="s", workspace_id="w", profile=None,
        mode="invalid", message="foo",
    )

    assert result["error"] is not None
    assert "inválida" in result["error"].lower()
