from __future__ import annotations

import json
import uuid
from datetime import datetime

import pytest
from postgrest.exceptions import APIError
from pydantic import ValidationError

from radar.core.services.data_quality_exceptions import (
    _INVALID_FINGERPRINT_MSG,
    DataQualityStorageError,
    _evidence_refs_payload,
    append_review,
    get_current_review_projection,
    get_exception,
    list_exceptions,
    open_or_observe_exception,
)
from radar.domain.data_quality import (
    DataQualityException,
    DataQualityReview,
    IssueCode,
)
from radar.domain.provenance import EvidenceRef, LocatorQuality, ReviewInfo
from radar.domain.source_bundle import SubjectKind

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Deterministic fake Supabase client
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, data):
        self.data = data


class _FakeQueryBuilder:
    def __init__(self, table_store, supabase=None):
        self._store = table_store
        self._supabase = supabase
        self._method = None
        self._payload = None
        self._filters = []
        self._order_col = None
        self._order_desc = False
        self._limit_val = None

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
        if self._supabase and self._supabase._skip_select_once:
            self._supabase._skip_select_once = False
            return _FakeResponse([])
        return self._do_select()

    def _match(self, row):
        for typ, col, val in self._filters:
            if typ == "eq":
                if row.get(col) != val:
                    return False
            elif typ == "neq":
                if row.get(col) == val:
                    return False
        return True

    def _filtered(self):
        return [r for r in self._store.values() if self._match(r)]

    def _do_select(self):
        rows = self._filtered()
        if self._order_col:
            rows.sort(
                key=lambda r: r.get(self._order_col) or "",
                reverse=self._order_desc,
            )
        if self._limit_val and len(rows) > self._limit_val:
            rows = rows[: self._limit_val]
        return _FakeResponse(rows)

    def _do_insert(self):
        if self._supabase and self._supabase._fail_next_insert is not None:
            err = self._supabase._fail_next_insert
            self._supabase._fail_next_insert = None
            raise err

        items = []
        raw = self._payload
        if isinstance(raw, dict):
            items.append(raw)
        else:
            items.extend(raw)

        for item in items:
            if "id" not in item:
                item["id"] = str(uuid.uuid4())
            for existing in self._store.values():
                match = True
                for key in (
                    "subject_kind",
                    "subject_id",
                    "field_path",
                    "issue_code",
                    "input_fingerprint",
                ):
                    if key in item and key in existing:
                        if item[key] != existing[key]:
                            match = False
                            break
                    else:
                        match = False
                        break
                else:
                    if match:
                        raise APIError({
                            "code": "23505",
                            "message": (
                                f'duplicate key: ({item["subject_kind"]}, '
                                f'{item["subject_id"]}, {item["field_path"]}, '
                                f'{item["issue_code"]}, {item["input_fingerprint"]})'
                            ),
                        })

        now = datetime.now().isoformat()
        new_items = []
        for item in items:
            row = dict(item)
            if "created_at" not in row:
                row["created_at"] = now
            if "last_observed_at" in row and row["last_observed_at"] is None:
                row["last_observed_at"] = now
            if "detected_at" in row and row["detected_at"] is None:
                row["detected_at"] = now
            if "reviewed_at" not in row:
                row["reviewed_at"] = now
            rid = str(uuid.uuid4())
            self._store[rid] = row
            new_items.append(self._store[rid])

        return _FakeResponse(new_items)

    def _do_update(self):
        matched = [rid for rid in self._store if self._match(self._store[rid])]
        for rid in matched:
            self._store[rid].update(self._payload)
        return _FakeResponse([])


class _FakeTable:
    def __init__(self, name, supabase):
        self._name = name
        self._supabase = supabase
        self._store = supabase._tables.setdefault(name, {})

    def select(self, *args):
        return _FakeQueryBuilder(self._store, self._supabase)

    def insert(self, data):
        qb = _FakeQueryBuilder(self._store, self._supabase)
        qb._method = "insert"
        qb._payload = data
        return qb

    def update(self, data):
        qb = _FakeQueryBuilder(self._store, self._supabase)
        qb._method = "update"
        qb._payload = data
        return qb


