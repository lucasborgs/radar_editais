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
    client = _FakeClient('{"is_opportunity": true, "is_hub": false, "agency": "FAPESC"}')
    out = od._triage(SearchHit("t", "http://u", "s", ""), client, "m")
    # `reason` entra no retorno desde o cache negativo (spec 07): vazio quando o
    # modelo não o fornece. Alimenta o log de descarte quando REJEITA.
    assert out == {
        "is_opportunity": True, "is_hub": False, "agency": "FAPESC", "reason": "",
    }


def test_triage_defaults_is_hub_false_when_absent():
    """Resposta sem is_hub (modelo antigo) não quebra — default False."""
    client = _FakeClient('{"is_opportunity": true, "agency": "X"}')
    out = od._triage(SearchHit("t", "http://u", "s", ""), client, "m")
    assert out["is_hub"] is False


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


def test_discover_dou_feeder_behind_flag(monkeypatch):
    """Com DISCOVERY_DOU_ENABLED=1, candidatos DOU entram ANTES do Tavily e
    passam pelo mesmo dedup (ledger + seen)."""
    import core.dou_feeder as df
    dou_hits = [
        SearchHit("Chamada MCTI", "http://in.gov.br/p1", "ementa", "texto dou",
                  agency="MCTI", full_text=True),
        SearchHit("Known", "http://x.org/known", "s", "c", full_text=True),
    ]
    tavily_hits = [SearchHit("B", "http://x.org/b", "sb", "cb")]
    monkeypatch.setenv("DISCOVERY_DOU_ENABLED", "1")
    monkeypatch.setattr(df, "dou_candidates", lambda day=None, **kw: dou_hits)
    monkeypatch.setattr(od.websearch, "web_search", lambda q, k=8: tavily_hits)
    monkeypatch.setattr(od, "_known_urls", lambda: {"http://x.org/known"})
    monkeypatch.setattr(od, "_make_client", lambda role: (object(), "m"))
    monkeypatch.setattr(od, "_triage",
                        lambda h, c, m: {"is_opportunity": True, "agency": ""})
    monkeypatch.setattr(od, "_page_text", lambda h: h.content or "")
    seen = []
    def fake_extract(h, txt, agency, c, m):
        seen.append((h.url, agency))
        return {"url": h.url, "url_hash": web_url_hash(h.url),
                "verificacao": "provisorio"}
    monkeypatch.setattr(od, "_extract", fake_extract)
    monkeypatch.setattr(od.ws, "discovery_config",
                        lambda: {"queries": ["q"], "max_results_per_query": 8,
                                 "max_candidates": 40})
    out = od.discover_opportunities(write=False)
    # DOU primeiro (espinha), known deduplicado, Tavily depois; agency da fonte
    # DOU prevalece sobre o palpite (vazio) da triagem.
    assert seen == [("http://in.gov.br/p1", "MCTI"), ("http://x.org/b", "")]
    assert len(out) == 2


def test_discover_dou_has_own_budget_does_not_starve_tavily(monkeypatch):
    """Orçamentos separados (achado do 1º shadow-run: DOU ~63/dia zerava o
    Tavily num cap compartilhado): DOU respeita max_dou_candidates; Tavily
    conta max_candidates do zero, mesmo com o DOU cheio."""
    import core.dou_feeder as df
    dou_hits = [SearchHit(f"D{i}", f"http://in.gov.br/p{i}", "s", "c",
                          full_text=True) for i in range(10)]
    tavily_hits = [SearchHit(f"T{i}", f"http://x.org/t{i}", "s", "c")
                   for i in range(5)]
    monkeypatch.setenv("DISCOVERY_DOU_ENABLED", "1")
    monkeypatch.setattr(df, "dou_candidates", lambda day=None, **kw: dou_hits)
    monkeypatch.setattr(od.websearch, "web_search", lambda q, k=8: tavily_hits)
    monkeypatch.setattr(od, "_known_urls", lambda: set())
    monkeypatch.setattr(od, "_make_client", lambda role: (object(), "m"))
    monkeypatch.setattr(od, "_triage",
                        lambda h, c, m: {"is_opportunity": True, "agency": ""})
    monkeypatch.setattr(od, "_page_text", lambda h: h.content or "")
    monkeypatch.setattr(od, "_extract",
                        lambda h, t, a, c, m: {"url": h.url,
                                               "url_hash": web_url_hash(h.url),
                                               "verificacao": "provisorio"})
    monkeypatch.setattr(od.ws, "discovery_config",
                        lambda: {"queries": ["q"], "max_results_per_query": 8,
                                 "max_candidates": 3, "max_dou_candidates": 4})
    out = od.discover_opportunities(write=False)
    urls = [r["url"] for r in out]
    # DOU capado em 4 (não 10); Tavily ganha os 3 do orçamento PRÓPRIO.
    assert len([u for u in urls if "in.gov.br" in u]) == 4
    assert len([u for u in urls if "x.org" in u]) == 3


