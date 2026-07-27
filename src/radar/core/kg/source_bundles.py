"""core/kg/source_bundles.py — Repositório append-only de SourceBundle (RT04-T02).

Persiste o histórico imutável de pacotes documentais versionados em
`source_bundles` (migration 044). Service-role only: RLS habilitada sem
policies de usuário final — acesso exclusivo via get_supabase_service().

Sem Supabase configurado, save → no-op False, load → None (degrada
gracioso como source_docs).
"""
from __future__ import annotations

import json
import logging
import os

from postgrest.exceptions import APIError

from radar.domain.source_bundle import SourceBundle

logger = logging.getLogger(__name__)

_TABLE = "source_bundles"


def _pg_configured() -> bool:
    return bool(os.getenv("SUPABASE_URL") and os.getenv("SUPABASE_SERVICE_KEY"))


_PG_UNIQUE_VIOLATION = "23505"


def save(bundle: SourceBundle) -> bool:
    """Persiste um SourceBundle de forma append-only e idempotente.

    Usa a constraint UNIQUE (subject_kind, subject_id, bundle_hash) do banco
    para detectar duplicatas materialmente idênticas. Retorna True tanto na
    primeira inserção quanto na repetição idempotente.

    Falhas reais de persistência (ex.: banco indisponível, schema ausente)
    são logadas e retornam False — nunca silenciadas como sucesso.

    Args:
        bundle: SourceBundle já validado pelo contrato do domínio.

    Returns:
        True se persistiu ou já existia (idempotente).
        False em falha real de persistência.
    """
    if not _pg_configured():
        logger.info("source_bundles.save: Supabase ausente — no-op")
        return False

    bundle_hash = bundle.compute_bundle_hash()
    payload = {
        "subject_kind": bundle.subject_kind.value,
        "subject_id": bundle.subject_id,
        "source": bundle.source,
        "bundle_hash": bundle_hash,
        "bundle": json.loads(bundle.model_dump_json()),
        "acquisition_status": bundle.acquisition_status.value,
        "collected_at": bundle.collected_at.isoformat(),
    }

    try:
        from radar.core.infra.db import get_supabase_service

        get_supabase_service().table(_TABLE).insert(payload).execute()
        return True
    except APIError as e:
        if e.code == _PG_UNIQUE_VIOLATION:
            return True  # idempotente: mesmo bundle já existe
        logger.warning(
            "source_bundles.save: erro de persistência "
            "(%s/%s): code=%s",
            bundle.subject_kind.value,
            bundle.subject_id,
            e.code,
        )
        return False
    except Exception as e:
        logger.warning(
            "source_bundles.save: erro inesperado "
            "(%s/%s): %s",
            bundle.subject_kind.value,
            bundle.subject_id,
            e,
        )
        return False


def load(subject_kind: str, subject_id: str) -> SourceBundle | None:
    """Carrega o último bundle complete do sujeito.

    Retorna o SourceBundle mais recente com acquisition_status='complete',
    ordenado por collected_at DESC, created_at DESC, id DESC (desempate
    determinístico). Bundles partial NUNCA substituem o último complete.

    Args:
        subject_kind: Valor textual do SubjectKind ("opportunity", "ict", ...).
        subject_id: ID canônico do sujeito.

    Returns:
        SourceBundle ou None se nenhum bundle complete existir.
    """
    if not _pg_configured():
        return None

    try:
        from radar.core.infra.db import get_supabase_service

        resp = (
            get_supabase_service()
            .table(_TABLE)
            .select("bundle")
            .eq("subject_kind", subject_kind)
            .eq("subject_id", subject_id)
            .eq("acquisition_status", "complete")
            .order("collected_at", desc=True)
            .order("created_at", desc=True)
            .order("id", desc=True)
            .limit(1)
            .execute()
        )
    except Exception as e:
        logger.warning(
            "source_bundles.load: falha lendo %s/%s: %s",
            subject_kind,
            subject_id,
            e,
        )
        return None

    rows = resp.data or []
    if not rows:
        return None

    bundle_data = rows[0].get("bundle")
    if not bundle_data:
        return None

    return SourceBundle.model_validate(bundle_data)