class FakeSupabase:
    """Deterministic fake that mimics the Supabase client interface.

    Stores data in-memory as dict[rid, row].
    Supports eq/neq filtering, ordering, limits.
    Enforces unique constraint on exceptions table insert.
    Simulates APIError on next insert when _fail_next_insert is set.
    """

    def __init__(self):
        self._tables: dict[str, dict[str, dict]] = {}
        self._fail_next_insert: APIError | None = None
        self._skip_select_once: bool = False

    def table(self, name):
        self._tables.setdefault(name, {})
        return _FakeTable(name, self)

    def __repr__(self):
        n = len(self._tables.get("data_quality_exceptions", {}))
        r = len(self._tables.get("data_quality_reviews", {}))
        return f"<FakeSupabase exceptions={n} reviews={r}>"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_FAST_NOW = "2026-07-29T12:00:00"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "http://test")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "test-key")


@pytest.fixture
def fs():
    return FakeSupabase()


@pytest.fixture(autouse=True)
def _mock_now(monkeypatch):
    import radar.core.services.data_quality_exceptions as svc
    monkeypatch.setattr(svc, "_now", lambda: _FAST_NOW)


@pytest.fixture(autouse=True)
def _mock_supabase(fs, monkeypatch):
    import radar.core.infra.db
    monkeypatch.setattr(radar.core.infra.db, "get_supabase_service", lambda: fs)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_exception(
    input_fingerprint: str = "fp-v1",
    status: str = "open",
    **overrides,
) -> DataQualityException:
    kwargs = dict(
        subject_kind=SubjectKind.OPPORTUNITY,
        subject_id="finep:589",
        field_path="deadline",
        issue_code=IssueCode.TEMPORAL_STATUS_WITHOUT_BASIS,
        input_fingerprint=input_fingerprint,
        status=status,
    )
    kwargs.update(overrides)
    return DataQualityException(**kwargs)


def make_review(exception_ref: str = "exc-uuid", **overrides) -> DataQualityReview:
    kwargs = dict(
        exception_ref=exception_ref,
        decision="confirm",
        justification="prazo confirmado conforme documento oficial",
        review=ReviewInfo(
            review_id="rev-001",
            actor_id="admin",
            reviewed_at=datetime(2026, 7, 29, 12, 0, 0),
        ),
    )
    kwargs.update(overrides)
    return DataQualityReview(**kwargs)


def ref(**overrides) -> EvidenceRef:
    kwargs = dict(
        source="finep",
        canonical_content_hash="sha256:" + "a" * 64,
        locator_quality=LocatorQuality.DOCUMENT_ONLY,
        document="pagina.html",
        source_url="http://example.com/doc",
    )
    kwargs.update(overrides)
    return EvidenceRef(**kwargs)


# ---------------------------------------------------------------------------
# Migration structure (static analysis)
# ---------------------------------------------------------------------------


def _migration_sql() -> str:
    with open("supabase/migrations/046_data_quality_exceptions.sql") as f:
        return f.read()


class TestMigrationStructure:
    def test_two_tables(self):
        assert _migration_sql().count("create table if not exists public.") == 2

    def test_exceptions_table(self):
        assert "public.data_quality_exceptions" in _migration_sql()

    def test_reviews_table(self):
        assert "public.data_quality_reviews" in _migration_sql()

    def test_rls_on_both(self):
        assert _migration_sql().count("enable row level security") == 2

    def test_unique_constraint(self):
        assert "unique (subject_kind, subject_id, field_path, issue_code, input_fingerprint)" in _migration_sql()

    def test_review_id_column(self):
        sql = _migration_sql()
        assert "review_id           text        not null unique" in sql

    def test_exception_fk(self):
        assert "references public.data_quality_exceptions(id)" in _migration_sql()

    def test_append_only_trigger(self):
        sql = _migration_sql()
        assert "reject_review_mutations" in sql
        assert "before update or delete on public.data_quality_reviews" in sql

    def test_no_user_policies(self):
        assert "create policy" not in _migration_sql()


# ---------------------------------------------------------------------------
# Supabase ausente — degradação graciosa
# ---------------------------------------------------------------------------