def test_social_domains_dropped_before_triage(monkeypatch):
    """Post de rede social nunca é a página da oportunidade — cai antes da
    triagem (sem gastar LLM). Subdomínio conta; agência própria não."""
    assert od._is_social("https://www.instagram.com/p/abc123") is True
    assert od._is_social("https://m.facebook.com/x") is True
    assert od._is_social("https://fapesc.sc.gov.br/edital") is False
    # 'instagram.com.golpe.br' não casa (sufixo exige fronteira de domínio)
    assert od._is_social("https://instagram.com.golpe.br/x") is False

    hits = [SearchHit("Post", "https://instagram.com/p/1", "s", "c"),
            SearchHit("Edital", "http://fapesc.sc.gov.br/e/1", "s", "c")]
    monkeypatch.setattr(od.websearch, "web_search", lambda q, k=8: hits)
    monkeypatch.setattr(od, "_known_urls", lambda: set())
    monkeypatch.setattr(od, "_make_client", lambda role: (object(), "m"))
    triaged = []
    def fake_triage(h, c, m):
        triaged.append(h.url)
        return {"is_opportunity": False, "agency": ""}
    monkeypatch.setattr(od, "_triage", fake_triage)
    monkeypatch.setattr(od.ws, "discovery_config",
                        lambda: {"queries": ["q"], "max_results_per_query": 8,
                                 "max_candidates": 40})
    od.discover_opportunities(write=False)
    assert triaged == ["http://fapesc.sc.gov.br/e/1"]


def test_discover_dou_disabled_by_default(monkeypatch):
    """Sem a flag, o feeder DOU nem é chamado (caminho Tavily intocado)."""
    import core.dou_feeder as df
    monkeypatch.delenv("DISCOVERY_DOU_ENABLED", raising=False)
    def boom(*a, **kw):
        raise AssertionError("dou_candidates não deveria ser chamado")
    monkeypatch.setattr(df, "dou_candidates", boom)
    monkeypatch.setattr(od.websearch, "web_search", lambda q, k=8: [])
    monkeypatch.setattr(od, "_known_urls", lambda: set())
    monkeypatch.setattr(od.ws, "discovery_config",
                        lambda: {"queries": ["q"], "max_results_per_query": 8,
                                 "max_candidates": 40})
    assert od.discover_opportunities(write=False) == []


def test_page_text_skips_fetch_for_full_text_hits(monkeypatch):
    """Hit com full_text (DOU) usa o content direto, sem full-fetch da URL."""
    import core.llm.agent_tools.profile_tools as pt
    def boom(url):
        raise AssertionError("full-fetch não deveria rodar para hit full_text")
    monkeypatch.setattr(pt, "_fetch_and_parse", boom)
    hit = SearchHit("T", "http://in.gov.br/p1", "snip", "texto completo do dou",
                    full_text=True)
    assert od._page_text(hit) == "texto completo do dou"


def test_ledger_via_kg_store_roundtrip(tmp_path, monkeypatch):
    """Ledger vive no kg_store (durável em PG no worker de prod, cujo FS é
    efêmero); modo file persiste em data/knowledge_graph/.discovery_ledger.json."""
    from core.kg import kg_store
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_KEY", raising=False)
    monkeypatch.setattr(kg_store, "KNOWLEDGE_GRAPH_DIR", tmp_path)
    monkeypatch.setattr(od, "_LEGACY_LEDGER", tmp_path / "nao-existe.json")
    od._save_ledger({"http://x.org/a", "http://x.org/b"})
    assert od._load_ledger() == {"http://x.org/a", "http://x.org/b"}
    assert (tmp_path / ".discovery_ledger.json").exists()


