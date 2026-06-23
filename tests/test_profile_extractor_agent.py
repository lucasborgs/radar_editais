"""Testes do path agente de ProfileExtractor (Sprint 4 do Cenário B).

Cobre:
  - tools (fetch_page, list_links_matching, lookup_cnpj, submit_profile) com
    requests.get mockado
  - dispatcher extract() → _extract_agent vs _extract_legacy
  - _extract_agent: happy path (submit), no-submit (low_confidence), exception
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.llm.agent_runtime import AgentResult  # noqa: E402
from core.llm.agent_tools import ExtractionState, build_profile_tools  # noqa: E402
from core.profile_extractor import ProfileExtractor  # noqa: E402

# ============================================================================
# Helpers
# ============================================================================

def _mock_response(*, status_code=200, html="", json_data=None):
    """Mock de requests.Response para HTML ou JSON."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = html
    if json_data is not None:
        resp.json = MagicMock(return_value=json_data)
    else:
        resp.json = MagicMock(side_effect=ValueError("not json"))
    if status_code >= 400:
        from requests import HTTPError
        resp.raise_for_status = MagicMock(
            side_effect=HTTPError(response=resp),
        )
    else:
        resp.raise_for_status = MagicMock()
    return resp


# ============================================================================
# fetch_page
# ============================================================================

def test_fetch_page_returns_text_and_caches(monkeypatch):
    state = ExtractionState()
    tools = build_profile_tools(state)
    fetch = next(t for t in tools if t.name == "fetch_page")

    html = "<html><head><title>ACME Bio</title></head><body><p>Empresa de bioeconomia.</p></body></html>"
    monkeypatch.setattr(
        "core.llm.agent_tools.profile_tools.requests.get",
        lambda *a, **kw: _mock_response(html=html),
    )

    out = fetch.invoke({"url": "https://acme.bio"})
    assert "ACME Bio" in out
    assert "bioeconomia" in out.lower()
    assert "https://acme.bio" in state.fetched
    assert state.fetched["https://acme.bio"]["title"] == "ACME Bio"


def test_fetch_page_serves_from_cache_on_second_call(monkeypatch):
    state = ExtractionState()
    tools = build_profile_tools(state)
    fetch = next(t for t in tools if t.name == "fetch_page")

    html = "<html><head><title>X</title></head><body>foo</body></html>"
    call_count = {"n": 0}

    def fake_get(*a, **kw):
        call_count["n"] += 1
        return _mock_response(html=html)

    monkeypatch.setattr("core.llm.agent_tools.profile_tools.requests.get", fake_get)

    fetch.invoke({"url": "https://x.com"})
    out = fetch.invoke({"url": "https://x.com"})
    assert "[cache]" in out
    assert call_count["n"] == 1


def test_fetch_page_handles_http_error(monkeypatch):
    state = ExtractionState()
    tools = build_profile_tools(state)
    fetch = next(t for t in tools if t.name == "fetch_page")

    monkeypatch.setattr(
        "core.llm.agent_tools.profile_tools.requests.get",
        lambda *a, **kw: _mock_response(status_code=404, html=""),
    )
    out = fetch.invoke({"url": "https://nope.com"})
    assert "404" in out
    assert "Tente outro link" in out


def test_fetch_page_handles_timeout(monkeypatch):
    from requests import Timeout
    state = ExtractionState()
    tools = build_profile_tools(state)
    fetch = next(t for t in tools if t.name == "fetch_page")

    def boom(*a, **kw):
        raise Timeout("slow")

    monkeypatch.setattr("core.llm.agent_tools.profile_tools.requests.get", boom)
    out = fetch.invoke({"url": "https://slow.com"})
    assert "Timeout" in out