class TestSupabaseAbsent:
    def test_open_or_observe_returns_false(self, monkeypatch):
        monkeypatch.delenv("SUPABASE_URL", raising=False)
        monkeypatch.delenv("SUPABASE_SERVICE_KEY", raising=False)
        exc = make_exception()
        assert open_or_observe_exception(exc) is False

    def test_list_returns_empty(self, monkeypatch):
        monkeypatch.delenv("SUPABASE_URL", raising=False)
        monkeypatch.delenv("SUPABASE_SERVICE_KEY", raising=False)
        assert list_exceptions() == []

    def test_get_returns_none(self, monkeypatch):
        monkeypatch.delenv("SUPABASE_URL", raising=False)
        monkeypatch.delenv("SUPABASE_SERVICE_KEY", raising=False)
        assert get_exception("any") is None

    def test_append_review_returns_false(self, monkeypatch):
        monkeypatch.delenv("SUPABASE_URL", raising=False)
        monkeypatch.delenv("SUPABASE_SERVICE_KEY", raising=False)
        review = make_review()
        assert append_review(review) is False

    def test_get_review_projection_returns_none(self, monkeypatch):
        monkeypatch.delenv("SUPABASE_URL", raising=False)
        monkeypatch.delenv("SUPABASE_SERVICE_KEY", raising=False)
        assert get_current_review_projection("any") is None


# ---------------------------------------------------------------------------
# input_fingerprint ausente ou vazio — rejeição
# ---------------------------------------------------------------------------


class TestInputFingerprintRequired:
    def test_none_fingerprint_raises(self):
        exc = make_exception(input_fingerprint=None)
        with pytest.raises(ValueError, match=_INVALID_FINGERPRINT_MSG):
            open_or_observe_exception(exc)

    def test_empty_fingerprint_raises(self):
        exc = make_exception(input_fingerprint="")
        with pytest.raises(ValueError, match=_INVALID_FINGERPRINT_MSG):
            open_or_observe_exception(exc)

    def test_whitespace_fingerprint_raises(self):
        exc = make_exception(input_fingerprint="   ")
        with pytest.raises(ValueError, match=_INVALID_FINGERPRINT_MSG):
            open_or_observe_exception(exc)


# ---------------------------------------------------------------------------
# open_or_observe_exception — idempotência e supersessão
# ---------------------------------------------------------------------------


class TestOpenOrObserve:
    def test_first_insert_creates_one_record(self, fs):
        assert open_or_observe_exception(make_exception()) is True
        rows = list_exceptions()
        assert len(rows) == 1
        assert rows[0]["status"] == "open"
        assert rows[0]["input_fingerprint"] == "fp-v1"

    def test_same_fingerprint_updates_only_last_observed_at(self, fs):
        exc = make_exception(detected_at=datetime(2026, 1, 1))
        assert open_or_observe_exception(exc) is True
        rows = list_exceptions()
        assert len(rows) == 1
        assert rows[0]["detected_at"] == "2026-01-01T00:00:00"
        assert rows[0]["last_observed_at"] == _FAST_NOW
        assert rows[0]["status"] == "open"

    def test_same_fingerprint_no_duplicate(self, fs):
        assert open_or_observe_exception(make_exception()) is True
        assert open_or_observe_exception(make_exception()) is True
        rows = list_exceptions()
        assert len(rows) == 1
        assert rows[0]["status"] == "open"

    def test_new_fingerprint_supersedes_old(self, fs):
        assert open_or_observe_exception(make_exception(input_fingerprint="fp-v1")) is True
        assert open_or_observe_exception(make_exception(input_fingerprint="fp-v2")) is True
        rows = list_exceptions()
        assert len(rows) == 2
        open_rows = [r for r in rows if r["status"] == "open"]
        superseded = [r for r in rows if r["status"] == "superseded"]
        assert len(open_rows) == 1
        assert len(superseded) == 1
        assert open_rows[0]["input_fingerprint"] == "fp-v2"
        assert superseded[0]["input_fingerprint"] == "fp-v1"

    def test_third_fingerprint_supersedes_second(self, fs):
        assert open_or_observe_exception(make_exception(input_fingerprint="fp-v1")) is True
        assert open_or_observe_exception(make_exception(input_fingerprint="fp-v2")) is True
        assert open_or_observe_exception(make_exception(input_fingerprint="fp-v3")) is True
        rows = list_exceptions()
        open_rows = [r for r in rows if r["status"] == "open"]
        assert len(open_rows) == 1
        assert open_rows[0]["input_fingerprint"] == "fp-v3"

    def test_different_group_not_affected(self, fs):
        e1 = make_exception(input_fingerprint="fp-v1", subject_id="finep:001")
        e2 = make_exception(input_fingerprint="fp-v1", subject_id="finep:002")
        assert open_or_observe_exception(e1) is True
        assert open_or_observe_exception(e2) is True
        rows = list_exceptions()
        assert len(rows) == 2
        assert all(r["status"] == "open" for r in rows)

    def test_reobserve_same_fingerprint_supersedes_orphans(self, fs):
        """Retry após falha parcial: insere fingerprint, depois convergência."""
        e1 = make_exception(input_fingerprint="fp-v1")
        assert open_or_observe_exception(e1) is True
        # Simulate partial failure: manually add an extra open record
        svc_table = fs.table("data_quality_exceptions")
        extra = {
            "subject_kind": "opportunity",
            "subject_id": "finep:589",
            "field_path": "deadline",
            "issue_code": "temporal_status_without_basis",
            "input_fingerprint": "fp-orphan",
            "status": "open",
            "schema_version": 1,
        }
        svc_table.insert(extra).execute()
        # Now reobserve fp-v1
        assert open_or_observe_exception(e1) is True
        rows = list_exceptions()
        open_rows = [r for r in rows if r["status"] == "open"]
        assert len(open_rows) == 1
        assert open_rows[0]["input_fingerprint"] == "fp-v1"


