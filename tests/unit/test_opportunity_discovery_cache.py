"""Testes da Descoberta: cache negativo + log de descarte + telemetria RT03-T04.

Cobre:
  - Cache negativo (spec 07) — rejeição com TTL elimina re-triagem.
  - _origin_domain robusto — None sem hostname, sem porta/userinfo/trailing dot.
  - search_available — detecção de credencial sem chamar API.
  - db=None: start_run não é chamado, finish_all não levanta.
  - start_run / finish_run com proteção try/except.
  - hub_expansion.skipped quando flag DISCOVERY_HUB_CRAWL_ENABLED desligada.
  - open_search skipped quando search_available=False (no_credentials).
  - partial runs com reason_code canônico (provider_error / unknown).
  - hubs_expanded contabilizado no relatório hub_expansion, não no pai.
  - Métricas por família (returned_family_*, query_failures_family_*).

Estratégia: mockamos triagem, extração, kg_store, busca e telemetria para
medir sem rede/LLM.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import radar.core.ingestion.opportunity_discovery as od
import radar.core.services.source_runs as sr
from radar.core.web_search import SearchHit

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Harness: um "kg_store" em memória + stubs de LLM/busca, injetados via monkeypatch
# ---------------------------------------------------------------------------

class _FakeStore:
    """Substitui kg_store: um único blob `discovery_ledger` em memória, durável
    entre execuções de discover_opportunities dentro do mesmo teste."""

    def __init__(self):
        self.ledger: dict = {"urls": [], "rejected": {}}

    def load(self, key, default=None):
        if key == "discovery_ledger":
            return self.ledger
        if key == "index":
            return {"editais": []}
        return {} if default is None else default

    def load_index(self, *, historico=False):
        return {"editais": []}

    def save(self, key, blob):
        if key == "discovery_ledger":
            self.ledger = blob


def _setup(monkeypatch, store, *, hits, triage_results):
    """Liga os stubs. `triage_results` mapeia url -> dict de veredito; cada URL
    triada consome/repete o veredito. Conta chamadas reais a _triage."""
    calls = {"triage": []}

    monkeypatch.setattr(od, "kg_store", store)

    # config mínima com TTL conhecido
    monkeypatch.setattr(
        od.ws, "discovery_config",
        lambda: {"queries": ["q"], "max_results_per_query": 8,
                 "max_candidates": 40, "max_dou_candidates": 80,
                 "max_hub_children": 8, "reject_cache_ttl_days": 30},
    )

    # search_available simula credencial presente (busca mockada não precisa de API key)
    monkeypatch.setattr(od.websearch, "search_available", lambda: True)

    # busca devolve sempre os mesmos hits (fonte estável que muda pouco — o caso
    # exato em que o cache negativo paga)
    monkeypatch.setattr(od.websearch, "web_search", lambda q, k=8: list(hits))

    # credenciais LLM "presentes" (clientes sentinela; _triage é mockado)
    monkeypatch.setattr(od, "_make_client", lambda role: ("client", "model"))

    def _fake_triage(hit, client, model):
        calls["triage"].append(hit.url)
        return dict(triage_results[hit.url])

    monkeypatch.setattr(od, "_triage", _fake_triage)
    # extração nunca deve importar aqui (todos os hits do teste são rejeitados)
    monkeypatch.setattr(od, "_extract", lambda *a, **k: None)
    monkeypatch.setattr(od, "_page_text", lambda hit: "texto")

    return calls


def _setup_with_telemetry(monkeypatch, store, *, hits, triage_results):
    """Like _setup, but também stuba start_run/finish_run para testar telemetria.

    Retorna dict com chaves: triage, start_run, finish_run (listas de chamadas).
    """
    calls = _setup(monkeypatch, store, hits=hits, triage_results=triage_results)
    monkeypatch.setattr(od, "_get_db", lambda: object())  # db não-None
    calls["start_run"] = []
    calls["finish_run"] = []

    def _fake_start_run(db, *, batch_id, source_key, mode):
        calls["start_run"].append({"channel": source_key, "mode": mode})
        return f"run_{source_key}"

    def _fake_finish_run(db, *, run_id, status, records_observed=None,
                         records_emitted=None, records_staged=None,
                         error_count=None, reason_code=None, metrics=None):
        calls["finish_run"].append({
            "run_id": run_id, "status": status,
            "records_observed": records_observed,
            "records_emitted": records_emitted,
            "records_staged": records_staged,
            "error_count": error_count,
            "reason_code": reason_code,
            "metrics": metrics,
        })
        return True

    monkeypatch.setattr(sr, "start_run", _fake_start_run)
    monkeypatch.setattr(sr, "finish_run", _fake_finish_run)
    return calls


_HIT = SearchHit(title="Lixo", url="https://exemplo.org/lixo",
                 snippet="nao e fomento", content="")
_REJECT = {"is_opportunity": False, "is_hub": False, "agency": "",
           "reason": "assistencia social, irrelevante a deep-tech"}


def test_rejected_url_within_ttl_skips_triage(monkeypatch):
    """(a) URL já rejeitada e dentro do TTL NÃO chama _triage de novo."""
    store = _FakeStore()
    calls = _setup(monkeypatch, store, hits=[_HIT],
                   triage_results={_HIT.url: _REJECT})

    # 1ª rodada: tria, rejeita, persiste no cache negativo
    od.discover_opportunities(write=True)
    assert calls["triage"] == [_HIT.url], "1ª rodada deve triar a URL"
    norm = od._norm_url(_HIT.url)
    assert norm in store.ledger["rejected"], "rejeição deve entrar no ledger"

    # 2ª rodada: URL dentro do TTL → pulada, _triage NÃO é chamado de novo
    calls["triage"].clear()
    od.discover_opportunities(write=True)
    assert calls["triage"] == [], "2ª rodada deve pular a triagem (cache negativo)"


def test_expired_ttl_retriages(monkeypatch):
    """(b) URL rejeitada com TTL expirado É re-triada."""
    store = _FakeStore()
    calls = _setup(monkeypatch, store, hits=[_HIT],
                   triage_results={_HIT.url: _REJECT})

    # Semeia o cache negativo com uma rejeição ANTIGA (40 dias > TTL 30)
    norm = od._norm_url(_HIT.url)
    old = datetime.now(timezone.utc) - timedelta(days=40)
    store.ledger["rejected"][norm] = {"reason": "antigo", "ts": old.isoformat()}

    od.discover_opportunities(write=True)
    assert calls["triage"] == [_HIT.url], "TTL expirado deve re-triar a URL"


def test_discard_is_logged_and_recorded(monkeypatch, caplog):
    """(c) O descarte é registrado no cache negativo COM motivo + logado."""
    import logging

    store = _FakeStore()
    _setup(monkeypatch, store, hits=[_HIT], triage_results={_HIT.url: _REJECT})

    with caplog.at_level(logging.INFO, logger=od.logger.name):
        od.discover_opportunities(write=True)

    norm = od._norm_url(_HIT.url)
    entry = store.ledger["rejected"][norm]
    assert entry["reason"], "rejeição deve guardar o motivo curto da triagem"
    assert "assistencia social" in entry["reason"]
    assert "ts" in entry and entry["ts"], "rejeição deve guardar timestamp"
    assert any("descarte na triagem" in r.message for r in caplog.records), \
        "o descarte deve ser logado (observabilidade do custo evitado)"


def test_dry_run_measures_skips_without_persisting(monkeypatch):
    """write=False (dry-run nativo, spec §Validação) mede skips do cache negativo
    sem persistir. Aqui: cache pré-semeado → a triagem é pulada e o ledger fica
    intacto (dry-run não escreve)."""
    store = _FakeStore()
    calls = _setup(monkeypatch, store, hits=[_HIT],
                   triage_results={_HIT.url: _REJECT})

    norm = od._norm_url(_HIT.url)
    fresh = datetime.now(timezone.utc)
    store.ledger["rejected"][norm] = {"reason": "lixo", "ts": fresh.isoformat()}
    before = dict(store.ledger)

    od.discover_opportunities(write=False)
    assert calls["triage"] == [], "cache fresco deve pular a triagem mesmo em dry-run"
    assert store.ledger == before, "dry-run (write=False) não deve persistir o ledger"


# =============================================================================
# _origin_domain — fix 1
# =============================================================================


def test_origin_domain_no_hostname():
    """URL sem hostname → None."""
    assert od._origin_domain("not-a-url") is None
    assert od._origin_domain("") is None
    assert od._origin_domain("data:text/plain,hello") is None


def test_origin_domain_strips_port():
    """Porta removida do domínio."""
    assert od._origin_domain("http://example.com:8080/path") == "example.com"


def test_origin_domain_strips_userinfo():
    """Userinfo removida do domínio."""
    assert od._origin_domain("http://user:pass@example.com/path") == "example.com"


def test_origin_domain_trailing_dot():
    """Trailing dot removido."""
    assert od._origin_domain("http://example.com./path") == "example.com"


def test_origin_domain_case_normalized():
    """Case normalizado para lowercase."""
    assert od._origin_domain("HTTP://ExAmPlE.CoM") == "example.com"


# =============================================================================
# search_available — fix 4
# =============================================================================


def test_search_available_true(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "sk-xxx")
    monkeypatch.delenv("WEB_SEARCH_BACKEND", raising=False)
    assert od.websearch.search_available() is True


def test_search_available_false(monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.delenv("WEB_SEARCH_BACKEND", raising=False)
    assert od.websearch.search_available() is False


# =============================================================================
# db=None guard — fix 2 (start_run não chamado, finish_all não levanta)
# =============================================================================


def test_db_none_skips_start_run(monkeypatch):
    """db=None → start_run NÃO é chamado."""
    store = _FakeStore()
    start_calls = []

    monkeypatch.setattr(od, "_get_db", lambda: None)

    monkeypatch.setattr(od, "kg_store", store)
    monkeypatch.setattr(od.ws, "discovery_config",
                        lambda: {"queries": ["q"],
                                 "max_results_per_query": 8,
                                 "max_candidates": 40, "max_dou_candidates": 80,
                                 "max_hub_children": 8, "reject_cache_ttl_days": 30})
    monkeypatch.setattr(od.websearch, "search_available", lambda: True)
    monkeypatch.setattr(od.websearch, "web_search", lambda q, k=8: [_HIT])
    monkeypatch.setattr(od, "_make_client", lambda role: ("client", "model"))
    monkeypatch.setattr(od, "_page_text", lambda hit: "texto")
    monkeypatch.setattr(od, "_triage", lambda *a: dict(_REJECT))
    monkeypatch.setattr(od, "_extract", lambda *a, **k: None)

    def _track_start(db, *, batch_id, source_key, mode):
        start_calls.append(source_key)
        return f"run_{source_key}"

    monkeypatch.setattr(sr, "start_run", _track_start)
    monkeypatch.setattr(sr, "finish_run", lambda *a, **k: True)

    od.discover_opportunities(write=True)
    assert start_calls == [], "start_run não deve ser chamado com db=None"


def test_db_none_still_processes_hits(monkeypatch):
    """db=None → hits ainda são processados (start_run/finish_run ignorados)."""
    store = _FakeStore()
    monkeypatch.setattr(od, "_get_db", lambda: None)
    monkeypatch.setattr(od, "kg_store", store)
    monkeypatch.setattr(od.ws, "discovery_config",
                        lambda: {"queries": ["q"],
                                 "max_results_per_query": 8,
                                 "max_candidates": 40, "max_dou_candidates": 80,
                                 "max_hub_children": 8, "reject_cache_ttl_days": 30})
    monkeypatch.setattr(od.websearch, "search_available", lambda: True)
    monkeypatch.setattr(od.websearch, "web_search", lambda q, k=8: [_HIT])
    monkeypatch.setattr(od, "_make_client", lambda role: ("client", "model"))
    monkeypatch.setattr(od, "_page_text", lambda hit: "texto")
    monkeypatch.setattr(od, "_triage", lambda *a: dict(_REJECT))
    monkeypatch.setattr(od, "_extract", lambda *a, **k: None)

    od.discover_opportunities(write=True)
    norm = od._norm_url(_HIT.url)
    assert norm in store.ledger.get("rejected", {}), "rejeição deve persistir mesmo com db=None"


def test_start_run_exception_logged(monkeypatch):
    """start_run exception loga mas não quebra."""
    store = _FakeStore()
    monkeypatch.setattr(od, "_get_db", lambda: object())
    monkeypatch.setattr(od, "kg_store", store)
    monkeypatch.setattr(od.ws, "discovery_config",
                        lambda: {"queries": ["q"],
                                 "max_results_per_query": 8,
                                 "max_candidates": 40, "max_dou_candidates": 80,
                                 "max_hub_children": 8, "reject_cache_ttl_days": 30})
    monkeypatch.setattr(od.websearch, "search_available", lambda: True)
    monkeypatch.setattr(od.websearch, "web_search", lambda q, k=8: [_HIT])
    monkeypatch.setattr(od, "_make_client", lambda role: ("client", "model"))
    monkeypatch.setattr(od, "_page_text", lambda hit: "texto")
    monkeypatch.setattr(od, "_triage", lambda *a: dict(_REJECT))
    monkeypatch.setattr(od, "_extract", lambda *a, **k: None)

    def _exploding_start(db, *, batch_id, source_key, mode):
        raise RuntimeError("start_run boom")

    monkeypatch.setattr(sr, "start_run", _exploding_start)
    monkeypatch.setattr(sr, "finish_run", lambda *a, **k: True)

    result = od.discover_opportunities(write=True)
    assert len(result) == 0, "deve completar sem levantar"


def test_finish_run_exception_logged(monkeypatch):
    """finish_run exception loga mas não quebra."""
    store = _FakeStore()
    monkeypatch.setattr(od, "_get_db", lambda: object())
    monkeypatch.setattr(od, "kg_store", store)
    monkeypatch.setattr(od.ws, "discovery_config",
                        lambda: {"queries": ["q"],
                                 "max_results_per_query": 8,
                                 "max_candidates": 40, "max_dou_candidates": 80,
                                 "max_hub_children": 8, "reject_cache_ttl_days": 30})
    monkeypatch.setattr(od.websearch, "search_available", lambda: True)
    monkeypatch.setattr(od.websearch, "web_search", lambda q, k=8: [_HIT])
    monkeypatch.setattr(od, "_make_client", lambda role: ("client", "model"))
    monkeypatch.setattr(od, "_page_text", lambda hit: "texto")
    monkeypatch.setattr(od, "_triage", lambda *a: dict(_REJECT))
    monkeypatch.setattr(od, "_extract", lambda *a, **k: None)

    def _fake_start(db, *, batch_id, source_key, mode):
        return f"run_{source_key}"

    def _exploding_finish(db, *, run_id, **kwargs):
        raise RuntimeError("finish_run boom")

    monkeypatch.setattr(sr, "start_run", _fake_start)
    monkeypatch.setattr(sr, "finish_run", _exploding_finish)

    result = od.discover_opportunities(write=True)
    assert len(result) == 0, "deve completar sem levantar"


# =============================================================================
# hub_expansion skipped — fix 3
# =============================================================================


def test_hub_expansion_skipped_when_disabled(monkeypatch):
    """Sem DISCOVERY_HUB_CRAWL_ENABLED → hub_expansion.skipped=True."""
    store = _FakeStore()
    monkeypatch.delenv("DISCOVERY_HUB_CRAWL_ENABLED", raising=False)
    calls = _setup_with_telemetry(monkeypatch, store, hits=[_HIT],
                                   triage_results={_HIT.url: _REJECT})

    od.discover_opportunities(write=True)
    fin = {c["run_id"]: c for c in calls["finish_run"]}
    assert fin["run_hub_expansion"]["status"] == "skipped"


# =============================================================================
# search_backend unavailable → skipped/no_credentials — fix 4
# =============================================================================


def test_search_skipped_when_unavailable(monkeypatch):
    """search_available=False → open_search skipped + reason=no_credentials."""
    store = _FakeStore()
    calls = _setup_with_telemetry(monkeypatch, store, hits=[_HIT],
                                   triage_results={_HIT.url: _REJECT})
    monkeypatch.setattr(od.websearch, "search_available", lambda: False)

    od.discover_opportunities(write=True)
    fin = {c["run_id"]: c for c in calls["finish_run"]}
    os_run = fin["run_open_search"]
    assert os_run["status"] == "skipped"
    assert os_run["reason_code"] == "no_credentials"


# =============================================================================
# partial runs com reason_code canônico — fix 5
# =============================================================================


def test_partial_reason_provider_error(monkeypatch):
    """query_failures > 0 → partial + reason_code=provider_error."""
    store = _FakeStore()
    calls = _setup_with_telemetry(monkeypatch, store, hits=[_HIT],
                                   triage_results={_HIT.url: _REJECT})
    monkeypatch.setattr(od.websearch, "web_search",
                        lambda q, k=8: (_ for _ in ()).throw(RuntimeError("timeout")))
    monkeypatch.setattr(od.ws, "discovery_queries",
                        lambda: [{"text": "inovação", "family": "state_funding"}])

    od.discover_opportunities(write=True)
    fin = {c["run_id"]: c for c in calls["finish_run"]}
    os_run = fin["run_open_search"]
    assert os_run["status"] == "partial"
    assert os_run["reason_code"] == "provider_error"
    assert os_run["error_count"] == 1


def test_partial_reason_unknown_on_triage_failure(monkeypatch):
    """triage_failed > 0 (e sem query_failures) → partial + reason_code=unknown."""
    store = _FakeStore()
    calls = _setup_with_telemetry(monkeypatch, store, hits=[_HIT],
                                   triage_results={_HIT.url: _REJECT})
    monkeypatch.setattr(od, "_triage", lambda *a: None)

    od.discover_opportunities(write=True)
    fin = {c["run_id"]: c for c in calls["finish_run"]}
    os_run = fin["run_open_search"]
    assert os_run["status"] == "partial"
    assert os_run["reason_code"] == "unknown"


# =============================================================================
# hubs_expanded no relatório hub_expansion — fix 6
# =============================================================================


def test_hubs_expanded_in_hub_expansion_report(monkeypatch):
    """hubs_expanded contabilizado no canal hub_expansion, não no open_search."""
    store = _FakeStore()
    monkeypatch.setenv("DISCOVERY_HUB_CRAWL_ENABLED", "1")

    # Um hit que a triagem marca como hub e oportunidade
    hub_hit = SearchHit(title="Hub de Inovação", url="https://hub.example.com",
                        snippet="plataforma de desafios", content="lista de desafios")
    hub_verdict = {"is_opportunity": True, "is_hub": True,
                   "agency": "hubcorp", "reason": ""}

    calls = _setup_with_telemetry(monkeypatch, store, hits=[hub_hit],
                                   triage_results={hub_hit.url: hub_verdict})
    # _expand_hub retorna filhos simulados
    child = SearchHit(title="Desafio X", url="https://hub.example.com/desafio-x",
                      snippet="desafio tecnológico", content="")

    monkeypatch.setattr(od, "_expand_hub",
                        lambda h, known, max_c: [child])
    monkeypatch.setattr(od, "_hub_child_hits",
                        lambda *a: [child])
    # triagem do filho rejeita (para não precisar extrair)
    triage_orig = od._triage
    monkeypatch.setattr(od, "_triage",
                        lambda hit, c, m: triage_orig(hit, c, m)
                        if hit.url == hub_hit.url
                        else dict(_REJECT))
    # extração aceita para o hub
    monkeypatch.setattr(od, "_page_text", lambda hit: "texto do hub")
    monkeypatch.setattr(od, "_extract", lambda h, pt, a, c, m: {
        "url": h.url, "title": h.title, "agency": a, "fonte": a or "hub",
    })

    od.discover_opportunities(write=True)
    fin = {c["run_id"]: c for c in calls["finish_run"]}
    hub_metrics = fin["run_hub_expansion"]["metrics"] or {}
    os_metrics = fin["run_open_search"]["metrics"] or {}
    assert hub_metrics.get("hubs_expanded", 0) >= 1, \
        "hub_expansion.hubs_expanded deve ser >= 1"
    if "hubs_expanded" in os_metrics:
        assert os_metrics["hubs_expanded"] == 0, \
            "open_search não deve contabilizar hubs_expanded"


# =============================================================================
# Métricas por família — fix 7
# =============================================================================


def test_per_family_metrics_returned(monkeypatch):
    """returned_family_{family} aparece nas métricas do open_search."""
    store = _FakeStore()
    monkeypatch.setattr(od, "_get_db", lambda: object())
    monkeypatch.setattr(od, "kg_store", store)
    monkeypatch.setattr(od.ws, "discovery_config",
                        lambda: {"queries": ["q"],
                                 "max_results_per_query": 8,
                                 "max_candidates": 40, "max_dou_candidates": 80,
                                 "max_hub_children": 8, "reject_cache_ttl_days": 30})
    monkeypatch.setattr(od.websearch, "search_available", lambda: True)
    monkeypatch.setattr(od.websearch, "web_search", lambda q, k=8: [_HIT])
    monkeypatch.setattr(od, "_make_client", lambda role: ("client", "model"))
    monkeypatch.setattr(od, "_page_text", lambda hit: "texto")
    monkeypatch.setattr(od, "_triage", lambda *a: dict(_REJECT))
    monkeypatch.setattr(od, "_extract", lambda *a, **k: None)

    # Semeia discovery_queries com família conhecida
    monkeypatch.setattr(od.ws, "discovery_queries",
                        lambda: [{"text": "inovação", "family": "state_funding"}])

    finish_calls = []
    def _capture_finish(db, *, run_id, **kw):
        finish_calls.append({"run_id": run_id, **kw})
        return True
    monkeypatch.setattr(sr, "start_run",
                        lambda db, *, batch_id, source_key, mode: f"run_{source_key}")
    monkeypatch.setattr(sr, "finish_run", _capture_finish)

    od.discover_opportunities(write=True)
    os_call = next(c for c in finish_calls if "open_search" in c["run_id"])
    metrics = os_call.get("metrics") or {}
    assert metrics.get("returned_family_state_funding", 0) == 1, \
        "deve reportar returned_family_state_funding=1"


def test_per_family_metrics_query_failures(monkeypatch):
    """query_failures_family_{family} aparece nas métricas do open_search."""
    store = _FakeStore()
    monkeypatch.setattr(od, "_get_db", lambda: object())
    monkeypatch.setattr(od, "kg_store", store)
    monkeypatch.setattr(od.ws, "discovery_config",
                        lambda: {"queries": ["q"],
                                 "max_results_per_query": 8,
                                 "max_candidates": 40, "max_dou_candidates": 80,
                                 "max_hub_children": 8, "reject_cache_ttl_days": 30})
    monkeypatch.setattr(od.websearch, "search_available", lambda: True)
    monkeypatch.setattr(od.websearch, "web_search",
                        lambda q, k=8: (_ for _ in ()).throw(RuntimeError("fail")))
    monkeypatch.setattr(od, "_make_client", lambda role: ("client", "model"))
    monkeypatch.setattr(od, "_page_text", lambda hit: "texto")
    monkeypatch.setattr(od, "_triage", lambda *a: dict(_REJECT))
    monkeypatch.setattr(od, "_extract", lambda *a, **k: None)

    monkeypatch.setattr(od.ws, "discovery_queries",
                        lambda: [{"text": "inovação", "family": "state_funding"}])

    finish_calls = []
    def _capture_finish(db, *, run_id, **kw):
        finish_calls.append({"run_id": run_id, **kw})
        return True
    monkeypatch.setattr(sr, "start_run",
                        lambda db, *, batch_id, source_key, mode: f"run_{source_key}")
    monkeypatch.setattr(sr, "finish_run", _capture_finish)

    od.discover_opportunities(write=True)
    os_call = next(c for c in finish_calls if "open_search" in c["run_id"])
    metrics = os_call.get("metrics") or {}
    assert metrics.get("query_failures_family_state_funding", 0) == 1, \
        "deve reportar query_failures_family_state_funding=1"
