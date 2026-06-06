"""Testes da Descoberta unificada na fonte `web` (Opção A, WIKI.md §12.4).

A Descoberta deixou de ter pipeline próprio: é a torneira automática da fonte
`web`. O engine (web_search + triagem + extração) grava registros no schema web
(`url`/`url_hash`/`texto_cru`/`verificacao=provisorio`) em web_raw/, e o
`_build_editais("web")` os ingere como qualquer página web — provisorio por-item.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.web_identity import normalize_web_url, web_url_hash
from pipeline.build_knowledge_graph import _build_editais


def _web_rec(**kw):
    """Registro bronze web no schema que a Descoberta emite (sem `verificacao`
    por padrão → testes que querem provisorio passam explícito)."""
    base = {
        "url": "https://fapesc.sc.gov.br/e/2026-001",
        "url_hash": "abc123def456",
        "title": "Edital de Inovação SC",
        "texto_cru": "Chamada de fomento à inovação para empresas.",
        "prazo_envio": "31/12/2026",
        "tema": "tecnologias digitais e conectividade",
        "publico_alvo": "empresas",
        "descricao": "Chamada de fomento à inovação.",
        "status": "ABERTA",
    }
    base.update(kw)
    return base


def test_web_discovery_enters_as_provisorio():
    out = _build_editais([_web_rec(verificacao="provisorio")], source="web")
    assert len(out) == 1
    e = out[0]
    assert e["verificacao"] == "provisorio"
    assert e["id"] == "web:abc123def456"      # identidade = web:<url_hash>
    assert e["source"] == "web"


def test_web_manual_defaults_to_verificado():
    """Item sem `verificacao` (seed manual / FINEP/FAPESP) → verificado."""
    out = _build_editais([_web_rec()], source="web")
    assert out[0]["verificacao"] == "verificado"


def test_web_themes_restricted_to_canonical_vocab():
    """Tema fora do vocab §5.9 é descartado (blinda o índice)."""
    out = _build_editais([_web_rec(
        tema="tecnologias digitais e conectividade; bagulho-nao-canonico"
    )], source="web")
    assert out[0]["themes"] == ["tecnologias digitais e conectividade"]


def test_web_detects_ict_requirement():
    out = _build_editais([_web_rec(
        texto_cru="A proposta deverá contar com uma ICT coexecutora."
    )], source="web")
    assert out[0]["requires_ict_partner"] is True


def test_web_dedup_by_id():
    out = _build_editais([_web_rec(), _web_rec()], source="web")
    assert len(out) == 1


# ---------------------------------------------------------------------------
# Engine de descoberta (core/opportunity_discovery) — com mocks
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


def test_extract_builds_web_record():
    client = _FakeClient(
        '{"titulo":"Edital X","prazo_envio":"31/12/2026","publico_alvo":"empresas",'
        '"descricao":"fomento","status":"ABERTA",'
        '"tema":["tecnologias digitais e conectividade","lixo"]}'
    )
    rec = od._extract(SearchHit("T", "http://fapesc.sc/e/1", "s", "corpo"),
                      "corpo da página", "FAPESC", client, "m")
    # Schema da fonte web, não mais um schema de discovery próprio.
    assert rec["url"] == normalize_web_url("http://fapesc.sc/e/1")
    assert rec["url_hash"] == web_url_hash("http://fapesc.sc/e/1")
    assert rec["texto_cru"] == "corpo da página"   # corpo guardado pro chunking
    assert rec["title"] == "Edital X"
    assert rec["verificacao"] == "provisorio"
    assert rec["agency"] == "FAPESC"               # sobrevive p/ graduação Fase C
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
    monkeypatch.setattr(od, "_page_text", lambda h: h.content or "")  # sem rede
    seen = []
    def fake_extract(h, txt, agency, c, m):
        seen.append(h.url)
        return {"url": h.url, "url_hash": web_url_hash(h.url), "verificacao": "provisorio"}
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
    monkeypatch.setattr(od, "_page_text", lambda h: h.content or "")  # sem rede
    monkeypatch.setattr(od, "_extract",
                        lambda h, t, a, c, m: {"url": h.url, "url_hash": web_url_hash(h.url),
                                               "verificacao": "provisorio"})
    monkeypatch.setattr(od.ws, "discovery_config",
                        lambda: {"queries": ["q"], "max_results_per_query": 8,
                                 "max_candidates": 40})
    out = od.discover_opportunities(write=False)
    assert [r["url"] for r in out] == ["http://x.org/a"]


def test_discover_no_credential_returns_empty(monkeypatch):
    monkeypatch.setattr(od.websearch, "web_search", lambda q, k=8:
                        [SearchHit("A", "http://x.org/a", "s", "c")])
    monkeypatch.setattr(od, "_known_urls", lambda: set())
    monkeypatch.setattr(od, "_make_client", lambda role: (None, None))
    monkeypatch.setattr(od.ws, "discovery_config",
                        lambda: {"queries": ["q"], "max_results_per_query": 8,
                                 "max_candidates": 40})
    assert od.discover_opportunities(write=False) == []
