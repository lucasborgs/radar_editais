"""Hardening pré-beta: net_guard anti-SSRF, caps de input, DEMO_MODE guard.

Spec histórica: docs/historical/hardening-pre-beta.md.
"""
from __future__ import annotations

import types

import pytest
from pydantic import ValidationError

from core.infra import net_guard
from core.infra.net_guard import PrivateAddressError, assert_public_url

pytestmark = pytest.mark.unit


def _fake_addrinfo(ip: str):
    return [(2, 1, 6, "", (ip, 80))]


# ── assert_public_url ────────────────────────────────────────────────────


class TestAssertPublicUrl:
    def test_scheme_bloqueado(self):
        with pytest.raises(ValueError, match="scheme"):
            assert_public_url("ftp://example.com/arquivo.pdf")

    def test_sem_hostname(self):
        with pytest.raises(ValueError):
            assert_public_url("http://")

    def test_loopback(self, monkeypatch):
        monkeypatch.setattr(
            net_guard.socket, "getaddrinfo",
            lambda *a, **k: _fake_addrinfo("127.0.0.1"),
        )
        with pytest.raises(PrivateAddressError):
            assert_public_url("http://localhost/admin")

    def test_metadata_link_local(self, monkeypatch):
        monkeypatch.setattr(
            net_guard.socket, "getaddrinfo",
            lambda *a, **k: _fake_addrinfo("169.254.169.254"),
        )
        with pytest.raises(PrivateAddressError):
            assert_public_url("http://169.254.169.254/latest/meta-data/")

    def test_hostname_publico_resolvendo_para_rfc1918(self, monkeypatch):
        # DNS "público" que resolve para IP interno — o vetor clássico.
        monkeypatch.setattr(
            net_guard.socket, "getaddrinfo",
            lambda *a, **k: _fake_addrinfo("10.0.0.5"),
        )
        with pytest.raises(PrivateAddressError):
            assert_public_url("https://inocente.example.com/edital.pdf")

    def test_ip_publico_passa(self, monkeypatch):
        monkeypatch.setattr(
            net_guard.socket, "getaddrinfo",
            lambda *a, **k: _fake_addrinfo("93.184.216.34"),
        )
        assert_public_url("https://example.com/edital.pdf")  # não levanta

    def test_dns_nao_resolve(self, monkeypatch):
        def _boom(*a, **k):
            raise net_guard.socket.gaierror("NXDOMAIN")

        monkeypatch.setattr(net_guard.socket, "getaddrinfo", _boom)
        with pytest.raises(ValueError, match="DNS"):
            assert_public_url("http://nao-existe.example.invalid/")


# ── safe_request: redirects revalidados ──────────────────────────────────


def _resp(status=200, headers=None):
    return types.SimpleNamespace(
        status_code=status,
        headers=headers or {},
        is_redirect=status in (301, 302, 303, 307) and bool(headers),
        is_permanent_redirect=False,
    )


class TestSafeRequestRedirects:
    def test_redirect_para_privado_bloqueado(self, monkeypatch):
        def fake_getaddrinfo(host, *a, **k):
            if host == "publico.example.com":
                return _fake_addrinfo("93.184.216.34")
            return _fake_addrinfo("127.0.0.1")

        monkeypatch.setattr(net_guard.socket, "getaddrinfo", fake_getaddrinfo)
        monkeypatch.setattr(
            net_guard.requests, "request",
            lambda m, u, **k: _resp(302, {"location": "http://127.0.0.1/interno"}),
        )
        with pytest.raises(PrivateAddressError):
            net_guard.safe_get("http://publico.example.com/doc.pdf")

    def test_loop_de_redirects_bloqueado(self, monkeypatch):
        monkeypatch.setattr(
            net_guard.socket, "getaddrinfo",
            lambda *a, **k: _fake_addrinfo("93.184.216.34"),
        )
        monkeypatch.setattr(
            net_guard.requests, "request",
            lambda m, u, **k: _resp(302, {"location": "http://publico.example.com/x"}),
        )
        with pytest.raises(ValueError, match="redirects"):
            net_guard.safe_get("http://publico.example.com/doc.pdf")

    def test_resposta_normal_passa(self, monkeypatch):
        monkeypatch.setattr(
            net_guard.socket, "getaddrinfo",
            lambda *a, **k: _fake_addrinfo("93.184.216.34"),
        )
        monkeypatch.setattr(
            net_guard.requests, "request", lambda m, u, **k: _resp(200),
        )
        resp = net_guard.safe_get("http://publico.example.com/doc.pdf")
        assert resp.status_code == 200


# ── Caps de input ─────────────────────────────────────────────────────────


class TestInputCaps:
    def test_writing_user_message_cap(self):
        from backend.routers.writing import WritingTurnRequest

        WritingTurnRequest(session_id="s", user_message="x" * 16_000)  # no cap
        with pytest.raises(ValidationError):
            WritingTurnRequest(session_id="s", user_message="x" * 16_001)

    def test_writing_section_hint_cap(self):
        from backend.routers.writing import WritingTurnRequest

        with pytest.raises(ValidationError):
            WritingTurnRequest(
                session_id="s", user_message="oi", section_hint="x" * 201,
            )

    def test_explore_message_cap(self):
        from backend.routers.explore import ExploreRequest

        ExploreRequest(message="x" * 4_000)  # no cap
        with pytest.raises(ValidationError):
            ExploreRequest(message="x" * 4_001)

    def test_explore_history_cap(self):
        from backend.routers.explore import ExploreRequest

        with pytest.raises(ValidationError):
            ExploreRequest(message="oi", history=[{"role": "user"}] * 51)


# ── DEMO_MODE guard ───────────────────────────────────────────────────────


class TestDemoModeGuard:
    def _clean(self, monkeypatch):
        for var in ("DEMO_MODE", "RAILWAY_ENVIRONMENT", "ENVIRONMENT",
                    "DEMO_MODE_ALLOW_PROD"):
            monkeypatch.delenv(var, raising=False)

    def test_recusa_demo_em_producao(self, monkeypatch):
        from backend.api import _guard_demo_mode

        self._clean(monkeypatch)
        monkeypatch.setenv("DEMO_MODE", "1")
        monkeypatch.setenv("RAILWAY_ENVIRONMENT", "production")
        with pytest.raises(RuntimeError, match="DEMO_MODE"):
            _guard_demo_mode()

    def test_override_deliberado_passa(self, monkeypatch):
        from backend.api import _guard_demo_mode

        self._clean(monkeypatch)
        monkeypatch.setenv("DEMO_MODE", "1")
        monkeypatch.setenv("RAILWAY_ENVIRONMENT", "production")
        monkeypatch.setenv("DEMO_MODE_ALLOW_PROD", "1")
        _guard_demo_mode()  # não levanta

    def test_demo_fora_de_producao_passa(self, monkeypatch):
        from backend.api import _guard_demo_mode

        self._clean(monkeypatch)
        monkeypatch.setenv("DEMO_MODE", "1")
        _guard_demo_mode()  # não levanta

    def test_producao_sem_demo_passa(self, monkeypatch):
        from backend.api import _guard_demo_mode

        self._clean(monkeypatch)
        monkeypatch.setenv("RAILWAY_ENVIRONMENT", "production")
        _guard_demo_mode()  # não levanta
