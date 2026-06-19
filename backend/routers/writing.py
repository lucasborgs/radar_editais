"""Writing Session (persistida em Postgres — ADR B1): sessões, turnos,
documento, checklist e export."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from backend.common import (
    CompanyProfileSchema,
    load_library_items,
    profile_from_workspace,
    to_py_profile,
    wiki_matcher,
)
from backend.rate_limit import limiter
from core.auth import CurrentUserId, DbClient
from core.services.content_library import get_workspace_id
from core.services.writing_session import (
    WritingSession,
    delete_session,
    get_session_document,
    list_sessions,
)

router = APIRouter(tags=["writing"])


# =============================================================================
# SCHEMAS
# =============================================================================


class WritingStartRequest(BaseModel):
    edital_id: str
    profile: CompanyProfileSchema
    library_item_ids: list[str] = []
    # W-D3: modo de escrita explícito ('proposal' | 'pitch'). Opcional — quando
    # ausente (default), a WritingSession deriva o modo do namespace do id
    # (`investidor:<slug>` → pitch; demais → proposal). Hoje todo pitch entra por
    # um id `investidor:`, então o override raramente é necessário; expomos o
    # campo para o front poder sinalizar a intenção (badge/header) sem depender
    # de inspecionar o id. Não altera a lógica do pitch em si.
    mode: str | None = None


class WritingTurnRequest(BaseModel):
    session_id: str
    user_message: str
    section_hint: str | None = None
    profile: CompanyProfileSchema | None = None
    library_item_ids: list[str] = []
    model_tier: str | None = None  # Fase 4 #24: 'fast' | 'auto' | 'pro'


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


class WritingSectionStartRequest(BaseModel):
    session_id: str
    section_title: str
    profile: CompanyProfileSchema | None = None
    library_item_ids: list[str] = []


class WritingSectionSaveRequest(BaseModel):
    session_id: str
    section_title: str
    content: str


class ChecklistUpdateRequest(BaseModel):
    item_id: str
    status: str  # "pending" | "addressed" | "not_applicable"


# =============================================================================
# ENDPOINTS
# =============================================================================


@router.post("/writing/start", summary="Inicia sessão de escrita de proposta")
@limiter.limit("10/minute")
def writing_start(
    request: Request,
    req: WritingStartRequest,
    user_id: CurrentUserId,
    db: DbClient,
):
    """
    Cria uma sessão de escrita persistida em Postgres para o edital selecionado.
    Retorna session_id, títulos das seções e contexto da sessão.
    """
    # Alvo de escrita: evento (edital/desafio/programa, no índice) OU entidade
    # (investidor:<slug>, em investidores.json → pitch outbound). Valida na fonte
    # certa por namespace do id; a WritingSession deriva o mode do mesmo id.
    if req.edital_id.startswith("investidor:"):
        from core.kg import kg_store
        if req.edital_id not in {i["id"] for i in kg_store.load_investidores()}:
            raise HTTPException(status_code=404, detail=f"Fundo '{req.edital_id}' não encontrado")
    elif wiki_matcher.get_edital_by_id(req.edital_id) is None:
        raise HTTPException(status_code=404, detail=f"Edital '{req.edital_id}' não encontrado")

    workspace_id = get_workspace_id(db, user_id)
    profile = to_py_profile(req.profile)
    library_items = load_library_items(db, workspace_id, req.library_item_ids)

    session = WritingSession(
        db=db,
        workspace_id=workspace_id,
        profile=profile,
        edital_id=req.edital_id,
        library_items=library_items,
        mode=req.mode,  # W-D3: opcional; None → modo derivado do id
    )
    return session.get_info()


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

    from core.compliance_monitor import check_compliance
    from core.llm_router import resolve_model

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

    # Run LLM turn and compliance check in parallel (ADR A4).
    turn_result, compliance_flags = await asyncio.gather(
        asyncio.to_thread(session.turn, req.user_message, req.section_hint),
        asyncio.to_thread(check_compliance, req.user_message, session.edital_id),
    )
    return {**turn_result, "compliance_flags": compliance_flags}


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

    Resolve eventos (editais/desafios/programas) via wiki_matcher e entidades
    (`investidor:<slug>`) via kg_store.load_investidores. Falha graciosa: erro
    de carga deixa as sessões sem título (o front mostra o id)."""
    if not sessions:
        return

    ids = {s["edital_id"] for s in sessions if s.get("edital_id")}
    titles: dict[str, str] = {}

    investor_ids = {i for i in ids if i.startswith("investidor:")}
    if investor_ids:
        try:
            from core.kg import kg_store
            for inv in kg_store.load_investidores():
                if inv["id"] in investor_ids and inv.get("name"):
                    titles[inv["id"]] = inv["name"]
        except Exception:
            pass  # sem títulos de fundo — front cai no id

    for eid in ids - investor_ids:
        try:
            card = wiki_matcher.get_edital_by_id(eid)
            if card and card.get("title"):
                titles[eid] = card["title"]
        except Exception:
            pass

    for s in sessions:
        s["edital_title"] = titles.get(s.get("edital_id"))


