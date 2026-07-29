"""
core/temporal.py — Consciência temporal canônica para prompts (Front 3).

Fonte única do bloco `[CONTEXTO TEMPORAL: ...]` injetado nos prompts de
match, escrita, brief e critic. Lê o payload do read model temporal canônico
via ``entity_catalog`` e nunca deduz fluxo contínuo de prazo ausente.

Função pura de leitura: sem escrita.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date

from radar.core.kg import entity_catalog
from radar.core.kg.schema import parse_deadline
from radar.core.services.temporal_read_model import today_sao_paulo

logger = logging.getLogger(__name__)


def _index_entry(edital_id: str) -> dict | None:
    """{deadline (dd/mm/yyyy), status} do edital/programa via SQL, ou None."""
    return entity_catalog.get_entity_temporal(edital_id)


@dataclass
class TemporalContext:
    """Estado temporal de um edital relativo a hoje.

    ``deadline`` vazio só é contínuo quando o read model o afirma
    explicitamente; no restante é validade a confirmar.
    """
    edital_id: str
    today: date
    deadline: date | None
    deadline_raw: str | None
    status: str | None
    days_remaining: int | None  # None se sem deadline
    expired: bool
    temporal_mode: str
    validity_state: str
    temporal_value: str | None
    decision_source: str | None
    last_verified_at: str | None


def temporal_context(edital_id: str) -> TemporalContext | None:
    """Monta o TemporalContext lendo o edital no catálogo gold relacional.

    Retorna None se a entidade não existir, pois nesse caso não há fonte
    autoritativa para afirmar seu estado temporal.
    """
    entry = _index_entry(edital_id)

    deadline_raw: str | None = entry.get("deadline") if entry else None
    status: str | None = entry.get("status") if entry else None

    if entry is None and status is None and deadline_raw is None:
        return None

    today = today_sao_paulo()
    deadline = parse_deadline(deadline_raw)
    days_remaining = (deadline - today).days if deadline is not None else None
    expired = deadline is not None and deadline < today

    return TemporalContext(
        edital_id=edital_id,
        today=today,
        deadline=deadline,
        deadline_raw=deadline_raw,
        status=status,
        days_remaining=days_remaining,
        expired=expired,
        temporal_mode=entry.get("temporal_mode") or "unknown",
        validity_state=entry.get("validity_state") or "needs_review",
        temporal_value=entry.get("temporal_value"),
        decision_source=entry.get("decision_source"),
        last_verified_at=entry.get("last_verified_at"),
    )


def render_temporal_block(edital_id: str) -> str:
    """Bloco `[CONTEXTO TEMPORAL: ...]` para um único edital (escrita/brief/critic).

    Retorna "" se não houver dados temporais sobre o edital — o caller injeta
    o bloco condicionalmente, sem poluir o prompt com vazio.
    """
    ctx = temporal_context(edital_id)
    if ctx is None:
        return ""

    today_s = ctx.today.isoformat()

    if ctx.validity_state == "needs_review":
        return (
            f"[CONTEXTO TEMPORAL: hoje é {today_s}. A validade do edital "
            f"{ctx.edital_id} está a confirmar. Não afirme que ele está aberto, "
            f"não invente prazo e não trate ausência de data como fluxo contínuo.]"
        )
    if ctx.validity_state == "closed" and ctx.deadline is None:
        return (
            f"[CONTEXTO TEMPORAL: hoje é {today_s}. O edital {ctx.edital_id} "
            f"deve ser tratado como encerrado. Não o descreva como aberto e não "
            f"invente uma nova data de encerramento.]"
        )
    if ctx.temporal_mode == "continuous" and ctx.validity_state == "active":
        prazo = (
            f"O edital {ctx.edital_id} não tem prazo de submissão fixo "
            f"(fluxo contínuo); não invente uma data de encerramento."
        )
    elif ctx.deadline is None:
        return ""
    elif ctx.expired:
        atraso = abs(ctx.days_remaining or 0)
        prazo = (
            f"ATENÇÃO: o prazo do edital {ctx.edital_id} encerrou em "
            f"{ctx.deadline_raw} — há {atraso} dia(s). Avise o usuário e NÃO "
            f"prossiga como se o edital estivesse aberto sem confirmação dele."
        )
    else:
        dias = ctx.days_remaining
        urgencia = " (prazo curto — destaque a urgência)" if dias is not None and dias <= 15 else ""
        prazo = (
            f"O edital {ctx.edital_id} encerra em {ctx.deadline_raw} "
            f"({dias} dia(s) restantes){urgencia}."
        )

    return (
        f"[CONTEXTO TEMPORAL: hoje é {today_s}. {prazo} "
        f"Ao mencionar prazos, relativize-os a hoje; copie status e deadline "
        f"verbatim do catálogo — nunca estime nem extrapole datas.]"
    )


def render_match_temporal_block() -> str:
    """Bloco temporal genérico para o Stage 2 do match (vários editais de uma vez).

    Não é específico de um edital — afirma a data de hoje e instrui o LLM a
    copiar `status`/`deadline` verbatim do catálogo recebido, nunca estimar.
    """
    today_s = today_sao_paulo().isoformat()
    return (
        f"[CONTEXTO TEMPORAL: hoje é {today_s}. Os editais listados já foram "
        f"filtrados por vigência. Ao comentar prazos, copie `status` e "
        f"`deadline` verbatim do catálogo — nunca estime, extrapole ou "
        f"presuma uma data que não esteja explícita.]"
    )


__all__ = [
    "TemporalContext",
    "temporal_context",
    "render_temporal_block",
    "render_match_temporal_block",
]
