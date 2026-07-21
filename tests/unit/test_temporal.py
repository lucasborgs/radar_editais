"""Consciência temporal canônica (Front 3) — core/temporal.

Cobre o cálculo de dias restantes / expiração contra date.today() e a
renderização do bloco `[CONTEXTO TEMPORAL: ...]` para os casos de fronteira:
prazo futuro, prazo curto (urgência), prazo vencido, fluxo contínuo (sem
deadline) e edital inexistente.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from core.kg import entity_catalog, temporal

pytestmark = pytest.mark.unit


def _patch_hypergraph(monkeypatch, editais: list[dict]):
    """Stub de entity_catalog.get_entity_temporal (fonte SQL de deadline/status)."""
    by_id = {
        e.get("id", ""): {"deadline": e.get("deadline"), "status": e.get("status")}
        for e in editais
    }
    monkeypatch.setattr(entity_catalog, "get_entity_temporal", lambda eid: by_id.get(eid))


def _fmt(d: date) -> str:
    return d.strftime("%d/%m/%Y")


def test_future_deadline_has_positive_days(monkeypatch):
    futuro = date.today() + timedelta(days=40)
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
    curto = date.today() + timedelta(days=5)
    _patch_hypergraph(monkeypatch, [
        {"id": "finep:2", "status": "ABERTA", "deadline": _fmt(curto)},
    ])
    block = temporal.render_temporal_block("finep:2")
    assert "urgência" in block
    assert "5 dia" in block


def test_expired_deadline_warns(monkeypatch):
    passado = date.today() - timedelta(days=3)
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


def test_unknown_edital_returns_empty_block(monkeypatch):
    _patch_hypergraph(monkeypatch, [])
    assert temporal.temporal_context("finep:404") is None
    assert temporal.render_temporal_block("finep:404") == ""


def test_today_is_actual_today_not_reference_date(monkeypatch):
    """"hoje" é date.today(), não uma data fixa do índice."""
    futuro = date.today() + timedelta(days=10)
    _patch_hypergraph(monkeypatch, [
        {"id": "finep:5", "status": "ABERTA", "deadline": _fmt(futuro)},
    ])
    block = temporal.render_temporal_block("finep:5")
    assert f"hoje é {date.today().isoformat()}" in block


def test_match_block_states_today(monkeypatch):
    block = temporal.render_match_temporal_block()
    assert f"hoje é {date.today().isoformat()}" in block
    assert "verbatim" in block
