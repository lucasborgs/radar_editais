"""Testes do PR4 (hardening pré-beta): resiliência de background + alertas.

Cobre os três eixos da spec histórica (docs/historical/hardening-pre-beta.md §PR4):
  4.1 retry= com backoff exponencial nas tasks UNITÁRIAS — e AUSENTE nos
      wrappers de cron (cron re-roda no dia seguinte; falha vira alerta);
  4.2 falha TRANSIENTE de triagem não vira rejeição persistente no ledger
      (o bug do cache de rejeição) — rejeição REAL continua indo ao ledger;
  4.3 send_alert (core/infra/notify.py): no-op sem env, envia via SMTP com env,
      e NUNCA propaga falha de envio.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import core.infra.notify as notify
import core.opportunity_discovery as od
import core.tasks as tasks
from core.web_search import SearchHit

# ---------------------------------------------------------------------------
# 4.1 — retry= nas tasks unitárias; SEM retry nos wrappers de cron
# ---------------------------------------------------------------------------

UNIT_TASKS = [
    "enrich_content",
    "embed_content",
    "reflect_workspace",
    "synthesize_patterns",
    "chunk_edital",
]
CRON_TASKS = ["synthesize_patterns_cron", "run_daily_etl", "discover_opportunities"]


@pytest.mark.parametrize("name", UNIT_TASKS)
def test_unit_task_has_exponential_retry(name):
    """Tasks unitárias idempotentes re-tentam (max 3) com backoff exponencial."""
    task = tasks.app.tasks[name]
    strat = task.retry_strategy
    assert strat is not None, f"{name} deveria ter retry= configurado"
    assert strat.max_attempts == 3
    assert strat.exponential_wait > 0, "backoff deve ser exponencial"


@pytest.mark.parametrize("name", CRON_TASKS)
def test_cron_wrapper_has_no_retry(name):
    """Wrappers de cron NÃO re-tentam: re-rodam no dia seguinte e a falha
    vira alerta por e-mail (4.3)."""
    assert tasks.app.tasks[name].retry_strategy is None, \
        f"{name} é cron — não deve ter retry="


# ---------------------------------------------------------------------------
# 4.2 — falha transiente de triagem ≠ rejeição no ledger
# ---------------------------------------------------------------------------

class _ExplodingClient:
    """Cliente LLM que explode na chamada (simula timeout/5xx transiente)."""

    class chat:  # noqa: N801 — imita a superfície client.chat.completions.create
        class completions:  # noqa: N801
            @staticmethod
            def create(**_):
                raise TimeoutError("LLM indisponível (transiente)")


class _FakeStore:
    """kg_store em memória: um único blob `discovery_ledger` (mesmo harness
    de tests/test_opportunity_discovery_cache.py)."""

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


_HIT = SearchHit(title="Chamada X", url="https://exemplo.org/chamada-x",
                 snippet="edital de inovação", content="")


def _setup_discovery(monkeypatch, store):
    """Stubs comuns: config mínima, busca com 1 hit, credenciais presentes."""
    monkeypatch.setattr(od, "kg_store", store)
    monkeypatch.setattr(
        od.ws, "discovery_config",
        lambda: {"queries": ["q"], "max_results_per_query": 8,
                 "max_candidates": 40, "max_dou_candidates": 80,
                 "max_hub_children": 8, "reject_cache_ttl_days": 30},
    )
    monkeypatch.setattr(od.websearch, "web_search", lambda q, k=8: [_HIT])
    monkeypatch.setattr(od, "_stage_records", lambda records: len(records))


def test_triage_returns_none_on_exception():
    """_triage devolve None quando o client LLM explode (falha transiente)."""
    assert od._triage(_HIT, _ExplodingClient(), "modelo") is None


def test_transient_triage_failure_does_not_touch_ledger(monkeypatch):
    """Triagem explodindo → URL pulada SEM gravar no ledger (nem cache
    negativo, nem dedup positivo) — ela volta na próxima rodada."""
    store = _FakeStore()
    _setup_discovery(monkeypatch, store)
    # Client real explodindo: exercita o caminho de exceção do _triage real.
    monkeypatch.setattr(od, "_make_client",
                        lambda role: (_ExplodingClient(), "modelo"))
    extract_calls = []
    monkeypatch.setattr(od, "_extract",
                        lambda *a, **k: extract_calls.append(1))

    records = od.discover_opportunities(write=True)

    norm = od._norm_url(_HIT.url)
    assert records == []
    assert norm not in store.ledger["rejected"], \
        "falha transiente NÃO pode virar rejeição persistente"
    assert norm not in store.ledger["urls"], \
        "falha transiente NÃO pode entrar no dedup positivo"
    assert extract_calls == [], "sem veredito não há extração"


def test_real_rejection_still_recorded_in_ledger(monkeypatch):
    """Rejeição REAL (LLM respondeu is_opportunity=false) segue indo ao
    cache negativo com motivo + timestamp."""
    store = _FakeStore()
    _setup_discovery(monkeypatch, store)
    monkeypatch.setattr(od, "_make_client", lambda role: ("client", "modelo"))
    monkeypatch.setattr(
        od, "_json_from_llm",
        lambda *a, **k: {"is_opportunity": False, "is_hub": False,
                         "agency": "", "reason": "notícia, não é chamada"},
    )

    od.discover_opportunities(write=True)

    norm = od._norm_url(_HIT.url)
    entry = store.ledger["rejected"].get(norm)
    assert entry, "rejeição real deve entrar no cache negativo"
    assert "notícia" in entry["reason"]
    assert entry["ts"]


def test_transient_extract_failure_does_not_touch_ledger(monkeypatch):
    """Extração falhando (rec=None) também não polui o ledger: a URL não
    entra no dedup positivo nem no cache negativo — volta na próxima run."""
    store = _FakeStore()
    _setup_discovery(monkeypatch, store)
    monkeypatch.setattr(od, "_make_client", lambda role: ("client", "modelo"))
    monkeypatch.setattr(
        od, "_triage",
        lambda *a, **k: {"is_opportunity": True, "is_hub": False,
                         "agency": "", "reason": ""},
    )
    monkeypatch.setattr(od, "_page_text", lambda hit: "texto")
    monkeypatch.setattr(od, "_extract", lambda *a, **k: None)  # falha

    records = od.discover_opportunities(write=True)

    norm = od._norm_url(_HIT.url)
    assert records == []
    assert norm not in store.ledger["urls"]
    assert norm not in store.ledger["rejected"]


# ---------------------------------------------------------------------------
# 4.3 — send_alert (core/infra/notify.py)
# ---------------------------------------------------------------------------

_ALERT_VARS = ("ALERT_SMTP_HOST", "ALERT_SMTP_PORT", "ALERT_SMTP_USER",
               "ALERT_SMTP_PASSWORD", "ALERT_EMAIL_FROM", "ALERT_EMAIL_TO")


def _clear_alert_env(monkeypatch):
    for var in _ALERT_VARS:
        monkeypatch.delenv(var, raising=False)


class _FakeSMTP:
    """Substitui smtplib.SMTP: grava as chamadas para asserção."""

    instances: list[_FakeSMTP] = []

    def __init__(self, host, port, timeout=None):
        self.host, self.port = host, port
        self.calls: list[tuple] = []
        _FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self):
        self.calls.append(("starttls",))

    def login(self, user, password):
        self.calls.append(("login", user, password))

    def send_message(self, msg):
        self.calls.append(("send_message", msg))


def test_send_alert_noop_without_env(monkeypatch, caplog):
    """Sem ALERT_SMTP_USER/ALERT_EMAIL_TO → no-op (False) com warning; nada
    de SMTP é tocado (dev não quebra)."""
    import logging

    _clear_alert_env(monkeypatch)
    monkeypatch.setattr(notify, "_warned_unconfigured", False)
    _FakeSMTP.instances = []
    monkeypatch.setattr(notify.smtplib, "SMTP", _FakeSMTP)

    with caplog.at_level(logging.WARNING, logger=notify.logger.name):
        assert notify.send_alert("assunto", "corpo") is False
        # Segunda chamada: warning só UMA vez por processo (sem ruído diário).
        assert notify.send_alert("assunto 2", "corpo 2") is False

    assert _FakeSMTP.instances == [], "no-op não pode abrir conexão SMTP"
    warnings = [r for r in caplog.records if "não configurados" in r.message]
    assert len(warnings) == 1, "warning de não-configurado deve sair 1x"


def test_send_alert_sends_via_smtp_with_env(monkeypatch):
    """Com env configurado, envia via STARTTLS + login + send_message."""
    _clear_alert_env(monkeypatch)
    monkeypatch.setenv("ALERT_SMTP_USER", "radar@example.com")
    monkeypatch.setenv("ALERT_SMTP_PASSWORD", "app-password")
    monkeypatch.setenv("ALERT_EMAIL_TO", "lucas@example.com")
    _FakeSMTP.instances = []
    monkeypatch.setattr(notify.smtplib, "SMTP", _FakeSMTP)

    assert notify.send_alert("[radar] teste", "corpo do alerta") is True

    (smtp,) = _FakeSMTP.instances
    assert smtp.host == "smtp.gmail.com" and smtp.port == 587  # defaults
    kinds = [c[0] for c in smtp.calls]
    assert kinds == ["starttls", "login", "send_message"]
    assert smtp.calls[1] == ("login", "radar@example.com", "app-password")
    msg = smtp.calls[2][1]
    assert msg["Subject"] == "[radar] teste"
    assert msg["From"] == "radar@example.com"  # FROM default = USER
    assert msg["To"] == "lucas@example.com"


def test_send_alert_never_propagates_failure(monkeypatch):
    """Falha de envio → False + log, NUNCA exceção (alerta não pode derrubar
    o cron que ele observa)."""
    _clear_alert_env(monkeypatch)
    monkeypatch.setenv("ALERT_SMTP_USER", "radar@example.com")
    monkeypatch.setenv("ALERT_EMAIL_TO", "lucas@example.com")

    def _boom(*a, **k):
        raise ConnectionRefusedError("SMTP fora do ar")

    monkeypatch.setattr(notify.smtplib, "SMTP", _boom)

    assert notify.send_alert("assunto", "corpo") is False  # não levanta


def test_discover_cron_alerts_on_total_failure(monkeypatch):
    """Except de topo do cron de discovery: 1 e-mail de falha total + re-raise
    (o job consta como failed no procrastinate)."""
    alerts: list[tuple[str, str]] = []
    monkeypatch.setattr(tasks, "send_alert",
                        lambda subject, body: alerts.append((subject, body)) or True)

    def _boom(*a, **k):
        raise RuntimeError("Tavily fora do ar")

    monkeypatch.setattr(od, "discover_opportunities", _boom)

    with pytest.raises(RuntimeError, match="Tavily fora do ar"):
        asyncio.run(tasks.discover_opportunities_task.func(timestamp=0))

    assert len(alerts) == 1, "máx. 1 e-mail por run de cron"
    subject, body = alerts[0]
    assert "discover_opportunities" in subject
    assert "Tavily fora do ar" in body