def test_fetch_page_respects_max_pages_limit(monkeypatch):
    state = ExtractionState()
    # Pré-popula cache com 10 URLs distintas (limite)
    for i in range(10):
        state.fetched[f"https://p{i}.com"] = {"text": "x", "title": f"t{i}", "links": []}

    tools = build_profile_tools(state)
    fetch = next(t for t in tools if t.name == "fetch_page")

    # 11ª URL nova é bloqueada
    monkeypatch.setattr(
        "core.llm.agent_tools.profile_tools.requests.get",
        lambda *a, **kw: _mock_response(html="<html><title>nova</title></html>"),
    )
    out = fetch.invoke({"url": "https://nova.com"})
    assert "Limite" in out
    assert "submit_profile" in out


def test_fetch_page_empty_url():
    state = ExtractionState()
    tools = build_profile_tools(state)
    fetch = next(t for t in tools if t.name == "fetch_page")
    assert "vazia" in fetch.invoke({"url": ""}).lower()


# ============================================================================
# list_links_matching
# ============================================================================

def test_list_links_matching_uses_cache(monkeypatch):
    state = ExtractionState()
    state.fetched["https://x.com"] = {
        "text": "...",
        "title": "X",
        "links": [
            {"text": "Sobre nós", "href": "/sobre"},
            {"text": "Produtos", "href": "/produtos"},
            {"text": "Contato", "href": "/contato"},
        ],
    }
    tools = build_profile_tools(state)
    ll = next(t for t in tools if t.name == "list_links_matching")

    out = ll.invoke({"url": "https://x.com", "pattern": "sobre"})
    assert "Sobre nós" in out
    assert "/sobre" in out
    assert "Produtos" not in out


def test_list_links_matching_pattern_in_href():
    state = ExtractionState()
    state.fetched["https://x.com"] = {
        "text": "...",
        "title": "X",
        "links": [
            {"text": "Veja mais", "href": "/about-us"},
        ],
    }
    tools = build_profile_tools(state)
    ll = next(t for t in tools if t.name == "list_links_matching")

    out = ll.invoke({"url": "https://x.com", "pattern": "about"})
    assert "Veja mais" in out


def test_list_links_matching_no_match():
    state = ExtractionState()
    state.fetched["https://x.com"] = {
        "text": "...", "title": "X",
        "links": [{"text": "Outro", "href": "/outro"}],
    }
    tools = build_profile_tools(state)
    ll = next(t for t in tools if t.name == "list_links_matching")

    out = ll.invoke({"url": "https://x.com", "pattern": "xyz"})
    assert "Nenhum link" in out


def test_list_links_matching_fetches_on_demand(monkeypatch):
    """Se a página não foi buscada antes, list_links_matching busca."""
    state = ExtractionState()
    html = '<html><body><a href="/about">About</a></body></html>'
    monkeypatch.setattr(
        "core.llm.agent_tools.profile_tools.requests.get",
        lambda *a, **kw: _mock_response(html=html),
    )
    tools = build_profile_tools(state)
    ll = next(t for t in tools if t.name == "list_links_matching")

    out = ll.invoke({"url": "https://x.com", "pattern": "about"})
    assert "About" in out
    assert "https://x.com" in state.fetched


# ============================================================================
# lookup_cnpj
# ============================================================================

def test_lookup_cnpj_gated_off_by_default():
    # Decisão 2026-06-21: Receita/BrasilAPI desativada por padrão. A tool só
    # entra na lista com CNPJ_LOOKUP_ENABLED=true.
    state = ExtractionState()
    tools = build_profile_tools(state)
    assert not any(t.name == "lookup_cnpj" for t in tools)


def test_lookup_cnpj_validates_format(monkeypatch):
    monkeypatch.setenv("CNPJ_LOOKUP_ENABLED", "true")
    state = ExtractionState()
    tools = build_profile_tools(state)
    lc = next(t for t in tools if t.name == "lookup_cnpj")

    out = lc.invoke({"cnpj": "123"})
    assert "não parece um CNPJ válido" in out


