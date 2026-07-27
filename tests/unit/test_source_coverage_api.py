"""Testes do endpoint ``GET /source-coverage`` (RT03-T06A).

Cobre:
  - usuário não administrativo é recusado;
  - payload válido para dados representativos;
  - tabelas vazias: todos canais como unknown/disabled, sem zeros fabricados;
  - denominador ausente permanece null;
  - canal gated desligado aparece como disabled;
  - erro de banco retorna erro categórico sem conteúdo bruto;
  - resposta não contém query, URL sensível, traceback ou campos não previstos;
  - endpoint não executa escrita;
  - wiring no app está correto.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from radar.core.infra.auth import get_admin_user_id

pytestmark = pytest.mark.unit


# ── Helpers ──────────────────────────────────────────────────────────────


def _run(
    source_key: str,
    status: str = "succeeded",
    started_at: str | None = None,
    completed_at: str | None = None,
    records_observed: int | None = None,
    records_emitted: int | None = None,
    records_staged: int | None = None,
) -> dict:
    if started_at is None:
        started_at = datetime.now(timezone.utc).isoformat()
    return {
        "source_key": source_key,
        "status": status,
        "started_at": started_at,
        "completed_at": completed_at or started_at,
        "records_observed": records_observed,
        "records_emitted": records_emitted,
        "records_staged": records_staged,
        "error_count": 0,
        "reason_code": None,
        "metrics": {},
    }


def _disc(
    status: str = "pending",
    discovery_channel: str | None = "open_search",
    query_family: str | None = None,
    origin_domain: str | None = None,
    created_at: str | None = None,
    reviewed_at: str | None = None,
) -> dict:
    if created_at is None:
        created_at = datetime.now(timezone.utc).isoformat()
    return {
        "id": "test-id",
        "status": status,
        "discovery_channel": discovery_channel,
        "query_family": query_family,
        "origin_domain": origin_domain,
        "created_at": created_at,
        "reviewed_at": reviewed_at,
    }


def _mock_db(runs: list[dict] | None = None,
             discovered: list[dict] | None = None) -> MagicMock:
    db = MagicMock()

    def _table_side_effect(name: str):
        t = MagicMock()

        if name == "source_runs":
            resp = MagicMock()
            resp.data = runs or []
            sel = MagicMock()
            sel.execute.return_value = resp
            t.select.return_value = sel

        elif name == "discovered_opportunities":
            resp = MagicMock()
            resp.data = discovered or []
            sel = MagicMock()
            sel.execute.return_value = resp
            t.select.return_value = sel

        return t

    db.table.side_effect = _table_side_effect
    return db


def _dump(model: BaseModel) -> dict[str, Any]:
    """Serializa Pydantic model para dict."""
    return json.loads(model.model_dump_json())


def _make_app() -> FastAPI:
    app = FastAPI()
    from radar.api.routers.source_coverage import router
    app.include_router(router)
    return app


_KNOWN_KEYS = {
    "finep", "fapesp", "fapesc", "web_curated",
    "open_search", "dou", "hub_expansion",
}

_EXPECTED_TOP_FIELDS = {
    "generated_at", "channels", "runs", "channel_funnel",
    "family_funnel", "gaps", "emerging_domains", "limitations",
}

# ══════════════════════════════════════════════════════════════════════════
# 1. Auth gate
# ══════════════════════════════════════════════════════════════════════════


class TestAdminGate:

    def test_non_admin_returns_403(self, monkeypatch):
        """Usuário não administrativo recebe 403 via HTTP."""
        monkeypatch.setenv("ADMIN_EMAILS", "ops@exemplo.com")
        monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
        monkeypatch.setenv("SUPABASE_JWT_SECRET", "test-secret")

        app = _make_app()
        # Injeta um payload que NÃO é admin
        def _fail_gate():
            return get_admin_user_id({"sub": "u1", "email": "cliente@startup.com"})
        app.dependency_overrides[get_admin_user_id] = _fail_gate

        client = TestClient(app)
        resp = client.get("/source-coverage")
        assert resp.status_code == 403
        assert "operador" in resp.json()["detail"].lower()

    def test_admin_allowed(self, monkeypatch):
        """Admin recebe 200."""
        monkeypatch.setenv("ADMIN_EMAILS", "ops@exemplo.com")

        app = _make_app()
        # Override a função de dependência diretamente
        app.dependency_overrides[get_admin_user_id] = lambda: "u1"

        with (
            patch("radar.api.routers.source_coverage.get_supabase_service") as mock_get_db,
        ):
            mock_get_db.return_value = _mock_db(runs=[], discovered=[])
            client = TestClient(app)
            resp = client.get("/source-coverage")

        assert resp.status_code == 200


# ══════════════════════════════════════════════════════════════════════════
# 2. Payload structure and representative data
# ══════════════════════════════════════════════════════════════════════════


class TestPayloadStructure:

    def test_top_level_fields(self, monkeypatch):
        monkeypatch.setenv("ADMIN_EMAILS", "ops@exemplo.com")

        with (
            patch("radar.api.routers.source_coverage.get_supabase_service") as mock_get_db,
            patch("radar.api.routers.source_coverage.coverage_channels") as mock_channels,
            patch("radar.api.routers.source_coverage.query_families") as mock_families,
            patch("radar.api.routers.source_coverage.datetime") as mock_dt,
        ):
            mock_get_db.return_value = _mock_db(runs=[], discovered=[])
            mock_channels.return_value = [
                {"source_key": "finep", "mode": "dedicated",
                 "display_name": "FINEP", "scope_note": "x",
                 "expected_interval_hours": 24, "enabled_by_default": True},
            ]
            mock_families.return_value = []
            mock_dt.now.return_value = datetime(2026, 7, 26, 12, 0, 0, tzinfo=timezone.utc)

            from radar.api.routers.source_coverage import get_source_coverage
            result = _dump(get_source_coverage("u1"))

        assert set(result.keys()) == _EXPECTED_TOP_FIELDS

    def test_all_seven_channels_present_in_health(self, monkeypatch):
        monkeypatch.setenv("ADMIN_EMAILS", "ops@exemplo.com")

        with (
            patch("radar.api.routers.source_coverage.get_supabase_service") as mock_get_db,
        ):
            mock_get_db.return_value = _mock_db(runs=[], discovered=[])
            from radar.api.routers.source_coverage import get_source_coverage
            result = _dump(get_source_coverage("u1"))

        channel_keys = {ch["source_key"] for ch in result["channels"]}
        assert channel_keys == _KNOWN_KEYS

    def test_representative_data(self, monkeypatch):
        monkeypatch.setenv("ADMIN_EMAILS", "ops@exemplo.com")

        runs = [
            _run("finep", "succeeded",
                 "2026-07-26T10:00:00+00:00",
                 completed_at="2026-07-26T10:30:00+00:00",
                 records_observed=10, records_emitted=8, records_staged=3),
            _run("open_search", "succeeded",
                 "2026-07-26T10:00:00+00:00",
                 completed_at="2026-07-26T10:30:00+00:00",
                 records_observed=20, records_emitted=15, records_staged=7),
        ]
        discovered = [
            _disc(status="promoted", discovery_channel="open_search",
                  query_family="state_innovation_funding",
                  created_at="2026-07-25T10:00:00+00:00",
                  reviewed_at="2026-07-25T12:00:00+00:00"),
        ]

        with (
            patch("radar.api.routers.source_coverage.get_supabase_service") as mock_get_db,
        ):
            mock_get_db.return_value = _mock_db(runs=runs, discovered=discovered)
            from radar.api.routers.source_coverage import get_source_coverage
            result = _dump(get_source_coverage("u1"))

        assert result["runs"]["finep"]["yield_rate"] == 3 / 8
        assert result["runs"]["open_search"]["yield_rate"] == 7 / 15

        os_funnel = result["channel_funnel"]["open_search"]
        assert os_funnel["approved"] == 1
        assert os_funnel["pending"] == 0

        sif_funnel = result["family_funnel"]["state_innovation_funding"]
        assert sif_funnel["approved"] == 1

    def test_promoted_generates_emerging_domain(self, monkeypatch):
        monkeypatch.setenv("ADMIN_EMAILS", "ops@exemplo.com")

        runs = [
            _run("open_search", "succeeded",
                 "2026-07-26T10:00:00+00:00",
                 completed_at="2026-07-26T10:30:00+00:00",
                 records_observed=5),
        ]
        discovered = [
            _disc(status="promoted", discovery_channel="open_search",
                  origin_domain="exemplo.gov.br",
                  created_at="2026-07-25T10:00:00+00:00",
                  reviewed_at="2026-07-25T12:00:00+00:00"),
            _disc(status="promoted", discovery_channel="open_search",
                  origin_domain="exemplo.gov.br",
                  created_at="2026-07-24T10:00:00+00:00",
                  reviewed_at="2026-07-24T12:00:00+00:00"),
        ]

        with (
            patch("radar.api.routers.source_coverage.get_supabase_service") as mock_get_db,
        ):
            mock_get_db.return_value = _mock_db(runs=runs, discovered=discovered)
            from radar.api.routers.source_coverage import get_source_coverage
            result = _dump(get_source_coverage("u1"))

        domains = [(d["domain"], d["approval_count"],
                     d["candidate_for_dedicated_monitoring"])
                   for d in result["emerging_domains"]]
        assert ("exemplo.gov.br", 2, True) in domains


# ══════════════════════════════════════════════════════════════════════════
# 3. Empty tables
# ══════════════════════════════════════════════════════════════════════════


class TestEmptyTables:

    def test_all_channels_unknown_or_disabled(self, monkeypatch):
        monkeypatch.setenv("ADMIN_EMAILS", "ops@exemplo.com")

        with (
            patch("radar.api.routers.source_coverage.get_supabase_service") as mock_get_db,
        ):
            mock_get_db.return_value = _mock_db(runs=[], discovered=[])
            from radar.api.routers.source_coverage import get_source_coverage
            result = _dump(get_source_coverage("u1"))

        health_map = {ch["source_key"]: ch["health"] for ch in result["channels"]}
        assert health_map["finep"] == "unknown"
        assert health_map["fapesp"] == "unknown"
        assert health_map["fapesc"] == "unknown"
        assert health_map["web_curated"] == "unknown"
        assert health_map["open_search"] == "unknown"
        assert health_map["dou"] == "disabled"
        assert health_map["hub_expansion"] == "disabled"

    def test_no_zeros_fabricated(self, monkeypatch):
        monkeypatch.setenv("ADMIN_EMAILS", "ops@exemplo.com")

        with (
            patch("radar.api.routers.source_coverage.get_supabase_service") as mock_get_db,
        ):
            mock_get_db.return_value = _mock_db(runs=[], discovered=[])
            from radar.api.routers.source_coverage import get_source_coverage
            result = _dump(get_source_coverage("u1"))

        finep_runs = result["runs"]["finep"]
        assert finep_runs["total_records_observed"] is None
        assert finep_runs["total_records_emitted"] is None
        assert finep_runs["total_records_staged"] is None
        assert finep_runs["yield_rate"] is None

    def test_empty_gaps_enabled_no_run(self, monkeypatch):
        monkeypatch.setenv("ADMIN_EMAILS", "ops@exemplo.com")

        with (
            patch("radar.api.routers.source_coverage.get_supabase_service") as mock_get_db,
        ):
            mock_get_db.return_value = _mock_db(runs=[], discovered=[])
            from radar.api.routers.source_coverage import get_source_coverage
            result = _dump(get_source_coverage("u1"))

        gap_signals = {(g["source_key"], g["signal"]) for g in result["gaps"]}
        for sk in ("finep", "fapesp", "fapesc", "web_curated", "open_search"):
            assert (sk, "enabled_no_run") in gap_signals
        assert ("dou", "enabled_no_run") not in gap_signals
        assert ("hub_expansion", "enabled_no_run") not in gap_signals


# ══════════════════════════════════════════════════════════════════════════
# 4. Denominator absent stays null
# ══════════════════════════════════════════════════════════════════════════


class TestDenominatorNull:

    def test_emitted_none_yields_null_rate(self, monkeypatch):
        monkeypatch.setenv("ADMIN_EMAILS", "ops@exemplo.com")

        runs = [
            _run("finep", "succeeded",
                 "2026-07-26T10:00:00+00:00",
                 completed_at="2026-07-26T10:30:00+00:00",
                 records_observed=10, records_emitted=None, records_staged=5),
        ]

        with (
            patch("radar.api.routers.source_coverage.get_supabase_service") as mock_get_db,
        ):
            mock_get_db.return_value = _mock_db(runs=runs, discovered=[])
            from radar.api.routers.source_coverage import get_source_coverage
            result = _dump(get_source_coverage("u1"))

        assert result["runs"]["finep"]["yield_rate"] is None
        assert result["runs"]["finep"]["total_records_emitted"] is None

    def test_empty_runs_all_nulls(self, monkeypatch):
        monkeypatch.setenv("ADMIN_EMAILS", "ops@exemplo.com")

        with (
            patch("radar.api.routers.source_coverage.get_supabase_service") as mock_get_db,
        ):
            mock_get_db.return_value = _mock_db(runs=[], discovered=[])
            from radar.api.routers.source_coverage import get_source_coverage
            result = _dump(get_source_coverage("u1"))

        for sk in _KNOWN_KEYS:
            m = result["runs"][sk]
            assert m["last_attempt"] is None
            assert m["last_success"] is None
            assert m["total_records_observed"] is None


# ══════════════════════════════════════════════════════════════════════════
# 5. Gated channel disabled
# ══════════════════════════════════════════════════════════════════════════


class TestGatedChannel:

    def test_disabled_when_flag_off(self, monkeypatch):
        monkeypatch.setenv("ADMIN_EMAILS", "ops@exemplo.com")
        monkeypatch.setenv("DISCOVERY_DOU_ENABLED", "0")

        with (
            patch("radar.api.routers.source_coverage.get_supabase_service") as mock_get_db,
        ):
            mock_get_db.return_value = _mock_db(runs=[], discovered=[])
            from radar.api.routers.source_coverage import get_source_coverage
            result = _dump(get_source_coverage("u1"))

        health_map = {ch["source_key"]: ch["health"] for ch in result["channels"]}
        assert health_map["dou"] == "disabled"

    def test_unknown_when_flag_on_but_no_runs(self, monkeypatch):
        monkeypatch.setenv("ADMIN_EMAILS", "ops@exemplo.com")
        monkeypatch.setenv("DISCOVERY_DOU_ENABLED", "1")

        with (
            patch("radar.api.routers.source_coverage.get_supabase_service") as mock_get_db,
        ):
            mock_get_db.return_value = _mock_db(runs=[], discovered=[])
            from radar.api.routers.source_coverage import get_source_coverage
            result = _dump(get_source_coverage("u1"))

        health_map = {ch["source_key"]: ch["health"] for ch in result["channels"]}
        assert health_map["dou"] == "unknown"


# ══════════════════════════════════════════════════════════════════════════
# 6. Database error — sanitization
# ══════════════════════════════════════════════════════════════════════════


class TestErrorSanitization:

    def test_db_error_returns_503_categorical(self, monkeypatch):
        monkeypatch.setenv("ADMIN_EMAILS", "ops@exemplo.com")
        monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
        monkeypatch.setenv("SUPABASE_JWT_SECRET", "test-secret")

        app = _make_app()
        app.dependency_overrides[get_admin_user_id] = lambda: "u1"

        with (
            patch("radar.api.routers.source_coverage.get_supabase_service") as mock_get_db,
        ):
            db = MagicMock()

            def _boom(name: str):
                raise RuntimeError("CONNECTION FAILED TO DATABASE at supabase://secret@prod")

            db.table.side_effect = _boom
            mock_get_db.return_value = db

            client = TestClient(app)
            resp = client.get("/source-coverage")

        assert resp.status_code == 503
        detail = resp.json()["detail"]
        assert "CONNECTION FAILED" not in detail
        assert "supabase" not in detail.lower()
        assert "http" not in detail
        assert "secret" not in detail.lower()
        assert "Traceback" not in detail
        assert "Erro ao gerar" in detail

    def test_response_has_no_sensitive_leak(self, monkeypatch):
        monkeypatch.setenv("ADMIN_EMAILS", "ops@exemplo.com")

        with (
            patch("radar.api.routers.source_coverage.get_supabase_service") as mock_get_db,
        ):
            mock_get_db.return_value = _mock_db(runs=[], discovered=[])
            from radar.api.routers.source_coverage import get_source_coverage
            result = get_source_coverage("u1")

        body_str = result.model_dump_json()
        assert "SELECT" not in body_str
        assert "supabase.co" not in body_str
        assert "Traceback" not in body_str
        assert "File \"" not in body_str


# ══════════════════════════════════════════════════════════════════════════
# 7. Exact projections
# ══════════════════════════════════════════════════════════════════════════


class TestExactProjections:

    def test_source_runs_selects_expected_fields(self, monkeypatch):
        """source_runs recebe projeção exata, sem *."""
        monkeypatch.setenv("ADMIN_EMAILS", "ops@exemplo.com")

        db = MagicMock()

        calls: list[str] = []

        def _table_side_effect(name: str):
            t = MagicMock()
            if name == "source_runs":
                def _select(cols: str):
                    calls.append(("source_runs", cols))
                    resp = MagicMock()
                    resp.data = []
                    sel = MagicMock()
                    sel.execute.return_value = resp
                    return sel
                t.select.side_effect = _select
            elif name == "discovered_opportunities":
                def _select(cols: str):
                    calls.append(("discovered_opportunities", cols))
                    resp = MagicMock()
                    resp.data = []
                    sel = MagicMock()
                    sel.execute.return_value = resp
                    return sel
                t.select.side_effect = _select
            return t

        db.table.side_effect = _table_side_effect

        with (
            patch("radar.api.routers.source_coverage.get_supabase_service") as mock_get_db,
        ):
            mock_get_db.return_value = db
            from radar.api.routers.source_coverage import get_source_coverage
            get_source_coverage("u1")

        assert len(calls) == 2
        table_name, cols = calls[0]
        assert table_name == "source_runs"
        assert "source_key" in cols
        assert "status" in cols
        assert "started_at" in cols
        assert "completed_at" in cols
        assert "records_observed" in cols
        assert "records_emitted" in cols
        assert "records_staged" in cols
        assert "*" not in cols

    def test_discovered_omits_id(self, monkeypatch):
        """discovered_opportunities não projeta id."""
        monkeypatch.setenv("ADMIN_EMAILS", "ops@exemplo.com")

        db = MagicMock()

        discovered_cols: str | None = None

        def _table_side_effect(name: str):
            t = MagicMock()
            if name == "source_runs":
                def _select(cols: str):
                    resp = MagicMock()
                    resp.data = []
                    sel = MagicMock()
                    sel.execute.return_value = resp
                    return sel
                t.select.side_effect = _select
            elif name == "discovered_opportunities":
                def _select(cols: str):
                    nonlocal discovered_cols
                    discovered_cols = cols
                    resp = MagicMock()
                    resp.data = []
                    sel = MagicMock()
                    sel.execute.return_value = resp
                    return sel
                t.select.side_effect = _select
            return t

        db.table.side_effect = _table_side_effect

        with (
            patch("radar.api.routers.source_coverage.get_supabase_service") as mock_get_db,
        ):
            mock_get_db.return_value = db
            from radar.api.routers.source_coverage import get_source_coverage
            get_source_coverage("u1")

        assert discovered_cols is not None
        assert "id" not in discovered_cols, f"id ainda projetado: {discovered_cols}"
        assert "status" in discovered_cols
        assert "discovery_channel" in discovered_cols
        assert "query_family" in discovered_cols
        assert "origin_domain" in discovered_cols
        assert "created_at" in discovered_cols
        assert "reviewed_at" in discovered_cols


# ══════════════════════════════════════════════════════════════════════════
# 8. get_supabase_service failure
# ══════════════════════════════════════════════════════════════════════════


class TestGetSupabaseServiceFailure:

    def test_service_failure_returns_503_sanitized(self, monkeypatch):
        """get_supabase_service() levanta → 503 categórico."""
        monkeypatch.setenv("ADMIN_EMAILS", "ops@exemplo.com")

        app = _make_app()
        app.dependency_overrides[get_admin_user_id] = lambda: "u1"

        with (
            patch("radar.api.routers.source_coverage.get_supabase_service") as mock_get,
        ):
            mock_get.side_effect = RuntimeError(
                "TIMEOUT connecting to supabase://user:secret@prod"
            )

            client = TestClient(app)
            resp = client.get("/source-coverage")

        assert resp.status_code == 503
        detail = resp.json()["detail"]
        assert "TIMEOUT" not in detail
        assert "secret" not in detail.lower()
        assert "supabase" not in detail.lower()
        assert "Erro ao gerar" in detail

    def test_service_failure_does_not_log_exc_info(self, monkeypatch, caplog):
        """Log contém apenas o nome da classe, não o segredo da exceção."""
        monkeypatch.setenv("ADMIN_EMAILS", "ops@exemplo.com")

        app = _make_app()
        app.dependency_overrides[get_admin_user_id] = lambda: "u1"

        with (
            patch("radar.api.routers.source_coverage.get_supabase_service") as mock_get,
            caplog.at_level(logging.ERROR, logger="radar.api.routers.source_coverage"),
        ):
            mock_get.side_effect = RuntimeError(
                "DB_PASSWORD=supersecret connection refused"
            )

            client = TestClient(app)
            client.get("/source-coverage")

        log_text = caplog.text
        # O nome da classe deve aparecer
        assert "RuntimeError" in log_text
        # O segredo da exceção não deve aparecer
        assert "supersecret" not in log_text
        assert "DB_PASSWORD" not in log_text
        assert "exc_info" not in log_text.lower()


# ══════════════════════════════════════════════════════════════════════════
# 9. No write operations
# ══════════════════════════════════════════════════════════════════════════


class TestNoWrites:

    def test_only_select_called(self, monkeypatch):
        monkeypatch.setenv("ADMIN_EMAILS", "ops@exemplo.com")

        db = MagicMock()
        table = MagicMock()
        sel_resp = MagicMock()
        sel_resp.data = []
        sel = MagicMock()
        sel.execute.return_value = sel_resp
        table.select.return_value = sel
        db.table.return_value = table

        with (
            patch("radar.api.routers.source_coverage.get_supabase_service") as mock_get_db,
        ):
            mock_get_db.return_value = db
            from radar.api.routers.source_coverage import get_source_coverage
            get_source_coverage("u1")

        calls = [c[0][0] for c in db.table.call_args_list]
        assert "source_runs" in calls
        assert "discovered_opportunities" in calls

        for call_name in ("insert", "update", "delete", "upsert"):
            method = getattr(table, call_name, None)
            if method is not None:
                assert method.call_count == 0, f"{call_name} foi chamado!"


# ══════════════════════════════════════════════════════════════════════════
# 10. Wiring — router registration
# ══════════════════════════════════════════════════════════════════════════


class TestWiring:

    def test_router_registered_in_app(self):
        """source_coverage_router está registrado no app principal."""
        from radar.api.app import app as radar_app

        found = False
        for r in radar_app.router.routes:
            if type(r).__name__ == "_IncludedRouter":
                router = r.include_context.included_router
                for route in router.routes:
                    p = getattr(route, "path", None)
                    if p and "source-coverage" in p:
                        found = True
                        break
            if found:
                break
        assert found, "source_coverage_router não encontrado nas rotas do app"

    def test_endpoints_have_admin_gate(self):
        """Todos os endpoints do router dependem de AdminUserId."""
        from radar.api.routers.source_coverage import router
        from radar.core.infra.auth import get_admin_user_id as gate

        routes = [r for r in router.routes if hasattr(r, "endpoint")]
        assert routes, "router source_coverage não expõe endpoints"
        for r in routes:
            deps = [d.call for d in r.dependant.dependencies]
            assert gate in deps, f"rota {r.path} sem gate de admin"

    def test_only_get_endpoint(self):
        """Router tem apenas um endpoint GET (read-only)."""
        from radar.api.routers.source_coverage import router

        methods: set[str] = set()
        for r in router.routes:
            for method in (r.methods or set()):
                methods.add(method)
        non_get = methods - {"GET", "HEAD"}
        assert not non_get, f"router expõe métodos não-GET: {non_get}"

    def test_response_model_used(self):
        """Endpoint usa response_model explícito."""
        from radar.api.routers.source_coverage import router

        for r in router.routes:
            if hasattr(r, "response_model") and r.response_model is not None:
                from radar.api.routers.source_coverage import SourceCoverageResponse
                assert r.response_model is SourceCoverageResponse or (
                    hasattr(r.response_model, "__name__")
                    and r.response_model.__name__ == "SourceCoverageResponse"
                )
                return
        pytest.fail("Nenhuma rota com response_model encontrada")