# ---------------------------------------------------------------------------
# Insert falha → não supersede anterior
# ---------------------------------------------------------------------------


class TestInsertFailureNoSupersede:
    def test_insert_error_does_not_supersede(self, fs):
        assert open_or_observe_exception(make_exception(input_fingerprint="fp-v1")) is True
        fs._fail_next_insert = APIError({
            "code": "23505",
            "message": "duplicate key",
        })
        result = open_or_observe_exception(make_exception(input_fingerprint="fp-v1"))
        assert result is True
        rows = list_exceptions()
        open_rows = [r for r in rows if r["status"] == "open"]
        assert len(open_rows) == 1

    def test_real_error_raises_and_does_not_supersede(self, fs):
        assert open_or_observe_exception(make_exception(input_fingerprint="fp-v1")) is True
        fs._fail_next_insert = APIError({
            "code": "PGRST116",
            "message": "syntax error",
        })
        with pytest.raises(DataQualityStorageError):
            open_or_observe_exception(make_exception(input_fingerprint="fp-v2"))
        rows = list_exceptions()
        open_rows = [r for r in rows if r["status"] == "open"]
        assert len(open_rows) == 1
        assert open_rows[0]["input_fingerprint"] == "fp-v1"


# ---------------------------------------------------------------------------
# review_id — preservação e idempotência
# ---------------------------------------------------------------------------


class TestReviewId:
    def test_review_id_persisted(self, fs):
        exc = make_exception()
        open_or_observe_exception(exc)
        rows = list_exceptions()
        exc_id = rows[0]["id"]
        review = make_review(exception_ref=exc_id)
        assert append_review(review) is True
        rev = get_current_review_projection(exc_id)
        assert rev is not None
        assert rev.review.review_id == "rev-001"

    def test_retry_same_review_id_idempotent(self, fs):
        exc = make_exception()
        open_or_observe_exception(exc)
        rows = list_exceptions()
        exc_id = rows[0]["id"]
        review = make_review(exception_ref=exc_id)
        assert append_review(review) is True
        assert append_review(review) is True
        rev = get_current_review_projection(exc_id)
        assert rev is not None
        assert rev.review.review_id == "rev-001"

    def test_different_review_ids_both_persisted(self, fs):
        exc = make_exception()
        open_or_observe_exception(exc)
        rows = list_exceptions()
        exc_id = rows[0]["id"]
        r1 = make_review(exception_ref=exc_id, review=ReviewInfo(
            review_id="rev-001", actor_id="admin",
            reviewed_at=datetime(2026, 7, 29, 12, 0, 0),
        ))
        r2 = make_review(exception_ref=exc_id, decision="correct",
                          corrected_value="2026-12-31",
                          justification="correcao",
                          evidence_refs=[ref(canonical_content_hash="sha256:" + "b" * 64)],
                          review=ReviewInfo(
                              review_id="rev-002", actor_id="admin",
                              reviewed_at=datetime(2026, 7, 29, 13, 0, 0),
                          ))
        assert append_review(r1) is True
        assert append_review(r2) is True
        # get_current_review_projection returns last
        last = get_current_review_projection(exc_id)
        assert last is not None
        assert last.review.review_id == "rev-002"

    def test_review_id_required(self, fs):
        review = make_review()
        review.review.review_id = ""
        with pytest.raises(ValueError, match="review_id must be non-empty"):
            append_review(review)

    def test_review_id_empty_rejected_by_model(self):
        with pytest.raises(ValidationError):
            ReviewInfo(review_id="", actor_id="admin", reviewed_at=datetime(2026, 7, 29, 12, 0, 0))