def test_lookup_cnpj_accepts_masked_format(monkeypatch):
    monkeypatch.setenv("CNPJ_LOOKUP_ENABLED", "true")
    state = ExtractionState()
    tools = build_profile_tools(state)
    lc = next(t for t in tools if t.name == "lookup_cnpj")

    monkeypatch.setattr(
        "core.llm.agent_tools.profile_tools.requests.get",
        lambda *a, **kw: _mock_response(
            json_data={
                "razao_social": "ACME LTDA",
                "porte": "DEMAIS",
                "cnae_fiscal_descricao": "Bioeconomia",
            },
        ),
    )
    out = lc.invoke({"cnpj": "11.222.333/0001-44"})
    assert "ACME LTDA" in out
    assert "Bioeconomia" in out


def test_lookup_cnpj_handles_404(monkeypatch):
    monkeypatch.setenv("CNPJ_LOOKUP_ENABLED", "true")
    state = ExtractionState()
    tools = build_profile_tools(state)
    lc = next(t for t in tools if t.name == "lookup_cnpj")

    monkeypatch.setattr(
        "core.llm.agent_tools.profile_tools.requests.get",
        lambda *a, **kw: _mock_response(status_code=404, html=""),
    )
    out = lc.invoke({"cnpj": "11222333000144"})
    assert "não encontrado" in out


def test_lookup_cnpj_handles_timeout(monkeypatch):
    from requests import Timeout
    monkeypatch.setenv("CNPJ_LOOKUP_ENABLED", "true")
    state = ExtractionState()
    tools = build_profile_tools(state)
    lc = next(t for t in tools if t.name == "lookup_cnpj")

    monkeypatch.setattr(
        "core.llm.agent_tools.profile_tools.requests.get",
        MagicMock(side_effect=Timeout("slow")),
    )
    out = lc.invoke({"cnpj": "11222333000144"})
    assert "timeout" in out.lower()


# ============================================================================
# submit_profile
# ============================================================================

def test_submit_profile_required_fields():
    state = ExtractionState()
    tools = build_profile_tools(state)
    sub = next(t for t in tools if t.name == "submit_profile")

    out = sub.invoke({
        "nome": "", "tipo_entidade": "empresa",
        "one_liner": "x", "descricao_atividades": "y",
    })
    assert "obrigatórios" in out


def test_submit_profile_valid_tipo():
    state = ExtractionState()
    tools = build_profile_tools(state)
    sub = next(t for t in tools if t.name == "submit_profile")

    out = sub.invoke({
        "nome": "ACME", "tipo_entidade": "xyz",
        "one_liner": "x", "descricao_atividades": "y",
    })
    assert "inválido" in out


def test_submit_profile_trl_range():
    state = ExtractionState()
    tools = build_profile_tools(state)
    sub = next(t for t in tools if t.name == "submit_profile")

    out = sub.invoke({
        "nome": "X", "tipo_entidade": "startup",
        "one_liner": "x", "descricao_atividades": "y",
        "trl": 15,
    })
    assert "fora do range" in out


def test_submit_profile_happy_path():
    state = ExtractionState()
    tools = build_profile_tools(state)
    sub = next(t for t in tools if t.name == "submit_profile")

    out = sub.invoke({
        "nome": "ACME Bio",
        "tipo_entidade": "startup",
        "one_liner": "Bioeconomia.",
        "descricao_atividades": "Produz X e Y.",
        "tamanho_empresa": "ME",
        "trl": 6,
    })
    assert "sucesso" in out
    assert state.submitted_profile["nome"] == "ACME Bio"
    assert state.submitted_profile["tipo_entidade"] == "startup"
    assert state.submitted_profile["trl"] == 6


def test_submit_profile_idempotent_after_first_call():
    state = ExtractionState()
    state.submitted_profile = {"nome": "Already submitted"}
    tools = build_profile_tools(state)
    sub = next(t for t in tools if t.name == "submit_profile")

    out = sub.invoke({
        "nome": "Outro", "tipo_entidade": "empresa",
        "one_liner": "x", "descricao_atividades": "y",
    })
    assert "já foi submetido" in out
    assert state.submitted_profile["nome"] == "Already submitted"  # não mexeu


# ============================================================================
# submit_profile — elegibilidade organizacional (uf / ano_fundacao / faturamento)
# ============================================================================

