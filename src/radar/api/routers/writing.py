"""Writing Session (persistida em Postgres — ADR B1): sessões, turnos,
documento, checklist e export."""

from __future__ import annotations

import asyncio
import json
import logging
import threading

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from radar.api.common import (
    CompanyProfileSchema,
    load_library_items,
    profile_from_workspace,
    to_py_profile,
)
from radar.api.rate_limit import limiter
from radar.core.infra.auth import CurrentUserId, DbClient
from radar.core.kg import entity_catalog
from radar.core.services.content_library import get_workspace_id
from radar.core.services.writing_session import (
    WritingSession,
    cancel_turn,
    delete_session,
    get_session_document,
    list_sessions,
    register_cancel_token,
    unregister_cancel_token,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["writing"])


# =============================================================================
# SCHEMAS
# =============================================================================


class WritingTurnRequest(BaseModel):
    session_id: str
    # Caps (PR1.2 hardening-pre-beta): o rate limit é por minuto; o cap limita
    # o custo LLM de UM request.
    user_message: str = Field(max_length=16_000)
    section_hint: str | None = Field(default=None, max_length=200)
    profile: CompanyProfileSchema | None = None
    library_item_ids: list[str] = []
    model_tier: str | None = None  # Fase 4 #24: 'fast' | 'auto' | 'pro'
    # H2: idempotency_key — UUID v4 gerado pelo frontend por tentativa distinta
    # de turno. Retentativas reusam a mesma chave. Backend cacheia a resposta
    # completa e a reentrega sem processar o LLM novamente.
    idempotency_key: str | None = None


# Sprint 2 do Cenário B: response do /writing/turn passa a carregar 2 campos
# opcionais (só presentes no path agente). Frontend usa `pending_user_input`
# para renderizar prompt destacado; `tool_trace` é exposto para observabilidade
# (debug + futura visualização). Campos legacy (draft_content) seguem iguais.
class PendingUserInput(BaseModel):
    field: str
    prompt: str


class ToolTraceEntry(BaseModel):
    id: str
    name: str
    input: dict
    output: str
    # W-D1: presente só em entradas save_draft bem-sucedidas — título normalizado
    # da seção persistida (usado pela co-edição do workspace p/ highlight + undo).
    saved_section: str | None = None
    # F3: veredito estruturado do critic (approved, issues, feedback) — presente
    # em entradas save_draft que passaram pelo critic.
    critic_result: dict | None = None


class WritingTurnResponse(BaseModel):
    session_id: str
    assistant_message: str
    turn_number: int
    success: bool
    error: str | None = None
    error_type: str | None = None
    draft_content: str | None = None
    pending_user_input: PendingUserInput | None = None
    tool_trace: list[ToolTraceEntry] | None = None
    compliance_flags: list[dict] = []
    # First-turn generation (Parte A)
    sections_done: list[str] = []
    failed_sections: list[str] = []
    # Sinaliza que um draft completo foi gerado neste turno
    draft_ready: bool = False
    # F4: plano gerado no 1º turno (plan-first). Presente quando o primeiro
    # turno propõe um plano em vez de gerar diretamente.
    plan: dict | None = None
    plan_pending: bool = False
    # PR6.2 (F10): turno cortado no teto de passos do agente (stop_reason ==
    # "max_steps") — o front mostra aviso discreto ("continue a conversa").
    truncated: bool = False


class WritingGenerateRequest(BaseModel):
    # Subconjunto explícito de seções a gerar. Ausente (default) → todas as
    # seções do outline ainda vazias (não clobbera o que já foi redigido).
    sections: list[str] | None = None
    profile: CompanyProfileSchema | None = None
    library_item_ids: list[str] = []
    model_tier: str | None = None


class WritingSectionStartRequest(BaseModel):
    session_id: str
    section_title: str
    profile: CompanyProfileSchema | None = None
    library_item_ids: list[str] = []


class WritingSectionSaveRequest(BaseModel):
    session_id: str
    section_title: str
    content: str


class WritingRefineRequest(BaseModel):
    session_id: str
    section_title: str
    instruction: str = Field(max_length=4_000)


# =============================================================================
# Helpers
# =============================================================================


def _sse(event: str, data: dict) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode()


_SSE_HEADERS = {
    "Cache-Control": "no-cache, no-transform",
    "X-Accel-Buffering": "no",
    "Connection": "keep-alive",
}


