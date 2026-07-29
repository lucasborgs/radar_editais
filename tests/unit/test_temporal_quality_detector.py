from __future__ import annotations

import uuid
from datetime import date, datetime

import pytest

from radar.core.services.temporal_quality import (
    DETECTOR_PRODUCER_VERSION,
    _build_temporal_fingerprint,
    _collect_ref_identities,
    _today_brasilia,
    check_edital_temporal_quality,
    detect_temporal_exception,
)
from radar.domain.data_quality import (
    IssueCode,
)
from radar.domain.provenance import EvidenceRef, LocatorQuality

pytestmark = pytest.mark.unit

# ===========================================================================
# Fake Supabase (deterministic, in-memory)
# ===========================================================================


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
            rows.sort(key=lambda r: r.get(self._order_col) or "", reverse=self._order_desc)
        if self._limit_val and len(rows) > self._limit_val:
            rows = rows[: self._limit_val]
        return _FakeResponse(rows)

    def _do_insert(self):
        from postgrest.exceptions import APIError

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
                        raise APIError({"code": "23505", "message": "duplicate key"})

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


class FakeSupabaseDQ:
    def __init__(self):
        self._tables: dict[str, dict[str, dict]] = {}
        self._fail_next_insert = None

    def table(self, name):
        self._tables.setdefault(name, {})
        return _FakeTable(name, self)


# ===========================================================================
# Fixtures
# ===========================================================================

_FAST_NOW = "2026-07-29T12:00:00"
_AS_OF = date(2026, 7, 29)


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "http://test")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "test-key")


@pytest.fixture
def fs():
    return FakeSupabaseDQ()


@pytest.fixture(autouse=True)
def _mock_now(monkeypatch):
    import radar.core.services.data_quality_exceptions as svc
    monkeypatch.setattr(svc, "_now", lambda: _FAST_NOW)


@pytest.fixture(autouse=True)
def _mock_supabase(fs, monkeypatch):
    import radar.core.infra.db
    monkeypatch.setattr(radar.core.infra.db, "get_supabase_service", lambda: fs)


def make_ref(hash_suffix: str = "a", **overrides) -> EvidenceRef:
    kwargs = dict(
        source="finep",
        canonical_content_hash=f"sha256:{hash_suffix * 64}",
        locator_quality=LocatorQuality.DOCUMENT_ONLY,
        document="pagina_oficial.html",
    )
    kwargs.update(overrides)
    return EvidenceRef(**kwargs)


def make_silver_ref(hash_suffix: str = "s") -> EvidenceRef:
    return EvidenceRef(
        source="finep",
        silver_source_hash=f"md5:{hash_suffix * 32}",
        locator_quality=LocatorQuality.DOCUMENT_ONLY,
        document="silver_doc.json",
    )


# ===========================================================================
# today_brasilia
# ===========================================================================


class TestTodayBrasilia:
    def test_returns_date(self):
        d = _today_brasilia()
        assert isinstance(d, date)

    def test_uses_america_sao_paulo(self):
        import zoneinfo
        tz = zoneinfo.ZoneInfo("America/Sao_Paulo")
        expected = datetime.now(tz).date()
        assert _today_brasilia() == expected


# ===========================================================================
# collect_ref_identities
# ===========================================================================


class TestCollectRefIdentities:
    def test_canonical_hash(self):
        ref = make_ref("a")
        ids = _collect_ref_identities([ref])
        assert ref.canonical_content_hash in ids

    def test_silver_source_hash(self):
        ref = make_silver_ref("s")
        ids = _collect_ref_identities([ref])
        assert ref.silver_source_hash in ids

    def test_bundle_and_content_hash(self):
        ref = make_ref("b", bundle_hash="sha256:" + "b" * 64, content_hash="sha256:" + "c" * 64)
        ids = _collect_ref_identities([ref])
        assert ref.bundle_hash in ids
        assert ref.content_hash in ids

    def test_empty_when_no_hashes(self):
        assert _collect_ref_identities([]) == []

    def test_multiple_refs(self):
        r1 = make_ref("a")
        r2 = make_silver_ref("b")
        ids = _collect_ref_identities([r1, r2])
        assert len(ids) == 2
        assert r1.canonical_content_hash in ids
        assert r2.silver_source_hash in ids


# ===========================================================================
# Fingerprint
# ===========================================================================


