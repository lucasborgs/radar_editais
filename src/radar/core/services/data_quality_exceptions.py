from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone

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


class DataQualityReviewConflictError(DataQualityStorageError):
    """Colisão material de revisão com o mesmo review_id."""


def _configured() -> bool:
    return bool(os.getenv("SUPABASE_URL") and os.getenv("SUPABASE_SERVICE_KEY"))


def _now() -> str:
    return datetime.now().isoformat()


# ---------------------------------------------------------------------------
# Sanitizacao de EvidenceRef
# ---------------------------------------------------------------------------


def _evidence_refs_payload(refs: list[EvidenceRef]) -> list[dict]:
    """Serializa lista de EvidenceRef removendo source_url."""
    return json.loads(
        json.dumps([
            {k: v for k, v in ref.model_dump(mode="json").items()
             if k != "source_url"}
            for ref in refs
        ])
    )


# ---------------------------------------------------------------------------
# Comparacao de payload material de revisao
# ---------------------------------------------------------------------------

_REVIEW_MATERIAL_KEYS = (
    "schema_version",
    "exception_id",
    "decision",
    "corrected_value",
    "justification",
    "evidence_refs",
    "actor_id",
    "reviewed_at",
)


def _normalize_ts(raw: object) -> datetime | None:
    """Converte string ISO para datetime UTC, tratando naive como UTC.

    Retorna None se o valor for inválido (payloads diferentes).
    """
    if not isinstance(raw, str):
        return None
    try:
        dt = datetime.fromisoformat(raw)
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _review_payload_matches(stored: dict, incoming: dict) -> bool:
    """True se o payload material de duas revisoes for identico.

    Compara todos os campos semânticos ignorando metadados internos
    (id, created_at, review_id). Normaliza ``reviewed_at`` antes da
    comparacao.

    Timestamps invalidos resultam em ``False`` (payload diferente).
    """
    for k in _REVIEW_MATERIAL_KEYS:
        a = stored.get(k)
        b = incoming.get(k)
        if k == "reviewed_at":
            a = _normalize_ts(a)
            b = _normalize_ts(b)
        if a != b:
            return False
    return True


# ---------------------------------------------------------------------------
# Exception persistence
# ---------------------------------------------------------------------------

_INVALID_FINGERPRINT_MSG = (
    "input_fingerprint must be non-empty for open_or_observe_exception"
)


def open_or_observe_exception(
    exception: DataQualityException,
) -> bool:
    """Persiste ou reobserva uma exceção de forma idempotente.

    1. Rejeita input_fingerprint ausente/vazio.
    2. Se mesma fingerprint já existe: atualiza last_observed_at,
       depois supersede outras abertas do mesmo grupo.
    3. Se fingerprint nova: insere, depois supersede as abertas
       anteriores do mesmo grupo (nunca antes).
    4. Retry após falha parcial (insert ok, supersede falhou)
       converge para exatamente uma versão aberta.

    Args:
        exception: DataQualityException validado pelo contrato.

    Returns:
        True se persistiu ou já existia (idempotente).
        False se Supabase não está configurado.

    Raises:
        ValueError: se input_fingerprint for None ou vazio.
        DataQualityStorageError: em falha real de persistência.
    """
    if not exception.input_fingerprint or not exception.input_fingerprint.strip():
        raise ValueError(_INVALID_FINGERPRINT_MSG)

    if not _configured():
        logger.info("data_quality_exceptions: Supabase ausente — no-op")
        return False

    kind = exception.subject_kind.value
    sid = exception.subject_id
    field = exception.field_path
    code = exception.issue_code.value
    fingerprint = exception.input_fingerprint

    try:
        from radar.core.infra.db import get_supabase_service

        svc = get_supabase_service()

        # 1. Verificar se a mesma fingerprint já existe
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
            # Atualiza last_observed_at
            updated = (
                svc.table(_TABLE_EXCEPTIONS)
                .update({"last_observed_at": _now()})
                .eq("id", row["id"])
                .execute()
            )
            if not updated.data:
                logger.warning(
                    "open_or_observe: reobservacao nao retornou registros "
                    "(%s/%s/%s)", kind, sid, fingerprint
                )
            # Só supersede se o registro atual ainda está open
            if row["status"] == "open":
                _supersede_other_open(svc, kind, sid, field, code, fingerprint)
            return True

        # 2. Inserir — se falhar (exceto 23505), nao supersede nada
        payload = _exception_payload(exception)
        inserted = svc.table(_TABLE_EXCEPTIONS).insert(payload).execute()

        if not inserted.data:
            logger.warning(
                "open_or_observe: insert nao retornou registros "
                "(%s/%s/%s)", kind, sid, fingerprint
            )

        # 3. Insert ok: agora supersede as abertas anteriores
        _supersede_other_open(svc, kind, sid, field, code, fingerprint)
        return True

    except APIError as e:
        if e.code == _PG_UNIQUE_VIOLATION:
            # Race: outro processo inseriu a mesma fingerprint
            # Reobserva e supersede
            _reobserve_and_supersede(svc, kind, sid, field, code, fingerprint)
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


