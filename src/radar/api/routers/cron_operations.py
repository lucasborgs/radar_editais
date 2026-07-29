"""Read-only operational panel for the three critical periodic tasks."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from radar.core.infra.auth import AdminUserId
from radar.core.infra.db import get_supabase_service
from radar.core.services.cron_operations import classify_jobs, dead_man_report

router = APIRouter(prefix="/admin/cron-operations", tags=["operations"])


class CronHealthResponse(BaseModel):
    generated_at: str
    runs: list[dict]
    jobs: dict[str, int]
    incidents: list[dict]


@router.get("", response_model=CronHealthResponse, summary="Saúde dos CRONs críticos")
def cron_health(_user_id: AdminUserId):
    try:
        db = get_supabase_service()
        runs = db.table("cron_runs").select(
            "task,scheduled_at,started_at,completed_at,status,job_id,image_version,counters,error_summary,last_step"
        ).order("started_at", desc=True).limit(100).execute().data or []
        incidents = db.table("operational_incidents").select(
            "fingerprint,kind,status,first_seen_at,last_seen_at,recovered_at"
        ).order("last_seen_at", desc=True).limit(50).execute().data or []
        # Never select args: this endpoint is intentionally safe to expose to an
        # operator and cannot become a payload leak.
        jobs = db.table("procrastinate_jobs").select("id,status,task_name,worker_id").in_("status", ["doing", "failed"]).execute().data or []
        job_ids = [str(j["id"]) for j in jobs if j.get("id") is not None]
        events = db.table("procrastinate_events").select("job_id,type,at").in_("job_id", job_ids).eq("type", "started").execute().data if job_ids else []
        workers = db.table("procrastinate_workers").select("id,last_heartbeat").execute().data or []
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Painel operacional indisponível") from exc

    return {"generated_at": datetime.now(timezone.utc).isoformat(),
            "runs": dead_man_report(runs),
            "jobs": classify_jobs(jobs, events or [], workers),
            "incidents": incidents}
