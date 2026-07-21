"""Estado operacional de promoções aprovadas da Descoberta.

Não participa da decisão editorial e não altera contratos dos jobs. É uma
camada de auditoria best-effort ao redor de bronze/gold/RAG, usando as tabelas
da migration 038.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from core.services.discovery_evidence import sanitized_error

_STAGES = ("source_ready", "bronze_ready", "silver_ready", "radar_ready", "rag_ready")


def initial_stages(route: str) -> dict[str, dict[str, Any]]:
    stages = {name: {"status": "pending", "attempt": 0} for name in _STAGES}
    stages["source_ready"] = {"status": "ready", "attempt": 1}
    if route == "web_source":
        stages["bronze_ready"] = {"status": "pending", "attempt": 0}
    return stages


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def aggregate_status(stages: dict[str, dict[str, Any]], route: str) -> str:
    values = {name: (stages.get(name) or {}).get("status", "pending") for name in _STAGES}
    if route == "web_source" and values["bronze_ready"] != "ready":
        return "awaiting_fetch" if values["bronze_ready"] != "failed" else "failed"
    ready = [values["radar_ready"] == "ready", values["rag_ready"] == "ready"]
    failed = [value == "failed" for value in values.values()]
    if all(ready):
        return "ready"
    if any(ready) and any(failed):
        return "partial_failure"
    if all(failed[-2:]):
        return "failed"
    return "processing"


def create_run(db, *, opportunity_id: str, route: str, edital_id: str | None = None,
               web_source_id: str | None = None, evidence_version: int = 1) -> dict[str, Any]:
    stages = initial_stages(route)
    status = aggregate_status(stages, route)
    row = {
        "discovered_opportunity_id": opportunity_id, "route": route,
        "status": status, "edital_id": edital_id, "web_source_id": web_source_id,
        "evidence_version": evidence_version, "stages": stages,
    }
    result = db.table("discovery_promotion_runs").insert(row).execute()
    run = (result.data or [row])[0]
    event(db, run["id"], "source_ready", "ready", actor="operator", artifact={"route": route})
    return run


def event(db, run_id: str, stage: str, status: str, *, actor: str = "system",
          attempt: int = 1, artifact: dict[str, Any] | None = None,
          error: Exception | str | None = None) -> None:
    row: dict[str, Any] = {
        "promotion_run_id": run_id, "stage": stage, "status": status, "actor": actor,
        "attempt": attempt, "artifact": artifact or {},
    }
    if error:
        row["error_summary"] = sanitized_error(error)
    db.table("discovery_promotion_events").insert(row).execute()


def update_stage(db, run: dict[str, Any], stage: str, status: str, *, artifact: dict[str, Any] | None = None,
                 error: Exception | str | None = None, actor: str = "system") -> dict[str, Any]:
    stages = dict(run.get("stages") or {})
    previous = dict(stages.get(stage) or {})
    attempt = int(previous.get("attempt") or 0) + (1 if status == "running" else 0)
    stages[stage] = {"status": status, "attempt": attempt or 1, "updated_at": _now(), **(artifact or {})}
    aggregate = aggregate_status(stages, run["route"])
    patch: dict[str, Any] = {"stages": stages, "status": aggregate, "updated_at": _now()}
    if aggregate in {"ready", "failed", "partial_failure"}:
        patch["completed_at"] = _now()
    if error:
        patch["error_summary"] = sanitized_error(error)
    result = db.table("discovery_promotion_runs").update(patch).eq("id", run["id"]).execute()
    updated = (result.data or [{**run, **patch}])[0]
    event(db, run["id"], stage, status, actor=actor, attempt=attempt or 1, artifact=artifact, error=error)
    return updated


def latest_run(db, opportunity_id: str) -> dict[str, Any] | None:
    response = (db.table("discovery_promotion_runs").select("*")
                .eq("discovered_opportunity_id", opportunity_id).order("started_at", desc=True).limit(1).execute())
    return (response.data or [None])[0]


def set_edital_id(db, run: dict[str, Any], edital_id: str) -> dict[str, Any]:
    response = (db.table("discovery_promotion_runs").update({"edital_id": edital_id, "updated_at": _now()})
                .eq("id", run["id"]).execute())
    return (response.data or [{**run, "edital_id": edital_id}])[0]


def mark_by_edital(edital_id: str, stage: str, status: str, *, error: Exception | str | None = None) -> None:
    """Observabilidade não pode fazer o job nativo falhar."""
    try:
        from core.infra.db import get_supabase_service
        db = get_supabase_service()
        response = (db.table("discovery_promotion_runs").select("*").eq("edital_id", edital_id)
                    .order("started_at", desc=True).limit(1).execute())
        run = (response.data or [None])[0]
        if run:
            update_stage(db, run, stage, status, error=error)
    except Exception:  # job de produção continua tendo sua semântica original
        return


def events_for_run(db, run_id: str) -> list[dict[str, Any]]:
    response = (db.table("discovery_promotion_events").select("stage,status,actor,attempt,artifact,error_summary,created_at")
                .eq("promotion_run_id", run_id).order("created_at").execute())
    return response.data or []