def _reobserve_and_supersede(svc, kind, sid, field, code, fingerprint):
    """Reobserva fingerprint (apos race/violacao) + supersede."""
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
        svc.table(_TABLE_EXCEPTIONS).update({
            "last_observed_at": _now(),
        }).eq("id", existing.data[0]["id"]).execute()
        if existing.data[0]["status"] == "open":
            _supersede_other_open(svc, kind, sid, field, code, fingerprint)


def _supersede_other_open(svc, kind, sid, field, code, fingerprint):
    """Marca como superseded as abertas do mesmo grupo com fingerprint
    diferente."""
    svc.table(_TABLE_EXCEPTIONS).update({
        "status": "superseded",
    }).eq("subject_kind", kind).eq("subject_id", sid).eq(
        "field_path", field
    ).eq("issue_code", code).eq(
        "status", "open"
    ).neq(
        "input_fingerprint", fingerprint
    ).execute()

    logger.info(
        "open_or_observe: superseded anteriores para "
        "%s/%s/%s/%s exceto fingerprint=%s",
        kind, sid, field, code, fingerprint,
    )


def list_exceptions(
    subject_kind: str | None = None,
    subject_id: str | None = None,
    status: str | None = None,
    issue_code: str | None = None,
    field_path: str | None = None,
    source: str | None = None,
    limit: int | None = None,
    offset: int = 0,
) -> list[dict]:
    """Lista exceções com filtros opcionais.

    Args:
        subject_kind: Filtrar por tipo de sujeito.
        subject_id: Filtrar por ID do sujeito.
        status: Filtrar por status (open, resolved, superseded).
        issue_code: Filtrar por código de exceção.
        field_path: Filtrar por caminho de campo.
        source: Filtrar por source dos EvidenceRef associados.
        limit: Limite opcional do recorte retornado.
        offset: Deslocamento opcional antes do recorte.

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
        if issue_code is not None:
            query = query.eq("issue_code", issue_code)
        if field_path is not None:
            query = query.eq("field_path", field_path)

        resp = query.execute()
        rows = resp.data or []
        if source is not None:
            normalized_source = source.strip().lower()
            rows = [
                row
                for row in rows
                if _row_matches_source(row, normalized_source)
            ]
        if offset:
            rows = rows[offset:]
        if limit is not None:
            rows = rows[:limit]
        return rows

    except Exception as exc:
        logger.warning(
            "data_quality_exceptions.list_exceptions: "
            "falha de leitura: type=%s",
            type(exc).__name__,
        )
        raise DataQualityStorageError(
            f"list_exceptions failed: type={type(exc).__name__}"
        ) from None


def _row_matches_source(row: dict, normalized_source: str) -> bool:
    refs = row.get("evidence_refs") or []
    for ref in refs:
        if isinstance(ref, dict):
            row_source = ref.get("source")
        else:
            row_source = getattr(ref, "source", None)
        if isinstance(row_source, str) and row_source.strip().lower() == normalized_source:
            return True
    return False


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


def mark_exception_resolved(exception_id: str) -> bool:
    """Resolve idempotentemente uma exceção aberta.

    A transição aceita apenas ``open -> resolved``. Um registro já resolvido
    conta como sucesso idempotente; ``superseded`` e ausente retornam False.
    """
    if not exception_id or not exception_id.strip():
        raise ValueError("exception_id must be non-empty")
    if not _configured():
        return False

    try:
        from radar.core.infra.db import get_supabase_service

        svc = get_supabase_service()
        svc.table(_TABLE_EXCEPTIONS).update({
            "status": "resolved",
        }).eq("id", exception_id).eq("status", "open").execute()

        current = (
            svc.table(_TABLE_EXCEPTIONS)
            .select("id, status")
            .eq("id", exception_id)
            .limit(1)
            .execute()
        )
        rows = current.data or []
        return bool(rows and rows[0].get("status") == "resolved")
    except APIError as exc:
        logger.warning(
            "data_quality_exceptions.mark_resolved: "
            "falha de persistencia id=%s code=%s type=%s",
            exception_id,
            exc.code,
            type(exc).__name__,
        )
        raise DataQualityStorageError(
            f"mark_exception_resolved failed: code={exc.code}"
        ) from None
    except Exception as exc:
        logger.warning(
            "data_quality_exceptions.mark_resolved: "
            "falha inesperada id=%s type=%s",
            exception_id,
            type(exc).__name__,
        )
        raise DataQualityStorageError(
            f"mark_exception_resolved failed: type={type(exc).__name__}"
        ) from None


# ---------------------------------------------------------------------------
# Review persistence
# ---------------------------------------------------------------------------


def append_review(review: DataQualityReview) -> bool:
    """Registra uma revisão append-only, idempotente por review_id.

    Se o mesmo review_id já existir, retorna True sem criar novo
    registro. Nunca atualiza ou remove revisões existentes.

    Args:
        review: DataQualityReview validado pelo contrato.

    Returns:
        True se persistiu ou já existia. False se Supabase não configurado.

    Raises:
        ValueError: se review_id for vazio.
        DataQualityStorageError: em falha real de persistência.
    """
    if not review.review.review_id or not review.review.review_id.strip():
        raise ValueError("review_id must be non-empty")

    if not _configured():
        logger.info("data_quality_reviews: Supabase ausente — no-op")
        return False

    try:
        from radar.core.infra.db import get_supabase_service

        svc = get_supabase_service()

        # Idempotencia por review_id
        existing = (
            svc.table(_TABLE_REVIEWS)
            .select("*")
            .eq("review_id", review.review.review_id)
            .limit(1)
            .execute()
        )
        if existing.data:
            payload = _review_payload(review)
            if _review_payload_matches(existing.data[0], payload):
                logger.info(
                    "append_review: review_id=%s ja existe — idempotente",
                    review.review.review_id,
                )
                return True
            raise DataQualityReviewConflictError(
                "append_review conflict: review_id already exists with "
                "different payload"
            )

        payload = _review_payload(review)
        svc.table(_TABLE_REVIEWS).insert(payload).execute()
        return True

    except APIError as e:
        if e.code == _PG_UNIQUE_VIOLATION:
            # Race: outro processo inseriu o mesmo review_id
            # Verificar se o payload é idêntico
            try:
                existing = (
                    svc.table(_TABLE_REVIEWS)
                    .select("*")
                    .eq("review_id", review.review.review_id)
                    .limit(1)
                    .execute()
                )
                if not existing.data:
                    raise DataQualityStorageError(
                        "append_review failed: 23505 race but no record found"
                    )
                existing_row = existing.data[0]
                payload = _review_payload(review)
                if _review_payload_matches(existing_row, payload):
                    return True
                raise DataQualityReviewConflictError(
                    "append_review conflict: review_id already exists with "
                    "different payload"
                )
            except DataQualityStorageError:
                raise
            except Exception as exc2:
                logger.warning(
                    "data_quality_reviews.append_review: "
                    "erro ao recuperar race: type=%s",
                    type(exc2).__name__,
                )
                raise DataQualityStorageError(
                    "append_review failed: race recovery error"
                ) from None
        logger.warning(
            "data_quality_reviews.append_review: "
            "erro de persistência: code=%s type=%s",
            e.code, type(e).__name__,
        )
        raise DataQualityStorageError(
            f"append_review failed: code={e.code}"
        ) from None
    except DataQualityStorageError:
        raise
    except Exception as exc:
        logger.warning(
            "data_quality_reviews.append_review: "
            "erro inesperado: type=%s",
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
                review_id=row["review_id"],
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
        "evidence_refs": _evidence_refs_payload(exception.evidence_refs),
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
        "review_id": review.review.review_id,
        "exception_id": review.exception_ref,
        "decision": review.decision,
        "corrected_value": review.corrected_value,
        "justification": review.justification,
        "evidence_refs": _evidence_refs_payload(review.evidence_refs),
        "actor_id": review.review.actor_id,
        "reviewed_at": review.review.reviewed_at.isoformat(),
    }


__all__ = [
    "DataQualityStorageError",
    "open_or_observe_exception",
    "list_exceptions",
    "get_exception",
    "mark_exception_resolved",
    "append_review",
    "get_current_review_projection",
    "DataQualityReviewConflictError",
    "_evidence_refs_payload",
    "_INVALID_FINGERPRINT_MSG",
    "_review_payload_matches",
    "_normalize_ts",
]
