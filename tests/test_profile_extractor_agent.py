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

from core.agent_runtime import AgentResult  # noqa: E402
from core.agent_tools import ExtractionState, build_profile_tools  # noqa: E402
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
        "core.agent_tools.profile_tools.requests.get",
        lambda *a, **kw: _mock_response(html=html),
    )

    out = fetch.call({"url": "https://acme.bio"})
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

    monkeypatch.setattr("core.agent_tools.profile_tools.requests.get", fake_get)

    fetch.call({"url": "https://x.com"})
    out = fetch.call({"url": "https://x.com"})
    assert "[cache]" in out
    assert call_count["n"] == 1


def test_fetch_page_handles_http_error(monkeypatch):
    state = ExtractionState()
    tools = build_profile_tools(state)
    fetch = next(t for t in tools if t.name == "fetch_page")

    monkeypatch.setattr(
        "core.agent_tools.profile_tools.requests.get",
        lambda *a, **kw: _mock_response(status_code=404, html=""),
    )
    out = fetch.call({"url": "https://nope.com"})
    assert "404" in out
    assert "Tente outro link" in out


def test_fetch_page_handles_timeout(monkeypatch):
    from requests import Timeout
    state = ExtractionState()
    tools = build_profile_tools(state)
    fetch = next(t for t in tools if t.name == "fetch_page")

    def boom(*a, **kw):
        raise Timeout("slow")

    monkeypatch.setattr("core.agent_tools.profile_tools.requests.get", boom)
    out = fetch.call({"url": "https://slow.com"})
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
        "core.agent_tools.profile_tools.requests.get",
        lambda *a, **kw: _mock_response(html="<html><title>nova</title></html>"),
    )
    out = fetch.call({"url": "https://nova.com"})
    assert "Limite" in out
    assert "submit_profile" in out


def test_fetch_page_empty_url():
    state = ExtractionState()
    tools = build_profile_tools(state)
    fetch = next(t for t in tools if t.name == "fetch_page")
    assert "vazia" in fetch.call({"url": ""}).lower()


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

    out = ll.call({"url": "https://x.com", "pattern": "sobre"})
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

    out = ll.call({"url": "https://x.com", "pattern": "about"})
    assert "Veja mais" in out


def test_list_links_matching_no_match():
    state = ExtractionState()
    state.fetched["https://x.com"] = {
        "text": "...", "title": "X",
        "links": [{"text": "Outro", "href": "/outro"}],
    }
    tools = build_profile_tools(state)
    ll = next(t for t in tools if t.name == "list_links_matching")

    out = ll.call({"url": "https://x.com", "pattern": "xyz"})
    assert "Nenhum link" in out


def test_list_links_matching_fetches_on_demand(monkeypatch):
    """Se a página não foi buscada antes, list_links_matching busca."""
    state = ExtractionState()
    html = '<html><body><a href="/about">About</a></body></html>'
    monkeypatch.setattr(
        "core.agent_tools.profile_tools.requests.get",
        lambda *a, **kw: _mock_response(html=html),
    )
    tools = build_profile_tools(state)
    ll = next(t for t in tools if t.name == "list_links_matching")

    out = ll.call({"url": "https://x.com", "pattern": "about"})
    assert "About" in out
    assert "https://x.com" in state.fetched


# ============================================================================
# lookup_cnpj
# ============================================================================

def test_lookup_cnpj_validates_format():
    state = ExtractionState()
    tools = build_profile_tools(state)
    lc = next(t for t in tools if t.name == "lookup_cnpj")

    out = lc.call({"cnpj": "123"})
    assert "não parece um CNPJ válido" in out


def test_lookup_cnpj_accepts_masked_format(monkeypatch):
    state = ExtractionState()
    tools = build_profile_tools(state)
    lc = next(t for t in tools if t.name == "lookup_cnpj")

    monkeypatch.setattr(
        "core.agent_tools.profile_tools.requests.get",
        lambda *a, **kw: _mock_response(
            json_data={
                "razao_social": "ACME LTDA",
                "porte": "DEMAIS",
                "cnae_fiscal_descricao": "Bioeconomia",
            },
        ),
    )
    out = lc.call({"cnpj": "11.222.333/0001-44"})
    assert "ACME LTDA" in out
    assert "Bioeconomia" in out


