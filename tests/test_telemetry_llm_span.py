"""Testes do wrapper core.infra.telemetry.llm_span (spec hardening PR5).

Contratos: no-op (yield None) com Langfuse desconfigurado; abre/fecha span de
generation quando habilitado; exceção do CALLER propaga intacta (span fecha com
o erro); falha de telemetria (abrir/fechar) nunca derruba a chamada do caller.

Testes puros: fakes simples, sem rede/Langfuse real.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.infra import telemetry  # noqa: E402


class _FakeSpan:
    def __init__(self):
        self.updates: list[dict] = []

    def update(self, **kwargs):
        self.updates.append(kwargs)


class _FakeCM:
    """Context manager fake do start_as_current_observation."""

    def __init__(self, span, exit_raises=False):
        self.span = span
        self.exit_raises = exit_raises
        self.exited_with: tuple | None = None

    def __enter__(self):
        return self.span

    def __exit__(self, exc_type, exc, tb):
        self.exited_with = (exc_type, exc, tb)
        if self.exit_raises:
            raise RuntimeError("langfuse caiu no exit")
        return False


class _FakeClient:
    def __init__(self, cm=None, start_raises=False):
        self.cm = cm
        self.start_raises = start_raises
        self.start_kwargs: dict | None = None

    def start_as_current_observation(self, **kwargs):
        if self.start_raises:
            raise RuntimeError("langfuse caiu no start")
        self.start_kwargs = kwargs
        return self.cm


def _enable(monkeypatch, client):
    monkeypatch.setattr(telemetry, "_ENABLED", True)
    monkeypatch.setattr(telemetry, "_client", client)


def test_disabled_yields_none():
    # conftest zera as keys → desabilitado por default nos testes.
    assert telemetry.is_enabled() is False
    with telemetry.llm_span("x", model="m") as span:
        assert span is None


def test_enabled_opens_generation_span_and_closes(monkeypatch):
    fake_span = _FakeSpan()
    cm = _FakeCM(fake_span)
    client = _FakeClient(cm=cm)
    _enable(monkeypatch, client)

    with telemetry.llm_span(
        "hyde", model="gpt-4o-mini", input="q", metadata={"workspace_id": "w1"},
    ) as span:
        assert span is fake_span

    assert client.start_kwargs["name"] == "hyde"
    assert client.start_kwargs["as_type"] == "generation"
    assert client.start_kwargs["metadata"] == {"workspace_id": "w1"}
    # model vai via span.update (precificação do Langfuse).
    assert {"model": "gpt-4o-mini"} in fake_span.updates
    # Fechou limpo (sem exceção).
    assert cm.exited_with == (None, None, None)


def test_caller_exception_propagates_and_closes_span(monkeypatch):
    fake_span = _FakeSpan()
    cm = _FakeCM(fake_span)
    _enable(monkeypatch, _FakeClient(cm=cm))

    with pytest.raises(ValueError, match="boom"):
        with telemetry.llm_span("x"):
            raise ValueError("boom")

    # Span fechado COM a exceção do caller (marca o erro no trace).
    assert cm.exited_with is not None
    assert cm.exited_with[0] is ValueError


def test_start_failure_degrades_to_none(monkeypatch):
    _enable(monkeypatch, _FakeClient(start_raises=True))
    with telemetry.llm_span("x", model="m") as span:
        assert span is None  # caller segue sem telemetria


def test_exit_failure_never_reaches_caller(monkeypatch):
    fake_span = _FakeSpan()
    cm = _FakeCM(fake_span, exit_raises=True)
    _enable(monkeypatch, _FakeClient(cm=cm))

    with telemetry.llm_span("x") as span:  # não deve levantar no fechamento
        assert span is fake_span
