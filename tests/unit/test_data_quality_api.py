"""Testes da API administrativa de exceções (RT05-T05).

Cobre:
  - gate administrativo fail-closed;
  - lista vazia e filtros válidos;
  - detalhe existente e inexistente;
  - sanitização de payload/erros;
  - revisão válida, rejeição por evidência ausente e idempotência de retry;
  - `actor_id` enviado pelo cliente não é aceito;
  - revisão não altera o estado editorial da Descoberta.
"""
from __future__ import annotations

import copy
import uuid
from datetime import datetime, timezone
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from postgrest.exceptions import APIError

from radar.api.routers.data_quality import router
from radar.core.infra.auth import get_admin_user_id
from radar.core.services.data_quality_exceptions import DataQualityStorageError

pytestmark = pytest.mark.unit


class _FakeResponse:
    def __init__(self, data):
        self.data = data


class _FakeQuery:
    def __init__(self, store, supabase, table_name):
        self._store = store
        self._supabase = supabase
        self._table_name = table_name
        self._method = None
        self._payload = None
        self._filters: list[tuple[str, str, Any]] = []
        self._order_col = None
        self._order_desc = False
        self._limit_val: int | None = None

    def select(self, *_):
        return self

    def eq(self, col, val):
        self._filters.append(("eq", col, val))
        return self

    def neq(self, col, val):
        self._filters.append(("neq", col, val))
        return self

    def order(self, col, *, desc=False):
        self._order_col = col
        self._order_desc = desc
        return self

    def limit(self, n):
        self._limit_val = n
        return self

    def execute(self):
        if self._method == "insert":
            return self._do_insert()
        if self._method == "update":
            return self._do_update()
        return self._do_select()

    def _match(self, row):
        for typ, col, val in self._filters:
            if typ == "eq" and row.get(col) != val:
                return False
            if typ == "neq" and row.get(col) == val:
                return False
        return True

    def _filtered(self):
        return [row for row in self._store.values() if self._match(row)]

    def _do_select(self):
        rows = self._filtered()
        if self._order_col is not None:
            rows = sorted(
                rows,
                key=lambda row: row.get(self._order_col) or "",
                reverse=self._order_desc,
            )
        if self._limit_val is not None:
            rows = rows[: self._limit_val]
        return _FakeResponse(rows)

    def _do_update(self):
        if self._table_name == "data_quality_exceptions":
            should_fail = self._supabase.fail_next_exception_resolve
            if should_fail and self._payload.get("status") == "resolved":
                self._supabase.fail_next_exception_resolve -= 1
                raise APIError({"code": "PGRST116", "message": "resolve failed"})

        matched = [rid for rid in self._store if self._match(self._store[rid])]
        for rid in matched:
            self._store[rid].update(copy.deepcopy(self._payload))
        return _FakeResponse([])

    def _do_insert(self):
        raw = self._payload
        items = [raw] if isinstance(raw, dict) else list(raw)
        new_rows = []
        for item in items:
            row = copy.deepcopy(item)
            if "id" not in row:
                row["id"] = str(uuid.uuid4())

            if self._table_name == "data_quality_exceptions":
                for existing in self._store.values():
                    same_group = all(
                        row.get(key) == existing.get(key)
                        for key in (
                            "subject_kind",
                            "subject_id",
                            "field_path",
                            "issue_code",
                            "input_fingerprint",
                        )
                    )
                    if same_group:
                        raise APIError({"code": "23505", "message": "duplicate"})

            if self._table_name == "data_quality_reviews":
                for existing in self._store.values():
                    if row.get("review_id") == existing.get("review_id"):
                        raise APIError({"code": "23505", "message": "duplicate"})

            now = datetime.now(timezone.utc).isoformat()
            row.setdefault("created_at", now)
            if "detected_at" in row and row["detected_at"] is None:
                row["detected_at"] = now
            if "last_observed_at" in row and row["last_observed_at"] is None:
                row["last_observed_at"] = now
            if self._table_name == "data_quality_reviews":
                row.setdefault("reviewed_at", now)

            self._store[row["id"]] = row
            new_rows.append(row)
        return _FakeResponse(new_rows)


class _FakeTable:
    def __init__(self, name, supabase):
        self._name = name
        self._supabase = supabase
        self._store = supabase._tables.setdefault(name, {})

    def select(self, *_):
        return _FakeQuery(self._store, self._supabase, self._name)

    def insert(self, data):
        q = _FakeQuery(self._store, self._supabase, self._name)
        q._method = "insert"
        q._payload = data
        return q

    def update(self, data):
        q = _FakeQuery(self._store, self._supabase, self._name)
        q._method = "update"
        q._payload = data
        return q