@router.delete("/writing/sessions/{session_id}", summary="Apaga sessão de escrita")
def writing_delete_session(session_id: str, user_id: CurrentUserId, db: DbClient):
    workspace_id = get_workspace_id(db, user_id)
    ok = delete_session(db, session_id, workspace_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Sessão não encontrada")
    return {"ok": True}


@router.get(
    "/writing/sessions/{session_id}/document",
    summary="Retorna documento (section_drafts) salvo da sessão",
)
def writing_session_document(
    session_id: str,
    user_id: CurrentUserId,
    db: DbClient,
):
    workspace_id = get_workspace_id(db, user_id)
    doc = get_session_document(db, session_id, workspace_id)
    if doc is None:
        raise HTTPException(
            status_code=404,
            detail=f"Sessão '{session_id}' não encontrada",
        )
    return doc


@router.get("/writing/{session_id}/checklist", summary="Checklist de requisitos do edital")
def writing_checklist(session_id: str, user_id: CurrentUserId, db: DbClient):
    """Reconstrói o checklist a partir do edital. Estado de marcação não é
    persistido nesta wave — o frontend deve re-aplicar as marcações localmente.
    """
    from core.services.checklist_service import build_checklist
    workspace_id = get_workspace_id(db, user_id)
    doc = get_session_document(db, session_id, workspace_id)
    if doc is None:
        raise HTTPException(status_code=404, detail=f"Sessão '{session_id}' não encontrada")
    return {"session_id": session_id, "items": build_checklist(doc["edital_id"])}


@router.put("/writing/{session_id}/checklist/{item_id}", summary="Atualiza status de requisito")
def writing_checklist_update(
    session_id: str,
    item_id: str,
    req: ChecklistUpdateRequest,
    user_id: CurrentUserId,
    db: DbClient,
):
    """Stateless — devolve o item atualizado. Persistência do checklist será
    adicionada em uma wave posterior (não existe coluna dedicada hoje).
    """
    from core.services.checklist_service import build_checklist
    workspace_id = get_workspace_id(db, user_id)
    doc = get_session_document(db, session_id, workspace_id)
    if doc is None:
        raise HTTPException(status_code=404, detail=f"Sessão '{session_id}' não encontrada")
    for item in build_checklist(doc["edital_id"]):
        if item["id"] == item_id:
            item["status"] = req.status
            return {"success": True, "item": item}
    raise HTTPException(status_code=404, detail=f"Item '{item_id}' não encontrado")


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
    from core.services.checklist_service import (
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
    review = await auto_review_checklist(
        proposal=document,
        edital_requirements=build_checklist(doc["edital_id"]),
        outline=outline,
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
    from core.services.checklist_service import _infer_section

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


@router.post("/writing/{session_id}/close", summary="Fecha a sessão e extrai sinal episódico")
def writing_close(session_id: str, user_id: CurrentUserId, db: DbClient):
    """Marca a sessão como `completed` e extrai sinal episódico da janela viva.

    Item 4 (Sprint 2): a compressão (`_compress_history`) já extrai sinal de
    turnos que saíram da janela. Mas sessões curtas (abaixo do threshold de
    compressão) nunca disparam compressão — seu sinal se perderia. Este endpoint
    cobre esse caso: roda `extract_session_signal` sobre o `_history` ainda na
    janela viva.

    Idempotência best-effort: pode haver leve sobreposição com o que a compressão
    já extraiu (os turnos comprimidos saem de `_history`, mas a janela viva pode
    conter turnos parcialmente já vistos em runs anteriores deste endpoint). O
    custo é um insight duplicado, não corrupção — aceitável dado o sinal heurístico.
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

    # Marca a sessão como fechada. `status` aceita active | completed | abandoned
    # (ver list_sessions); 'completed' é o terminal de uma sessão concluída.
    try:
        db.table("writing_sessions").update({"status": "completed"}).eq(
            "id", session_id
        ).execute()
    except Exception:
        pass

    # Extrai sinal da janela viva (turnos que nunca foram comprimidos).
    signals = session.extract_session_signal(session._history)
    inserted = session._persist_session_signals(signals) if signals else 0
    return {"closed": True, "signals_extracted": inserted}


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
