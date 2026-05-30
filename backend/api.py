"""
Radar Editais — FastAPI Backend v1 (FINEP-only)

Executar da raiz do projeto:
    uvicorn backend.api:app --reload --port 8000

Docs automáticos: http://localhost:8000/docs
"""

from dotenv import load_dotenv

load_dotenv()


import logging
import os
import uuid

import jwt
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from backend.auth_routes import router as auth_router
from backend.library_routes import router as library_router
from core.auth import CurrentUserId, DbClient
from core.content_library import get_item, get_workspace_id
from core.hybrid_match_service import HybridMatchService
from core.kg_match_service import KGMatchService
from core.logging_config import request_id_var, setup_logging
from core.profile_extractor import ProfileExtractor
from core.writing_session import (
    WritingSession,
    delete_session,
    get_session_document,
    list_sessions,
)
from domain.user_profile import CompanyProfile as PyCompanyProfile

setup_logging()
logger = logging.getLogger(__name__)

# =============================================================================
# APP + CORS
# =============================================================================

app = FastAPI(
    title="Radar Editais API",
    description="Plataforma de matching e escrita de propostas para editais FINEP",
    version="2.0.0",
)


class RequestIdMiddleware:
    """Middleware ASGI puro que atribui um request_id por request.

    Usa ASGI puro (não BaseHTTPMiddleware) para que o contextvar propague
    corretamente aos logs no mesmo contexto async, e persiste o id em
    `scope["state"]` para que o exception handler de 500 — que roda acima desta
    camada na cadeia ASGI — ainda consiga lê-lo via `request.state`.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming = dict(scope.get("headers", []))
        rid = incoming.get(b"x-request-id", b"").decode() or uuid.uuid4().hex[:12]
        scope.setdefault("state", {})["request_id"] = rid
        token = request_id_var.set(rid)

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                headers = message.setdefault("headers", [])
                headers.append((b"x-request-id", rid.encode()))
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            request_id_var.reset(token)


def _allowed_origins() -> list[str]:
    # localhost sempre liberado para dev; FRONTEND_URL (CSV) adiciona origens de prod.
    defaults = ["http://localhost:3000", "http://127.0.0.1:3000"]
    extra = [o.strip() for o in os.getenv("FRONTEND_URL", "").split(",") if o.strip()]
    return list(dict.fromkeys(defaults + extra))


ALLOWED_ORIGINS = _allowed_origins()

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
# Registrado após o CORS → fica mais externo, atribuindo o request_id cedo.
app.add_middleware(RequestIdMiddleware)

# =============================================================================
# RATE LIMITING (slowapi) — proteção de custo LLM
# =============================================================================


def _rate_limit_key(request: Request) -> str:
    """Particiona o rate limit por usuário autenticado; cai para IP se anônimo.

    A assinatura do JWT NÃO é verificada aqui — isso serve apenas para escolher
    a chave do bucket. A validação real do token acontece em cada endpoint via
    CurrentUserId.
    """
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        try:
            payload = jwt.decode(auth[7:], options={"verify_signature": False})
            sub = payload.get("sub")
            if sub:
                return f"user:{sub}"
        except Exception:
            pass
    return get_remote_address(request)


limiter = Limiter(key_func=_rate_limit_key)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# =============================================================================
# ROUTERS
# =============================================================================

app.include_router(auth_router)
app.include_router(library_router)

# =============================================================================
# SINGLETONS
# =============================================================================

wiki_matcher = HybridMatchService()
kg_service = KGMatchService()


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    # Este handler roda acima do RequestIdMiddleware na cadeia ASGI, então o
    # contextvar já foi resetado — recuperamos o id persistido no scope state.
    rid = getattr(request.state, "request_id", "-")
    request_id_var.set(rid)
    # Stack trace completo fica no servidor (correlacionável por request_id);
    # o cliente recebe só uma mensagem genérica + o id para suporte.
    logger.error("Erro não tratado em %s %s", request.method, request.url.path, exc_info=exc)

    # O 500 é gerado acima do CORSMiddleware, então o header CORS precisa ser
    # reaplicado manualmente — refletindo o Origin da request se for permitido.
    origin = request.headers.get("origin", "")
    headers = {"X-Request-ID": rid}
    if origin in ALLOWED_ORIGINS:
        headers["Access-Control-Allow-Origin"] = origin
        headers["Access-Control-Allow-Credentials"] = "true"
    return JSONResponse(
        status_code=500,
        content={
            "detail": "Erro interno no servidor. Tente novamente.",
            "request_id": rid,
        },
        headers=headers,
    )


# =============================================================================
# SCHEMAS PYDANTIC
# =============================================================================


class CompanyProfileSchema(BaseModel):
    nome: str = ""
    cnpj: str = ""
    url_site: str = ""
    tipo_entidade: str = "empresa"
    one_liner: str = ""
    solution_summary: str = ""
    descricao_atividades: str = ""
    portfolio_projetos: str = ""
    tamanho_empresa: str = ""
    capital_social: float | None = None
    equipe_resumo: str = ""
    trl: int | None = None
    tipos_financiamento_interesse: list[str] = []


class MatchRequest(BaseModel):
    profile: CompanyProfileSchema
    top_k: int = 10


class WritingStartRequest(BaseModel):
    edital_id: str
    profile: CompanyProfileSchema
    library_item_ids: list[str] = []


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


class ProfileExtractRequest(BaseModel):
    url: str


# =============================================================================
# HELPERS
# =============================================================================


def _to_py_profile(schema: CompanyProfileSchema) -> PyCompanyProfile:
    return PyCompanyProfile(
        nome=schema.nome,
        cnpj=schema.cnpj,
        url_site=schema.url_site,
        tipo_entidade=schema.tipo_entidade,
        one_liner=schema.one_liner,
        solution_summary=schema.solution_summary,
        descricao_atividades=schema.descricao_atividades,
        portfolio_projetos=schema.portfolio_projetos,
        tamanho_empresa=schema.tamanho_empresa,
        capital_social=schema.capital_social,
        equipe_resumo=schema.equipe_resumo,
        trl=schema.trl,
        tipos_financiamento_interesse=schema.tipos_financiamento_interesse,
    )


def _load_library_items(db, workspace_id: str, item_ids: list[str]) -> list[dict]:
    if not item_ids:
        return []
    return [
        item for item_id in item_ids
        if (item := get_item(db, item_id, workspace_id)) is not None
    ]


def _profile_from_workspace(db, workspace_id: str) -> PyCompanyProfile:
    """Lê workspaces.profile e instancia CompanyProfile.

    Fallback usado quando o cliente não envia o profile no payload (resumir
    sessão a partir de session_id puro).
    """
    result = (
        db.table("workspaces")
        .select("profile")
        .eq("id", workspace_id)
        .maybe_single()
        .execute()
    )
    raw = ((result.data if result else None) or {}).get("profile") or {}
    allowed = {
        "nome", "cnpj", "url_site", "tipo_entidade", "one_liner",
        "solution_summary", "descricao_atividades", "portfolio_projetos",
        "tamanho_empresa", "capital_social", "equipe_resumo", "trl",
        "tipos_financiamento_interesse",
    }
    return PyCompanyProfile(**{k: v for k, v in raw.items() if k in allowed})


# =============================================================================
# ENDPOINTS — EDITAIS
# =============================================================================


@app.get("/", include_in_schema=False)
def root():
    return {"message": "Radar Editais API v2", "docs": "/docs"}


@app.get("/stats", summary="Estatísticas do catálogo FINEP")
def get_stats():
    return wiki_matcher.get_stats()


@app.get("/editais", summary="Lista editais FINEP com filtros opcionais")
def list_editais(
    status: str | None = Query(None, description="ABERTA | ENCERRADA | Desconhecido"),
    tema: str | None = Query(None, description="Filtro por tema (substring)"),
    limit: int = Query(200, ge=1, le=500),
):
    return wiki_matcher.list_editais(status=status, tema=tema, limit=limit)


@app.get("/editais/{edital_id}", summary="Card completo de um edital")
def get_edital(edital_id: str):
    edital = wiki_matcher.get_edital_by_id(edital_id)
    if edital is None:
        raise HTTPException(status_code=404, detail=f"Edital '{edital_id}' não encontrado")
    return edital


# =============================================================================
# ENDPOINTS — KNOWLEDGE GRAPH (público, sem auth — vitrine do Dashboard)
# =============================================================================


class KGExploreRequest(BaseModel):
    message: str
    history: list[dict] = []
    edital_ids: list[str] = []
    node_id: str | None = None
    node_type: str | None = None


@app.get("/graph", summary="Nós + arestas do knowledge graph (público)")
def get_graph():
    """Grafo Obsidian-style do catálogo: editais, temas e públicos-alvo.

    Público — alimenta a visualização do Dashboard sem exigir login.
    """
    return kg_service.get_graph()


@app.post("/kg-explore", summary="Chat exploratório sobre o catálogo (público, sem perfil)")
@limiter.limit("3/minute")
def kg_explore(request: Request, req: KGExploreRequest):
    """Conversa stateless com o knowledge graph — sem perfil, sem sessão.

    Vitrine do produto: o visitante explora o landscape de fomento antes de
    se cadastrar. O histórico é mantido pelo cliente e reenviado a cada turno.

    Sprint 3 do Cenário B: quando AGENT_EXPLORE_DEFAULT_ENABLED=true, roda o
    agente Anthropic com 4 tools (list_editais, get_edital, find_analogues,
    get_graph_neighbors). Como o endpoint é público (sem workspace), o
    rollout é controlado por env var em vez de coluna em workspaces.
    Default OFF — segue o pipeline determinístico (catálogo inteiro no prompt).
    """
    if not req.message.strip():
        raise HTTPException(status_code=422, detail="Mensagem vazia.")
    agent_enabled = os.getenv("AGENT_EXPLORE_DEFAULT_ENABLED", "false").lower() == "true"
    answer = kg_service.explore(
        req.message, req.history, req.edital_ids, req.node_id, req.node_type,
        agent_enabled=agent_enabled,
    )
    return {"answer": answer}


# =============================================================================
# ENDPOINTS — MATCHING
# =============================================================================


@app.post("/match", summary="Rankeamento de editais via LLM (Karpathy-style)")
@limiter.limit("10/minute")
def match_editais(request: Request, req: MatchRequest, user_id: CurrentUserId, db: DbClient):
    """
    Recebe um perfil de empresa e retorna editais FINEP rankeados por relevância.
    A LLM lê o catálogo completo e justifica cada recomendação por dimensão.

    O workspace_id do usuário é derivado do JWT e plumbado para o matcher
    para permitir pesos customizados (matching_weights) com fallback global.
    """
    profile = _to_py_profile(req.profile)
    if not profile.is_complete():
        raise HTTPException(
            status_code=422,
            detail="Perfil incompleto. Preencha pelo menos nome e descricao_atividades.",
        )
    workspace_id = get_workspace_id(db, user_id)
    return {
        "matches": wiki_matcher.match(
            profile=profile, top_k=req.top_k, workspace_id=workspace_id
        )
    }


# =============================================================================
# ENDPOINTS — OPPORTUNITY BRIEF (Fase 3 #21)
# =============================================================================

class OpportunityBriefRequest(BaseModel):
    edital_id: str
    profile: CompanyProfileSchema | None = None
    model_tier: str | None = None  # Fase 4 #24


_VALID_STATUS_TRANSITIONS = {
    "matched", "brief_gerado", "proposta_iniciada",
    "submetida", "em_analise", "aprovada", "reprovada", "desistiu",
}


class ApplicationStatusUpdate(BaseModel):
    status: str
    feedback_notas: str | None = None


@app.put("/applications/{application_id}/status", summary="Atualiza status de uma application_log (Fase 3 #23)")
def update_application_status(
    application_id: str,
    payload: ApplicationStatusUpdate,
    user_id: CurrentUserId,
    db: DbClient,
):
    """Atualiza status manualmente (submetida, aprovada, reprovada, desistiu).

    O trigger log_application_event registra a transição em application_events
    automaticamente. RLS garante que o usuário só atualiza applications do
    próprio workspace.
    """
    if payload.status not in _VALID_STATUS_TRANSITIONS:
        raise HTTPException(
            status_code=422,
            detail=f"Status inválido. Use: {', '.join(sorted(_VALID_STATUS_TRANSITIONS))}",
        )
    update: dict = {"status": payload.status}
    if payload.feedback_notas is not None:
        update["feedback_notas"] = payload.feedback_notas

    result = (
        db.table("application_log")
        .update(update)
        .eq("id", application_id)
        .execute()
    )
    if not result.data:
        raise HTTPException(status_code=404, detail="application_log não encontrada")
    return result.data[0]


@app.get("/commands", summary="Lista slash commands disponíveis e LLM tiers (Fase 4 #24/#25)")
def list_commands():
    """Registry de slash commands + model tiers para o frontend popular UI.

    Cada comando mapeia para um endpoint existente; o frontend renderiza
    autocomplete quando o usuário digita /xxx no chat. Não exige auth (apenas
    catálogo público de capacidades).
    """
    from core.llm_router import list_tiers
    from core.skills import available_skills
    return {
        "commands": [
            {
                "name": "match",
                "endpoint": "POST /match",
                "description": "Rankeia editais para o perfil da empresa",
                "args": ["top_k?"],
            },
            {
                "name": "brief",
                "endpoint": "POST /opportunity/brief",
                "description": "Brief de Oportunidade (decision matrix + GO/NO-GO) para um edital",
                "args": ["edital_id"],
            },
            {
                "name": "draft",
                "endpoint": "POST /writing/start",
                "description": "Inicia sessão de escrita de proposta",
                "args": ["edital_id", "library_item_ids?"],
            },
            {
                "name": "review",
                "endpoint": "POST /writing/{session_id}/checklist/auto-review",
                "description": "Roda 3 passes de revisão (compliance + qualidade + completude)",
                "args": [],
            },
            {
                "name": "reflect",
                "endpoint": "POST /me/reflect",
                "description": "Síntese de outcomes anteriores em insights",
                "args": [],
            },
        ],
        "model_tiers": list_tiers(),
        "skills": available_skills(),
    }


@app.post("/opportunity/brief", summary="Gera Brief de Oportunidade para um edital (decision matrix + GO/NO-GO)")
@limiter.limit("10/minute")
def opportunity_brief(
    request: Request, req: OpportunityBriefRequest, user_id: CurrentUserId, db: DbClient
):
    """Avaliação formal antes de iniciar uma proposta (ADR §3.6, RADAR Gap 6).

    Score determinístico (HybridMatch Stage 1) + narrativa LLM. Persiste
    automaticamente em application_log com status='brief_gerado' (trigger
    em application_events registra o evento).

    Se `profile` não for enviado, lê de workspaces.profile.
    """
    from core.opportunity_brief_service import generate_brief

    workspace_id = get_workspace_id(db, user_id)
    profile = (
        _to_py_profile(req.profile) if req.profile
        else _profile_from_workspace(db, workspace_id)
    )
    return generate_brief(db, workspace_id, profile, req.edital_id, model_tier=req.model_tier)


# =============================================================================
# ENDPOINTS — WRITING SESSION (persistido em Postgres — ADR B1)
# =============================================================================


@app.post("/writing/start", summary="Inicia sessão de escrita de proposta")
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
    edital = wiki_matcher.get_edital_by_id(req.edital_id)
    if edital is None:
        raise HTTPException(status_code=404, detail=f"Edital '{req.edital_id}' não encontrado")

    workspace_id = get_workspace_id(db, user_id)
    profile = _to_py_profile(req.profile)
    library_items = _load_library_items(db, workspace_id, req.library_item_ids)

    session = WritingSession(
        db=db,
        workspace_id=workspace_id,
        profile=profile,
        edital_id=req.edital_id,
        library_items=library_items,
    )
    return session.get_info()


@app.post(
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
        _to_py_profile(req.profile) if req.profile
        else _profile_from_workspace(db, workspace_id)
    )
    library_items = _load_library_items(db, workspace_id, req.library_item_ids)

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


@app.get("/writing/sessions", summary="Lista sessões de escrita do workspace")
def writing_list_sessions(
    user_id: CurrentUserId,
    db: DbClient,
    status: str | None = Query(None, description="active | completed | abandoned"),
):
    workspace_id = get_workspace_id(db, user_id)
    return {"sessions": list_sessions(db, workspace_id, status=status)}


@app.delete("/writing/sessions/{session_id}", summary="Apaga sessão de escrita")
def writing_delete_session(session_id: str, user_id: CurrentUserId, db: DbClient):
    workspace_id = get_workspace_id(db, user_id)
    ok = delete_session(db, session_id, workspace_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Sessão não encontrada")
    return {"ok": True}


@app.get(
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


@app.get("/writing/{session_id}/checklist", summary="Checklist de requisitos do edital")
def writing_checklist(session_id: str, user_id: CurrentUserId, db: DbClient):
    """Reconstrói o checklist a partir do edital. Estado de marcação não é
    persistido nesta wave — o frontend deve re-aplicar as marcações localmente.
    """
    from core.checklist_service import build_checklist
    workspace_id = get_workspace_id(db, user_id)
    doc = get_session_document(db, session_id, workspace_id)
    if doc is None:
        raise HTTPException(status_code=404, detail=f"Sessão '{session_id}' não encontrada")
    return {"session_id": session_id, "items": build_checklist(doc["edital_id"])}


@app.put("/writing/{session_id}/checklist/{item_id}", summary="Atualiza status de requisito")
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
    from core.checklist_service import build_checklist
    workspace_id = get_workspace_id(db, user_id)
    doc = get_session_document(db, session_id, workspace_id)
    if doc is None:
        raise HTTPException(status_code=404, detail=f"Sessão '{session_id}' não encontrada")
    for item in build_checklist(doc["edital_id"]):
        if item["id"] == item_id:
            item["status"] = req.status
            return {"success": True, "item": item}
    raise HTTPException(status_code=404, detail=f"Item '{item_id}' não encontrado")


@app.post(
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
    from core.checklist_service import auto_review_checklist, build_checklist
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
    return {"session_id": session_id, "review": review}


@app.get("/writing/{session_id}/document", summary="Retorna estado atual do documento")
def writing_get_document(session_id: str, user_id: CurrentUserId, db: DbClient):
    workspace_id = get_workspace_id(db, user_id)
    doc = get_session_document(db, session_id, workspace_id)
    if doc is None:
        raise HTTPException(status_code=404, detail=f"Sessão '{session_id}' não encontrada")
    return doc


@app.put("/writing/{session_id}/section", summary="Salva conteúdo editado de uma seção")
def writing_save_section(
    session_id: str,
    req: WritingSectionSaveRequest,
    user_id: CurrentUserId,
    db: DbClient,
):
    workspace_id = get_workspace_id(db, user_id)
    profile = _profile_from_workspace(db, workspace_id)
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


@app.get("/writing/{session_id}/export", summary="Exporta documento completo como Markdown")
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


# =============================================================================
# FILE TREE / Supabase Storage (Fase 3 #22)
# =============================================================================

@app.post("/writing/{session_id}/save-to-storage", summary="Salva export da sessão em Supabase Storage")
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


@app.get("/files", summary="Lista arquivos do workspace no storage (file tree)")
def files_list(user_id: CurrentUserId, db: DbClient, prefix: str | None = None):
    """File tree do workspace. RLS filtra automaticamente para o workspace do user.

    Path estrutura: <workspace_id>/<session_id>/<filename>. Se `prefix` for None,
    lista tudo dentro do workspace; se for "<session_id>", lista só dessa sessão.
    """
    workspace_id = get_workspace_id(db, user_id)
    base_prefix = f"{workspace_id}/" if prefix is None else f"{workspace_id}/{prefix}"
    try:
        result = db.storage.from_("proposals").list(base_prefix)
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Falha ao listar storage: {e}",
        ) from e
    return {"prefix": base_prefix, "files": result}


@app.get("/files/signed-url", summary="Gera signed URL para download de um arquivo")
def files_signed_url(path: str, user_id: CurrentUserId, db: DbClient, expires_in: int = 3600):
    """Retorna signed URL com TTL. RLS no storage protege contra cross-workspace access."""
    workspace_id = get_workspace_id(db, user_id)
    if not path.startswith(f"{workspace_id}/"):
        # Defense-in-depth — RLS já protege, mas falhamos cedo.
        raise HTTPException(status_code=403, detail="Path fora do workspace")
    try:
        signed = db.storage.from_("proposals").create_signed_url(path, expires_in)
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Falha ao gerar signed URL: {e}",
        ) from e
    return {"path": path, "signed_url": signed.get("signedURL"), "expires_in": expires_in}


@app.post("/writing/section-start", summary="Mensagem inicial para uma seção da proposta")
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
        _to_py_profile(req.profile) if req.profile
        else _profile_from_workspace(db, workspace_id)
    )
    library_items = _load_library_items(db, workspace_id, req.library_item_ids)
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


# =============================================================================
# ENDPOINTS — PROFILE EXTRACTION
# =============================================================================


@app.post("/profile/extract", summary="Extrai perfil da empresa a partir de URL")
@limiter.limit("3/minute")
def extract_profile(request: Request, req: ProfileExtractRequest):
    """
    Recebe a URL do site da empresa, faz scraping e usa LLM para inferir
    os campos do CompanyProfile. Retorna perfil parcial + mapa de confiança.

    Sprint 4 do Cenário B: quando AGENT_PROFILE_EXTRACTOR_DEFAULT_ENABLED=true,
    roda o agente Anthropic com 4 tools (fetch_page, list_links_matching,
    lookup_cnpj, submit_profile) — investigação multi-página + enriquecimento
    via BrasilAPI. Default OFF — segue o pipeline determinístico (1 fetch
    da home + 1 LLM call). Sem workspace (endpoint público no onboarding),
    rollout é binário via env, igual ao /kg-explore.
    """
    url = req.url.strip()
    if not url.startswith(("http://", "https://")):
        raise HTTPException(status_code=422, detail="URL deve começar com http:// ou https://")

    agent_enabled = os.getenv(
        "AGENT_PROFILE_EXTRACTOR_DEFAULT_ENABLED", "false",
    ).lower() == "true"
    result = ProfileExtractor().extract(url, agent_enabled=agent_enabled)

    return {
        "profile": {
            "nome": result.profile.nome,
            "cnpj": result.profile.cnpj,
            "tipo_entidade": result.profile.tipo_entidade,
            "one_liner": result.profile.one_liner,
            "solution_summary": result.profile.solution_summary,
            "descricao_atividades": result.profile.descricao_atividades,
            "portfolio_projetos": result.profile.portfolio_projetos,
            "tamanho_empresa": result.profile.tamanho_empresa,
            "capital_social": result.profile.capital_social,
            "trl": result.profile.trl,
            "equipe_resumo": result.profile.equipe_resumo,
            "tipos_financiamento_interesse": result.profile.tipos_financiamento_interesse,
        },
        "confidence": result.confidence,
        "source_title": result.source_title,
        "low_confidence": result.low_confidence,
        "error": result.error,
    }
