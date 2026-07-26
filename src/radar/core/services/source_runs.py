"""Repositório idempotente para `source_runs` (RT03-T02).

Persistência best-effort: falha de telemetria nunca interrompe aquisição.
Usado por instrumentação de ETL (T03) e Descoberta multicanal (T04).

Não persiste: query completa, URL com path/query, conteúdo, traceback,
prompt, resposta LLM, segredo ou credencial.
"""
from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

# Estados terminais — finish_run só aceita estes e nunca regride.
_TERMINAL_STATUSES: frozenset[str] = frozenset({
    "succeeded",
    "partial",
    "failed",
    "skipped",
})

_VALID_FINAL_STATUSES: frozenset[str] = _TERMINAL_STATUSES

_REASON_CODES: frozenset[str] = frozenset({
    "no_credentials",
    "weekend_skip",
    "timeout",
    "parse_error",
    "provider_error",
    "empty_result",
    "unknown",
})

# Chaves seguras para métricas — só letras, dígitos e underscore.
_SAFE_METRIC_KEY_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ══════════════════════════════════════════════════════════════════════════
# Validação pré-DB
# ══════════════════════════════════════════════════════════════════════════


def _validate_counters(
    records_observed: int | None = None,
    records_emitted: int | None = None,
    records_staged: int | None = None,
    error_count: int | None = None,
) -> None:
    for name, val in [
        ("records_observed", records_observed),
        ("records_emitted", records_emitted),
        ("records_staged", records_staged),
        ("error_count", error_count),
    ]:
        if val is not None:
            if not isinstance(val, int) or val < 0:
                raise ValueError(f"{name} must be a non-negative integer, got {val!r}")


def _normalize_reason(code: str | None) -> str | None:
    """Normaliza reason_code: retorna None se não for canônico."""
    if code is None:
        return None
    if code in _REASON_CODES:
        return code
    logger.warning("reason_code %r não é canônico; omitindo", code)
    return None


def _sanitize_metrics(metrics: dict[str, Any] | None) -> dict[str, Any] | None:
    """Sanitiza métricas: só chaves seguras e valores numéricos finitos >= 0.

    Rejeita strings, objetos aninhados, booleanos, listas.
    Chave insegura é removida com warning.
    """
    if metrics is None:
        return None
    clean: dict[str, Any] = {}
    for key, val in metrics.items():
        if not isinstance(key, str) or not _SAFE_METRIC_KEY_RE.match(key):
            logger.warning("metrics: removendo chave insegura %r", key)
            continue
        if not (isinstance(val, (int, float))) or isinstance(val, bool):
            logger.warning("metrics: chave %r rejeitada — valor não numérico %r", key, val)
            continue
        try:
            if isinstance(val, float) and (not (val >= 0) or val != val or val == float("inf")):
                logger.warning("metrics: chave %r rejeitada — valor inválido %r", key, val)
                continue
        except (ValueError, TypeError):
            logger.warning("metrics: chave %r rejeitada — erro ao validar %r", key, val)
            continue
        if val < 0:
            logger.warning("metrics: chave %r rejeitada — valor negativo %r", key, val)
            continue
        clean[key] = val
    return clean


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

    Nunca atualiza nem reabre linha existente. Se já existir uma run
    (terminal ou running), retorna seu ID. Caso contrário, insere nova.

    Falha do DB é logada e não relançada. Retorna None se a persistência
    falhar.
    """
    batch = str(batch_id)
    try:
        # Tenta ler run existente primeiro — nunca atualiza conflito.
        existing = (
            db.table("source_runs")
            .select("id, status")
            .eq("batch_id", batch)
            .eq("source_key", source_key)
            .maybe_single()
            .execute()
        )
        if existing.data:
            return str(existing.data["id"])

        run_id = str(uuid.uuid4())
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
            .insert(data)
            .execute()
        )
        if resp.data:
            return str(resp.data[0]["id"])
        return None
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

    Atômico: usa WHERE status NOT IN (terminal) para impedir regressão
    sem race condition. Só aceita estados finais válidos.

    Falha do DB é logada e não relançada. Retorna True se persistiu,
    False se foi ignorada (já terminal, run inexistente, ou DB falhou).
    """
    if status not in _VALID_FINAL_STATUSES:
        raise ValueError(f"invalid final status {status!r}; expected one of {sorted(_VALID_FINAL_STATUSES)}")

    _validate_counters(
        records_observed=records_observed,
        records_emitted=records_emitted,
        records_staged=records_staged,
        error_count=error_count,
    )

    rid = str(run_id)
    reason = _normalize_reason(reason_code)
    safe_metrics = _sanitize_metrics(metrics)

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
    if reason is not None:
        patch["reason_code"] = reason
    if safe_metrics is not None:
        patch["metrics"] = safe_metrics

    try:
        result = (
            db.table("source_runs")
            .update(patch)
            .eq("id", rid)
            .not_.in_("status", list(_TERMINAL_STATUSES))
            .execute()
        )
        if not result.data:
            logger.info(
                "finish_run ignorada: run %s já terminal ou inexistente",
                rid,
            )
            return False
        return True
    except Exception:
        logger.exception(
            "finish_run falhou (best-effort) run=%s status=%s",
            run_id, status,
        )
        return False
