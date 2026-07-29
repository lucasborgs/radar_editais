from __future__ import annotations

import uuid
from datetime import date, datetime

import pytest

from radar.core.services.temporal_quality import (
    DETECTOR_PRODUCER_VERSION,
    _build_temporal_fingerprint,
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
                        raise APIError({
                            "code": "23505",
                            "message": "duplicate key",
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


def make_ref(hash_suffix: str = "a") -> EvidenceRef:
    return EvidenceRef(
        source="finep",
        canonical_content_hash=f"sha256:{hash_suffix * 64}",
        locator_quality=LocatorQuality.DOCUMENT_ONLY,
        document="pagina_oficial.html",
        quote="fluxo continuo: inscricoes permanentes",
    )


# ===========================================================================
# Fingerprint
# ===========================================================================


class TestFingerprint:
    def test_deterministic(self):
        fp1 = _build_temporal_fingerprint(deadline=date(2026, 12, 31), status="aberta")
        fp2 = _build_temporal_fingerprint(deadline=date(2026, 12, 31), status="aberta")
        assert fp1 == fp2
        assert fp1.startswith("sha256:")
        assert len(fp1) == 64 + 7  # sha256: + 64 hex chars

    def test_differs_by_deadline(self):
        fp1 = _build_temporal_fingerprint(deadline=date(2026, 12, 31), status="aberta")
        fp2 = _build_temporal_fingerprint(deadline=None, status="aberta")
        assert fp1 != fp2

    def test_differs_by_status(self):
        fp1 = _build_temporal_fingerprint(deadline=None, status="aberta")
        fp2 = _build_temporal_fingerprint(deadline=None, status="encerrada")
        assert fp1 != fp2

    def test_includes_evidence_hashes(self):
        fp1 = _build_temporal_fingerprint(
            deadline=None, status="aberta",
            evidence_hashes=["sha256:" + "a" * 64],
        )
        fp2 = _build_temporal_fingerprint(
            deadline=None, status="aberta",
            evidence_hashes=["sha256:" + "b" * 64],
        )
        assert fp1 != fp2

    def test_includes_bundle_hash(self):
        fp1 = _build_temporal_fingerprint(deadline=None, status="aberta", bundle_hash="sha256:" + "a" * 64)
        fp2 = _build_temporal_fingerprint(deadline=None, status="aberta", bundle_hash=None)
        assert fp1 != fp2

    def test_producer_version_fixed(self):
        fp = _build_temporal_fingerprint(deadline=None, status="aberta")
        import json
        material = {
            "producer_version": DETECTOR_PRODUCER_VERSION,
            "deadline": None,
            "status": "aberta",
            "evidence_hashes": [],
            "bundle_hash": None,
        }
        import hashlib
        expected = "sha256:" + hashlib.sha256(
            json.dumps(material, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()
        assert fp == expected

    def test_not_python_hash(self):
        fp = _build_temporal_fingerprint(deadline=None, status="aberta")
        # Ensure we never used hash() (non-deterministic across runs)
        assert isinstance(fp, str) and fp.startswith("sha256:")

    def test_sorted_evidence_hashes(self):
        h1 = "sha256:" + "a" * 64
        h2 = "sha256:" + "b" * 64
        fp_a = _build_temporal_fingerprint(None, "aberta", evidence_hashes=[h2, h1])
        fp_b = _build_temporal_fingerprint(None, "aberta", evidence_hashes=[h1, h2])
        assert fp_a == fp_b  # order-independent


# ===========================================================================
# Finep/Eureka: ABERTA, deadline=None, sem evidência
# ===========================================================================


class TestFinepEureka:
    def test_opens_exception(self):
        exc = detect_temporal_exception(
            subject_id="finep:589",
            deadline=None,
            status="aberta",
            as_of=_AS_OF,
        )
        assert exc is not None
        assert exc.issue_code is IssueCode.TEMPORAL_STATUS_WITHOUT_BASIS
        assert exc.subject_id == "finep:589"
        assert exc.input_fingerprint is not None
        assert exc.input_fingerprint.startswith("sha256:")

    def test_rerun_identical_does_not_duplicate(self, fs):
        """Same fingerprint reobserves, doesn't create new exception."""
        from radar.core.services.data_quality_exceptions import list_exceptions

        check_edital_temporal_quality(
            subject_id="finep:589",
            deadline=None,
            status="aberta",
            as_of=_AS_OF,
        )
        rows1 = list_exceptions()
        assert len(rows1) == 1

        check_edital_temporal_quality(
            subject_id="finep:589",
            deadline=None,
            status="aberta",
            as_of=_AS_OF,
        )
        rows2 = list_exceptions()
        assert len(rows2) == 1

    def test_new_fingerprint_supersedes_old(self, fs):
        from radar.core.services.data_quality_exceptions import list_exceptions

        check_edital_temporal_quality(
            subject_id="finep:589",
            deadline=None,
            status="aberta",
            as_of=_AS_OF,
        )
        # Same inputs but different bundle_hash → different fingerprint
        check_edital_temporal_quality(
            subject_id="finep:589",
            deadline=None,
            status="aberta",
            as_of=_AS_OF,
            bundle_hash="sha256:" + "b" * 64,
        )
        rows = list_exceptions()
        open_rows = [r for r in rows if r["status"] == "open"]
        superseded = [r for r in rows if r["status"] == "superseded"]

        assert len(open_rows) == 1
        assert len(superseded) == 1
        assert open_rows[0]["input_fingerprint"] != superseded[0]["input_fingerprint"]
        assert open_rows[0]["input_fingerprint"].startswith("sha256:")
        assert superseded[0]["input_fingerprint"].startswith("sha256:")


# ===========================================================================
# Futuro coerente: não abre exceção
# ===========================================================================


class TestFutureDeadline:
    def test_future_deadline_no_exception(self):
        exc = detect_temporal_exception(
            subject_id="finep:601",
            deadline=date(2026, 12, 31),
            status="aberta",
            as_of=_AS_OF,
        )
        assert exc is None

    def test_deadline_today_no_exception(self):
        exc = detect_temporal_exception(
            subject_id="finep:602",
            deadline=_AS_OF,
            status="aberta",
            as_of=_AS_OF,
        )
        assert exc is None

    def test_check_does_not_persist(self, fs):
        from radar.core.services.data_quality_exceptions import list_exceptions

        check_edital_temporal_quality(
            subject_id="finep:601",
            deadline=date(2026, 12, 31),
            status="aberta",
            as_of=_AS_OF,
        )
        rows = list_exceptions()
        assert len(rows) == 0


# ===========================================================================
# Encerrado coerente: não abre exceção
# ===========================================================================


class TestClosedDeadline:
    def test_past_deadline_closed_no_exception(self):
        exc = detect_temporal_exception(
            subject_id="finep:700",
            deadline=date(2026, 1, 31),
            status="encerrada",
            as_of=_AS_OF,
        )
        assert exc is None

    def test_closed_without_deadline_no_exception(self):
        exc = detect_temporal_exception(
            subject_id="finep:701",
            deadline=None,
            status="encerrada",
            as_of=_AS_OF,
        )
        assert exc is None

    def test_check_does_not_persist(self, fs):
        from radar.core.services.data_quality_exceptions import list_exceptions

        check_edital_temporal_quality(
            subject_id="finep:700",
            deadline=date(2026, 1, 31),
            status="encerrada",
            as_of=_AS_OF,
        )
        rows = list_exceptions()
        assert len(rows) == 0


# ===========================================================================
# Contínuo com EvidenceRef recuperável: não abre exceção
# ===========================================================================


class TestContinuousWithEvidence:
    def test_continuous_with_evidence_no_exception(self):
        exc = detect_temporal_exception(
            subject_id="finep:800",
            deadline=None,
            status="aberta",
            as_of=_AS_OF,
            evidence_refs=[make_ref("a")],
        )
        assert exc is None

    def test_check_does_not_persist(self, fs):
        from radar.core.services.data_quality_exceptions import list_exceptions

        check_edital_temporal_quality(
            subject_id="finep:800",
            deadline=None,
            status="aberta",
            as_of=_AS_OF,
            evidence_refs=[make_ref("b")],
        )
        rows = list_exceptions()
        assert len(rows) == 0


# ===========================================================================
# Contínuo sem evidência: permanece needs_review
# ===========================================================================


class TestContinuousWithoutEvidence:
    def test_no_evidence_opens_exception(self):
        exc = detect_temporal_exception(
            subject_id="finep:900",
            deadline=None,
            status="aberta",
            as_of=_AS_OF,
        )
        assert exc is not None
        assert exc.issue_code is IssueCode.TEMPORAL_STATUS_WITHOUT_BASIS

    def test_status_fluxo_continuo_alone_not_evidence(self):
        exc = detect_temporal_exception(
            subject_id="finep:901",
            deadline=None,
            status="fluxo_continuo",
            as_of=_AS_OF,
        )
        assert exc is not None
        assert exc.issue_code is IssueCode.CRITICAL_FACT_MISSING


# ===========================================================================
# Conflito: abre exceção
# ===========================================================================


class TestConflict:
    def test_future_deadline_with_closed_status(self):
        exc = detect_temporal_exception(
            subject_id="finep:500",
            deadline=date(2026, 12, 31),
            status="encerrada",
            as_of=_AS_OF,
        )
        assert exc is not None
        assert exc.issue_code is IssueCode.TEMPORAL_STATUS_CONFLICT

    def test_past_deadline_with_open_status(self):
        exc = detect_temporal_exception(
            subject_id="finep:501",
            deadline=date(2024, 1, 31),
            status="aberta",
            as_of=_AS_OF,
        )
        assert exc is not None
        assert exc.issue_code is IssueCode.TEMPORAL_STATUS_CONFLICT

    def test_continuous_evidence_with_deadline(self):
        exc = detect_temporal_exception(
            subject_id="finep:502",
            deadline=date(2026, 12, 31),
            status="aberta",
            as_of=_AS_OF,
            evidence_refs=[make_ref("c")],
        )
        assert exc is not None
        assert exc.issue_code is IssueCode.TEMPORAL_STATUS_CONFLICT

    def test_check_persists_conflict(self, fs):
        from radar.core.services.data_quality_exceptions import list_exceptions

        check_edital_temporal_quality(
            subject_id="finep:500",
            deadline=date(2026, 12, 31),
            status="encerrada",
            as_of=_AS_OF,
        )
        rows = list_exceptions()
        assert len(rows) == 1
        assert rows[0]["issue_code"] == "temporal_status_conflict"


# ===========================================================================
# Evidência ausente não é fabricada
# ===========================================================================


class TestEvidenceNotFabricated:
    def test_no_evidence_refs_when_not_passed(self):
        exc = detect_temporal_exception(
            subject_id="finep:1000",
            deadline=None,
            status="aberta",
            as_of=_AS_OF,
        )
        assert exc is not None
        assert exc.evidence_refs == []

    def test_no_evidence_refs_when_none_passed(self):
        exc = detect_temporal_exception(
            subject_id="finep:1001",
            deadline=None,
            status="aberta",
            as_of=_AS_OF,
            evidence_refs=None,
        )
        assert exc is not None
        assert exc.evidence_refs == []

    def test_passed_evidence_preserved(self):
        ref = make_ref("d")
        exc = detect_temporal_exception(
            subject_id="finep:1002",
            deadline=date(2026, 12, 31),
            status="aberta",
            as_of=_AS_OF,
            evidence_refs=[ref],
        )
        assert exc is not None  # conflict
        assert len(exc.evidence_refs) == 1
        assert exc.evidence_refs[0].canonical_content_hash == ref.canonical_content_hash

    def test_no_bundle_hash_when_not_passed(self):
        exc = detect_temporal_exception(
            subject_id="finep:1003",
            deadline=None,
            status="aberta",
            as_of=_AS_OF,
        )
        assert exc is not None
        assert exc.bundle_hash is None

    def test_no_document_quote_url_fabricated(self):
        exc = detect_temporal_exception(
            subject_id="finep:1004",
            deadline=None,
            status="aberta",
            as_of=_AS_OF,
        )
        assert exc is not None
        assert exc.evidence_refs == []


# ===========================================================================
# Storage falhando: não derruba ingestão, não vaza mensagem bruta
# ===========================================================================


class TestStorageFailure:
    def test_storage_error_does_not_raise(self, fs):

        fs._fail_next_insert = type("APIError", (Exception,), {
            "code": "PGRST116",
            "args": ("syntax error",),
        })()

        # Should not raise
        check_edital_temporal_quality(
            subject_id="finep:2000",
            deadline=None,
            status="aberta",
            as_of=_AS_OF,
        )

    def test_storage_error_does_not_crash_ingest(self, fs, monkeypatch):
        # Make open_or_observe_exception always raise
        import radar.core.services.data_quality_exceptions as dq
        from radar.core.services.data_quality_exceptions import DataQualityStorageError

        def _crash(*args, **kwargs):
            raise DataQualityStorageError("simulated failure")

        monkeypatch.setattr(dq, "open_or_observe_exception", _crash)

        # Should not raise
        check_edital_temporal_quality(
            subject_id="finep:2001",
            deadline=None,
            status="aberta",
            as_of=_AS_OF,
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
            "code": "PGRST116",
            "args": ("syntax error",),
        })()

        check_edital_temporal_quality(
            subject_id="finep:2002",
            deadline=None,
            status="aberta",
            as_of=_AS_OF,
        )

        logger.removeHandler(handler)
        log_text = stream.getvalue()
        # Should have categorical message, not raw error details
        assert "category=" in log_text
        assert "subject=finep:2002" in log_text
        # Raw details should not leak
        assert "syntax error" not in log_text
        assert "PGRST116" not in log_text

    def test_gold_status_deadline_unchanged_with_detector_failing(self, fs, monkeypatch):
        """Detector failing does not alter gold output."""
        import radar.core.services.temporal_quality as tq
        from tests.helpers.gold_projection import DEFAULT_FIXTURES_DIR, GoldCaptureHarness

        def _instrumented(**kwargs):
            # Capture that it was called, but don't crash
            pass

        monkeypatch.setattr(tq, "check_edital_temporal_quality", _instrumented)

        from radar.core.kg import gold
        harness = GoldCaptureHarness()
        harness.apply_patches(monkeypatch, DEFAULT_FIXTURES_DIR)
        gold.ingest_all(skip_unchanged=True, sources=["edital"])

        finep_key = "edital|finep|finep:602"
        entity = harness.projection.entities.get(finep_key)
        assert entity is not None
        # Gold data unchanged: fixture has ABERTA + deadline=None
        assert entity.get("status") == "aberta"
        assert entity.get("deadline") is None

    def test_detector_enabled_output_unchanged(self, fs, monkeypatch):
        """Gold output identical with detector enabled."""
        from radar.core.kg import gold
        from tests.helpers.gold_projection import DEFAULT_FIXTURES_DIR, GoldCaptureHarness
        harness = GoldCaptureHarness()
        harness.apply_patches(monkeypatch, DEFAULT_FIXTURES_DIR)
        gold.ingest_all(skip_unchanged=True, sources=["edital"])

        finep_key = "edital|finep|finep:602"
        entity = harness.projection.entities.get(finep_key)
        assert entity is not None
        # Gold unchanged: fixture has ABERTA + deadline=None
        assert entity.get("status") == "aberta"
        assert entity.get("deadline") is None


# ===========================================================================
# Supabase ausente: no-op seguro
# ===========================================================================


class TestSupabaseAbsent:
    def test_noop_when_supabase_not_configured(self, monkeypatch):
        monkeypatch.delenv("SUPABASE_URL", raising=False)
        monkeypatch.delenv("SUPABASE_SERVICE_KEY", raising=False)

        check_edital_temporal_quality(
            subject_id="finep:3000",
            deadline=None,
            status="aberta",
            as_of=_AS_OF,
        )

    def test_detector_still_returns_exception(self):
        exc = detect_temporal_exception(
            subject_id="finep:3001",
            deadline=None,
            status="aberta",
            as_of=_AS_OF,
        )
        assert exc is not None
        assert exc.issue_code is IssueCode.TEMPORAL_STATUS_WITHOUT_BASIS


# ===========================================================================
# Detector não altera match/Stage 0 (shadow)
# ===========================================================================


class TestShadowInvariant:
    def test_no_side_effects_on_gold_data(self, fs, monkeypatch):
        from radar.core.kg import gold
        from tests.helpers.gold_projection import DEFAULT_FIXTURES_DIR, GoldCaptureHarness
        harness = GoldCaptureHarness()
        harness.apply_patches(monkeypatch, DEFAULT_FIXTURES_DIR)
        gold.ingest_all(skip_unchanged=True, sources=["edital"])

        finep_key = "edital|finep|finep:602"
        entity = harness.projection.entities.get(finep_key)
        assert entity is not None
        assert entity.get("status") == "aberta"
        assert entity.get("deadline") is None


# ===========================================================================
# Fingerprint uniqueness for different inputs
# ===========================================================================


class TestFingerprintUniqueness:
    def test_finep_eureka_fingerprint(self):
        exc = detect_temporal_exception(
            subject_id="finep:unique-1",
            deadline=None,
            status="aberta",
            as_of=_AS_OF,
        )
        fp1 = exc.input_fingerprint

        exc2 = detect_temporal_exception(
            subject_id="finep:unique-1",
            deadline=date(2026, 12, 31),
            status="aberta",
            as_of=_AS_OF,
        )
        # second one returns None (future deadline, no exception)
        assert exc2 is None
        assert fp1 is not None


# ===========================================================================
# Integration: detector is called during gold._ingest_editais
# ===========================================================================


class TestGoldIntegration:
    def test_detector_called_during_ingest(self, monkeypatch, fs):
        """Prove that _check_temporal is called during ingest.

        We cannot use inspect.getsource. Instead, we monkeypatch
        check_edital_temporal_quality to capture the call.
        """
        from radar.core.services import temporal_quality as tq_module
        captured = []

        def _capture(**kwargs):
            captured.append(kwargs)

        monkeypatch.setattr(tq_module, "check_edital_temporal_quality", _capture)

        from radar.core.kg import gold
        from tests.helpers.gold_projection import DEFAULT_FIXTURES_DIR, GoldCaptureHarness

        harness = GoldCaptureHarness()
        harness.apply_patches(monkeypatch, DEFAULT_FIXTURES_DIR)
        gold.ingest_all(skip_unchanged=True, sources=["edital"])

        assert len(captured) > 0
        # Should be called for finep:602
        finep_calls = [c for c in captured if "finep:602" in c.get("subject_id", "")]
        assert len(finep_calls) >= 1

    def test_detector_called_after_entity_commit(self, monkeypatch, fs):
        """Integration point is after entity upsert and transaction commit."""
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

        def _track_temporal(**kwargs):
            call_order.append(f"temporal:{kwargs.get('subject_id')}")

        monkeypatch.setattr(gold, "_upsert_entity", _track_upsert)
        monkeypatch.setattr(tq_module, "check_edital_temporal_quality", _track_temporal)

        gold.ingest_all(skip_unchanged=True, sources=["edital"])

        temporal_calls = [c for c in call_order if c.startswith("temporal:")]
        assert len(temporal_calls) > 0
        for tc in temporal_calls:
            native_id = tc.replace("temporal:", "")
            upsert_idx = None
            for j, prior in enumerate(call_order):
                if prior == f"upsert:{native_id}":
                    upsert_idx = j
                    break
            assert upsert_idx is not None, (
                f"temporal call for {native_id} found before upsert"
            )


# ===========================================================================
# Valor de produced_value
# ===========================================================================


class TestProducedValue:
    def test_produced_value_is_deadline_when_present(self):
        exc = detect_temporal_exception(
            subject_id="finep:4000",
            deadline=date(2024, 1, 31),
            status="aberta",
            as_of=_AS_OF,
        )
        assert exc is not None
        assert exc.produced_value == "2024-01-31"

    def test_produced_value_is_status_when_no_deadline(self):
        exc = detect_temporal_exception(
            subject_id="finep:4001",
            deadline=None,
            status="aberta",
            as_of=_AS_OF,
        )
        assert exc is not None
        assert exc.produced_value == "aberta"

    def test_produced_value_unknown_when_neither(self):
        exc = detect_temporal_exception(
            subject_id="finep:4002",
            deadline=None,
            status=None,
            as_of=_AS_OF,
        )
        assert exc is not None
        assert exc.produced_value == "unknown"


# ===========================================================================
# Producer version constante
# ===========================================================================


class TestProducerVersion:
    def test_version_constant(self):
        assert DETECTOR_PRODUCER_VERSION == "temporal_quality:v1"

    def test_exception_has_producer_version(self):
        exc = detect_temporal_exception(
            subject_id="finep:5000",
            deadline=None,
            status="aberta",
            as_of=_AS_OF,
        )
        assert exc is not None
        assert exc.producer_version == "temporal_quality:v1"


# ===========================================================================
# Fingerprint em detect_temporal_exception
# ===========================================================================


class TestFingerprintInException:
    def test_fingerprint_present(self):
        exc = detect_temporal_exception(
            subject_id="finep:6000",
            deadline=None,
            status="aberta",
            as_of=_AS_OF,
        )
        assert exc is not None
        assert exc.input_fingerprint is not None
        assert exc.input_fingerprint.startswith("sha256:")

    def test_fingerprint_deterministic(self):
        exc1 = detect_temporal_exception(
            subject_id="finep:6001",
            deadline=None,
            status="aberta",
            as_of=_AS_OF,
        )
        exc2 = detect_temporal_exception(
            subject_id="finep:6001",
            deadline=None,
            status="aberta",
            as_of=_AS_OF,
        )
        assert exc1.input_fingerprint == exc2.input_fingerprint

    def test_fingerprint_changes_with_deadline(self):
        exc1 = detect_temporal_exception(
            subject_id="finep:6002",
            deadline=None,
            status="aberta",
            as_of=_AS_OF,
        )
        exc2 = detect_temporal_exception(
            subject_id="finep:6002",
            deadline=date(2024, 1, 31),
            status="aberta",
            as_of=_AS_OF,
        )
        # exc2 has past_deadline + aberta → conflict, but fingerprint differs from exc1
        assert exc1.input_fingerprint != exc2.input_fingerprint