# ---------------------------------------------------------------------------
# _review_payload_matches — comparação material
# ---------------------------------------------------------------------------


class TestReviewPayloadMatches:
    def test_identical_payloads(self):
        from radar.core.services.data_quality_exceptions import (
            _review_payload,
            _review_payload_matches,
        )
        review = make_review()
        payload = _review_payload(review)
        assert _review_payload_matches(payload, payload) is True

    def test_different_decision(self):
        from radar.core.services.data_quality_exceptions import (
            _review_payload,
            _review_payload_matches,
        )
        r1 = make_review(decision="confirm")
        r2 = make_review(decision="mark_unknown", justification="unknown")
        assert _review_payload_matches(
            _review_payload(r1), _review_payload(r2)
        ) is False

    def test_different_exception_id(self):
        from radar.core.services.data_quality_exceptions import (
            _review_payload,
            _review_payload_matches,
        )
        r1 = make_review(exception_ref="uuid-a")
        r2 = make_review(exception_ref="uuid-b")
        assert _review_payload_matches(
            _review_payload(r1), _review_payload(r2)
        ) is False

    def test_different_actor_id(self):
        from radar.core.services.data_quality_exceptions import (
            _review_payload,
            _review_payload_matches,
        )
        r1 = make_review(review=ReviewInfo(
            review_id="r", actor_id="admin",
            reviewed_at=datetime(2026, 7, 29, 12, 0, 0),
        ))
        r2 = make_review(review=ReviewInfo(
            review_id="r", actor_id="other",
            reviewed_at=datetime(2026, 7, 29, 12, 0, 0),
        ))
        assert _review_payload_matches(
            _review_payload(r1), _review_payload(r2)
        ) is False

    def _base_payload(self, reviewed_at: str) -> dict:
        return dict(
            schema_version=1,
            exception_id="exc-uuid",
            decision="confirm",
            corrected_value=None,
            justification="test",
            evidence_refs=[],
            actor_id="admin",
            reviewed_at=reviewed_at,
        )

    def test_naive_vs_aware_utc_equal(self):
        from radar.core.services.data_quality_exceptions import (
            _review_payload_matches,
        )
        a = self._base_payload("2026-07-29T12:00:00")
        b = self._base_payload("2026-07-29T12:00:00+00:00")
        assert _review_payload_matches(a, b) is True

    def test_offset_vs_z_equal(self):
        from radar.core.services.data_quality_exceptions import (
            _review_payload_matches,
        )
        a = self._base_payload("2026-07-29T09:00:00-03:00")
        b = self._base_payload("2026-07-29T12:00:00Z")
        assert _review_payload_matches(a, b) is True

    def test_truly_different_instants(self):
        from radar.core.services.data_quality_exceptions import (
            _review_payload_matches,
        )
        a = self._base_payload("2026-07-29T12:00:00Z")
        b = self._base_payload("2026-07-30T12:00:00Z")
        assert _review_payload_matches(a, b) is False

    def test_invalid_timestamp_is_different(self):
        from radar.core.services.data_quality_exceptions import (
            _review_payload_matches,
        )
        a = self._base_payload("not-a-timestamp")
        b = self._base_payload("2026-07-29T12:00:00Z")
        assert _review_payload_matches(a, b) is False


# ---------------------------------------------------------------------------
# append_review — colisão sequencial (mesmo review_id, payload diferente)
# ---------------------------------------------------------------------------