def _truncate_key(key: str | None) -> str:
    """Trunca/anônima a idempotency key para logs (UUID v4 completo é ruído)."""
    if not key:
        return "-"
    if len(key) <= 16:
        return key
    return f"{key[:8]}…{key[-4:]}"


def _should_cache(response: dict) -> bool:
    """Decide se a resposta de um turno deve ser guardada para replay.

    Falhas NÃO são cacheadas: um retry com a mesma idempotency key deve
    re-executar o turno, não receber a falha congelada.
    """
    return response.get("success", True) is not False


def _check_idempotency(db: DbClient, key: str | None, session_id: str) -> dict | None:
    if not key:
        return None
    row = db.table("writing_turn_idempotency").select("response_json").eq(
        "idempotency_key", key,
    ).maybe_single().execute()
    if row and row.data:
        return row.data["response_json"]
    return None


def _record_idempotency(db: DbClient, key: str | None, session_id: str, response: dict) -> None:
    if not key:
        return
    if not _should_cache(response):
        logger.warning(
            "idempotency[%s] key=%s: resposta de falha NÃO cacheada",
            session_id, _truncate_key(key),
        )
        return
    try:
        db.table("writing_turn_idempotency").insert({
            "idempotency_key": key,
            "session_id": session_id,
            "response_json": response,
        }).execute()
    except Exception as e:
        logger.warning("failed to record idempotency key %s: %s", _truncate_key(key), e)


