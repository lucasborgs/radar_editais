"""Repositório append-only dos artifacts RT06-T01."""
from __future__ import annotations

import json
import logging
import os

from postgrest.exceptions import APIError

from radar.domain.adaptive_extraction import ExtractionArtifact

logger = logging.getLogger(__name__)

TABLE = "document_extractions"
UNIQUE_VIOLATION = "23505"


class ExtractionStorageError(Exception):
    """Falha sanitizada de durabilidade do artifact."""


def _configured() -> bool:
    return bool(os.getenv("SUPABASE_URL") and os.getenv("SUPABASE_SERVICE_KEY"))


def is_configured() -> bool:
    """Indica se o repositório pode confirmar durabilidade."""
    return _configured()


def load(fingerprint: str) -> ExtractionArtifact | None:
    if not _configured():
        return None
    try:
        from radar.core.infra.db import get_supabase_service

        response = (
            get_supabase_service()
            .table(TABLE)
            .select("artifact")
            .eq("fingerprint", fingerprint)
            .order("created_at", desc=True)
            .execute()
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("document_extractions.load: category=%s", type(exc).__name__)
        raise ExtractionStorageError("load failed") from None
    rows = response.data or []
    if not rows:
        return None
    fallback: ExtractionArtifact | None = None
    for row in rows:
        try:
            artifact = row.get("artifact")
            parsed = ExtractionArtifact.model_validate(artifact) if artifact else None
            fallback = fallback or parsed
            if parsed and parsed.status.value in {"complete", "partial"}:
                return parsed
        except Exception:  # noqa: BLE001
            logger.warning("document_extractions.load: invalid artifact ignored")
    return fallback


def load_attempt(fingerprint: str, attempt_id: str) -> ExtractionArtifact | None:
    """Carrega uma tentativa específica para confirmar sua própria gravação."""
    if not _configured():
        return None
    try:
        from radar.core.infra.db import get_supabase_service

        response = (
            get_supabase_service()
            .table(TABLE)
            .select("artifact")
            .eq("fingerprint", fingerprint)
            .eq("attempt_id", attempt_id)
            .limit(1)
            .execute()
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("document_extractions.load_attempt: category=%s", type(exc).__name__)
        raise ExtractionStorageError("load attempt failed") from None
    for row in response.data or []:
        try:
            artifact = row.get("artifact")
            if artifact:
                return ExtractionArtifact.model_validate(artifact)
        except Exception:  # noqa: BLE001
            logger.warning("document_extractions.load_attempt: invalid artifact ignored")
    return None


def list_for_subject(subject_id: str) -> list[ExtractionArtifact]:
    """Lista versões sem escolher autoridade; a seleção pertence ao read model."""
    if not _configured():
        return []
    try:
        from radar.core.infra.db import get_supabase_service

        response = (
            get_supabase_service()
            .table(TABLE)
            .select("artifact")
            .eq("subject_id", subject_id)
            .order("created_at", desc=True)
            .execute()
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("document_extractions.list: category=%s", type(exc).__name__)
        raise ExtractionStorageError("list failed") from None
    artifacts: list[ExtractionArtifact] = []
    for row in response.data or []:
        try:
            if row.get("artifact"):
                artifacts.append(ExtractionArtifact.model_validate(row["artifact"]))
        except Exception:  # noqa: BLE001
            logger.warning("document_extractions.list: invalid artifact ignored")
    return artifacts


def list_for_subjects(subject_ids: list[str]) -> dict[str, list[ExtractionArtifact]]:
    """Carrega artifacts de vários sujeitos em uma leitura.

    O retorno inclui somente sujeitos encontrados.  A função existe para que
    consumidores de cards não façam uma consulta por oportunidade.
    """
    ids = sorted({sid for sid in subject_ids if isinstance(sid, str) and sid.strip()})
    out: dict[str, list[ExtractionArtifact]] = {sid: [] for sid in ids}
    if not ids or not _configured():
        return out
    try:
        from radar.core.infra.db import get_supabase_service

        response = (
            get_supabase_service()
            .table(TABLE)
            .select("subject_id, artifact")
            .in_("subject_id", ids)
            .order("created_at", desc=True)
            .execute()
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("document_extractions.list_many: category=%s", type(exc).__name__)
        raise ExtractionStorageError("list failed") from None
    for row in response.data or []:
        sid = row.get("subject_id")
        try:
            if sid in out and row.get("artifact"):
                out[sid].append(ExtractionArtifact.model_validate(row["artifact"]))
        except Exception:  # noqa: BLE001
            logger.warning("document_extractions.list_many: invalid artifact ignored")
    return out


def save(artifact: ExtractionArtifact) -> bool:
    """Insere uma tentativa; conflitos de identidade são idempotentes."""
    if not _configured():
        logger.info("document_extractions.save: Supabase ausente — no-op")
        return False
    payload = {
        "fingerprint": artifact.fingerprint,
        "attempt_id": artifact.attempt_id,
        "subject_id": artifact.subject_id,
        "asset_hash": artifact.asset_hash,
        "bundle_hash": artifact.bundle_hash,
        "schema_version": artifact.schema_version,
        "status": artifact.status.value,
        "artifact": json.loads(artifact.model_dump_json()),
        "created_at": artifact.created_at.isoformat(),
    }
    try:
        from radar.core.infra.db import get_supabase_service

        get_supabase_service().table(TABLE).insert(payload).execute()
        return True
    except APIError as exc:
        if exc.code == UNIQUE_VIOLATION:
            return True
        logger.warning("document_extractions.save: category=api_error")
        raise ExtractionStorageError("save failed") from None
    except Exception as exc:  # noqa: BLE001
        logger.warning("document_extractions.save: category=%s", type(exc).__name__)
        raise ExtractionStorageError("save failed") from None


__all__ = [
    "ExtractionStorageError",
    "is_configured",
    "list_for_subject",
    "list_for_subjects",
    "load",
    "load_attempt",
    "save",
]