class TestAppendReviewCollision:
    def _setup(self, fs):
        exc = make_exception()
        open_or_observe_exception(exc)
        return list_exceptions()[0]["id"]

    def test_different_decision_raises(self, fs):
        exc_id = self._setup(fs)
        r1 = make_review(exception_ref=exc_id, review=ReviewInfo(
            review_id="rev-collide", actor_id="admin",
            reviewed_at=datetime(2026, 7, 29, 12, 0, 0),
        ))
        assert append_review(r1) is True
        r2 = make_review(exception_ref=exc_id, decision="mark_unknown",
                         justification="unknown now",
                         review=ReviewInfo(
                             review_id="rev-collide", actor_id="admin",
                             reviewed_at=datetime(2026, 7, 29, 12, 0, 0),
                         ))
        with pytest.raises(DataQualityStorageError, match="review_id collision"):
            append_review(r2)

    def test_different_exception_id_raises(self, fs):
        exc_id = self._setup(fs)
        r1 = make_review(exception_ref=exc_id, review=ReviewInfo(
            review_id="rev-collide-2", actor_id="admin",
            reviewed_at=datetime(2026, 7, 29, 12, 0, 0),
        ))
        assert append_review(r1) is True
        r2 = make_review(exception_ref="00000000-0000-0000-0000-000000000000",
                         review=ReviewInfo(
                             review_id="rev-collide-2", actor_id="admin",
                             reviewed_at=datetime(2026, 7, 29, 12, 0, 0),
                         ))
        with pytest.raises(DataQualityStorageError, match="review_id collision"):
            append_review(r2)

    def test_different_actor_id_raises(self, fs):
        exc_id = self._setup(fs)
        r1 = make_review(exception_ref=exc_id, review=ReviewInfo(
            review_id="rev-collide-3", actor_id="admin",
            reviewed_at=datetime(2026, 7, 29, 12, 0, 0),
        ))
        assert append_review(r1) is True
        r2 = make_review(exception_ref=exc_id, review=ReviewInfo(
            review_id="rev-collide-3", actor_id="other-admin",
            reviewed_at=datetime(2026, 7, 29, 12, 0, 0),
        ))
        with pytest.raises(DataQualityStorageError, match="review_id collision"):
            append_review(r2)

    def test_different_reviewed_at_raises(self, fs):
        exc_id = self._setup(fs)
        r1 = make_review(exception_ref=exc_id, review=ReviewInfo(
            review_id="rev-collide-4", actor_id="admin",
            reviewed_at=datetime(2026, 7, 29, 12, 0, 0),
        ))
        assert append_review(r1) is True
        r2 = make_review(exception_ref=exc_id, review=ReviewInfo(
            review_id="rev-collide-4", actor_id="admin",
            reviewed_at=datetime(2026, 7, 30, 12, 0, 0),
        ))
        with pytest.raises(DataQualityStorageError, match="review_id collision"):
            append_review(r2)


# ---------------------------------------------------------------------------
# source_url removido
# ---------------------------------------------------------------------------


class TestSourceUrlRemoved:
    def test_exception_payload_no_source_url(self):
        exc = make_exception(evidence_refs=[ref(source_url="http://example.com/doc")])
        from radar.core.services.data_quality_exceptions import _exception_payload
        payload = _exception_payload(exc)
        for ev in payload["evidence_refs"]:
            assert "source_url" not in ev

    def test_review_payload_no_source_url(self):
        review = make_review(
            evidence_refs=[ref(source_url="http://example.com/doc")],
        )
        from radar.core.services.data_quality_exceptions import _review_payload
        payload = _review_payload(review)
        for ev in payload["evidence_refs"]:
            assert "source_url" not in ev

    def test_persisted_exception_no_source_url(self, fs):
        exc = make_exception(evidence_refs=[ref(source_url="http://example.com/doc")])
        open_or_observe_exception(exc)
        rows = list_exceptions()
        for ev in rows[0]["evidence_refs"]:
            assert "source_url" not in ev

    def test_persisted_review_no_source_url(self, fs):
        exc = make_exception(evidence_refs=[ref(source_url="http://example.com/doc")])
        open_or_observe_exception(exc)
        rows = list_exceptions()
        exc_id = rows[0]["id"]
        review = make_review(
            exception_ref=exc_id,
            evidence_refs=[ref(source_url="http://example.com/doc")],
        )
        append_review(review)
        rev = get_current_review_projection(exc_id)
        assert rev is not None
        for ev in rev.evidence_refs:
            assert ev.source_url is None

    def test_evidence_refs_payload_helper(self):
        refs = [ref(source_url="http://x.com"), ref(source_url="http://y.com")]
        payload = _evidence_refs_payload(refs)
        for p in payload:
            assert "source_url" not in p

    def test_href_not_in_exception_payload(self, fs):
        exc = make_exception(evidence_refs=[ref(
            source_url="http://example.com",
            canonical_content_hash="sha256:" + "c" * 64,
        )])
        open_or_observe_exception(exc)
        rows = list_exceptions()
        raw = json.dumps(rows)
        assert "source_url" not in raw
        assert "http://" not in raw