def test_submit_profile_threads_eligibility_fields():
    state = ExtractionState()
    tools = build_profile_tools(state)
    sub = next(t for t in tools if t.name == "submit_profile")

    out = sub.invoke({
        "nome": "ACME Bio", "tipo_entidade": "startup",
        "one_liner": "Bioeconomia.", "descricao_atividades": "Produz X.",
        "uf": "sp", "ano_fundacao": 2019, "faturamento_anual": 1_200_000,
    })
    assert "sucesso" in out
    assert state.submitted_profile["uf"] == "SP"  # normaliza p/ maiúsculo
    assert state.submitted_profile["ano_fundacao"] == 2019
    assert state.submitted_profile["faturamento_anual"] == 1_200_000.0


def test_submit_profile_rejects_bad_uf():
    state = ExtractionState()
    tools = build_profile_tools(state)
    sub = next(t for t in tools if t.name == "submit_profile")
    out = sub.invoke({
        "nome": "X", "tipo_entidade": "empresa",
        "one_liner": "x", "descricao_atividades": "y", "uf": "São Paulo",
    })
    assert "uf" in out.lower() and "inválida" in out
    assert state.submitted_profile is None


def test_submit_profile_rejects_bad_ano_fundacao():
    state = ExtractionState()
    tools = build_profile_tools(state)
    sub = next(t for t in tools if t.name == "submit_profile")
    out = sub.invoke({
        "nome": "X", "tipo_entidade": "empresa",
        "one_liner": "x", "descricao_atividades": "y", "ano_fundacao": 1500,
    })
    assert "ano_fundacao" in out and "range" in out
    assert state.submitted_profile is None


def test_submit_profile_eligibility_fields_optional():
    """Sem os campos novos, submit segue funcionando (defaults vazios/None)."""
    state = ExtractionState()
    tools = build_profile_tools(state)
    sub = next(t for t in tools if t.name == "submit_profile")
    out = sub.invoke({
        "nome": "X", "tipo_entidade": "empresa",
        "one_liner": "x", "descricao_atividades": "y",
    })
    assert "sucesso" in out
    assert state.submitted_profile["uf"] == ""
    assert state.submitted_profile["ano_fundacao"] is None
    assert state.submitted_profile["faturamento_anual"] is None


def test_extract_agent_threads_eligibility_into_profile(monkeypatch):
    """_extract_agent mapeia uf/ano_fundacao/faturamento do submit ao CompanyProfile."""
    pe = ProfileExtractor()
    fake_result = AgentResult(
        final_text="ok", steps=[], stop_reason="end_turn",
        usage={"input_tokens": 1, "output_tokens": 1},
    )

    def fake_run_agent(**kw):
        sub = next(t for t in kw["tools"] if t.name == "submit_profile")
        sub.invoke({
            "nome": "ACME Bio", "tipo_entidade": "startup",
            "one_liner": "Bio.", "descricao_atividades": "Produz X.",
            "uf": "MG", "ano_fundacao": 2017, "faturamento_anual": 800_000,
        })
        return fake_result

    monkeypatch.setattr("core.llm.agent_runtime.run_agent", fake_run_agent)
    result = pe._extract_agent("https://acme.bio")
    assert result.profile.uf == "MG"
    assert result.profile.ano_fundacao == 2017
    assert result.profile.faturamento_anual == 800_000.0


# ============================================================================
# ProfileExtractor — dispatcher
# ============================================================================

def test_extract_dispatches_to_agent_when_flag_true(monkeypatch):
    pe = ProfileExtractor()
    called = {"agent": False, "legacy": False}

    monkeypatch.setattr(
        ProfileExtractor, "_extract_agent",
        lambda self, url: called.__setitem__("agent", True) or _make_dummy_result(),
    )
    monkeypatch.setattr(
        ProfileExtractor, "_extract_legacy",
        lambda self, url: called.__setitem__("legacy", True) or _make_dummy_result(),
    )

    pe.extract("https://x.com", agent_enabled=True)
    assert called["agent"] is True
    assert called["legacy"] is False


