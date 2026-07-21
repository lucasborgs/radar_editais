"""Testes do cache negativo + log de descarte da Descoberta (spec 07).

O problema que isto cobre: URLs rejeitadas na triagem entram num cache negativo
persistido no ledger (`discovery_ledger.rejected`) com TTL. Dentro do TTL, a URL
é pulada SEM nova chamada `_triage` — corta a re-triagem (re-pagamento) diária das
mesmas URLs lixo. Após o TTL, a URL volta a ser triada.

Estratégia: mockamos a triagem (`_triage`) e a persistência (`kg_store`) para
medir, sem rede/LLM, quantas chamadas de triagem o cache elimina e que o descarte
é logado.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import radar.core.ingestion.opportunity_discovery as od
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
