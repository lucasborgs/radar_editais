"""Repositório idempotente para `source_runs` (RT03-T02).

Persistência best-effort: falha de telemetria nunca interrompe aquisição.
Usado por instrumentação de ETL (T03) e Descoberta multicanal (T04).

Não persiste: query completa, URL com path/query, conteúdo, traceback,
prompt, resposta LLM, segredo ou credencial.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

# Estados terminais — `finish_run` não aceita transição de saída.
_TERMINAL_STATUSES: frozenset[str] = frozenset(
    {"succeeded", "failed", "skipped"},
)

_REASON_CODES: frozenset[str] = frozenset({
    "no_credentials",
    "weekend_skip",
    "timeout",
    "parse_error",
    "provider_error",
    "empty_result",
    "unknown",
})


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _valid_reason(code: str | None) -> str | None:
    if code is None:
        return None
    if code not in _REASON_CODES:
        logger.warning("reason_code %r não é canônico; persistindo mesmo assim", code)
    return code


# ══════════════════════════════════════════════════════════════════════════
# API pública
# ══════════════════════════════════════════════════════════════════════════


def start_run(
    db: Any,
    *,
    batch_id: str | uuid.UUID,
    source_key: str,
    mode: str,
) -> str | None:
    """Abre ou retorna o UUID de uma `source_run` para (batch_id, source_key).

    Idempotente: se já existir uma run com status terminal, retorna o UUID
    sem modificar. Se existir uma `running`, retorna seu UUID. Caso
    contrário, insere nova linha.

    Falha do DB é logada e não relançada. Retorna None se a persistência
    falhar — o caller continua sem interrupção.
    """
    run_id = str(uuid.uuid4())
    try:
        batch = str(batch_id)
        data = {
            "id": run_id,
            "batch_id": batch,
            "source_key": source_key,
            "mode": mode,
            "status": "running",
            "started_at": _now().isoformat(),
            "error_count": 0,
            "metrics": {},
        }
        resp = (
            db.table("source_runs")
            .upsert(data, on_conflict="batch_id,source_key", ignore_duplicates=False)
            .execute()
        )
        existing = resp.data[0] if resp.data else None
        if existing and existing.get("id") != run_id:
            # Conflito: outra run já existe para (batch_id, source_key).
            current_status = existing.get("status")
            if current_status in _TERMINAL_STATUSES:
                return str(existing["id"])
            # Está running → reutiliza.
            return str(existing["id"])
        return run_id
    except Exception:
        logger.exception(
            "start_run falhou (best-effort) batch=%s source=%s",
            batch_id, source_key,
        )
        return None


def finish_run(
    db: Any,
    *,
    run_id: str | uuid.UUID,
    status: str,
    records_observed: int | None = None,
    records_emitted: int | None = None,
    records_staged: int | None = None,
    error_count: int | None = None,
    reason_code: str | None = None,
    metrics: dict[str, Any] | None = None,
) -> bool:
    """Finaliza uma `source_run` com estado e contadores.

    Idempotente: se a run já estiver em estado terminal, não altera.
    Falha do DB é logada e não relançada. Retorna True se persistiu,
    False se falhou ou foi ignorada (terminal).
    """
    try:
        rid = str(run_id)

        # Verifica estado atual — não regride terminal.
        current = (
            db.table("source_runs")
            .select("status")
            .eq("id", rid)
            .maybe_single()
            .execute()
        )
        if current.data:
            if current.data.get("status") in _TERMINAL_STATUSES:
                logger.info(
                    "finish_run ignorada: run %s já está %s",
                    rid, current.data["status"],
                )
                return False

        patch: dict[str, Any] = {
            "status": status,
            "completed_at": _now().isoformat(),
        }
        if records_observed is not None:
            patch["records_observed"] = records_observed
        if records_emitted is not None:
            patch["records_emitted"] = records_emitted
        if records_staged is not None:
            patch["records_staged"] = records_staged
        if error_count is not None:
            patch["error_count"] = error_count
        code = _valid_reason(reason_code)
        if code is not None:
            patch["reason_code"] = code
        if metrics is not None:
            patch["metrics"] = metrics

        (db.table("source_runs").update(patch).eq("id", rid).execute())
        return True
    except Exception:
        logger.exception(
            "finish_run falhou (best-effort) run=%s status=%s",
            run_id, status,
        )
        return False