def test_extract_dispatches_to_legacy_by_default(monkeypatch):
    pe = ProfileExtractor()
    called = {"agent": False, "legacy": False}

    monkeypatch.setattr(
        ProfileExtractor, "_extract_agent",
        lambda self, url: called.__setitem__("agent", True) or _make_dummy_result(),
    )
    monkeypatch.setattr(
        ProfileExtractor, "_extract_legacy",
        lambda self, url: called.__setitem__("legacy", True) or _make_dummy_result(),
    )

    pe.extract("https://x.com")
    assert called["agent"] is False
    assert called["legacy"] is True


def _make_dummy_result():
    from core.profile_extractor import ExtractResult
    from domain.user_profile import CompanyProfile
    return ExtractResult(
        profile=CompanyProfile(),
        confidence={"nome": "missing"},
        source_title="",
        low_confidence=True,
    )


# ============================================================================
# _extract_agent — happy path / no-submit / error
# ============================================================================

def test_extract_agent_happy_path(monkeypatch):
    """Stub run_agent: simula agente que submita perfil via tool."""
    pe = ProfileExtractor()

    fake_result = AgentResult(
        final_text="Perfil extraído.",
        steps=[],
        stop_reason="end_turn",
        usage={"input_tokens": 100, "output_tokens": 20},
    )

    def fake_run_agent(**kw):
        # Simula side effect das tools no state via callback que o agente faria
        tools = kw["tools"]
        sub = next(t for t in tools if t.name == "submit_profile")
        sub.invoke({
            "nome": "ACME Bio",
            "tipo_entidade": "startup",
            "one_liner": "Bioeconomia.",
            "descricao_atividades": "Produz X e Y.",
            "tamanho_empresa": "ME",
            "trl": 6,
        })
        return fake_result

    monkeypatch.setattr("core.llm.agent_runtime.run_agent", fake_run_agent)

    result = pe._extract_agent("https://acme.bio")
    assert result.error is None
    assert result.profile.nome == "ACME Bio"
    assert result.profile.tipo_entidade == "startup"
    assert result.profile.trl == 6
    assert result.confidence["nome"] == "high"
    assert result.low_confidence is False  # 4 campos obrigatórios preenchidos


def test_extract_agent_no_submit_returns_low_confidence(monkeypatch):
    """Agente termina sem chamar submit_profile → low_confidence + erro."""
    pe = ProfileExtractor()

    fake_result = AgentResult(
        final_text="Sem dados suficientes.",
        steps=[],
        stop_reason="end_turn",
        usage={"input_tokens": 50, "output_tokens": 10},
    )
    monkeypatch.setattr("core.llm.agent_runtime.run_agent", lambda **kw: fake_result)

    result = pe._extract_agent("https://x.com")
    assert result.low_confidence is True
    assert result.error is not None
    assert "Agente terminou sem submeter" in result.error


def test_extract_agent_handles_run_agent_exception(monkeypatch):
    """Se run_agent levanta, retorna empty_result com erro descritivo."""
    pe = ProfileExtractor()

    def boom(**kw):
        raise RuntimeError("API down")

    monkeypatch.setattr("core.llm.agent_runtime.run_agent", boom)

    result = pe._extract_agent("https://x.com")
    assert result.low_confidence is True
    assert result.error is not None
    assert "agent_failure" in result.error


def test_extract_agent_max_steps_low_confidence(monkeypatch):
    """Agente atinge max_steps sem submit → low_confidence."""
    pe = ProfileExtractor()

    fake_result = AgentResult(
        final_text="",
        steps=[],
        stop_reason="max_steps",
        usage={"input_tokens": 200, "output_tokens": 30},
    )
    monkeypatch.setattr("core.llm.agent_runtime.run_agent", lambda **kw: fake_result)

    result = pe._extract_agent("https://x.com")
    assert result.low_confidence is True
    assert "max_steps" in result.error


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