# ---------------------------------------------------------------------------
# list_exceptions com filtros
# ---------------------------------------------------------------------------


class TestListExceptions:
    def test_filter_by_subject_kind(self, fs):
        open_or_observe_exception(make_exception(
            subject_kind=SubjectKind.OPPORTUNITY,
            input_fingerprint="fp-o",
        ))
        open_or_observe_exception(make_exception(
            subject_kind=SubjectKind.INVESTOR,
            input_fingerprint="fp-i",
            subject_id="inv:001",
        ))
        rows = list_exceptions(subject_kind="opportunity")
        assert len(rows) == 1
        assert rows[0]["input_fingerprint"] == "fp-o"

    def test_filter_by_status(self, fs):
        e1 = make_exception(input_fingerprint="fp-v1")
        e2 = make_exception(input_fingerprint="fp-v2")
        open_or_observe_exception(e1)
        open_or_observe_exception(e2)
        rows = list_exceptions(status="superseded")
        assert len(rows) == 1
        assert rows[0]["input_fingerprint"] == "fp-v1"


# ---------------------------------------------------------------------------
# Semântica de erro
# ---------------------------------------------------------------------------


class TestErrorSemantics:
    def test_storage_error_sanitized(self):
        err = DataQualityStorageError("open_or_observe failed: code=PGRST116")
        msg = str(err)
        assert "traceback" not in msg
        assert "password" not in msg
        assert "secret" not in msg
        assert "http://" not in msg

    def test_storage_error_categorical(self):
        err = DataQualityStorageError("list_exceptions failed")
        assert "failed" in str(err)

    def test_storage_error_not_bool(self):
        err = DataQualityStorageError("any msg")
        assert not isinstance(err, bool)


# ---------------------------------------------------------------------------
# Reobservação de resolved/superseded
# ---------------------------------------------------------------------------


class TestReobserveResolvedSuperseded:
    def test_a_b_a_reobserve_keeps_b_open(self, fs):
        """A→B→A: B stays open, A stays superseded on reobserve."""
        assert open_or_observe_exception(make_exception(input_fingerprint="fp-v1")) is True
        assert open_or_observe_exception(make_exception(input_fingerprint="fp-v2")) is True
        assert open_or_observe_exception(make_exception(input_fingerprint="fp-v1")) is True
        rows = list_exceptions()
        open_rows = [r for r in rows if r["status"] == "open"]
        superseded = [r for r in rows if r["status"] == "superseded"]
        assert len(open_rows) == 1
        assert len(superseded) == 1
        assert open_rows[0]["input_fingerprint"] == "fp-v2"
        assert superseded[0]["input_fingerprint"] == "fp-v1"

    def test_reobserve_resolved_does_not_supersede(self, fs):
        """Reobservar fingerprint resolvido: não supersede outros."""
        assert open_or_observe_exception(make_exception(input_fingerprint="fp-v1")) is True
        store = fs._tables["data_quality_exceptions"]
        rid = next(iter(store))
        store[rid]["status"] = "resolved"
        # Insert another open record that could be superseded
        extra = dict(
            subject_kind="opportunity",
            subject_id="finep:589",
            field_path="deadline",
            issue_code="temporal_status_without_basis",
            input_fingerprint="fp-orphan",
            status="open",
        )
        fs._tables["data_quality_exceptions"][str(uuid.uuid4())] = extra
        assert open_or_observe_exception(make_exception(input_fingerprint="fp-v1")) is True
        rows = list_exceptions()
        # Both should still exist: resolved stays resolved, orphan stays open
        resolved = [r for r in rows if r["status"] == "resolved"]
        open_orphans = [r for r in rows if r["status"] == "open" and r["input_fingerprint"] == "fp-orphan"]
        assert len(resolved) == 1
        assert len(open_orphans) == 1
        assert resolved[0]["last_observed_at"] == _FAST_NOW

    def test_reobserve_superseded_does_not_supersede(self, fs):
        """Reobservar fingerprint superseded: não supersede outros."""
        assert open_or_observe_exception(make_exception(input_fingerprint="fp-v1")) is True
        store = fs._tables["data_quality_exceptions"]
        rid = next(iter(store))
        store[rid]["status"] = "superseded"
        extra = dict(
            subject_kind="opportunity",
            subject_id="finep:589",
            field_path="deadline",
            issue_code="temporal_status_without_basis",
            input_fingerprint="fp-orphan",
            status="open",
        )
        fs._tables["data_quality_exceptions"][str(uuid.uuid4())] = extra
        assert open_or_observe_exception(make_exception(input_fingerprint="fp-v1")) is True
        rows = list_exceptions()
        superseded = [r for r in rows if r["status"] == "superseded" and r["input_fingerprint"] == "fp-v1"]
        open_orphans = [r for r in rows if r["status"] == "open"]
        assert len(superseded) == 1
        assert len(open_orphans) == 1
        assert superseded[0]["last_observed_at"] == _FAST_NOW

    def test_reobserve_keeps_last_observed_at_updated(self, fs):
        """Reobservar resolved/superseded ainda atualiza last_observed_at."""
        assert open_or_observe_exception(make_exception(input_fingerprint="fp-v1")) is True
        store = fs._tables["data_quality_exceptions"]
        rid = next(iter(store))
        store[rid]["status"] = "resolved"
        store[rid]["last_observed_at"] = "2026-01-01T00:00:00"
        assert open_or_observe_exception(make_exception(input_fingerprint="fp-v1")) is True
        rows = list_exceptions()
        assert rows[0]["last_observed_at"] == _FAST_NOW
        assert rows[0]["status"] == "resolved"


