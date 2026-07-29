#!/usr/bin/env python3
"""Inspeção e recuperação segura dos CRONs; dry-run por padrão."""
from __future__ import annotations

import argparse
import os
from datetime import datetime, timedelta, timezone

import psycopg

STUCK_MINUTES = int(os.getenv("CRON_STUCK_MINUTES", "90"))


def main() -> int:
    p = argparse.ArgumentParser(description="Operação segura dos CRONs Radar")
    p.add_argument("action", choices=("list", "finish-stuck", "retry-failed"))
    p.add_argument("--job-id", type=int, action="append", default=[])
    p.add_argument("--apply", action="store_true")
    args = p.parse_args()
    if args.action != "list" and (not args.apply or not args.job_id):
        p.error("ações mutáveis exigem --apply e ao menos um --job-id")
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        p.error("DATABASE_URL ausente")
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        if args.action == "list":
            cur.execute("""
                select j.id, j.task_name, j.status, j.attempts, j.scheduled_at,
                       max(e.at) filter (where e.type = 'started') as last_started_at
                from public.procrastinate_jobs j
                left join public.procrastinate_events e on e.job_id = j.id
                where j.status in ('doing','failed')
                group by j.id, j.task_name, j.status, j.attempts, j.scheduled_at
                order by j.id
            """)
            for row in cur.fetchall():
                print({"id": row[0], "task": row[1], "status": row[2], "attempts": row[3], "scheduled_at": row[4].isoformat() if row[4] else None, "last_started_at": row[5].isoformat() if row[5] else None})
            return 0
        print(f"DRY-RUN: {args.action} jobs {args.job_id} (apply={args.apply})")
        cur.execute("""
            select j.id, j.status, j.worker_id,
                   max(e.at) filter (where e.type = 'started') as last_started_at,
                   w.last_heartbeat
            from public.procrastinate_jobs j
            left join public.procrastinate_events e on e.job_id = j.id
            left join public.procrastinate_workers w on w.id = j.worker_id
            where j.id = any(%s)
            group by j.id, j.status, j.worker_id, w.last_heartbeat
        """, (args.job_id,))
        rows = cur.fetchall()
        now = datetime.now(timezone.utc)
        if len(rows) != len(set(args.job_id)):
            raise SystemExit("um ou mais job IDs não existem")
        for job_id, status, worker_id, started_at, heartbeat in rows:
            if args.action == "finish-stuck":
                stale = started_at and started_at < now - timedelta(minutes=STUCK_MINUTES)
                worker_dead = not worker_id or not heartbeat or heartbeat < now - timedelta(minutes=STUCK_MINUTES)
                if status != "doing" or not stale or not worker_dead:
                    raise SystemExit(f"job {job_id} não está comprovadamente órfão")
            elif status != "failed":
                raise SystemExit(f"job {job_id} não está failed")
        if args.action == "finish-stuck":
            cur.execute("update public.procrastinate_jobs set status='cancelled' where id = any(%s) and status='doing'", (args.job_id,))
        else:
            cur.execute("update public.procrastinate_jobs set status='todo', worker_id=null where id = any(%s) and status='failed'", (args.job_id,))
        if not args.apply:
            conn.rollback()
            print("Nenhuma mutação aplicada.")
            return 0
        conn.commit()
        cur.execute("select id,status from public.procrastinate_jobs where id = any(%s)", (args.job_id,))
        verified = cur.fetchall()
        print(f"Mutação verificada em {len(verified)} job(s) em {datetime.now(timezone.utc).isoformat()}: {verified}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
