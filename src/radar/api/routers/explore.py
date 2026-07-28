"""Chat exploratório sobre o catálogo + extração de perfil (auth opcional).

Unifica os antigos endpoints:
  - POST /kg-explore (graph.py)   — chat sem perfil, anônimo
  - POST /frontdoor/turn          — chat com perfil + extração, auth opcional

Per spec match-evolution.md Fase 2.
"""

from __future__ import annotations

import asyncio
import json
import logging

import jwt
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from radar.api.common import CompanyProfileSchema, explore_agent
from radar.api.rate_limit import get_client_ip, limiter
from radar.core.infra.auth import CurrentUserId, DbClient, OptionalDbClient, OptionalUserId
from radar.core.ingestion.profile_extractor import ProfileExtractor
from radar.core.kg.planning_node import is_complex_proposal
from radar.core.services.content_library import get_workspace_id
from radar.core.services.writing_session import persist_frontdoor_turn

logger = logging.getLogger(__name__)

router = APIRouter(tags=["explore"])


class ExploreRequest(BaseModel):
    # Caps (PR1.2 hardening-pre-beta): endpoint aceita anônimo — cap agressivo.
    message: str = Field(max_length=4_000)
    history: list[dict] = Field(default_factory=list, max_length=50)
    profile: CompanyProfileSchema | None = None
    edital_ids: list[str] = []
    node_id: str | None = None
    node_type: str | None = None
    session_id: str | None = None


def _profile_context_block(profile: CompanyProfileSchema | None) -> str:
    if profile is None:
        return ""
    filled: list[str] = []
    for field, label in (
        ("nome", "Empresa"),
        ("tipo_entidade", "Tipo"),
        ("one_liner", "Proposta"),
        ("solution_summary", "Solução"),
        ("descricao_atividades", "Atividades"),
        ("tamanho_empresa", "Porte"),
        ("uf", "UF"),
        ("trl", "TRL"),
        ("estagio", "Estágio"),
    ):
        val = getattr(profile, field, None)
        if val not in (None, "", [], "empresa"):
            filled.append(f"{label}: {val}")
    if not filled:
        return ""
    return "[Perfil parcial da empresa do usuário] " + " · ".join(filled)


def _explore_key(request: Request) -> str:
    """Key function for explore rate limit.

    Autenticado → bucket auth:{sub} com rate 10/min.
    Anônimo → bucket anon:{ip} com rate 3/min.
    """
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        try:
            payload = jwt.decode(auth[7:], options={"verify_signature": False})
            sub = payload.get("sub")
            if sub:
                return f"auth:{sub}"
        except Exception:
            pass
    return f"anon:{get_client_ip(request)}"


def _explore_limit(key: str) -> str:
    """Dynamic limit: autenticado 10/min, anônimo 3/min."""
    if key.startswith("auth:"):
        return "10/minute"
    return "3/minute"


@router.post(
    "/explore",
    summary="Chat exploratório + extração de perfil (auth opcional)",
)
@limiter.limit(_explore_limit, key_func=_explore_key)
def explore(
    request: Request,
    req: ExploreRequest,
    user_id: OptionalUserId,
    db: OptionalDbClient,
):
    """Conversa exploratória sobre o catálogo + extração de perfil.

    ExploreAgent classifica internamente a intenção (factual/reasoning/agent)
    e roteia para a rota adequada. Com perfil presente, a resposta é enriquecida
    com extração de profile_updates via ProfileExtractor.

    - Anônimo (sem JWT): rate 3/min, sem extração de perfil, sem persistência.
    - Autenticado (com JWT): rate 10/min, com extração e persistência da
      conversa nas tabelas writing_sessions/session_turns (kind='frontdoor').

    O histórico é enviado pelo cliente a cada turno; a persistência permite
    retomar a conversa pelo sidebar (/conversations).
    """
    message = req.message.strip()
    if not message:
        raise HTTPException(status_code=422, detail="Mensagem vazia.")

    ctx = _profile_context_block(req.profile)
    explore_message = f"{ctx}\n\n{message}" if ctx else message
    current = req.profile.model_dump() if req.profile is not None else {}

    answer, explore_meta = explore_agent.explore_with_meta(
        explore_message, req.history, req.edital_ids, req.node_id,
        req.node_type, has_profile=req.profile is not None,
        profile_text=ctx or None,
        # Estágio 0 (PR5/PR4.1): perfil estruturado p/ o filtro de elegibilidade
        # DENTRO da tool do agente (não só no match direto do router abaixo).
        profile=current or None,
    )

    return _post_process(message, req, ctx, current, answer, explore_meta, user_id, db)


def _post_process(
    message: str,
    req: ExploreRequest,
    ctx: str,
    current: dict,
    answer: str,
    explore_meta: dict,
    user_id: str | None,
    db,
) -> dict:
    """Pós-processamento do turno de explore — extração de diff de perfil e
    persistência. Match NÃO é mais acionado automaticamente aqui (P2): o
    usuário acessa os resultados via `/radar/matches` ou navegando até
    a página /radar. Compartilhado por `/explore` (sync) e
    `/explore/stream` — a mesma pipeline roda depois do `done` do
    streaming, via `asyncio.to_thread`."""
    # PR1 (four-phase-workflow): detecta se pergunta pede planejamento.
    result: dict = {
        "answer": answer,
        "truncated": explore_meta["truncated"],
        "route_decision": explore_meta.get("route_decision"),
        "called_tools": explore_meta.get("called_tools", []),
    }
    if is_complex_proposal(message, answer, len(req.history)):
        result["next_action"] = {
            "offer": "Estruturar proposta?",
            "options": [
                {"label": "Estruturar", "action": "goto_planning"},
                {"label": "Começar a escrever", "action": "goto_execution"},
            ],
        }
    diff = None
    if req.profile is not None:
        diff = ProfileExtractor().extract_diff_from_message(message, current)
        result["profile_diff"] = diff or None

    if user_id and db:
        workspace_id = None
        try:
            workspace_id = get_workspace_id(db, user_id)
            profile_diff_list = diff if diff else None
            persisted = persist_frontdoor_turn(
                db, workspace_id, message, result["answer"], profile_diff_list, req.session_id,
                matched_editais=result.get("matched_editais"),
                matched_entities=result.get("matched_entities"),
            )
            result["session_id"] = persisted["session_id"]
            result["entry_ids"] = persisted["entry_ids"]
        except Exception as e:
            logger.warning("explore: falha ao persistir turno: %s", e)

    return result