def test_lookup_cnpj_handles_404(monkeypatch):
    state = ExtractionState()
    tools = build_profile_tools(state)
    lc = next(t for t in tools if t.name == "lookup_cnpj")

    monkeypatch.setattr(
        "core.agent_tools.profile_tools.requests.get",
        lambda *a, **kw: _mock_response(status_code=404, html=""),
    )
    out = lc.call({"cnpj": "11222333000144"})
    assert "não encontrado" in out


def test_lookup_cnpj_handles_timeout(monkeypatch):
    from requests import Timeout
    state = ExtractionState()
    tools = build_profile_tools(state)
    lc = next(t for t in tools if t.name == "lookup_cnpj")

    monkeypatch.setattr(
        "core.agent_tools.profile_tools.requests.get",
        MagicMock(side_effect=Timeout("slow")),
    )
    out = lc.call({"cnpj": "11222333000144"})
    assert "timeout" in out.lower()


# ============================================================================
# submit_profile
# ============================================================================

def test_submit_profile_required_fields():
    state = ExtractionState()
    tools = build_profile_tools(state)
    sub = next(t for t in tools if t.name == "submit_profile")

    out = sub.call({
        "nome": "", "tipo_entidade": "empresa",
        "one_liner": "x", "descricao_atividades": "y",
    })
    assert "obrigatórios" in out


def test_submit_profile_valid_tipo():
    state = ExtractionState()
    tools = build_profile_tools(state)
    sub = next(t for t in tools if t.name == "submit_profile")

    out = sub.call({
        "nome": "ACME", "tipo_entidade": "xyz",
        "one_liner": "x", "descricao_atividades": "y",
    })
    assert "inválido" in out


def test_submit_profile_trl_range():
    state = ExtractionState()
    tools = build_profile_tools(state)
    sub = next(t for t in tools if t.name == "submit_profile")

    out = sub.call({
        "nome": "X", "tipo_entidade": "startup",
        "one_liner": "x", "descricao_atividades": "y",
        "trl": 15,
    })
    assert "fora do range" in out


def test_submit_profile_happy_path():
    state = ExtractionState()
    tools = build_profile_tools(state)
    sub = next(t for t in tools if t.name == "submit_profile")

    out = sub.call({
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

    out = sub.call({
        "nome": "Outro", "tipo_entidade": "empresa",
        "one_liner": "x", "descricao_atividades": "y",
    })
    assert "já foi submetido" in out
    assert state.submitted_profile["nome"] == "Already submitted"  # não mexeu


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
        sub.call({
            "nome": "ACME Bio",
            "tipo_entidade": "startup",
            "one_liner": "Bioeconomia.",
            "descricao_atividades": "Produz X e Y.",
            "tamanho_empresa": "ME",
            "trl": 6,
        })
        return fake_result

    monkeypatch.setattr("core.agent_runtime.run_agent", fake_run_agent)

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
    monkeypatch.setattr("core.agent_runtime.run_agent", lambda **kw: fake_result)

    result = pe._extract_agent("https://x.com")
    assert result.low_confidence is True
    assert result.error is not None
    assert "Agente terminou sem submeter" in result.error


def test_extract_agent_handles_run_agent_exception(monkeypatch):
    """Se run_agent levanta, retorna empty_result com erro descritivo."""
    pe = ProfileExtractor()

    def boom(**kw):
        raise RuntimeError("API down")

    monkeypatch.setattr("core.agent_runtime.run_agent", boom)

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
    monkeypatch.setattr("core.agent_runtime.run_agent", lambda **kw: fake_result)

    result = pe._extract_agent("https://x.com")
    assert result.low_confidence is True
    assert "max_steps" in result.error


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