class FakeSupabase:
    def __init__(self):
        self._tables: dict[str, dict[str, dict]] = {}
        self.fail_next_exception_resolve = 0

    def table(self, name):
        return _FakeTable(name, self)


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("ADMIN_EMAILS", "ops@exemplo.com")
    monkeypatch.setenv("SUPABASE_URL", "http://test")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "test-key")


@pytest.fixture
def fake_db():
    return FakeSupabase()


@pytest.fixture(autouse=True)
def _install_db(fake_db, monkeypatch):
    import radar.core.infra.db

    monkeypatch.setattr(
        radar.core.infra.db,
        "get_supabase_service",
        lambda: fake_db,
    )


def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    return app


def _evidence(*, source: str = "finep", quote: str = "Trecho versionado") -> dict:
    return {
        "schema_version": 1,
        "source": source,
        "document": "edital.html",
        "quote": quote,
        "canonical_content_hash": "sha256:" + "a" * 64,
        "locator_quality": "document_only",
    }


def _exception_row(
    *,
    exception_id: str = "exc-1",
    subject_kind: str = "opportunity",
    subject_id: str = "finep:602",
    field_path: str = "deadline",
    issue_code: str = "temporal_status_without_basis",
    produced_value: str = "ABERTA",
    status: str = "open",
    source: str = "finep",
    source_url: str = "https://secret.example/hidden",
) -> dict:
    evidence_ref = _evidence(source=source)
    evidence_ref["source_url"] = source_url
    return {
        "id": exception_id,
        "subject_kind": subject_kind,
        "subject_id": subject_id,
        "field_path": field_path,
        "issue_code": issue_code,
        "produced_state": "inferred",
        "produced_value": produced_value,
        "evidence_refs": [evidence_ref],
        "bundle_hash": "sha256:" + "b" * 64,
        "producer_version": "temporal_quality:v1",
        "input_fingerprint": "sha256:" + "c" * 64,
        "status": status,
        "detected_at": "2026-07-29T12:00:00+00:00",
        "last_observed_at": "2026-07-29T12:00:00+00:00",
    }


def _resolve_real_admin(payload: dict) -> str:
    return get_admin_user_id(payload)


class TestAdminGate:
    def test_unauthenticated_request_is_blocked(self):
        app = _make_app()
        client = TestClient(app)

        resp = client.get("/data-quality/exceptions")

        assert resp.status_code == 401

    def test_non_admin_is_blocked(self):
        app = _make_app()
        app.dependency_overrides[get_admin_user_id] = lambda: _resolve_real_admin(
            {"sub": "u1", "email": "cliente@startup.com"}
        )
        client = TestClient(app)

        resp = client.get("/data-quality/exceptions")

        assert resp.status_code == 403


class TestListAndDetail:
    def test_list_empty(self):
        app = _make_app()
        app.dependency_overrides[get_admin_user_id] = lambda: "admin-1"
        client = TestClient(app)

        resp = client.get("/data-quality/exceptions")

        assert resp.status_code == 200
        assert resp.json() == {
            "items": [],
            "limit": 25,
            "offset": 0,
            "has_more": False,
            "next_offset": None,
        }

    def test_list_filters_valid(self, fake_db):
        fake_db.table("data_quality_exceptions").insert(
            _exception_row(exception_id="exc-1", source="finep")
        ).execute()
        fake_db.table("data_quality_exceptions").insert(
            _exception_row(
                exception_id="exc-2",
                source="fapesp",
                issue_code="fact_conflict",
                field_path="status",
                produced_value="ENCERRADA",
            )
        ).execute()
        app = _make_app()
        app.dependency_overrides[get_admin_user_id] = lambda: "admin-1"
        client = TestClient(app)

        resp = client.get(
            "/data-quality/exceptions",
            params={
                "status": "open",
                "code": "temporal_status_without_basis",
                "source": "finep",
                "field": "deadline",
            },
        )

        payload = resp.json()
        assert resp.status_code == 200
        assert payload["limit"] == 25
        assert payload["items"][0]["id"] == "exc-1"
        assert payload["items"][0]["source"] == "finep"
        assert payload["items"][0]["state"] == "open"

    def test_detail_existing_and_missing(self, fake_db):
        fake_db.table("data_quality_exceptions").insert(
            _exception_row(exception_id="exc-1")
        ).execute()
        app = _make_app()
        app.dependency_overrides[get_admin_user_id] = lambda: "admin-1"
        client = TestClient(app)

        ok = client.get("/data-quality/exceptions/exc-1")
        missing = client.get("/data-quality/exceptions/nao-existe")

        assert ok.status_code == 200
        assert missing.status_code == 404
        payload = ok.json()
        assert payload["id"] == "exc-1"
        assert payload["source"] == "finep"
        assert "source_url" not in payload["evidence_refs"][0]
        assert payload["current_review"] is None

    def test_payload_sanitized_on_storage_failure(self, monkeypatch):
        app = _make_app()
        app.dependency_overrides[get_admin_user_id] = lambda: "admin-1"
        monkeypatch.setattr(
            "radar.api.routers.data_quality.list_exceptions",
            lambda **_: (_ for _ in ()).throw(DataQualityStorageError("secret raw payload")),
        )
        client = TestClient(app)

        resp = client.get("/data-quality/exceptions")

        assert resp.status_code == 503
        assert "secret raw payload" not in resp.text
        assert "Falha ao consultar a fila de exceções." in resp.text


