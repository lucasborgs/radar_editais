from __future__ import annotations

import json
import logging
import os
from datetime import datetime

from postgrest.exceptions import APIError

from radar.domain.data_quality import (
    DATA_QUALITY_SCHEMA_VERSION,
    DataQualityException,
    DataQualityReview,
)
from radar.domain.provenance import EvidenceRef, ReviewInfo

logger = logging.getLogger(__name__)

_TABLE_EXCEPTIONS = "data_quality_exceptions"
_TABLE_REVIEWS = "data_quality_reviews"
_PG_UNIQUE_VIOLATION = "23505"


class DataQualityStorageError(Exception):
    """Erro real de persistência na fila de qualidade.

    Mensagem sanitizada — sem detalhes de provedor, URLs ou corpos de
    resposta. Não inclui:

    - duplicata (23505) → idempotente, tratado como sucesso
    - Supabase não configurado → degradação graciosa (None / False)
    - registro inexistente → None legítimo
    """


def _configured() -> bool:
    return bool(os.getenv("SUPABASE_URL") and os.getenv("SUPABASE_SERVICE_KEY"))


def _now() -> str:
    return datetime.now().isoformat()


# ---------------------------------------------------------------------------
# Exception persistence
# ---------------------------------------------------------------------------


def open_or_observe_exception(
    exception: DataQualityException,
) -> bool:
    """Persiste ou reobserva uma exceção de forma idempotente.

    Se a mesma fingerprint já existir: atualiza apenas last_observed_at.
    Se fingerprint diferente para o mesmo (subject_kind, subject_id,
    field_path, issue_code): insere novo registro, marca os anteriores
    abertos como superseded.

    Args:
        exception: DataQualityException validado pelo contrato.

    Returns:
        True se persistiu ou já existia (idempotente).
        False se Supabase não está configurado.

    Raises:
        DataQualityStorageError: em falha real de persistência.
    """
    if not _configured():
        logger.info("data_quality_exceptions: Supabase ausente — no-op")
        return False

    kind = exception.subject_kind.value
    sid = exception.subject_id
    field = exception.field_path
    code = exception.issue_code.value
    fingerprint = exception.input_fingerprint or ""

    try:
        from radar.core.infra.db import get_supabase_service

        svc = get_supabase_service()

        # 1. Check se a mesma fingerprint já existe
        existing = (
            svc.table(_TABLE_EXCEPTIONS)
            .select("id, status")
            .eq("subject_kind", kind)
            .eq("subject_id", sid)
            .eq("field_path", field)
            .eq("issue_code", code)
            .eq("input_fingerprint", fingerprint)
            .limit(1)
            .execute()
        )

        if existing.data:
            row = existing.data[0]
            svc.table(_TABLE_EXCEPTIONS).update({
                "last_observed_at": _now(),
            }).eq("id", row["id"]).execute()
            return True

        # 2. Nova fingerprint: supersede abertas anteriores do mesmo
        # sujeito/campo/código
        svc.table(_TABLE_EXCEPTIONS).update({
            "status": "superseded",
        }).eq("subject_kind", kind).eq("subject_id", sid).eq(
            "field_path", field
        ).eq("issue_code", code).eq(
            "status", "open"
        ).neq(
            "input_fingerprint", fingerprint
        ).execute()

        # 3. Inserir
        payload = _exception_payload(exception)
        svc.table(_TABLE_EXCEPTIONS).insert(payload).execute()
        return True

    except APIError as e:
        if e.code == _PG_UNIQUE_VIOLATION:
            return True
        logger.warning(
            "data_quality_exceptions.open_or_observe: "
            "erro de persistência (%s/%s): code=%s type=%s",
            kind, sid, e.code, type(e).__name__,
        )
        raise DataQualityStorageError(
            f"open_or_observe failed: code={e.code}"
        ) from None
    except Exception as exc:
        logger.warning(
            "data_quality_exceptions.open_or_observe: "
            "erro inesperado (%s/%s): type=%s",
            kind, sid, type(exc).__name__,
        )
        raise DataQualityStorageError(
            f"open_or_observe failed: type={type(exc).__name__}"
        ) from None


def list_exceptions(
    subject_kind: str | None = None,
    subject_id: str | None = None,
    status: str | None = None,
) -> list[dict]:
    """Lista exceções com filtros opcionais.

    Args:
        subject_kind: Filtrar por tipo de sujeito.
        subject_id: Filtrar por ID do sujeito.
        status: Filtrar por status (open, resolved, superseded).

    Returns:
        Lista de dicionários com dados brutos das exceções.
        Lista vazia se nenhuma encontrada ou Supabase não configurado.

    Raises:
        DataQualityStorageError: em falha real de leitura.
    """
    if not _configured():
        return []

    try:
        from radar.core.infra.db import get_supabase_service

        query = (
            get_supabase_service()
            .table(_TABLE_EXCEPTIONS)
            .select("*")
            .order("last_observed_at", desc=True)
        )

        if subject_kind is not None:
            query = query.eq("subject_kind", subject_kind)
        if subject_id is not None:
            query = query.eq("subject_id", subject_id)
        if status is not None:
            query = query.eq("status", status)

        resp = query.execute()
        return resp.data or []

    except Exception as exc:
        logger.warning(
            "data_quality_exceptions.list_exceptions: "
            "falha de leitura: type=%s",
            type(exc).__name__,
        )
        raise DataQualityStorageError(
            f"list_exceptions failed: type={type(exc).__name__}"
        ) from None


