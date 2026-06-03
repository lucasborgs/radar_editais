"""Testes da ingestão de descoberta no KG (item 2.2 — Fase A, incremento 1).

Cobre o caminho determinístico bronze-de-descoberta → entry de edital provisório.
O engine de descoberta (web_search + triagem + extração) vem no incremento 2.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pipeline.build_knowledge_graph import _build_discovery_editais


def _rec(**kw):
    base = {
        "source": "FAPESC",
        "native_id": "2026-001",
        "titulo": "Edital de Inovação SC",
        "link": "http://fapesc.sc.gov.br/e/2026-001",
        "prazo_envio": "31/12/2026",
        "tema": "tecnologias digitais e conectividade",
        "publico_alvo": "empresas",
        "descricao": "Chamada de fomento à inovação.",
        "status": "ABERTA",
    }
    base.update(kw)
    return base


def test_discovery_enters_as_provisorio():
    out = _build_discovery_editais([_rec()])
    assert len(out) == 1
    e = out[0]
    assert e["verificacao"] == "provisorio"
    assert e["id"] == "fapesc:2026-001"        # source normalizado (lower)
    assert e["source"] == "fapesc"


def test_discovery_themes_restricted_to_canonical_vocab():
    """Tema fora do vocab §5.9 é descartado (blinda a ponte/invariante)."""
    out = _build_discovery_editais([_rec(
        tema="tecnologias digitais e conectividade; bagulho-nao-canonico"
    )])
    assert out[0]["themes"] == ["tecnologias digitais e conectividade"]


def test_discovery_detects_ict_requirement():
    out = _build_discovery_editais([_rec(
        descricao="A proposta deverá contar com uma ICT coexecutora."
    )])
    assert out[0]["requires_ict_partner"] is True


def test_discovery_fallback_source_web_and_slug_id():
    """Sem source → 'web'; sem native_id → slug do link."""
    out = _build_discovery_editais([{
        "titulo": "Desafio aberto", "link": "http://x.org/abc",
        "descricao": "...", "status": "ABERTA",
    }])
    assert out[0]["source"] == "web"
    assert out[0]["id"].startswith("web:")


def test_discovery_dedup_by_id():
    out = _build_discovery_editais([_rec(), _rec()])
    assert len(out) == 1


# ---------------------------------------------------------------------------
# Engine de descoberta (core/opportunity_discovery) — incremento 2, com mocks
# ---------------------------------------------------------------------------

from core import opportunity_discovery as od
from core.web_search import SearchHit


class _FakeMsg:
    def __init__(self, content):
        self.message = type("M", (), {"content": content})


class _FakeClient:
    """Cliente OpenAI-like que devolve um JSON fixo."""
    def __init__(self, content):
        self._content = content
        self.chat = type("C", (), {"completions": self})()

    def create(self, **kw):
        return type("R", (), {"choices": [_FakeMsg(self._content)]})()


def test_triage_parses_json():
    client = _FakeClient('{"is_opportunity": true, "agency": "FAPESC"}')
    out = od._triage(SearchHit("t", "http://u", "s", ""), client, "m")
    assert out == {"is_opportunity": True, "agency": "FAPESC"}


def test_triage_failure_discards():
    client = _FakeClient("não é json")
    out = od._triage(SearchHit("t", "http://u", "s", ""), client, "m")
    assert out["is_opportunity"] is False


def test_extract_builds_record():
    client = _FakeClient(
        '{"titulo":"Edital X","prazo_envio":"31/12/2026","publico_alvo":"empresas",'
        '"descricao":"fomento","status":"ABERTA",'
        '"tema":["tecnologias digitais e conectividade","lixo"]}'
    )
    rec = od._extract(SearchHit("T", "http://fapesc.sc/e/1", "s", "corpo"),
                      "corpo da página", "FAPESC", client, "m")
    assert rec["source"] == "fapesc"            # agency slugificada
    assert rec["link"] == "http://fapesc.sc/e/1"
    assert rec["verificacao"] == "provisorio"
    assert "tecnologias digitais e conectividade" in rec["tema"]


def test_discover_dedups_known_and_duplicates(monkeypatch):
    hits = [
        SearchHit("A", "http://x.org/a", "sa", "ca"),
        SearchHit("A dup", "http://x.org/a/", "sa", "ca"),   # mesma URL normalizada
        SearchHit("Known", "http://x.org/known", "sk", "ck"),
        SearchHit("B", "http://x.org/b", "sb", "cb"),
    ]
    monkeypatch.setattr(od.websearch, "web_search", lambda q, k=8: hits)
    monkeypatch.setattr(od, "_known_urls", lambda: {"http://x.org/known"})
    monkeypatch.setattr(od, "_make_client", lambda role: (object(), "m"))
    monkeypatch.setattr(od, "_triage",
                        lambda h, c, m: {"is_opportunity": True, "agency": "Ag"})
    seen = []
    def fake_extract(h, txt, agency, c, m):
        seen.append(h.url)
        return {"link": h.url, "source": "ag", "native_id": "x", "verificacao": "provisorio"}
    monkeypatch.setattr(od, "_extract", fake_extract)
    monkeypatch.setattr(od.ws, "discovery_config",
                        lambda: {"queries": ["q1"], "max_results_per_query": 8,
                                 "max_candidates": 40})

    out = od.discover_opportunities(write=False)
    # a (dedup do dup), b — known excluído
    assert sorted(seen) == ["http://x.org/a", "http://x.org/b"]
    assert len(out) == 2


def test_discover_skips_non_opportunity(monkeypatch):
    hits = [SearchHit("A", "http://x.org/a", "s", "c"),
            SearchHit("B", "http://x.org/b", "s", "c")]
    monkeypatch.setattr(od.websearch, "web_search", lambda q, k=8: hits)
    monkeypatch.setattr(od, "_known_urls", lambda: set())
    monkeypatch.setattr(od, "_make_client", lambda role: (object(), "m"))
    monkeypatch.setattr(od, "_triage",
                        lambda h, c, m: {"is_opportunity": h.url.endswith("a"),
                                         "agency": ""})
    monkeypatch.setattr(od, "_extract",
                        lambda h, t, a, c, m: {"link": h.url, "source": "web",
                                               "native_id": "x", "verificacao": "provisorio"})
    monkeypatch.setattr(od.ws, "discovery_config",
                        lambda: {"queries": ["q"], "max_results_per_query": 8,
                                 "max_candidates": 40})
    out = od.discover_opportunities(write=False)
    assert [r["link"] for r in out] == ["http://x.org/a"]


def test_discover_no_credential_returns_empty(monkeypatch):
    monkeypatch.setattr(od.websearch, "web_search", lambda q, k=8:
                        [SearchHit("A", "http://x.org/a", "s", "c")])
    monkeypatch.setattr(od, "_known_urls", lambda: set())
    monkeypatch.setattr(od, "_make_client", lambda role: (None, None))
    monkeypatch.setattr(od.ws, "discovery_config",
                        lambda: {"queries": ["q"], "max_results_per_query": 8,
                                 "max_candidates": 40})
    assert od.discover_opportunities(write=False) == []
