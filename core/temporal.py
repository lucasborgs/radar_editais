"""
core/temporal.py — Consciência temporal canônica para prompts (Front 3).

Fonte única do bloco `[CONTEXTO TEMPORAL: ...]` injetado nos prompts de
match, escrita, brief e critic. Lê `deadline`/`status` do índice
(`index.json`) com fallback para a wiki page, calcula dias restantes contra
`date.today()`, e renderiza um bloco de texto pronto para prompt.

Por que `date.today()` e não `index.reference_date` como "hoje":
  • O re-filtro de vigência do HybridMatch já usa `date.today()` em runtime
    (defesa-em-profundidade §7.1 WIKI.md). Ancorar a escrita/brief/critic na
    MESMA data mantém todo o produto consistente — match e texto nunca
    discordam sobre se um edital está vigente.
  • `reference_date` é quando o índice foi construído; pode estar stale se o
    cron não rebuildou. Expomos como `reference_date` para transparência
    (detecção de staleness), mas "hoje" é sempre o presente real.

Função pura de leitura: sem escrita, cache leve por mtime do índice.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date, datetime

from config import KNOWLEDGE_GRAPH_DIR
from core.edital_id import wiki_page_path
from core.wiki_schema import parse_deadline

logger = logging.getLogger(__name__)

_INDEX_FILE = KNOWLEDGE_GRAPH_DIR / "index.json"

# Cache leve: (mtime, index_dict). Reconstruído quando o arquivo muda.
_index_cache: tuple[float, dict] | None = None


def _load_index() -> dict:
    global _index_cache
    if not _INDEX_FILE.exists():
        return {}
    try:
        mtime = _INDEX_FILE.stat().st_mtime
        if _index_cache is not None and _index_cache[0] == mtime:
            return _index_cache[1]
        data = json.loads(_INDEX_FILE.read_text(encoding="utf-8"))
        _index_cache = (mtime, data)
        return data
    except Exception as e:
        logger.warning("temporal: falha ao ler index.json: %s", e)
        return {}


def _index_entry(edital_id: str) -> dict | None:
    for entry in _load_index().get("editais", []):
        if entry.get("id") == edital_id:
            return entry
    return None


def _reference_date() -> date | None:
    raw = _load_index().get("reference_date")
    if not raw:
        return None
    try:
        return datetime.strptime(str(raw).strip(), "%Y-%m-%d").date()
    except ValueError:
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
    """Monta o TemporalContext de um edital lendo índice + wiki page.

    Retorna None se o edital não existir em nenhuma das fontes (não dá pra
    afirmar nada temporal sobre ele). Deadline é buscado primeiro no índice
    (sempre fresco no build) e, se ausente, na wiki page.
    """
    entry = _index_entry(edital_id)

    deadline_raw: str | None = entry.get("deadline") if entry else None
    status: str | None = entry.get("status") if entry else None

    # Fallback para a wiki page quando o índice não tem o campo.
    if (deadline_raw is None or status is None) and edital_id:
        wiki_file = wiki_page_path(edital_id)
        if wiki_file.exists():
            try:
                page = json.loads(wiki_file.read_text(encoding="utf-8"))
                if deadline_raw is None:
                    deadline_raw = page.get("deadline")
                if status is None:
                    status = page.get("status")
            except Exception as e:
                logger.debug("temporal: falha ao ler wiki page %s: %s", edital_id, e)

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