def test_ledger_merges_legacy_file(tmp_path, monkeypatch):
    """Migração: ledger file-based legado (bronze/discovery_raw) entra por
    UNIÃO no load — absorvido no próximo save."""
    import json as _json

    from core.kg import kg_store
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_KEY", raising=False)
    monkeypatch.setattr(kg_store, "KNOWLEDGE_GRAPH_DIR", tmp_path)
    legacy = tmp_path / ".ledger.json"
    legacy.write_text(_json.dumps(["http://velho.org/1"]), encoding="utf-8")
    monkeypatch.setattr(od, "_LEGACY_LEDGER", legacy)
    od._save_ledger({"http://novo.org/2"})
    assert od._load_ledger() == {"http://velho.org/1", "http://novo.org/2"}


# ---------------------------------------------------------------------------
# Crawl de hub (1 nível) — Fase 5
# ---------------------------------------------------------------------------

def test_hub_child_hits_same_domain_under_path():
    """Links sob o caminho do hub, mesmo domínio, viram filhos; externos e o
    próprio hub são descartados."""
    links = [
        {"text": "Desafio A", "href": "/inovacao-aberta/desafio-a"},
        {"text": "Desafio B", "href": "https://tupy.com.br/inovacao-aberta/desafio-b"},
        {"text": "Home", "href": "/"},                              # raiz
        {"text": "Hub", "href": "/inovacao-aberta"},                # o próprio hub
        {"text": "Externo", "href": "https://outrodominio.com/x"},  # outro domínio
        {"text": "LinkedIn", "href": "https://linkedin.com/company/tupy"},  # social
    ]
    out = od._hub_child_hits("https://tupy.com.br/inovacao-aberta", links, set(), 8)
    urls = [h.url for h in out]
    assert "https://tupy.com.br/inovacao-aberta/desafio-a" in urls
    assert "https://tupy.com.br/inovacao-aberta/desafio-b" in urls
    assert all("outrodominio" not in u and "linkedin" not in u for u in urls)
    assert "https://tupy.com.br/" not in urls
    assert len(out) == 2


def test_hub_child_hits_keyword_match_outside_path():
    """Link de desafio fora do path do hub entra pelo slug-keyword."""
    links = [{"text": "Nosso desafio tecnológico", "href": "/desafios/visao-computacional"}]
    out = od._hub_child_hits("https://empresa.com/inovacao", links, set(), 8)
    assert [h.url for h in out] == ["https://empresa.com/desafios/visao-computacional"]


def test_hub_child_hits_dedups_known_and_caps():
    links = [{"text": f"Desafio {i}", "href": f"/inovacao/desafio-{i}"} for i in range(10)]
    known = {od._norm_url("https://e.com/inovacao/desafio-0")}
    out = od._hub_child_hits("https://e.com/inovacao", links, known, 3)
    assert len(out) == 3                                   # cap respeitado
    assert all("desafio-0" not in h.url for h in out)      # known excluído


def test_hub_crawl_disabled_by_default(monkeypatch):
    """Sem a flag, is_hub não dispara expansão (caminho intocado)."""
    monkeypatch.delenv("DISCOVERY_HUB_CRAWL_ENABLED", raising=False)
    hits = [SearchHit("Hub", "http://e.com/inovacao", "s", "c")]
    monkeypatch.setattr(od.websearch, "web_search", lambda q, k=8: hits)
    monkeypatch.setattr(od, "_known_urls", lambda: set())
    monkeypatch.setattr(od, "_make_client", lambda role: (object(), "m"))
    monkeypatch.setattr(od, "_triage",
                        lambda h, c, m: {"is_opportunity": False, "is_hub": True, "agency": ""})
    def boom(*a, **kw):
        raise AssertionError("_expand_hub não deveria rodar com a flag desligada")
    monkeypatch.setattr(od, "_expand_hub", boom)
    monkeypatch.setattr(od.ws, "discovery_config",
                        lambda: {"queries": ["q"], "max_results_per_query": 8,
                                 "max_candidates": 40})
    assert od.discover_opportunities(write=False) == []


