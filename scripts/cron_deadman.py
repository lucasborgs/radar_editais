#!/usr/bin/env python3
"""Dead-man externo aos processos do worker (psycopg + biblioteca padrão)."""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import smtplib
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage

import psycopg

CRON_TASKS = ("run_daily_etl", "discover_opportunities", "warm_edital_chunks")
SLA_MINUTES = {"run_daily_etl": 1800, "discover_opportunities": 1800, "warm_edital_chunks": 1800}
STUCK_MINUTES = int(os.getenv("CRON_STUCK_MINUTES", "90"))


def safe_text(value: object, limit: int = 240) -> str:
    text = re.sub(r"(?i)(api[_-]?key|token|password|secret|cnpj|cpf)\s*[:=]\s*[^,;\s]+", r"\1=[redacted]", str(value))
    return re.sub(r"\b\d{14}\b", "[cnpj]", text)[:limit]


def _fingerprint(kind: str, detail: str) -> str:
    return hashlib.sha256(f"{kind}:{detail}".encode()).hexdigest()


def _rows(conn, sql: str, params=()):
    with conn.cursor() as cur:
        cur.execute(sql, params)
        columns = [d.name for d in cur.description]
        return [dict(zip(columns, row, strict=True)) for row in cur.fetchall()]


def send_alert(subject: str, body: str) -> bool:
    user = os.getenv("ALERT_SMTP_USER", "").strip()
    recipient = os.getenv("ALERT_EMAIL_TO", "").strip()
    if not user or not recipient:
        return False
    try:
        msg = EmailMessage()
        msg["Subject"] = safe_text(subject)
        msg["From"] = os.getenv("ALERT_EMAIL_FROM", "").strip() or user
        msg["To"] = recipient
        msg.set_content(safe_text(body, 4000))
        with smtplib.SMTP(os.getenv("ALERT_SMTP_HOST", "smtp.gmail.com"), int(os.getenv("ALERT_SMTP_PORT", "587")), timeout=20) as smtp:
            smtp.starttls()
            smtp.login(user, os.getenv("ALERT_SMTP_PASSWORD", ""))
            smtp.send_message(msg)
        return True
    except Exception:
        return False


def observe_incident(conn, *, kind: str, detail: str, active: bool, message: str) -> bool:
    fingerprint = _fingerprint(kind, detail)
    now = datetime.now(timezone.utc)
    with conn.cursor() as cur:
        cur.execute("select id,status from public.operational_incidents where fingerprint=%s", (fingerprint,))
        row = cur.fetchone()
        if active:
            if row and row[1] == "open":
                cur.execute("update public.operational_incidents set last_seen_at=%s where id=%s", (now, row[0]))
                return False
            cur.execute("""
                insert into public.operational_incidents(fingerprint,kind,status,last_seen_at,details)
                values (%s,%s,'open',%s,jsonb_build_object('message',%s::text))
                on conflict (fingerprint) do update set status='open', last_seen_at=excluded.last_seen_at,
                    recovered_at=null, details=excluded.details
            """, (fingerprint, kind, now, safe_text(message)))
            return True
        if row and row[1] == "open":
            cur.execute("update public.operational_incidents set status='recovered', recovered_at=%s, last_seen_at=%s where id=%s", (now, now, row[0]))
            send_alert(f"[radar] recuperação: {kind}", f"O incidente {kind} foi recuperado.")
    return False


def run(conn, now: datetime | None = None) -> dict[str, int]:
    now = now or datetime.now(timezone.utc)
    runs = _rows(conn, "select task,completed_at,status from public.cron_runs order by started_at desc limit 100")
    jobs = _rows(conn, """
        select j.id,j.task_name,j.status,j.attempts,j.worker_id,
               max(e.at) filter (where e.type='started') as started_at
        from public.procrastinate_jobs j left join public.procrastinate_events e on e.job_id=j.id
        where j.status in ('doing','failed')
        group by j.id,j.task_name,j.status,j.attempts,j.worker_id
    """)
    workers = _rows(conn, "select id,last_heartbeat from public.procrastinate_workers")
    heartbeats = {w["id"]: w.get("last_heartbeat") for w in workers}
    alerts = 0
    for task in CRON_TASKS:
        successes = [r for r in runs if r["task"] == task and r["status"] in ("succeeded", "partial") and r.get("completed_at")]
        latest = max(successes, key=lambda r: r["completed_at"], default=None)
        late = not latest or now - latest["completed_at"] > timedelta(minutes=SLA_MINUTES[task])
        if observe_incident(conn, kind=f"cron_late:{task}", detail=task, active=late, message=f"sem sucesso recente: {task}"):
            alerts += 1
            send_alert(f"[radar] CRON atrasado: {task}", f"Não houve sucesso de {task} dentro do SLA.")
    cutoff = now - timedelta(minutes=STUCK_MINUTES)
    for job in jobs:
        started = job.get("started_at")
        if job["status"] == "failed" and (job.get("attempts") or 0) >= 3:
            active, kind, detail = True, "task_retries_exhausted", str(job["task_name"])
        elif job["status"] == "doing" and started and started < cutoff:
            heartbeat = heartbeats.get(job.get("worker_id"))
            active, kind, detail = not heartbeat or heartbeat < cutoff, "job_stuck", str(job["id"])
        else:
            continue
        if observe_incident(conn, kind=kind, detail=detail, active=active, message=f"incidente {kind}"):
            alerts += 1
            send_alert(f"[radar] incidente operacional: {kind}", safe_text(f"job {job['id']}"))
    stale = [w for w in workers if not w.get("last_heartbeat") or w["last_heartbeat"] < cutoff]
    if not workers:
        stale = [{"id": "none"}]
    if observe_incident(conn, kind="worker_heartbeat_stale", detail="worker", active=bool(stale), message="heartbeat ausente"):
        alerts += 1
        send_alert("[radar] heartbeat do worker ausente", "O heartbeat do worker está atrasado.")
    return {"alerts": alerts, "jobs_checked": len(jobs), "workers_stale": len(stale)}


def main() -> int:
    argparse.ArgumentParser(description="Dead-man externo dos CRONs").parse_args()
    dsn = os.getenv("DATABASE_URL")
    if not dsn:
        raise SystemExit("DATABASE_URL ausente")
    with psycopg.connect(dsn) as conn:
        print(run(conn))
        conn.commit()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
