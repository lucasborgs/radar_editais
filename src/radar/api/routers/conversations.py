"""Conversas unificadas (spec frontend chat-first, fase 2).

Lista e detalha conversas de QUALQUER `kind` (writing | frontdoor) sobre as
mesmas tabelas writing_sessions/session_turns — a generalização "uma conversa é
uma conversa" da spec. O sidebar do front consome `GET /conversations`; abrir
uma conversa frontdoor reconstrói o transcript via `GET /conversations/{id}`.

As entradas heterogêneas (msg | diff | radar) ficam em session_turns: `msg` usa
role+content; `diff`/`radar` usam `payload`. Este router não roda LLM nem aplica
diffs — só lê/grava o que aconteceu ("AI drafts, humans decide" inalterado).
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from radar.core.infra.auth import CurrentUserId, DbClient
from radar.core.kg import entity_catalog
from radar.core.services.consultant import ConsultantRepository
from radar.core.services.content_library import get_workspace_id
from radar.core.services.writing_session import (
    append_entry,
    get_conversation,
    list_sessions,
    update_entry_payload,
)

router = APIRouter(tags=["conversations"])


# =============================================================================
# SCHEMAS
# =============================================================================


class ConversationSummary(BaseModel):
    """Item da lista unificada do sidebar (spec, Contratos de API)."""

    session_id: str
    kind: Literal["frontdoor", "writing", "consultant"]
    title: str | None = None  # frontdoor usa title; writing deriva edital_title
    edital_id: str | None = None
    edital_title: str | None = None  # resolvido server-side (batch), p/ writing
    status: Literal["active", "completed", "abandoned"]
    turn_count: int
    created_at: str
    updated_at: str


class Entry(BaseModel):
    """Entrada do transcript (spec, Contratos de API)."""

    id: int  # session_turns.id
    turn_index: int
    entry_kind: Literal["msg", "diff", "radar"]
    role: Literal["user", "assistant"]
    content: str  # entry_kind=msg
    payload: dict | None = None  # entry_kind=diff|radar


class ConversationDetail(BaseModel):
    session_id: str
    kind: Literal["frontdoor", "writing"]
    title: str | None = None
    edital_id: str | None = None
    status: Literal["active", "completed", "abandoned"]
    created_at: str
    updated_at: str
    entries: list[Entry]


class AppendEntryRequest(BaseModel):
    """Append de entrada não-msg (radar inline; diff também aceito).

    `msg` NÃO é appendável por aqui — mensagens nascem no /frontdoor/turn ou no
    /writing/turn (têm role+content e disparam LLM). Este endpoint serve
    entradas geradas no cliente (ex.: cards de radar após o usuário aceitar o diff).
    """

    entry_kind: Literal["radar", "diff"]
    payload: dict[str, Any]


class UpdateEntryRequest(BaseModel):
    """Substitui o payload de uma entrada (ex.: status do diff
    pending→accepted/dismissed)."""

    payload: dict[str, Any]


# =============================================================================
# ENDPOINTS
# =============================================================================


@router.get("/conversations", summary="Lista unificada de conversas do workspace")
def list_conversations(user_id: CurrentUserId, db: DbClient):
    """Lista todas as conversas (writing + frontdoor) do workspace, recentes
    primeiro. Substitui `GET /writing/sessions` no front (o antigo sobrevive até
    a fase 3 matar os últimos consumidores).

    `title` vem só para frontdoor; para writing o título do edital é resolvido
    server-side em batch (`get_opportunity_titles`), evitando N lookups
    client-side por `getEditalById`.
    """
    workspace_id = get_workspace_id(db, user_id)
    sessions = list_sessions(db, workspace_id)
    try:
        sessions.extend(ConsultantRepository().list(db, workspace_id))
    except Exception:
        # A deploy ainda sem a migration nova não derruba o histórico legado.
        pass
    sessions.sort(key=lambda item: item.get("updated_at") or "", reverse=True)

    # Resolve títulos de edital em batch (mesmo helper do /writing/sessions).
    # get_opportunity_titles não depende do read model temporal (mais robusto
    # que get_edital por-id). Falha de resolução é graça — o front cai no ID cru.
    ids = {s["edital_id"] for s in sessions if s.get("edital_id")}
    try:
        titles = entity_catalog.get_opportunity_titles(list(ids)) if ids else {}
    except Exception:
        titles = {}

    # list_sessions devolve kind/title (migration 020); normalizamos kind para o
    # default 'writing' por robustez contra rows pré-migração.
    conversations = [
        ConversationSummary(
            session_id=s["session_id"],
            kind=s.get("kind") or "writing",
            title=s.get("title"),
            edital_id=s.get("edital_id"),
            edital_title=titles.get(s.get("edital_id")),
            status=s["status"],
            turn_count=s.get("turn_count", 0),
            created_at=s["created_at"],
            updated_at=s["updated_at"],
        )
        for s in sessions
    ]
    return {"conversations": conversations}


@router.get(
    "/conversations/{session_id}",
    summary="Header + transcript de uma conversa",
    response_model=ConversationDetail,
)
def get_conversation_detail(session_id: str, user_id: CurrentUserId, db: DbClient):
    """Header da conversa + entradas ordenadas por turn_index. 404 se a conversa
    não existir ou for de outro workspace."""
    workspace_id = get_workspace_id(db, user_id)
    convo = get_conversation(db, session_id, workspace_id)
    if convo is None:
        raise HTTPException(status_code=404, detail="Conversa não encontrada")
    return ConversationDetail(**convo)


@router.post(
    "/conversations/{session_id}/entries",
    summary="Appenda entrada não-msg (radar) ao transcript",
    response_model=Entry,
)
def append_conversation_entry(
    session_id: str,
    req: AppendEntryRequest,
    user_id: CurrentUserId,
    db: DbClient,
):
    """Appenda uma entrada (radar/diff) gerada no cliente. Atribui o próximo
    turn_index, role='assistant', content=''. 404 se a conversa for de outro
    workspace."""
    workspace_id = get_workspace_id(db, user_id)
    conversation = get_conversation(db, session_id, workspace_id)
    if conversation and conversation.get("kind") == "frontdoor":
        raise HTTPException(
            status_code=410,
            detail="Conversas antigas são somente leitura; continue pela jornada do consultor.",
        )
    entry = append_entry(db, session_id, workspace_id, req.entry_kind, req.payload)
    if entry is None:
        raise HTTPException(status_code=404, detail="Conversa não encontrada")
    return Entry(**entry)


@router.patch(
    "/conversations/{session_id}/entries/{entry_id}",
    summary="Substitui o payload de uma entrada (ex.: status do diff)",
    response_model=Entry,
)
def patch_conversation_entry(
    session_id: str,
    entry_id: int,
    req: UpdateEntryRequest,
    user_id: CurrentUserId,
    db: DbClient,
):
    """Substitui o payload de uma entrada do transcript — caso de uso: o usuário
    aceita/descarta o diff (status pending→accepted/dismissed). 404 se a
    entrada/conversa não pertencer ao workspace."""
    workspace_id = get_workspace_id(db, user_id)
    conversation = get_conversation(db, session_id, workspace_id)
    if conversation and conversation.get("kind") == "frontdoor":
        raise HTTPException(
            status_code=410,
            detail="Conversas antigas são somente leitura; continue pela jornada do consultor.",
        )
    entry = update_entry_payload(db, session_id, entry_id, workspace_id, req.payload)
    if entry is None:
        raise HTTPException(status_code=404, detail="Entrada não encontrada")
    return Entry(**entry)