def test_hub_crawl_fans_out_children_behind_flag(monkeypatch):
    """Com a flag, o hub é expandido: cada filho passa por triagem+extração."""
    monkeypatch.setenv("DISCOVERY_HUB_CRAWL_ENABLED", "1")
    hub = SearchHit("Hub", "http://e.com/inovacao", "s", "c")
    children = [SearchHit("Desafio 1", "http://e.com/inovacao/d1", "s", ""),
                SearchHit("Desafio 2", "http://e.com/inovacao/d2", "s", "")]
    monkeypatch.setattr(od.websearch, "web_search", lambda q, k=8: [hub])
    monkeypatch.setattr(od, "_known_urls", lambda: set())
    monkeypatch.setattr(od, "_make_client", lambda role: (object(), "m"))

    def fake_triage(h, c, m):
        # o hub não é oportunidade mas é hub; os filhos são oportunidades
        if h.url.endswith("/inovacao"):
            return {"is_opportunity": False, "is_hub": True, "agency": ""}
        return {"is_opportunity": True, "is_hub": False, "agency": ""}
    monkeypatch.setattr(od, "_triage", fake_triage)
    monkeypatch.setattr(od, "_expand_hub", lambda h, known, n: children)
    monkeypatch.setattr(od, "_page_text", lambda h: h.content or "")
    monkeypatch.setattr(od, "_extract",
                        lambda h, t, a, c, m: {"url": h.url, "url_hash": web_url_hash(h.url),
                                               "verificacao": "provisorio"})
    monkeypatch.setattr(od.ws, "discovery_config",
                        lambda: {"queries": ["q"], "max_results_per_query": 8,
                                 "max_candidates": 40})
    out = od.discover_opportunities(write=False)
    urls = sorted(r["url"] for r in out)
    assert urls == ["http://e.com/inovacao/d1", "http://e.com/inovacao/d2"]


def test_hub_children_do_not_re_expand(monkeypatch):
    """Crawl é de 1 nível: um filho que também triasse como hub NÃO re-expande."""
    monkeypatch.setenv("DISCOVERY_HUB_CRAWL_ENABLED", "1")
    hub = SearchHit("Hub", "http://e.com/inovacao", "s", "c")
    child = SearchHit("Filho-hub", "http://e.com/inovacao/sub", "s", "")
    monkeypatch.setattr(od.websearch, "web_search", lambda q, k=8: [hub])
    monkeypatch.setattr(od, "_known_urls", lambda: set())
    monkeypatch.setattr(od, "_make_client", lambda role: (object(), "m"))
    monkeypatch.setattr(od, "_triage",
                        lambda h, c, m: {"is_opportunity": False, "is_hub": True, "agency": ""})
    calls = []
    def fake_expand(h, known, n):
        calls.append(h.url)
        return [child] if h.url.endswith("/inovacao") else [SearchHit("x", "http://e.com/z", "s", "")]
    monkeypatch.setattr(od, "_expand_hub", fake_expand)
    monkeypatch.setattr(od, "_page_text", lambda h: h.content or "")
    monkeypatch.setattr(od, "_extract", lambda h, t, a, c, m: None)
    monkeypatch.setattr(od.ws, "discovery_config",
                        lambda: {"queries": ["q"], "max_results_per_query": 8,
                                 "max_candidates": 40})
    od.discover_opportunities(write=False)
    # só o hub depth-0 foi expandido; o filho (depth 1) não.
    assert calls == ["http://e.com/inovacao"]


def test_discover_no_credential_returns_empty(monkeypatch):
    monkeypatch.setattr(od.websearch, "web_search", lambda q, k=8:
                        [SearchHit("A", "http://x.org/a", "s", "c")])
    monkeypatch.setattr(od, "_known_urls", lambda: set())
    monkeypatch.setattr(od, "_make_client", lambda role: (None, None))
    monkeypatch.setattr(od.ws, "discovery_config",
                        lambda: {"queries": ["q"], "max_results_per_query": 8,
                                 "max_candidates": 40})
    assert od.discover_opportunities(write=False) == []