def get_exception(exception_id: str) -> dict | None:
    """Retorna uma exceção pelo ID.

    Args:
        exception_id: UUID da exceção.

    Returns:
        Dicionário com dados da exceção ou None se não encontrada
        (ou Supabase não configurado).

    Raises:
        DataQualityStorageError: em falha real de leitura.
    """
    if not _configured():
        return None

    try:
        from radar.core.infra.db import get_supabase_service

        resp = (
            get_supabase_service()
            .table(_TABLE_EXCEPTIONS)
            .select("*")
            .eq("id", exception_id)
            .limit(1)
            .execute()
        )
        rows = resp.data or []
        return rows[0] if rows else None

    except Exception as exc:
        logger.warning(
            "data_quality_exceptions.get_exception: "
            "falha de leitura: type=%s",
            type(exc).__name__,
        )
        raise DataQualityStorageError(
            f"get_exception failed: type={type(exc).__name__}"
        ) from None


# ---------------------------------------------------------------------------
# Review persistence
# ---------------------------------------------------------------------------


def append_review(review: DataQualityReview) -> bool:
    """Registra uma revisão append-only.

    A revisão é vinculada à exceção por exception_ref (que deve ser o
    UUID da exceção). Nunca atualiza ou remove revisões existentes.

    Args:
        review: DataQualityReview validado pelo contrato.

    Returns:
        True se persistiu. False se Supabase não configurado.

    Raises:
        DataQualityStorageError: em falha real de persistência.
    """
    if not _configured():
        logger.info("data_quality_reviews: Supabase ausente — no-op")
        return False

    try:
        from radar.core.infra.db import get_supabase_service

        payload = _review_payload(review)
        get_supabase_service().table(_TABLE_REVIEWS).insert(payload).execute()
        return True

    except Exception as exc:
        logger.warning(
            "data_quality_reviews.append_review: "
            "erro de persistência: type=%s",
            type(exc).__name__,
        )
        raise DataQualityStorageError(
            f"append_review failed: type={type(exc).__name__}"
        ) from None


def get_current_review_projection(
    exception_id: str,
) -> DataQualityReview | None:
    """Retorna a última revisão de uma exceção, se houver.

    Args:
        exception_id: UUID da exceção.

    Returns:
        DataQualityReview ou None se não houver revisão
        (ou Supabase não configurado).

    Raises:
        DataQualityStorageError: em falha real de leitura.
    """
    if not _configured():
        return None

    try:
        from radar.core.infra.db import get_supabase_service

        resp = (
            get_supabase_service()
            .table(_TABLE_REVIEWS)
            .select("*")
            .eq("exception_id", exception_id)
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        rows = resp.data or []
        if not rows:
            return None

        row = rows[0]
        return DataQualityReview(
            schema_version=row.get("schema_version", DATA_QUALITY_SCHEMA_VERSION),
            exception_ref=row["exception_id"],
            decision=row["decision"],
            corrected_value=row.get("corrected_value"),
            justification=row["justification"],
            evidence_refs=[
                EvidenceRef(**ref) for ref in (row.get("evidence_refs") or [])
            ],
            review=ReviewInfo(
                review_id=row["id"],
                actor_id=row["actor_id"],
                reviewed_at=row["reviewed_at"],
                overridden=row["decision"] == "correct",
            ),
        )

    except Exception as exc:
        logger.warning(
            "data_quality_exceptions.get_current_review_projection: "
            "falha de leitura: type=%s",
            type(exc).__name__,
        )
        raise DataQualityStorageError(
            f"get_current_review_projection failed: "
            f"type={type(exc).__name__}"
        ) from None


# ---------------------------------------------------------------------------
# Payload builders
# ---------------------------------------------------------------------------


def _exception_payload(exception: DataQualityException) -> dict:
    return {
        "schema_version": exception.schema_version,
        "subject_kind": exception.subject_kind.value,
        "subject_id": exception.subject_id,
        "field_path": exception.field_path,
        "issue_code": exception.issue_code.value,
        "produced_state": (
            exception.produced_state.value if exception.produced_state else None
        ),
        "produced_value": exception.produced_value,
        "evidence_refs": json.loads(
            json.dumps(
                [ref.model_dump(mode="json") for ref in exception.evidence_refs]
            )
        ),
        "bundle_hash": exception.bundle_hash,
        "producer_version": exception.producer_version,
        "input_fingerprint": exception.input_fingerprint or "",
        "status": exception.status,
        "detected_at": (
            exception.detected_at.isoformat() if exception.detected_at else _now()
        ),
        "last_observed_at": (
            exception.last_observed_at.isoformat()
            if exception.last_observed_at
            else _now()
        ),
    }


def _review_payload(review: DataQualityReview) -> dict:
    return {
        "schema_version": review.schema_version,
        "exception_id": review.exception_ref,
        "decision": review.decision,
        "corrected_value": review.corrected_value,
        "justification": review.justification,
        "evidence_refs": json.loads(
            json.dumps(
                [ref.model_dump(mode="json") for ref in review.evidence_refs]
            )
        ),
        "actor_id": review.review.actor_id,
        "reviewed_at": review.review.reviewed_at.isoformat(),
    }


__all__ = [
    "DataQualityStorageError",
    "open_or_observe_exception",
    "list_exceptions",
    "get_exception",
    "append_review",
    "get_current_review_projection",
]