class TestFingerprint:
    def test_deterministic(self):
        fp1 = _build_temporal_fingerprint(deadline=date(2026, 12, 31), status="aberta")
        fp2 = _build_temporal_fingerprint(deadline=date(2026, 12, 31), status="aberta")
        assert fp1 == fp2
        assert fp1.startswith("sha256:")
        assert len(fp1) == 64 + 7

    def test_differs_by_deadline(self):
        fp1 = _build_temporal_fingerprint(deadline=date(2026, 12, 31), status="aberta")
        fp2 = _build_temporal_fingerprint(deadline=None, status="aberta")
        assert fp1 != fp2

    def test_differs_by_status(self):
        fp1 = _build_temporal_fingerprint(deadline=None, status="aberta")
        fp2 = _build_temporal_fingerprint(deadline=None, status="encerrada")
        assert fp1 != fp2

    def test_equivalent_aberta_normalization(self):
        """ABERTA, aberta and  aberta  all produce same fingerprint."""
        fp1 = _build_temporal_fingerprint(None, "ABERTA")
        fp2 = _build_temporal_fingerprint(None, "aberta")
        fp3 = _build_temporal_fingerprint(None, "  aberta  ")
        assert fp1 == fp2 == fp3

    def test_includes_evidence_hashes(self):
        fp1 = _build_temporal_fingerprint(None, None, evidence_hashes=["sha256:" + "a" * 64])
        fp2 = _build_temporal_fingerprint(None, None, evidence_hashes=["sha256:" + "b" * 64])
        assert fp1 != fp2

    def test_includes_bundle_hash(self):
        fp1 = _build_temporal_fingerprint(None, None, bundle_hash="sha256:" + "a" * 64)
        fp2 = _build_temporal_fingerprint(None, None, bundle_hash=None)
        assert fp1 != fp2

    def test_silver_source_hash_in_fingerprint(self):
        """Evidence based on silver_source_hash participates in fingerprint."""
        fp1 = _build_temporal_fingerprint(None, None, evidence_hashes=["md5:" + "a" * 32])
        fp2 = _build_temporal_fingerprint(None, None, evidence_hashes=["md5:" + "b" * 32])
        assert fp1 != fp2

    def test_producer_version_fixed(self):
        import hashlib
        import json
        material = {
            "producer_version": "temporal_quality:v1",
            "deadline": None, "status": None,
            "evidence_hashes": [], "bundle_hash": None,
        }
        expected = "sha256:" + hashlib.sha256(
            json.dumps(material, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()
        assert _build_temporal_fingerprint(None, None) == expected

    def test_sorted_evidence_hashes(self):
        h1 = "sha256:" + "a" * 64
        h2 = "sha256:" + "b" * 64
        fp_a = _build_temporal_fingerprint(None, None, evidence_hashes=[h2, h1])
        fp_b = _build_temporal_fingerprint(None, None, evidence_hashes=[h1, h2])
        assert fp_a == fp_b

    def test_normalized_status_none_for_empty(self):
        fp = _build_temporal_fingerprint(None, "")
        assert fp == _build_temporal_fingerprint(None, None)

    def test_normalized_status_none_for_whitespace(self):
        fp = _build_temporal_fingerprint(None, "   ")
        assert fp == _build_temporal_fingerprint(None, None)


# ===========================================================================
# Finep/Eureka: ABERTA, deadline=None, sem evidência
# ===========================================================================


class TestFinepEureka:
    def test_opens_exception(self):
        exc = detect_temporal_exception(
            subject_id="finep:589",
            deadline=None,
            status="ABERTA",
            as_of=_AS_OF,
        )
        assert exc is not None
        assert exc.issue_code is IssueCode.TEMPORAL_STATUS_WITHOUT_BASIS
        assert exc.subject_id == "finep:589"
        assert exc.input_fingerprint.startswith("sha256:")

    def test_rerun_identical_does_not_duplicate(self, fs):
        from radar.core.services.data_quality_exceptions import list_exceptions

        check_edital_temporal_quality(
            subject_id="finep:589", deadline=None, status="ABERTA", as_of=_AS_OF,
        )
        assert len(list_exceptions()) == 1

        check_edital_temporal_quality(
            subject_id="finep:589", deadline=None, status="ABERTA", as_of=_AS_OF,
        )
        assert len(list_exceptions()) == 1

    def test_new_fingerprint_supersedes_old(self, fs):
        from radar.core.services.data_quality_exceptions import list_exceptions

        check_edital_temporal_quality(
            subject_id="finep:589", deadline=None, status="ABERTA", as_of=_AS_OF,
        )
        check_edital_temporal_quality(
            subject_id="finep:589", deadline=None, status="ABERTA", as_of=_AS_OF,
            bundle_hash="sha256:" + "b" * 64,
        )
        rows = list_exceptions()
        open_rows = [r for r in rows if r["status"] == "open"]
        superseded = [r for r in rows if r["status"] == "superseded"]
        assert len(open_rows) == 1
        assert len(superseded) == 1
        assert open_rows[0]["input_fingerprint"] != superseded[0]["input_fingerprint"]


# ===========================================================================
# Futuro coerente
# ===========================================================================


class TestFutureDeadline:
    def test_future_deadline_no_exception(self):
        assert detect_temporal_exception(
            subject_id="finep:601", deadline=date(2026, 12, 31), status="aberta", as_of=_AS_OF,
        ) is None

    def test_deadline_today_no_exception(self):
        assert detect_temporal_exception(
            subject_id="finep:602", deadline=_AS_OF, status="aberta", as_of=_AS_OF,
        ) is None

    def test_check_does_not_persist(self, fs):
        from radar.core.services.data_quality_exceptions import list_exceptions

        check_edital_temporal_quality(
            subject_id="finep:601", deadline=date(2026, 12, 31), status="aberta", as_of=_AS_OF,
        )
        assert list_exceptions() == []


# ===========================================================================
# Encerrado coerente
# ===========================================================================


class TestClosedDeadline:
    def test_past_deadline_closed_no_exception(self):
        assert detect_temporal_exception(
            subject_id="finep:700", deadline=date(2026, 1, 31), status="encerrada", as_of=_AS_OF,
        ) is None

    def test_closed_without_deadline_no_exception(self):
        assert detect_temporal_exception(
            subject_id="finep:701", deadline=None, status="encerrada", as_of=_AS_OF,
        ) is None

    def test_check_does_not_persist(self, fs):
        from radar.core.services.data_quality_exceptions import list_exceptions

        check_edital_temporal_quality(
            subject_id="finep:700", deadline=date(2026, 1, 31), status="encerrada", as_of=_AS_OF,
        )
        assert list_exceptions() == []


# ===========================================================================
# Contínuo — EvidenceRef genérico não vira continuidade
# ===========================================================================


class TestContinuous:
    def test_continuous_with_explicit_evidence_no_exception(self):
        """Explicit continuous_evidence allows continuous mode (active)."""
        assert detect_temporal_exception(
            subject_id="finep:800", deadline=None, status="aberta", as_of=_AS_OF,
            continuous_evidence=make_ref("a"),
        ) is None

    def test_generic_evidence_ref_not_continuous(self):
        """Generic evidence_refs are NOT passed to evaluate_temporal as continuous."""
        exc = detect_temporal_exception(
            subject_id="finep:801", deadline=None, status="aberta", as_of=_AS_OF,
            evidence_refs=[make_ref("b")],
        )
        assert exc is not None
        assert exc.issue_code is IssueCode.TEMPORAL_STATUS_WITHOUT_BASIS

    def test_continuous_without_evidence_opens_exception(self):
        exc = detect_temporal_exception(
            subject_id="finep:802", deadline=None, status="aberta", as_of=_AS_OF,
        )
        assert exc is not None
        assert exc.issue_code is IssueCode.TEMPORAL_STATUS_WITHOUT_BASIS

    def test_status_fluxo_continuo_alone_not_evidence(self):
        exc = detect_temporal_exception(
            subject_id="finep:803", deadline=None, status="fluxo_continuo", as_of=_AS_OF,
        )
        assert exc is not None
        assert exc.issue_code is IssueCode.CRITICAL_FACT_MISSING


# ===========================================================================
# Conflito
# ===========================================================================


class TestConflict:
    def test_future_deadline_with_closed_status(self):
        exc = detect_temporal_exception(
            subject_id="finep:500", deadline=date(2026, 12, 31), status="encerrada", as_of=_AS_OF,
        )
        assert exc is not None
        assert exc.issue_code is IssueCode.TEMPORAL_STATUS_CONFLICT

    def test_past_deadline_with_open_status(self):
        exc = detect_temporal_exception(
            subject_id="finep:501", deadline=date(2024, 1, 31), status="aberta", as_of=_AS_OF,
        )
        assert exc is not None
        assert exc.issue_code is IssueCode.TEMPORAL_STATUS_CONFLICT

    def test_continuous_evidence_with_deadline(self):
        exc = detect_temporal_exception(
            subject_id="finep:502", deadline=date(2026, 12, 31), status="aberta", as_of=_AS_OF,
            continuous_evidence=make_ref("c"),
        )
        assert exc is not None
        assert exc.issue_code is IssueCode.TEMPORAL_STATUS_CONFLICT

    def test_check_persists_conflict(self, fs):
        from radar.core.services.data_quality_exceptions import list_exceptions

        check_edital_temporal_quality(
            subject_id="finep:500", deadline=date(2026, 12, 31), status="encerrada", as_of=_AS_OF,
        )
        rows = list_exceptions()
        assert len(rows) == 1
        assert rows[0]["issue_code"] == "temporal_status_conflict"


# ===========================================================================
# Evidência ausente não é fabricada
# ===========================================================================


class TestEvidenceNotFabricated:
    def test_no_refs_when_not_passed(self):
        exc = detect_temporal_exception(
            subject_id="finep:1000", deadline=None, status="aberta", as_of=_AS_OF,
        )
        assert exc is not None
        assert exc.evidence_refs == []

    def test_no_refs_when_none_passed(self):
        exc = detect_temporal_exception(
            subject_id="finep:1001", deadline=None, status="aberta", as_of=_AS_OF,
            evidence_refs=None, continuous_evidence=None,
        )
        assert exc is not None
        assert exc.evidence_refs == []

    def test_continuous_evidence_included_in_exception(self):
        ref = make_ref("d")
        exc = detect_temporal_exception(
            subject_id="finep:1002", deadline=date(2026, 12, 31), status="aberta", as_of=_AS_OF,
            continuous_evidence=ref,
        )
        assert exc is not None
        assert len(exc.evidence_refs) == 1
        assert exc.evidence_refs[0].canonical_content_hash == ref.canonical_content_hash

    def test_evidence_refs_preserved(self):
        ref = make_ref("e")
        exc = detect_temporal_exception(
            subject_id="finep:1003", deadline=None, status="aberta", as_of=_AS_OF,
            evidence_refs=[ref],
        )
        assert exc is not None
        assert len(exc.evidence_refs) == 1
        assert exc.evidence_refs[0].canonical_content_hash == ref.canonical_content_hash

    def test_continuous_and_evidence_refs_both_preserved(self):
        ce = make_ref("f", document="continuous.html")
        er = make_ref("g", document="generic.html")
        exc = detect_temporal_exception(
            subject_id="finep:1004", deadline=date(2026, 12, 31), status="aberta", as_of=_AS_OF,
            continuous_evidence=ce, evidence_refs=[er],
        )
        assert exc is not None
        assert len(exc.evidence_refs) == 2
        docs = {r.document for r in exc.evidence_refs}
        assert docs == {"continuous.html", "generic.html"}

    def test_no_bundle_hash_when_not_passed(self):
        exc = detect_temporal_exception(
            subject_id="finep:1005", deadline=None, status="aberta", as_of=_AS_OF,
        )
        assert exc is not None
        assert exc.bundle_hash is None

    def test_no_duplicate_refs(self):
        ref = make_ref("h")
        exc = detect_temporal_exception(
            subject_id="finep:1006", deadline=date(2026, 12, 31), status="aberta", as_of=_AS_OF,
            continuous_evidence=ref, evidence_refs=[ref],
        )
        assert exc is not None
        assert len(exc.evidence_refs) == 1


# ===========================================================================
# Storage falhando
# ===========================================================================


class TestStorageFailure:
    def test_storage_error_does_not_raise(self, fs):
        fs._fail_next_insert = type("APIError", (Exception,), {
            "code": "PGRST116", "args": ("syntax error",),
        })()
        check_edital_temporal_quality(
            subject_id="finep:2000", deadline=None, status="ABERTA", as_of=_AS_OF,
        )

    def test_storage_error_does_not_crash_ingest(self, fs, monkeypatch):
        import radar.core.services.data_quality_exceptions as dq
        from radar.core.services.data_quality_exceptions import DataQualityStorageError

        monkeypatch.setattr(dq, "open_or_observe_exception", lambda *a, **kw: (_ for _ in ()).throw(DataQualityStorageError("simulated")))
        check_edital_temporal_quality(
            subject_id="finep:2001", deadline=None, status="ABERTA", as_of=_AS_OF,
        )

    def test_exception_does_not_leak_raw_message(self, fs):
        import logging
        from io import StringIO

        stream = StringIO()
        handler = logging.StreamHandler(stream)
        logger = logging.getLogger("radar.core.services.temporal_quality")
        logger.addHandler(handler)
        logger.setLevel(logging.WARNING)

        fs._fail_next_insert = type("APIError", (Exception,), {
            "code": "PGRST116", "args": ("syntax error",),
        })()
        check_edital_temporal_quality(
            subject_id="finep:2002", deadline=None, status="ABERTA", as_of=_AS_OF,
        )
        logger.removeHandler(handler)
        log_text = stream.getvalue()
        assert "category=" in log_text
        assert "subject=finep:2002" in log_text
        assert "syntax error" not in log_text
        assert "PGRST116" not in log_text

    def test_gold_status_deadline_unchanged_with_detector_failing(self, fs, monkeypatch):
        import radar.core.services.temporal_quality as tq
        from radar.core.kg import gold
        from tests.helpers.gold_projection import DEFAULT_FIXTURES_DIR, GoldCaptureHarness

        monkeypatch.setattr(tq, "check_edital_temporal_quality", lambda **kw: None)
        harness = GoldCaptureHarness()
        harness.apply_patches(monkeypatch, DEFAULT_FIXTURES_DIR)
        gold.ingest_all(skip_unchanged=True, sources=["edital"])
        entity = harness.projection.entities.get("edital|finep|finep:602")
        assert entity is not None
        assert entity.get("status") == "aberta"
        assert entity.get("deadline") is None

    def test_detector_enabled_output_unchanged(self, fs, monkeypatch):
        from radar.core.kg import gold
        from tests.helpers.gold_projection import DEFAULT_FIXTURES_DIR, GoldCaptureHarness

        harness = GoldCaptureHarness()
        harness.apply_patches(monkeypatch, DEFAULT_FIXTURES_DIR)
        gold.ingest_all(skip_unchanged=True, sources=["edital"])
        entity = harness.projection.entities.get("edital|finep|finep:602")
        assert entity is not None
        assert entity.get("status") == "aberta"
        assert entity.get("deadline") is None


# ===========================================================================
# Supabase ausente
# ===========================================================================


class TestSupabaseAbsent:
    def test_noop_when_supabase_not_configured(self, monkeypatch):
        monkeypatch.delenv("SUPABASE_URL", raising=False)
        monkeypatch.delenv("SUPABASE_SERVICE_KEY", raising=False)
        check_edital_temporal_quality(
            subject_id="finep:3000", deadline=None, status="ABERTA", as_of=_AS_OF,
        )

    def test_detector_still_returns_exception(self):
        exc = detect_temporal_exception(
            subject_id="finep:3001", deadline=None, status="aberta", as_of=_AS_OF,
        )
        assert exc is not None
        assert exc.issue_code is IssueCode.TEMPORAL_STATUS_WITHOUT_BASIS


# ===========================================================================
# Shadow invariant
# ===========================================================================


class TestShadowInvariant:
    def test_no_side_effects_on_gold_data(self, fs, monkeypatch):
        from radar.core.kg import gold
        from tests.helpers.gold_projection import DEFAULT_FIXTURES_DIR, GoldCaptureHarness

        harness = GoldCaptureHarness()
        harness.apply_patches(monkeypatch, DEFAULT_FIXTURES_DIR)
        gold.ingest_all(skip_unchanged=True, sources=["edital"])
        entity = harness.projection.entities.get("edital|finep|finep:602")
        assert entity is not None
        assert entity.get("status") == "aberta"
        assert entity.get("deadline") is None


# ===========================================================================
# Integration: detector is called during gold._ingest_editais
# ===========================================================================


class TestGoldIntegration:
    def test_detector_called_during_ingest(self, monkeypatch, fs):
        from radar.core.services import temporal_quality as tq_module
        captured = []

        def _capture(**kw):
            captured.append(kw)

        monkeypatch.setattr(tq_module, "check_edital_temporal_quality", _capture)
        from radar.core.kg import gold
        from tests.helpers.gold_projection import DEFAULT_FIXTURES_DIR, GoldCaptureHarness

        harness = GoldCaptureHarness()
        harness.apply_patches(monkeypatch, DEFAULT_FIXTURES_DIR)
        gold.ingest_all(skip_unchanged=True, sources=["edital"])
        assert len(captured) > 0
        finep_calls = [c for c in captured if "finep:602" in c.get("subject_id", "")]
        assert len(finep_calls) >= 1

    def test_detector_called_after_entity_commit(self, monkeypatch, fs):
        from radar.core.kg import gold
        from radar.core.services import temporal_quality as tq_module
        from tests.helpers.gold_projection import DEFAULT_FIXTURES_DIR, GoldCaptureHarness

        harness = GoldCaptureHarness()
        harness.apply_patches(monkeypatch, DEFAULT_FIXTURES_DIR)

        call_order = []
        stub_upsert = gold._upsert_entity

        def _track_upsert(cur, **f):
            call_order.append(f"upsert:{f.get('native_id')}")
            return stub_upsert(cur, **f)

        monkeypatch.setattr(gold, "_upsert_entity", _track_upsert)
        monkeypatch.setattr(tq_module, "check_edital_temporal_quality", lambda **kw: call_order.append(f"temporal:{kw.get('subject_id')}"))

        gold.ingest_all(skip_unchanged=True, sources=["edital"])
        for tc in [c for c in call_order if c.startswith("temporal:")]:
            native_id = tc.replace("temporal:", "")
            assert any(c == f"upsert:{native_id}" for c in call_order[:call_order.index(tc)]), f"temporal call for {native_id} before upsert"

    def test_detector_receives_raw_status(self, monkeypatch, fs):
        """Prove that raw_status is passed to detector, not normalized status."""
        from radar.core.services import temporal_quality as tq_module
        captured = []

        def _capture(**kw):
            captured.append(kw)

        monkeypatch.setattr(tq_module, "check_edital_temporal_quality", _capture)
        from radar.core.kg import gold
        from tests.helpers.gold_projection import DEFAULT_FIXTURES_DIR, GoldCaptureHarness

        harness = GoldCaptureHarness()
        harness.apply_patches(monkeypatch, DEFAULT_FIXTURES_DIR)
        gold.ingest_all(skip_unchanged=True, sources=["edital"])
        finep_call = next(c for c in captured if c["subject_id"] == "finep:602")
        assert finep_call["status"] == "ABERTA"


# ===========================================================================
# Raw conflict through gold pipeline
# ===========================================================================


class TestRawConflictGoldIntegration:
    def _ingest_finep_602(self, monkeypatch, bronze_record):
        from radar.core.kg import gold
        from tests.helpers.gold_projection import DEFAULT_FIXTURES_DIR, GoldCaptureHarness

        harness = GoldCaptureHarness()
        harness.apply_patches(monkeypatch, DEFAULT_FIXTURES_DIR)
        orig_bronze = gold._bronze_record

        def _patched_bronze(src, st):
            if src == "finep" and st == "602":
                return bronze_record
            return orig_bronze(src, st)

        monkeypatch.setattr(gold, "_bronze_record", _patched_bronze)
        gold.ingest_all(skip_unchanged=True, sources=["edital"])
        return harness.projection.entities["edital|finep|finep:602"]

    def test_raw_aberta_past_deadline_opens_conflict(self, fs, monkeypatch):
        """raw ABERTA + prazo passado → temporal_status_conflict."""
        from radar.core.services.data_quality_exceptions import list_exceptions

        bronze_rec = {
            "chamada_id": "602",
            "status": "ABERTA",
            "prazo_envio": "01/01/2024",
            "titulo": "Edital ABERTA com prazo passado",
        }
        entity = self._ingest_finep_602(monkeypatch, bronze_rec)
        assert entity["status"] == "encerrada"
        assert entity["deadline"] == "2024-01-01"
        assert "raw_status" not in entity
        assert "raw_status" not in entity["metadata"]

        rows = [r for r in list_exceptions() if r["subject_id"] == "finep:602"]
        assert len(rows) == 1
        assert rows[0]["issue_code"] == "temporal_status_conflict"

    def test_raw_encerrada_future_deadline_opens_conflict(self, fs, monkeypatch):
        """raw ENCERRADA + prazo futuro → temporal_status_conflict."""
        from radar.core.services.data_quality_exceptions import list_exceptions

        bronze_rec = {
            "chamada_id": "602",
            "status": "ENCERRADA",
            "prazo_envio": "31/12/2099",
            "titulo": "Edital ENCERRADA com prazo futuro",
        }
        entity = self._ingest_finep_602(monkeypatch, bronze_rec)
        assert entity["status"] == "aberta"
        assert entity["deadline"] == "2099-12-31"
        assert "raw_status" not in entity
        assert "raw_status" not in entity["metadata"]

        rows = [r for r in list_exceptions() if r["subject_id"] == "finep:602"]
        assert len(rows) == 1
        assert rows[0]["issue_code"] == "temporal_status_conflict"


# ===========================================================================
# Produced value & producer version
# ===========================================================================


class TestProducedValue:
    def test_produced_value_is_deadline_when_present(self):
        exc = detect_temporal_exception(
            subject_id="finep:4000", deadline=date(2024, 1, 31), status="aberta", as_of=_AS_OF,
        )
        assert exc is not None
        assert exc.produced_value == "2024-01-31"

    def test_produced_value_is_status_when_no_deadline(self):
        exc = detect_temporal_exception(
            subject_id="finep:4001", deadline=None, status="aberta", as_of=_AS_OF,
        )
        assert exc is not None
        assert exc.produced_value == "aberta"

    def test_produced_value_unknown_when_neither(self):
        exc = detect_temporal_exception(
            subject_id="finep:4002", deadline=None, status=None, as_of=_AS_OF,
        )
        assert exc is not None
        assert exc.produced_value == "unknown"


class TestProducerVersion:
    def test_version_constant(self):
        assert DETECTOR_PRODUCER_VERSION == "temporal_quality:v1"

    def test_exception_has_producer_version(self):
        exc = detect_temporal_exception(
            subject_id="finep:5000", deadline=None, status="aberta", as_of=_AS_OF,
        )
        assert exc is not None
        assert exc.producer_version == "temporal_quality:v1"


# ===========================================================================
# Fingerprint in exception
# ===========================================================================


class TestFingerprintInException:
    def test_fingerprint_present(self):
        exc = detect_temporal_exception(
            subject_id="finep:6000", deadline=None, status="aberta", as_of=_AS_OF,
        )
        assert exc is not None
        assert exc.input_fingerprint.startswith("sha256:")

    def test_fingerprint_deterministic(self):
        exc1 = detect_temporal_exception(
            subject_id="finep:6001", deadline=None, status="aberta", as_of=_AS_OF,
        )
        exc2 = detect_temporal_exception(
            subject_id="finep:6001", deadline=None, status="aberta", as_of=_AS_OF,
        )
        assert exc1.input_fingerprint == exc2.input_fingerprint

    def test_fingerprint_changes_with_deadline(self):
        exc1 = detect_temporal_exception(
            subject_id="finep:6002", deadline=None, status="aberta", as_of=_AS_OF,
        )
        exc2 = detect_temporal_exception(
            subject_id="finep:6002", deadline=date(2024, 1, 31), status="aberta", as_of=_AS_OF,
        )
        assert exc1.input_fingerprint != exc2.input_fingerprint

    def test_aberta_and_aberta_same_fingerprint(self):
        exc1 = detect_temporal_exception(
            subject_id="finep:6003", deadline=None, status="aberta", as_of=_AS_OF,
        )
        exc2 = detect_temporal_exception(
            subject_id="finep:6003", deadline=None, status="ABERTA", as_of=_AS_OF,
        )
        assert exc1.input_fingerprint == exc2.input_fingerprint


# ===========================================================================
# as_of default
# ===========================================================================


class TestAsOfDefault:
    def test_check_uses_brasilia_default(self, monkeypatch):
        from radar.core.services import temporal_quality as tq
        captured = {}

        def _capture(**kw):
            captured.update(kw)
            return None

        monkeypatch.setattr(tq, "detect_temporal_exception", _capture)

        check_edital_temporal_quality(
            subject_id="finep:7000", deadline=None, status="aberta",
        )
        assert "as_of" in captured
        assert isinstance(captured["as_of"], date)
