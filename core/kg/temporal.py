"""
core/temporal.py — Consciência temporal canônica para prompts (Front 3).

Fonte única do bloco `[CONTEXTO TEMPORAL: ...]` injetado nos prompts de
match, escrita, brief e critic. Lê `deadline`/`status` do catálogo SQL
(entities, via entity_catalog), calcula dias restantes contra `date.today()`,
e renderiza um bloco de texto pronto para prompt.

Função pura de leitura: sem escrita.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date

from core.kg import entity_catalog
from core.kg.schema import parse_deadline

logger = logging.getLogger(__name__)


def _index_entry(edital_id: str) -> dict | None:
    """{deadline (dd/mm/yyyy), status} do edital/programa via SQL, ou None."""
    return entity_catalog.get_entity_temporal(edital_id)


def _reference_date() -> date | None:
    return None


@dataclass
class TemporalContext:
    """Estado temporal de um edital relativo a hoje.

    `deadline` None = fluxo contínuo (sem prazo fixo) — espelha o tratamento
    do build/runtime, onde deadline ausente NÃO é considerado expirado.
    """
    edital_id: str
    today: date
    reference_date: date | None
    deadline: date | None
    deadline_raw: str | None
    status: str | None
    days_remaining: int | None  # None se sem deadline
    expired: bool


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

    today = date.today()
    deadline = parse_deadline(deadline_raw)
    days_remaining = (deadline - today).days if deadline is not None else None
    expired = deadline is not None and deadline < today

    return TemporalContext(
        edital_id=edital_id,
        today=today,
        reference_date=_reference_date(),
        deadline=deadline,
        deadline_raw=deadline_raw,
        status=status,
        days_remaining=days_remaining,
        expired=expired,
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

    if ctx.deadline is None:
        prazo = (
            f"O edital {ctx.edital_id} não tem prazo de submissão fixo "
            f"(fluxo contínuo); não invente uma data de encerramento."
        )
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
    today_s = date.today().isoformat()
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