# ---------------------------------------------------------------------------
# append_review — violação 23505 (condição de corrida)
# ---------------------------------------------------------------------------


class TestAppendReviewRace:
    def test_23505_same_payload_idempotent(self, fs):
        """23505 race com mesmo payload → idempotente."""
        exc = make_exception()
        open_or_observe_exception(exc)
        exc_id = list_exceptions()[0]["id"]
        # Ensure reviews table dict is initialized
        fs.table("data_quality_reviews")
        rid = str(uuid.uuid4())
        review = make_review(exception_ref=exc_id)
        from radar.core.services.data_quality_exceptions import _review_payload
        payload = _review_payload(review)
        fs._tables["data_quality_reviews"][rid] = payload
        fs._skip_select_once = True
        fs._fail_next_insert = APIError({"code": "23505", "message": "duplicate"})
        assert append_review(review) is True

    def test_23505_different_payload_raises(self, fs):
        """23505 race com payload diferente → DataQualityStorageError."""
        exc = make_exception()
        open_or_observe_exception(exc)
        exc_id = list_exceptions()[0]["id"]
        fs.table("data_quality_reviews")
        rid = str(uuid.uuid4())
        from radar.core.services.data_quality_exceptions import _review_payload
        confirm_review = make_review(
            exception_ref=exc_id,
            decision="confirm",
            justification="different justification",
            review=ReviewInfo(
                review_id="rev-race-002",
                actor_id="admin",
                reviewed_at=datetime(2026, 7, 29, 12, 0, 0),
            ),
        )
        fs._tables["data_quality_reviews"][rid] = _review_payload(confirm_review)
        review = make_review(
            exception_ref=exc_id,
            decision="correct",
            corrected_value="2026-12-31",
            justification="corrected date",
            evidence_refs=[ref()],
            review=ReviewInfo(
                review_id="rev-race-002",
                actor_id="admin",
                reviewed_at=datetime(2026, 7, 29, 12, 0, 0),
            ),
        )
        fs._skip_select_once = True
        fs._fail_next_insert = APIError({"code": "23505", "message": "duplicate"})
        with pytest.raises(DataQualityStorageError, match="review_id collision"):
            append_review(review)

    def test_23505_race_no_record_found(self, fs):
        """23505 race mas registro sumiu entre violação e recuperação."""
        review = make_review(exception_ref="no-such-exc")
        fs._fail_next_insert = APIError({"code": "23505", "message": "duplicate"})
        with pytest.raises(DataQualityStorageError, match="23505 race but no record found"):
            append_review(review)


# ---------------------------------------------------------------------------
# Migration — reexecutabilidade e constraints
# ---------------------------------------------------------------------------


class TestMigrationExecutability:
    def test_drop_trigger_if_exists(self):
        assert "drop trigger if exists trg_reviews_append_only" in _migration_sql()

    def test_input_fingerprint_check_constraint(self):
        assert "check (btrim(input_fingerprint) <> '')" in _migration_sql()

    def test_no_default_on_input_fingerprint(self):
        sql = _migration_sql()
        assert "default ''" not in sql
