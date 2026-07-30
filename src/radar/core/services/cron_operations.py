"""Ledger e dead-man dos CRONs críticos.

Best-effort no worker: uma falha de observabilidade nunca mascara a falha do
job. Os payloads aceitos são apenas contadores e identificadores seguros.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from radar.core.infra.notify import send_alert

logger = logging.getLogger(__name__)
CRON_TASKS = ("run_daily_etl", "discover_opportunities", "warm_edital_chunks")
SLA_MINUTES = {"run_daily_etl": 30 * 60, "discover_opportunities": 30 * 60, "warm_edital_chunks": 3 * 60 * 60}
STUCK_MINUTES = int(os.getenv("CRON_STUCK_MINUTES", "90"))
_SAFE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def safe_error(exc: BaseException | str, limit: int = 240) -> str:
    raw = str(exc) if isinstance(exc, BaseException) else str(exc)
    raw = re.sub(r"(?i)(api[_-]?key|token|password|secret|cnpj|cpf)\s*[:=]\s*[^,;\s]+", r"\1=[redacted]", raw)
    raw = re.sub(r"\b\d{14}\b", "[cnpj]", raw)
    return raw[:limit]


def safe_counters(counters: dict[str, Any] | None) -> dict[str, int | float]:
    out: dict[str, int | float] = {}
    for k, v in (counters or {}).items():
        if isinstance(k, str) and _SAFE.match(k) and isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0:
            out[k] = v
    return out


def _job_id(task: str, scheduled_at: int) -> str:
    return f"{task}:{scheduled_at}"


def start_cron(db: Any, *, task: str, scheduled_at: int, job_id: str | None = None) -> str | None:
    scheduled = datetime.fromtimestamp(int(scheduled_at), tz=timezone.utc).isoformat()
    rid = str(uuid.uuid4())
    data = {"id": rid, "task": task, "scheduled_at": scheduled, "started_at": _now().isoformat(),
            "status": "running", "job_id": job_id or _job_id(task, scheduled_at),
            "image_version": os.getenv("IMAGE_VERSION") or os.getenv("COMMIT_SHA") or os.getenv("RAILWAY_GIT_COMMIT_SHA")}
    try:
        db.table("cron_runs").upsert(
            data, on_conflict="task,scheduled_at", ignore_duplicates=True,
        ).execute()
        persisted = db.table("cron_runs").select("id").eq("task", task).eq("scheduled_at", scheduled).maybe_single().execute()
        if persisted.data and persisted.data.get("id"):
            return str(persisted.data["id"])
        return None
    except Exception:
        logger.warning("cron ledger start failed task=%s", task, exc_info=False)
        return None


def finish_cron(db: Any, *, run_id: str, status: str, last_step: str, counters: dict[str, Any] | None = None,
                error: BaseException | str | None = None) -> None:
    if status not in {"succeeded", "partial", "failed"}:
        raise ValueError(status)
    patch = {"status": status, "completed_at": _now().isoformat(), "last_step": last_step,
             "counters": safe_counters(counters), "error_summary": safe_error(error) if error else None}
    try:
        db.table("cron_runs").update(patch).eq("id", run_id).eq("status", "running").execute()
    except Exception:
        logger.warning("cron ledger finish failed run_id=%s", run_id, exc_info=False)


def incident_key(kind: str, detail: str = "") -> str:
    return hashlib.sha256(f"{kind}:{detail}".encode()).hexdigest()


def observe_incident(db: Any, *, kind: str, detail: str, active: bool, message: str) -> bool:
    """Returns True only when an alert should be sent (open transition)."""
    fp = incident_key(kind, detail)
    try:
        row = db.table("operational_incidents").select("id,status").eq("fingerprint", fp).maybe_single().execute().data
        now = _now().isoformat()
        if active:
            if row and row.get("status") == "open":
                db.table("operational_incidents").update({"last_seen_at": now}).eq("id", row["id"]).execute()
                return False
            payload = {"fingerprint": fp, "kind": kind, "status": "open", "last_seen_at": now, "details": {"message": safe_error(message)}}
            db.table("operational_incidents").upsert(payload, on_conflict="fingerprint").execute()
            return True
        if row and row.get("status") == "open":
            db.table("operational_incidents").update({"status": "recovered", "recovered_at": now, "last_seen_at": now}).eq("id", row["id"]).execute()
            send_alert(f"[radar] recuperação: {kind}", f"O incidente {kind} foi recuperado.")
    except Exception:
        logger.warning("incident ledger unavailable kind=%s", kind, exc_info=False)
    return False


def dead_man_report(rows: list[dict], now: datetime | None = None) -> list[dict]:
    now = now or _now()
    report = []
    for task in CRON_TASKS:
        own = [r for r in rows if r.get("task") == task]
        latest = max(own, key=lambda r: r.get("scheduled_at") or "", default=None)
        success = next((r for r in sorted(own, key=lambda r: r.get("completed_at") or "", reverse=True) if r.get("status") in {"succeeded", "partial"}), None)
        completed = success.get("completed_at") if success else None
        if isinstance(completed, datetime):
            completed_at = completed
        elif completed:
            completed_at = datetime.fromisoformat(str(completed).replace("Z", "+00:00"))
        else:
            completed_at = None
        due = not completed_at or now - completed_at > timedelta(minutes=SLA_MINUTES[task])
        report.append({"task": task, "latest": latest, "last_success": success, "late": due,
                       "next_expected": None, "stuck_timeout_minutes": STUCK_MINUTES})
    return report


def _as_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if value:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return None


def classify_jobs(
    jobs: list[dict], events: list[dict], workers: list[dict], now: datetime | None = None,
) -> dict[str, int]:
    """Classify running jobs using event start and worker heartbeat timestamps."""
    now = now or _now()
    started_by_job: dict[str, datetime] = {}
    for event in events:
        if event.get("type") != "started" or not event.get("job_id"):
            continue
        at = _as_datetime(event.get("at"))
        if at and (event["job_id"] not in started_by_job or at > started_by_job[event["job_id"]]):
            started_by_job[event["job_id"]] = at
    heartbeat_by_worker = {w.get("id"): _as_datetime(w.get("last_heartbeat")) for w in workers}
    doing = stuck = failed = 0
    timeout = timedelta(minutes=STUCK_MINUTES)
    for job in jobs:
        status = job.get("status")
        if status == "failed":
            failed += 1
        elif status == "doing":
            started = started_by_job.get(str(job.get("id")))
            heartbeat = heartbeat_by_worker.get(job.get("worker_id"))
            old_start = started is not None and now - started > timeout
            no_heartbeat = heartbeat is None or now - heartbeat > timeout
            if old_start and no_heartbeat:
                stuck += 1
            else:
                doing += 1
    return {"doing": doing, "stuck": stuck, "failed": failed}
