"""Consciência temporal canônica (Front 3) — core/temporal.

Cobre o cálculo de dias restantes / expiração contra date.today() e a
renderização do bloco `[CONTEXTO TEMPORAL: ...]` para os casos de fronteira:
prazo futuro, prazo curto (urgência), prazo vencido, fluxo contínuo (sem
deadline) e edital inexistente.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from radar.core.kg import entity_catalog, temporal
from radar.core.services.temporal_read_model import today_sao_paulo

pytestmark = pytest.mark.unit


def _patch_hypergraph(monkeypatch, editais: list[dict]):
    """Stub de entity_catalog.get_entity_temporal (fonte SQL de deadline/status)."""
    by_id = {}
    for e in editais:
        deadline = e.get("deadline")
        parsed = temporal.parse_deadline(deadline)
        if parsed is not None:
            state = "active" if parsed >= today_sao_paulo() else "closed"
            mode, value = "fixed", parsed.isoformat()
        else:
            state, mode, value = "active", "continuous", None
        by_id[e.get("id", "")] = {
            "deadline": deadline, "status": e.get("status"),
            "temporal_mode": mode, "validity_state": state,
            "temporal_value": value, "decision_source": "source",
            "last_verified_at": None,
        }
    monkeypatch.setattr(entity_catalog, "get_entity_temporal", lambda eid: by_id.get(eid))


def _fmt(d: date) -> str:
    return d.strftime("%d/%m/%Y")


def test_future_deadline_has_positive_days(monkeypatch):
    futuro = today_sao_paulo() + timedelta(days=40)
    _patch_hypergraph(monkeypatch, [
        {"id": "finep:1", "status": "ABERTA", "deadline": _fmt(futuro)},
    ])
    ctx = temporal.temporal_context("finep:1")
    assert ctx is not None
    assert ctx.days_remaining == 40
    assert ctx.expired is False
    block = temporal.render_temporal_block("finep:1")
    assert "encerra em" in block
    assert "urgência" not in block  # 40 dias não é prazo curto


def test_short_deadline_flags_urgency(monkeypatch):
    curto = today_sao_paulo() + timedelta(days=5)
    _patch_hypergraph(monkeypatch, [
        {"id": "finep:2", "status": "ABERTA", "deadline": _fmt(curto)},
    ])
    block = temporal.render_temporal_block("finep:2")
    assert "urgência" in block
    assert "5 dia" in block


def test_expired_deadline_warns(monkeypatch):
    passado = today_sao_paulo() - timedelta(days=3)
    _patch_hypergraph(monkeypatch, [
        {"id": "finep:3", "status": "ABERTA", "deadline": _fmt(passado)},
    ])
    ctx = temporal.temporal_context("finep:3")
    assert ctx.expired is True
    assert ctx.days_remaining == -3
    block = temporal.render_temporal_block("finep:3")
    assert "ATENÇÃO" in block
    assert "não prossiga" in block.lower()


def test_continuous_flow_no_deadline(monkeypatch):
    _patch_hypergraph(monkeypatch, [
        {"id": "finep:4", "status": "ABERTA", "deadline": None},
    ])
    ctx = temporal.temporal_context("finep:4")
    assert ctx.deadline is None
    assert ctx.days_remaining is None
    assert ctx.expired is False
    block = temporal.render_temporal_block("finep:4")
    assert "fluxo contínuo" in block


def test_needs_review_without_deadline_has_no_transition_prompt(monkeypatch):
    monkeypatch.setattr(entity_catalog, "get_entity_temporal", lambda _: {
        "deadline": None, "status": "ABERTA", "temporal_mode": "unknown",
        "validity_state": "needs_review", "temporal_value": None,
    })

    block = temporal.render_temporal_block("finep:review")
    assert "validade do edital finep:review está a confirmar" in block.lower()
    assert "não afirme que ele está aberto" in block.lower()


def test_closed_without_deadline_has_no_transition_prompt(monkeypatch):
    monkeypatch.setattr(entity_catalog, "get_entity_temporal", lambda _: {
        "deadline": None, "status": "ENCERRADA", "temporal_mode": "unknown",
        "validity_state": "closed", "temporal_value": None,
    })

    block = temporal.render_temporal_block("finep:closed")
    assert "deve ser tratado como encerrado" in block.lower()
    assert "não o descreva como aberto" in block.lower()


def test_temporal_block_never_renders_none_deadline(monkeypatch):
    monkeypatch.setattr(entity_catalog, "get_entity_temporal", lambda _: {
        "deadline": None, "status": "?", "temporal_mode": "unknown",
        "validity_state": "active", "temporal_value": None,
    })

    block = temporal.render_temporal_block("finep:invalid")
    assert block == ""
    assert "None dia" not in block
    assert "encerra em None" not in block


def test_unknown_edital_returns_empty_block(monkeypatch):
    _patch_hypergraph(monkeypatch, [])
    assert temporal.temporal_context("finep:404") is None
    assert temporal.render_temporal_block("finep:404") == ""


def test_today_is_actual_sao_paulo_day_not_reference_date(monkeypatch):
    """"hoje" usa o dia civil de São Paulo, não uma data fixa do índice."""
    futuro = today_sao_paulo() + timedelta(days=10)
    _patch_hypergraph(monkeypatch, [
        {"id": "finep:5", "status": "ABERTA", "deadline": _fmt(futuro)},
    ])
    block = temporal.render_temporal_block("finep:5")
    assert f"hoje é {today_sao_paulo().isoformat()}" in block


def test_match_block_states_today(monkeypatch):
    block = temporal.render_match_temporal_block()
    assert f"hoje é {today_sao_paulo().isoformat()}" in block
    assert "verbatim" in block
