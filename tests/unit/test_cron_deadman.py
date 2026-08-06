from datetime import datetime, timedelta, timezone

from scripts import cron_deadman


class _Cursor:
    def __init__(self, db):
        self.db = db
        self.description = []
        self._rows = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql, params=()):
        normalized = " ".join(sql.split()).lower()
        if normalized.startswith("select task,completed_at,status"):
            self.description = [type("D", (), {"name": key}) for key in ("task", "completed_at", "status")]
            self._rows = [(r["task"], r["completed_at"], r["status"]) for r in self.db.runs]
        elif "from public.procrastinate_jobs" in normalized:
            self.description = [type("D", (), {"name": key}) for key in ("id", "task_name", "status", "attempts", "worker_id", "started_at")]
            self._rows = [(r["id"], r["task_name"], r["status"], r["attempts"], r["worker_id"], r["started_at"]) for r in self.db.jobs]
            assert "j.started_at" not in normalized
            assert "procrastinate_events" in normalized
        elif normalized.startswith("select id,last_heartbeat"):
            self.description = [type("D", (), {"name": key}) for key in ("id", "last_heartbeat")]
            self._rows = [(r["id"], r["last_heartbeat"]) for r in self.db.workers]
        else:
            self.description = []
            self._rows = []

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return None


class _DB:
    def __init__(self, now):
        self.runs = []
        self.jobs = [
            {"id": 1, "task_name": "run_daily_etl", "status": "doing", "attempts": 1,
             "worker_id": "w1", "started_at": now - timedelta(minutes=5)},
            {"id": 2, "task_name": "run_daily_etl", "status": "doing", "attempts": 1,
             "worker_id": "w2", "started_at": now - timedelta(minutes=120)},
        ]
        self.workers = [{"id": "w1", "last_heartbeat": now}, {"id": "w2", "last_heartbeat": now - timedelta(minutes=120)}]

    def cursor(self):
        return _Cursor(self)

    def commit(self):
        pass


def test_deadman_uses_real_job_event_schema_and_distinguishes_recent_doing(monkeypatch):
    now = datetime.now(timezone.utc)
    db = _DB(now)
    alerts = []
    monkeypatch.setattr(cron_deadman, "observe_incident", lambda *args, **kwargs: False)
    monkeypatch.setattr(cron_deadman, "send_alert", lambda subject, body: alerts.append((subject, body)))
    result = cron_deadman.run(db, now)
    assert result["jobs_checked"] == 2
    assert result["workers_stale"] == 1