def _sse(event: str, data: dict) -> bytes:
    """Formata um frame SSE. `json.dumps` escapa quebras de linha do próprio
    JSON — o `\\n\\n` literal abaixo é só o delimitador de frame do protocolo."""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode()


@router.post(
    "/explore/stream",
    summary="Chat exploratório em streaming (SSE) — item 1, TASK 3",
)
@limiter.limit(_explore_limit, key_func=_explore_key)
async def explore_stream_endpoint(
    request: Request,
    req: ExploreRequest,
    user_id: OptionalUserId,
    db: OptionalDbClient,
):
    """Variação SSE de `/explore` — tokens ao vivo durante a geração,
    seguidos de um frame `done` com o MESMO shape do `/explore` síncrono
    (pós-processamento idêntico: diff de perfil, persistência —
    `_post_process`, rodado em thread por ser bloqueante).

    Formato:
        event: token  data: {"text": "<delta>"}
        event: tool   data: {"name": "<tool>", "phase": "end"}
        event: done   data: {...payload idêntico ao /explore...}
        event: error  data: {"message": "<msg>"}  — TERMINAL: nunca é seguido
            de um "done" no mesmo turno (ver `except` em `gen()`); a UI deve
            encerrar o turno no "error", não esperar um "done" que não vem.

    `/explore` (sync) fica intacto — este é um endpoint novo, aditivo.
    """
    message = req.message.strip()
    if not message:
        raise HTTPException(status_code=422, detail="Mensagem vazia.")

    ctx = _profile_context_block(req.profile)
    explore_message = f"{ctx}\n\n{message}" if ctx else message
    current = req.profile.model_dump() if req.profile is not None else {}

    # Item 3 (TASK 3): escopo da thread-por-sessão do explore. `thread_id` só existe
    # com usuário autenticado (workspace) + sessão; anônimo/sem sessão → None →
    # stateless (caminho de hoje). Passa-se o thread_id PRONTO (não workspace_id) p/
    # manter o wiring de tools do streaming byte-idêntico (ortogonalidade da T3).
    thread_id = None
    if user_id and db is not None and req.session_id:
        try:
            ws = get_workspace_id(db, user_id)
            if ws:
                thread_id = f"{ws}:{req.session_id}"
        except Exception as e:  # noqa: BLE001 — degrada para stateless
            logger.warning("explore/stream: thread_id não resolvido (%s) — stateless", e)

    async def gen():
        try:
            answer = ""
            explore_meta: dict = {}
            async for event in explore_agent.explore_stream(
                explore_message, req.history, req.edital_ids, req.node_id,
                req.node_type, has_profile=req.profile is not None,
                profile_text=ctx or None,
                profile=current or None,
                thread_id=thread_id,
            ):
                if event.kind == "token":
                    yield _sse("token", {"text": event.text})
                elif event.kind == "tool_end":
                    yield _sse("tool", {"name": event.name, "phase": "end"})
                elif event.kind == "final":
                    answer = event.answer
                    explore_meta = event.meta

            # Disconnect do cliente antes daqui (fecha a aba, perde a rede) faz o
            # Starlette encerrar este generator sem rodar _post_process — o turno
            # NUNCA é persistido. Divergência aceita vs /explore sync (que só
            # responde depois de persistir): streaming troca "sempre persiste"
            # por "persiste se o cliente ficar até o fim".
            result = await asyncio.to_thread(
                _post_process, message, req, ctx, current, answer, explore_meta, user_id, db,
            )
            yield _sse("done", result)
        except Exception as e:
            logger.error("explore/stream: falha: %s", e)
            yield _sse("error", {"message": "Erro ao processar. Tente novamente."})

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


class VerdictsRequest(BaseModel):
    # Cap generoso (top-K do radar é 8 hoje) sem deixar o body virar scan.
    oportunidade_ids: list[str] = Field(max_length=32)


@router.post(
    "/match/verdicts",
    summary="Vereditos LLM cacheados do radar (Estágio 2 — fetch cache-only)",
)
def fetch_match_verdicts(req: VerdictsRequest, user_id: CurrentUserId, db: DbClient):
    """Poll do card: devolve os vereditos JÁ computados do workspace para os ids
    pedidos — zero LLM (o cômputo é da task `compute_match_verdicts`). Devolve o
    veredito mais recente por par SEM revalidar o `input_hash`: o poll acontece
    segundos após o defer; um perfil editado no meio re-hasheia e re-enfileira no
    próximo refresh do radar (a linha é substituída pelo upsert)."""
    if not req.oportunidade_ids:
        return {"verdicts": {}}
    workspace_id = get_workspace_id(db, user_id)
    rows = (
        db.table("match_verdicts")
        .select("oportunidade_id, verdict")
        .eq("workspace_id", workspace_id)
        .in_("oportunidade_id", req.oportunidade_ids)
        .execute()
        .data
        or []
    )
    return {"verdicts": {r["oportunidade_id"]: r["verdict"] for r in rows}}
