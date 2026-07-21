"""
Profile drift detector — heurística simples (Gap 4).

`CompanyProfile` é o prefixo estático de toda WritingSession; se estiver
desatualizado, todas as sessões herdam contexto errado silenciosamente. A
camada de identidade não tem reflexão (por design — perfil é declaração,
não inferência), mas precisa de um sinal de "olha, talvez seja hora de
revisar".

Estratégia: heurística pura, sem LLM. Disponibiliza um sinal pro frontend
exibir banner; decisão de atualizar fica com o usuário (filosofia
"AI drafts, humans decide" aplicada à camada de identidade).

Regra: `stale=True` se ambos forem verdade:
  • workspace.profile_updated_at < hoje - STALE_AFTER_DAYS (default 90)
  • count(content_items criados depois) >= MIN_NEW_ITEMS (default 3)

A combinação evita falso positivo: perfil de 6 meses sem nova evidência
(library vazia) provavelmente continua válido; perfil de 6 meses com 10
items novos provavelmente reflete uma empresa que mudou.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from supabase import Client

logger = logging.getLogger(__name__)

STALE_AFTER_DAYS = 90
MIN_NEW_ITEMS = 3


def detect_profile_drift(
    db: Client,
    workspace_id: str,
    stale_after_days: int = STALE_AFTER_DAYS,
    min_new_items: int = MIN_NEW_ITEMS,
) -> dict:
    """Avalia se o profile do workspace está provavelmente desatualizado.

    Returns dict:
      stale: bool                      — sinal pra UI
      days_since_update: int           — quantos dias desde a última edição
                                          do profile
      new_items_since: int             — items na library criados depois
                                          da última edição do profile
      profile_updated_at: str          — ISO timestamp (debug)
      recommendation: str | None       — texto pra exibir; None se !stale

    Falha graciosa: em erro de DB devolve stale=False com diagnóstico em
    log (não bloqueia o frontend).
    """
    try:
        ws_result = (
            db.table("workspaces")
            .select("profile_updated_at, created_at")
            .eq("id", workspace_id)
            .maybe_single()
            .execute()
        )
    except Exception as e:
        logger.warning("detect_profile_drift: falha ao ler workspace=%s: %s", workspace_id, e)
        return _empty_drift()

    row = ws_result.data if ws_result else None
    if not row:
        return _empty_drift()

    # Fallback: workspaces pré-migration 011 podem não ter profile_updated_at.
    profile_updated_at_raw = row.get("profile_updated_at") or row.get("created_at")
    if not profile_updated_at_raw:
        return _empty_drift()

    try:
        profile_updated_at = _parse_iso(profile_updated_at_raw)
    except (ValueError, TypeError) as e:
        logger.debug("detect_profile_drift: parse falhou para %r: %s", profile_updated_at_raw, e)
        return _empty_drift()

    now = datetime.now(timezone.utc)
    days_since = (now - profile_updated_at).days

    # Conta items criados depois da última edição do profile.
    try:
        new_items = (
            db.table("content_items")
            .select("id", count="exact")
            .eq("workspace_id", workspace_id)
            .is_("archived_at", "null")
            .gt("created_at", profile_updated_at.isoformat())
            .execute()
        )
        new_items_count = new_items.count or 0
    except Exception as e:
        logger.warning("detect_profile_drift: falha ao contar items: %s", e)
        new_items_count = 0

    stale = days_since >= stale_after_days and new_items_count >= min_new_items
    recommendation = None
    if stale:
        recommendation = (
            f"Seu perfil foi atualizado pela última vez há {days_since} dias. "
            f"Você adicionou {new_items_count} itens à biblioteca desde então. "
            "Considere revisar o perfil para manter o contexto das propostas atualizado."
        )

    return {
        "stale": stale,
        "days_since_update": days_since,
        "new_items_since": new_items_count,
        "profile_updated_at": profile_updated_at.isoformat(),
        "recommendation": recommendation,
    }


def _empty_drift() -> dict:
    return {
        "stale": False,
        "days_since_update": 0,
        "new_items_since": 0,
        "profile_updated_at": None,
        "recommendation": None,
    }


def _parse_iso(value: str | datetime) -> datetime:
    """Aceita string ISO ou datetime nativo (supabase-py varia entre versões)."""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    # Supabase devolve algo como "2026-05-28T12:34:56.789+00:00" ou com "Z".
    s = value.replace("Z", "+00:00") if isinstance(value, str) else str(value)
    dt = datetime.fromisoformat(s)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