@router.post(
    "/writing/turn",
    summary="Turno da sessão de escrita",
    response_model=WritingTurnResponse,
    response_model_exclude_none=True,
)
@limiter.limit("10/minute")
async def writing_turn(
    request: Request,
    req: WritingTurnRequest,
    user_id: CurrentUserId,
    db: DbClient,
):
    """
    Processa um turno da conversa de escrita.

    Roda LLM principal + ComplianceMonitor em paralelo via asyncio.gather (ADR A4).
    Como ComplianceMonitor avalia a mensagem do USUÁRIO (não a resposta do LLM),
    ambos podem rodar simultaneamente — latência total ≈ max(LLM, monitor) ≈ LLM.
    Retorna draft + compliance_flags numa única resposta.

    Constrói o objeto WritingSession a partir do estado em Postgres (sem cache
    de instâncias entre requests). Esta abordagem troca uma pequena latência
    de carregamento por correção em deploys multi-instância.
    """
    import asyncio

    from radar.core.infra.llm_router import resolve_model

    workspace_id = get_workspace_id(db, user_id)
    profile = (
        to_py_profile(req.profile) if req.profile
        else profile_from_workspace(db, workspace_id)
    )
    library_items = load_library_items(db, workspace_id, req.library_item_ids)

    try:
        session = WritingSession(
            db=db,
            workspace_id=workspace_id,
            profile=profile,
            session_id=req.session_id,
            library_items=library_items,
            model=resolve_model(req.model_tier),
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e

    # PR3 (four-phase-workflow): compliance integrado ao Critic (executado dentro
    # de save_draft). Removeu o ComplianceMonitor paralelo.

    # H2: idempotency check
    cached = _check_idempotency(db, req.idempotency_key, req.session_id)
    if cached:
        return cached

    turn_result = await asyncio.to_thread(session.turn, req.user_message, req.section_hint)
    sections_done = turn_result.get("sections_done", [])
    response = {**turn_result, "compliance_flags": [],
            "draft_ready": bool(sections_done)}
    _record_idempotency(db, req.idempotency_key, req.session_id, response)
    return response


@router.post(
    "/writing/turn/stream",
    summary="Turno da sessão de escrita em streaming SSE",
    response_model=None,
)
@limiter.limit("10/minute")
async def writing_turn_stream(
    request: Request,
    req: WritingTurnRequest,
    user_id: CurrentUserId,
    db: DbClient,
):
    """Variação SSE de `/writing/turn` — tokens ao vivo durante a geração,
    seguidos de um frame `done` com o MESMO payload do endpoint síncrono.

    Formato:
        event: token  data: {"text": "<delta>"}
        event: tool   data: {"name": "<tool>", "phase": "end"}
        event: done   data: {...payload idêntico ao /writing/turn...}
        event: error  data: {"message": "<msg>"}
    """
    from radar.core.infra.llm_router import resolve_model

    workspace_id = get_workspace_id(db, user_id)
    profile = (
        to_py_profile(req.profile) if req.profile
        else profile_from_workspace(db, workspace_id)
    )
    library_items = load_library_items(db, workspace_id, req.library_item_ids)

    # H2: idempotency ANTES de construir a sessão/iniciar o produtor. Num retry
    # com a mesma key, não devemos re-executar o LLM nem re-persistir turnos.
    cached = _check_idempotency(db, req.idempotency_key, req.session_id)
    if cached:
        logger.info(
            "writing_turn_stream[%s] idempotency hit key=%s — replaying",
            req.session_id, _truncate_key(req.idempotency_key),
        )

        async def _replay():
            yield _sse("done", cached)

        return StreamingResponse(
            _replay(), media_type="text/event-stream", headers=_SSE_HEADERS,
        )

    try:
        session = WritingSession(
            db=db,
            workspace_id=workspace_id,
            profile=profile,
            session_id=req.session_id,
            library_items=library_items,
            model=resolve_model(req.model_tier),
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except Exception as e:
        logger.error(
            "writing_turn_stream[%s] session build failed stage=build exc=%s",
            req.session_id, type(e).__name__,
        )
        raise HTTPException(status_code=500, detail="Não foi possível iniciar a sessão.") from e

    queue: asyncio.Queue = asyncio.Queue()
    loop = asyncio.get_running_loop()

    def _produce():
        """Runs in background thread: iterates _turn_agent_streaming
        and feeds events to the async queue."""
        session_id_val = session.session_id
        cancel_ev = register_cancel_token(session_id_val)
        try:
            for event in session._turn_agent_streaming(
                req.user_message, req.section_hint,
            ):
                if cancel_ev.is_set():
                    break
                loop.call_soon_threadsafe(queue.put_nowait, event)
        except Exception as e:
            logger.error(
                "writing_turn_stream[%s] producer error stage=stream exc=%s: %s",
                session_id_val, type(e).__name__, e,
            )
            loop.call_soon_threadsafe(queue.put_nowait, {
                "kind": "final",
                "session_id": session_id_val,
                "assistant_message": "",
                "success": False,
                "error": "Não foi possível gerar o texto. Tente novamente.",
            })
        finally:
            unregister_cancel_token(session_id_val)
            loop.call_soon_threadsafe(queue.put_nowait, None)

    thread = threading.Thread(target=_produce, daemon=True)
    thread.start()

    async def gen():
        try:
            was_tool_or_token = False
            while True:
                item = await queue.get()
                if item is None:
                    break
                kind = item.get("kind")
                if kind == "token":
                    was_tool_or_token = True
                    yield _sse("token", {"text": item.get("text", "")})
                elif kind == "tool_end":
                    was_tool_or_token = True
                    yield _sse("tool", {"name": item.get("name", ""), "phase": "end"})
                elif kind == "final":
                    payload = {k: v for k, v in item.items() if k != "kind"}
                    _record_idempotency(db, req.idempotency_key, req.session_id, payload)
                    yield _sse("done", payload)
                    break
        except Exception as e:
            logger.error(
                "writing_turn_stream[%s] SSE error stage=stream exc=%s: %s",
                session.session_id, type(e).__name__, e,
            )
            if not was_tool_or_token:
                # Fallback silencioso: stream nunca começou → tenta batch
                try:
                    result = await asyncio.to_thread(
                        session.turn, req.user_message, req.section_hint,
                    )
                    _record_idempotency(db, req.idempotency_key, req.session_id, result)
                    yield _sse("done", result)
                except Exception as e2:
                    logger.error(
                        "writing_turn_stream[%s] fallback error stage=batch exc=%s: %s",
                        session.session_id, type(e2).__name__, e2,
                    )
                    yield _sse("error", {
                        "message": "Erro ao processar o turno. Tente novamente.",
                    })
            else:
                yield _sse("error", {
                    "message": "Erro ao processar o turno. Tente novamente.",
                })

    return StreamingResponse(gen(), media_type="text/event-stream", headers=_SSE_HEADERS)


@router.post(
    "/writing/{session_id}/cancel",
    summary="Cancela um turno de escrita em andamento",
)
def writing_cancel_turn(
    session_id: str,
    user_id: CurrentUserId,
    db: DbClient,
):
    """Cancela o turno de escrita em andamento para a sessão especificada.
    O turno em execução será interrompido na próxima verificação de cancelamento."""
    ok = cancel_turn(session_id)
    if not ok:
        return {"ok": False, "message": "Nenhum turno em andamento para esta sessão."}
    return {"ok": True, "message": "Turno cancelado."}


@router.post(
    "/writing/{session_id}/generate",
    summary="Gera a proposta completa (batch de todas as seções do outline)",
)
@limiter.limit("3/minute")
async def writing_generate(
    request: Request,
    session_id: str,
    req: WritingGenerateRequest,
    user_id: CurrentUserId,
    db: DbClient,
):
    """Modo "gerar proposta completa": escreve todas as seções do outline de uma
    vez (orquestrador determinístico + agente interno por seção).

    Operação LONGA — uma run de agente por seção. Roda em thread para não
    bloquear o event loop (espelha /writing/turn). Reconstrói a WritingSession a
    partir do DB (sem cache de instâncias). Retorna as seções geradas/falhas e o
    documento atualizado.
    """
    import asyncio

    from radar.core.infra.llm_router import resolve_model

    workspace_id = get_workspace_id(db, user_id)
    profile = (
        to_py_profile(req.profile) if req.profile
        else profile_from_workspace(db, workspace_id)
    )
    library_items = load_library_items(db, workspace_id, req.library_item_ids)
    try:
        session = WritingSession(
            db=db,
            workspace_id=workspace_id,
            profile=profile,
            session_id=session_id,
            library_items=library_items,
            model=resolve_model(req.model_tier),
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e

    return await asyncio.to_thread(session.generate_full_proposal, req.sections)


@router.get("/writing/sessions", summary="Lista sessões de escrita do workspace")
def writing_list_sessions(
    user_id: CurrentUserId,
    db: DbClient,
    status: str | None = Query(None, description="active | completed | abandoned"),
):
    """Lista sessões do workspace, cada uma já com `edital_id` + um título
    utilizável (`edital_title`).

    W-D2: a listagem resolve o título do alvo no servidor — evita o front ter
    que disparar N getEditalById (e cobre alvos `investidor:` que não vivem no
    índice de editais). Resolução best-effort: alvo não encontrado fica sem
    título e o front cai no id.
    """
    workspace_id = get_workspace_id(db, user_id)
    sessions = list_sessions(db, workspace_id, status=status)
    _attach_target_titles(sessions)
    return {"sessions": sessions}


def _attach_target_titles(sessions: list[dict]) -> None:
    """Preenche `edital_title` em cada sessão a partir do alvo (edital ou fundo).

    Resolve eventos (editais/desafios/programas) e entidades (`investidor:<slug>`)
    via entity_catalog/SQL. Falha graciosa: erro de carga deixa as sessões sem
    título (o front mostra o id)."""
    if not sessions:
        return

    ids = {s["edital_id"] for s in sessions if s.get("edital_id")}
    try:
        # A lista precisa só de título: não consulta cartões nem temporalidade.
        titles = entity_catalog.get_opportunity_titles(list(ids))
    except Exception:
        titles = {}  # falha graciosa — front cai no id

    for s in sessions:
        s["edital_title"] = titles.get(s.get("edital_id"))


@router.delete("/writing/sessions/{session_id}", summary="Apaga sessão de escrita")
def writing_delete_session(session_id: str, user_id: CurrentUserId, db: DbClient):
    workspace_id = get_workspace_id(db, user_id)
    ok = delete_session(db, session_id, workspace_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Sessão não encontrada")
    return {"ok": True}


@router.post(
    "/writing/{session_id}/checklist/auto-review",
    summary="LLM revisa documento em 3 passes paralelas (compliance, qualidade, completude)",
)
@limiter.limit("10/minute")
async def writing_checklist_auto_review(
    request: Request,
    session_id: str,
    user_id: CurrentUserId,
    db: DbClient,
):
    """
    Roda 3 passes de revisão em paralelo (ADR C4):
      - compliance:   requisitos obrigatórios do edital cobertos?
      - quality:      clareza, coerência, persuasão, tom
      - completeness: seções presentes e com profundidade adequada
    """
    from radar.core.services.checklist_service import (
        auto_review_checklist,
        build_checklist,
    )
    workspace_id = get_workspace_id(db, user_id)
    doc = get_session_document(db, session_id, workspace_id)
    if doc is None:
        raise HTTPException(status_code=404, detail=f"Sessão '{session_id}' não encontrada")

    document = "\n\n---\n\n".join(
        f"## {s['title']}\n\n{s['content']}" if s["content"]
        else f"## {s['title']}\n\n*[A preencher]*"
        for s in doc["sections"]
    )
    outline = [s["title"] for s in doc["sections"]]

    # Carrega o playbook de monitoramento para enriquecer o pass de compliance
    # com as heurísticas e anti-padrões do mecanismo (ex: reembolso vs subvenção).
    # Falha silenciosa: playbook ausente → compliance roda sem regras adicionais.
    playbook_monitor = ""
    try:
        from radar.core.kg.edital_id import source_of
        from radar.core.skills import load_playbook
        edital_id = doc["edital_id"]
        card = entity_catalog.get_edital(edital_id) or {}
        mechanism = str(card.get("mechanism", "") or "")
        source = source_of(edital_id)
        playbook_monitor = load_playbook(mechanism, source).for_monitor()
    except Exception:
        pass

    review = await auto_review_checklist(
        proposal=document,
        edital_requirements=build_checklist(doc["edital_id"]),
        outline=outline,
        playbook_context=playbook_monitor,
        workspace_id=workspace_id,
        session_id=session_id,
    )
    _attach_issue_sections(review, outline)
    return {"session_id": session_id, "review": review}


def _attach_issue_sections(review: dict, outline: list[str]) -> None:
    """Anexa um campo `section` a cada issue do auto-review, ancorando os
    findings no editor do workspace (spec §F4/W6 — reusa `_infer_section`).

    - completeness: a própria seção do outline (campo `title`).
    - compliance:   inferida do texto do requisito.
    - quality:      inferida do trecho (excerpt); cai em "Geral" se nada casar.

    A seção inferida só vira âncora se bater com um título do outline; caso
    contrário o front a trata como "Geral" (bloco no topo do documento)."""
    from radar.core.services.checklist_service import _infer_section

    outline_set = set(outline)

    def anchor(raw: str) -> str:
        sec = _infer_section(raw or "")
        return sec if sec in outline_set else "Geral"

    for issue in review.get("compliance", {}).get("issues", []) or []:
        if isinstance(issue, dict):
            issue["section"] = anchor(issue.get("requirement", ""))

    for issue in review.get("quality", {}).get("issues", []) or []:
        if isinstance(issue, dict):
            issue["section"] = anchor(issue.get("excerpt", ""))

    for sec in review.get("completeness", {}).get("sections", []) or []:
        if isinstance(sec, dict):
            title = sec.get("title", "")
            sec["section"] = title if title in outline_set else "Geral"


@router.get("/writing/{session_id}/document", summary="Retorna estado atual do documento")
def writing_get_document(session_id: str, user_id: CurrentUserId, db: DbClient):
    workspace_id = get_workspace_id(db, user_id)
    doc = get_session_document(db, session_id, workspace_id)
    if doc is None:
        raise HTTPException(status_code=404, detail=f"Sessão '{session_id}' não encontrada")
    return doc


@router.put("/writing/{session_id}/section", summary="Salva conteúdo editado de uma seção")
def writing_save_section(
    session_id: str,
    req: WritingSectionSaveRequest,
    user_id: CurrentUserId,
    db: DbClient,
):
    workspace_id = get_workspace_id(db, user_id)
    profile = profile_from_workspace(db, workspace_id)
    try:
        session = WritingSession(
            db=db,
            workspace_id=workspace_id,
            profile=profile,
            session_id=req.session_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    session.set_section_content(req.section_title, req.content)
    return {"success": True, "section_title": req.section_title}


@router.get("/writing/chunks/{chunk_id}", summary="Retorna texto de um chunk para exibição em tooltip de citação")
def writing_get_chunk(chunk_id: str, db: DbClient):
    row = db.table("edital_chunks").select("id, text, section").eq("id", chunk_id).maybe_single().execute()
    if not row.data:
        raise HTTPException(status_code=404, detail="Chunk não encontrado")
    return {"id": row.data["id"], "text": row.data["text"], "section": row.data.get("section", "")}


@router.get("/writing/{session_id}/export", summary="Exporta documento completo como Markdown")
def writing_export(session_id: str, user_id: CurrentUserId, db: DbClient):
    workspace_id = get_workspace_id(db, user_id)
    doc = get_session_document(db, session_id, workspace_id)
    if doc is None:
        raise HTTPException(status_code=404, detail=f"Sessão '{session_id}' não encontrada")
    parts = []
    for s in doc["sections"]:
        if s["content"]:
            parts.append(f"## {s['title']}\n\n{s['content']}")
        else:
            parts.append(f"## {s['title']}\n\n*[A preencher]*")
    return {"markdown": "\n\n---\n\n".join(parts), "session_id": session_id}


@router.post("/writing/{session_id}/save-to-storage", summary="Salva export da sessão em Supabase Storage")
def writing_save_to_storage(session_id: str, user_id: CurrentUserId, db: DbClient):
    """Exporta a sessão como markdown e faz upload para o bucket `proposals`.

    Path: <workspace_id>/<session_id>/<timestamp>.md
    RLS em storage.objects garante isolamento por workspace (ver migration 006).
    Retorna o path final + signed URL com TTL de 1 hora.
    """
    from datetime import datetime

    workspace_id = get_workspace_id(db, user_id)
    doc = get_session_document(db, session_id, workspace_id)
    if doc is None:
        raise HTTPException(status_code=404, detail=f"Sessão '{session_id}' não encontrada")
    parts = []
    for s in doc["sections"]:
        if s["content"]:
            parts.append(f"## {s['title']}\n\n{s['content']}")
        else:
            parts.append(f"## {s['title']}\n\n*[A preencher]*")
    markdown = "\n\n---\n\n".join(parts)

    ts = datetime.utcnow().strftime("%Y%m%dT%H%M%S")
    path = f"{workspace_id}/{session_id}/proposal_{ts}.md"

    try:
        db.storage.from_("proposals").upload(
            path,
            markdown.encode("utf-8"),
            file_options={"content-type": "text/markdown", "upsert": "true"},
        )
        signed = db.storage.from_("proposals").create_signed_url(path, 3600)
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Falha ao salvar no storage: {e}",
        ) from e

    return {"path": path, "signed_url": signed.get("signedURL"), "session_id": session_id}


@router.post("/writing/section-start", summary="Mensagem inicial para uma seção da proposta")
@limiter.limit("10/minute")
def writing_section_start(
    request: Request,
    req: WritingSectionStartRequest,
    user_id: CurrentUserId,
    db: DbClient,
):
    """
    Gera a mensagem de boas-vindas contextualizada para a seção selecionada.
    Reconstrói a WritingSession a partir do DB para reaproveitar o contexto.
    """
    workspace_id = get_workspace_id(db, user_id)
    profile = (
        to_py_profile(req.profile) if req.profile
        else profile_from_workspace(db, workspace_id)
    )
    library_items = load_library_items(db, workspace_id, req.library_item_ids)
    try:
        session = WritingSession(
            db=db,
            workspace_id=workspace_id,
            profile=profile,
            session_id=req.session_id,
            library_items=library_items,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    starter = session.get_section_starter(req.section_title)
    return {"starter_message": starter, "section_title": req.section_title}


@router.post(
    "/writing/{session_id}/refine",
    summary="Refina uma seção específica com instrução do usuário (FASE 3)",
)
@limiter.limit("10/minute")
async def writing_refine(
    request: Request,
    session_id: str,
    req: WritingRefineRequest,
    user_id: CurrentUserId,
    db: DbClient,
):
    """Refinement mode: reescreve uma seção específica seguindo instrução do
    usuário. A seção passa pelo Critic (qualidade + compliance) antes de ser
    salva.

    - MAX_STEPS = 20 (vs 10 do turno normal)
    - Critic roda automaticamente via save_draft
    - Retorna o novo conteúdo + feedback do Critic
    """
    workspace_id = get_workspace_id(db, user_id)
    profile = profile_from_workspace(db, workspace_id)
    try:
        session = WritingSession(
            db=db,
            workspace_id=workspace_id,
            profile=profile,
            session_id=session_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e

    result = await asyncio.to_thread(
        session.refine_section,
        req.section_title,
        req.instruction,
    )
    return result