class TestReviewFlow:
    def test_valid_decision(self, fake_db):
        fake_db.table("data_quality_exceptions").insert(
            _exception_row(exception_id="exc-1")
        ).execute()
        app = _make_app()
        app.dependency_overrides[get_admin_user_id] = lambda: "admin-1"
        client = TestClient(app)

        resp = client.post(
            "/data-quality/exceptions/exc-1/reviews",
            json={
                "review_id": "review-1",
                "decision": "confirm_continuous",
                "justification": "A evidência versionada já está ligada à fila.",
                "evidence_refs": [_evidence()],
            },
        )

        payload = resp.json()
        assert resp.status_code == 201
        assert payload["id"] == "exc-1"
        assert payload["state"] == "resolved"
        assert payload["current_review"]["review_id"] == "review-1"
        assert payload["current_review"]["decision"] == "confirm_continuous"
        assert payload["current_review"]["evidence_refs"][0]["source"] == "finep"

    def test_decision_requires_evidence_is_rejected(self, fake_db):
        fake_db.table("data_quality_exceptions").insert(
            _exception_row(exception_id="exc-1")
        ).execute()
        app = _make_app()
        app.dependency_overrides[get_admin_user_id] = lambda: "admin-1"
        client = TestClient(app)

        resp = client.post(
            "/data-quality/exceptions/exc-1/reviews",
            json={
                "review_id": "review-1",
                "decision": "confirm_continuous",
                "justification": "Sem evidência suficiente.",
            },
        )

        assert resp.status_code == 422

    def test_actor_id_is_rejected(self, fake_db):
        fake_db.table("data_quality_exceptions").insert(
            _exception_row(exception_id="exc-1")
        ).execute()
        app = _make_app()
        app.dependency_overrides[get_admin_user_id] = lambda: "admin-1"
        client = TestClient(app)

        resp = client.post(
            "/data-quality/exceptions/exc-1/reviews",
            json={
                "review_id": "review-1",
                "decision": "mark_unknown",
                "justification": "A decisão vem da autenticação.",
                "actor_id": "cliente-1",
            },
        )

        assert resp.status_code == 422

    def test_retry_is_idempotent(self, fake_db):
        fake_db.fail_next_exception_resolve = 1
        fake_db.table("data_quality_exceptions").insert(
            _exception_row(exception_id="exc-1")
        ).execute()
        editorial_row = {
            "id": "disc-1",
            "status": "pending",
            "created_at": "2026-07-29T12:00:00+00:00",
        }
        fake_db.table("discovered_opportunities")._store["disc-1"] = editorial_row

        app = _make_app()
        app.dependency_overrides[get_admin_user_id] = lambda: "admin-1"
        client = TestClient(app)
        body = {
            "review_id": "review-1",
            "decision": "confirm_continuous",
            "justification": "Retry suportado pelo serviço existente.",
            "evidence_refs": [_evidence()],
        }

        first = client.post("/data-quality/exceptions/exc-1/reviews", json=body)
        second = client.post("/data-quality/exceptions/exc-1/reviews", json=body)

        assert first.status_code == 503
        assert second.status_code == 201
        assert fake_db.table("discovered_opportunities")._store["disc-1"] == editorial_row

    def test_storage_failure_is_sanitized(self, monkeypatch):
        app = _make_app()
        app.dependency_overrides[get_admin_user_id] = lambda: "admin-1"
        monkeypatch.setattr(
            "radar.api.routers.data_quality.review_temporal_exception",
            lambda **_: (_ for _ in ()).throw(DataQualityStorageError("secret raw payload")),
        )
        client = TestClient(app)

        resp = client.post(
            "/data-quality/exceptions/exc-1/reviews",
            json={
                "review_id": "review-1",
                "decision": "mark_unknown",
                "justification": "Falha de storage sanitizada.",
            },
        )

        assert resp.status_code == 503
        assert "secret raw payload" not in resp.text
        assert "Falha ao consultar a fila de exceções." in resp.text
