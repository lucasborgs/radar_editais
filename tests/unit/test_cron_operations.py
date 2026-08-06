from datetime import datetime, timedelta, timezone

from radar.core.services.cron_operations import (
    classify_jobs,
    dead_man_report,
    safe_counters,
    safe_error,
)


class _PersistedQuery:
    def __init__(self, db):
        self.db = db
        self.is_upsert = False

    def select(self, *_args):
        return self

    def eq(self, field, value):
        self.db.filters[field] = value
        return self

    def maybe_single(self):
        return self

    def upsert(self, data, *, on_conflict, ignore_duplicates):
        self.db.upserts.append((data, on_conflict, ignore_duplicates))
        self.is_upsert = True
        return self

    def update(self, *_args):
        return self

    def execute(self):
        if self.is_upsert:
            return type("R", (), {"data": None})()
        return type("R", (), {"data": {"id": self.db.persisted_id}})()


class _LedgerDB:
    def __init__(self, persisted_id="persisted"):
        self.persisted_id = persisted_id
        self.upserts = []
        self.filters = {}

    def table(self, _name):
        return _PersistedQuery(self)


def test_start_cron_uses_atomic_upsert_and_returns_persisted_id():
    from radar.core.services.cron_operations import start_cron

    db = _LedgerDB()
    assert start_cron(db, task="run_daily_etl", scheduled_at=1) == "persisted"
    assert len(db.upserts) == 1
    assert db.upserts[0][1:] == ("task,scheduled_at", True)


def test_safe_error_and_counters_redact_sensitive_values():
    assert "123456780001" not in safe_error("cnpj=12345678000199")
    assert safe_counters({"ok": 2, "bad-key": 4, "secret": "x"}) == {"ok": 2}

def test_dead_man_detects_missing_success():
    report = dead_man_report([], datetime.now(timezone.utc))
    assert len(report) == 3 and all(r["late"] for r in report)

def test_dead_man_accepts_recent_success():
    now = datetime.now(timezone.utc)
    rows = [{"task": "run_daily_etl", "status": "succeeded", "scheduled_at": now.isoformat(), "completed_at": (now - timedelta(minutes=1)).isoformat()}]
    item = next(r for r in dead_man_report(rows, now) if r["task"] == "run_daily_etl")
    assert item["late"] is False

def test_panel_distinguishes_recent_doing_from_stuck():
    now = datetime.now(timezone.utc)
    jobs = [
        {"id": "recent", "status": "doing", "worker_id": "w1"},
        {"id": "stuck", "status": "doing", "worker_id": "w2"},
    ]
    events = [
        {"job_id": "recent", "type": "started", "at": (now - timedelta(minutes=1)).isoformat()},
        {"job_id": "stuck", "type": "started", "at": (now - timedelta(minutes=120)).isoformat()},
    ]
    workers = [{"id": "w1", "last_heartbeat": now.isoformat()}, {"id": "w2", "last_heartbeat": (now - timedelta(minutes=120)).isoformat()}]
    result = classify_jobs(jobs, events, workers, now)
    assert result == {"doing": 1, "stuck": 1, "failed": 0}
