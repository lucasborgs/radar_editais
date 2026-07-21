"""Gate de operador (ADMIN_EMAILS) — decisão de produto 2026-07-03.

A fila da Descoberta é ferramenta do operador do sistema, não do cliente final.
Cobre o contrato do gate: allowlist por e-mail do JWT (case-insensitive, CSV),
fail-closed (env vazia/ausente = ninguém), payload sem e-mail (DEMO_MODE) = 403,
e o wiring: todos os endpoints de discovered.py dependem de AdminUserId.
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from radar.core.infra.auth import get_admin_user_id, is_admin_payload

pytestmark = pytest.mark.unit


def test_admin_email_allowed(monkeypatch):
    monkeypatch.setenv("ADMIN_EMAILS", "ops@exemplo.com")
    assert is_admin_payload({"sub": "u1", "email": "ops@exemplo.com"}) is True
    assert get_admin_user_id({"sub": "u1", "email": "ops@exemplo.com"}) == "u1"


def test_admin_email_case_insensitive_and_csv(monkeypatch):
    monkeypatch.setenv("ADMIN_EMAILS", "Ops@Exemplo.com , outra@x.com")
    assert is_admin_payload({"sub": "u1", "email": "OPS@exemplo.COM"}) is True
    assert is_admin_payload({"sub": "u2", "email": "outra@x.com"}) is True


def test_non_admin_email_forbidden(monkeypatch):
    monkeypatch.setenv("ADMIN_EMAILS", "ops@exemplo.com")
    assert is_admin_payload({"sub": "u1", "email": "cliente@startup.com"}) is False
    with pytest.raises(HTTPException) as exc:
        get_admin_user_id({"sub": "u1", "email": "cliente@startup.com"})
    assert exc.value.status_code == 403


def test_empty_allowlist_is_fail_closed(monkeypatch):
    monkeypatch.delenv("ADMIN_EMAILS", raising=False)
    assert is_admin_payload({"sub": "u1", "email": "ops@exemplo.com"}) is False
    with pytest.raises(HTTPException) as exc:
        get_admin_user_id({"sub": "u1", "email": "ops@exemplo.com"})
    assert exc.value.status_code == 403


def test_payload_without_email_forbidden(monkeypatch):
    """DEMO_MODE gera payload sem e-mail — a fila não faz parte da demo."""
    monkeypatch.setenv("ADMIN_EMAILS", "ops@exemplo.com")
    assert is_admin_payload({"sub": "demo-user", "demo": True}) is False
    with pytest.raises(HTTPException):
        get_admin_user_id({"sub": "demo-user", "demo": True})


def test_discovered_endpoints_require_admin():
    """Wiring: os endpoints da fila dependem de AdminUserId (não CurrentUserId).

    Inspeciona as dependencies das rotas do router — se alguém reverter para
    CurrentUserId, este teste quebra.
    """
    from radar.api.routers import discovered
    from radar.core.infra.auth import get_admin_user_id as gate

    routes = [r for r in discovered.router.routes if hasattr(r, "endpoint")]
    assert routes, "router de descoberta não expõe endpoints"
    for r in routes:
        deps = [
            d.call for d in r.dependant.dependencies
        ]
        assert gate in deps, f"rota {r.path} sem gate de admin"
