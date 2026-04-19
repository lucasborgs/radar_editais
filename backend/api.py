"""
Radar Editais — FastAPI Backend v1 (FINEP-only)

Executar da raiz do projeto:
    uvicorn backend.api:app --reload --port 8000

Docs automáticos: http://localhost:8000/docs
"""

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Optional

from core.hybrid_match_service import HybridMatchService
from core.writing_session import WritingSession
from core.profile_extractor import ProfileExtractor
from domain.user_profile import CompanyProfile as PyCompanyProfile
from backend.auth_routes import router as auth_router
from backend.library_routes import router as library_router

# =============================================================================
# APP + CORS
# =============================================================================

app = FastAPI(
    title="Radar Editais API",
    description="Plataforma de matching e escrita de propostas para editais FINEP",
    version="2.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# =============================================================================
# ROUTERS
# =============================================================================

app.include_router(auth_router)
app.include_router(library_router)

# =============================================================================
# SINGLETONS
# =============================================================================

wiki_matcher = HybridMatchService()


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    return JSONResponse(
        status_code=500,
        content={"detail": str(exc)},
        headers={"Access-Control-Allow-Origin": "http://localhost:3000"},
    )

# Session store em memória: {session_id: WritingSession}
_writing_sessions: dict[str, WritingSession] = {}

# =============================================================================
# SCHEMAS PYDANTIC
# =============================================================================


class CompanyProfileSchema(BaseModel):
    nome: str = ""
    cnpj: str = ""
    url_site: str = ""
    tipo_entidade: str = "empresa"
    one_liner: str = ""
    problem_statement: str = ""
    solution_summary: str = ""
    descricao_atividades: str = ""
    portfolio_projetos: str = ""
    tamanho_empresa: str = ""
    faturamento_anual_faixa: str = ""
    localizacao: str = ""
    capital_social: Optional[float] = None
    certificacoes: list[str] = []
    equipe_resumo: str = ""
    trl: Optional[int] = None
    tipos_financiamento_interesse: list[str] = []
    uso_financiamento: list[str] = []
    valor_buscado: Optional[float] = None


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
    section_hint: Optional[str] = None


class WritingSectionStartRequest(BaseModel):
    session_id: str
    section_title: str


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
# HELPER
# =============================================================================


def _to_py_profile(schema: CompanyProfileSchema) -> PyCompanyProfile:
    return PyCompanyProfile(
        nome=schema.nome,
        cnpj=schema.cnpj,
        url_site=schema.url_site,
        tipo_entidade=schema.tipo_entidade,
        one_liner=schema.one_liner,
        problem_statement=schema.problem_statement,
        solution_summary=schema.solution_summary,
        descricao_atividades=schema.descricao_atividades,
        portfolio_projetos=schema.portfolio_projetos,
        tamanho_empresa=schema.tamanho_empresa,
        faturamento_anual_faixa=schema.faturamento_anual_faixa,
        localizacao=schema.localizacao,
        capital_social=schema.capital_social,
        certificacoes=schema.certificacoes,
        equipe_resumo=schema.equipe_resumo,
        trl=schema.trl,
        tipos_financiamento_interesse=schema.tipos_financiamento_interesse,
        uso_financiamento=schema.uso_financiamento,
        valor_buscado=schema.valor_buscado,
    )


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
    status: Optional[str] = Query(None, description="ABERTA | ENCERRADA | Desconhecido"),
    tema: Optional[str] = Query(None, description="Filtro por tema (substring)"),
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
# ENDPOINTS — MATCHING
# =============================================================================


@app.post("/match", summary="Rankeamento de editais via LLM (Karpathy-style)")
def match_editais(req: MatchRequest):
    """
    Recebe um perfil de empresa e retorna editais FINEP rankeados por relevância.
    A LLM lê o catálogo completo e justifica cada recomendação por dimensão.
    """
    profile = _to_py_profile(req.profile)
    if not profile.is_complete():
        raise HTTPException(
            status_code=422,
            detail="Perfil incompleto. Preencha pelo menos nome e descricao_atividades.",
        )
    return {"matches": wiki_matcher.match(profile=profile, top_k=req.top_k)}


# =============================================================================
# ENDPOINTS — WRITING SESSION
# =============================================================================


@app.post("/writing/start", summary="Inicia sessão de escrita de proposta")
def writing_start(req: WritingStartRequest, request: Request):
    """
    Cria uma sessão de escrita para o edital selecionado.
    Retorna session_id e títulos das seções disponíveis.
    """
    edital = wiki_matcher.get_edital_by_id(req.edital_id)
    if edital is None:
        raise HTTPException(status_code=404, detail=f"Edital '{req.edital_id}' não encontrado")

    profile = _to_py_profile(req.profile)

    library_items: list[dict] = []
    if req.library_item_ids:
        try:
            from core.content_library import get_workspace_id, get_item
            from core.auth import _decode_token
            auth_header = request.headers.get("Authorization", "")
            if auth_header.startswith("Bearer "):
                payload = _decode_token(auth_header[7:])
                workspace_id = get_workspace_id(payload["sub"])
                library_items = [
                    item for item_id in req.library_item_ids
                    if (item := get_item(item_id, workspace_id)) is not None
                ]
        except Exception:
            pass

    session = WritingSession(
        edital_id=req.edital_id,
        profile=profile,
        library_items=library_items,
    )
    _writing_sessions[session.session_id] = session

    return session.get_info()


@app.post("/writing/turn", summary="Turno da sessão de escrita")
def writing_turn(req: WritingTurnRequest):
    """
    Processa um turno da conversa de escrita.
    O Writer LLM recebe os documentos do edital como contexto estático e gera a resposta.
    """
    session = _writing_sessions.get(req.session_id)
    if session is None:
        raise HTTPException(
            status_code=404,
            detail=f"Sessão '{req.session_id}' não encontrada ou expirada",
        )
    return session.turn(req.user_message, section_hint=req.section_hint)


@app.get("/writing/{session_id}/checklist", summary="Checklist de requisitos do edital")
def writing_checklist(session_id: str):
    from core.checklist_service import build_checklist
    session = _writing_sessions.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"Sessão '{session_id}' não encontrada")

    if not hasattr(session, "_checklist"):
        session._checklist = build_checklist(session.edital_id)
    return {"session_id": session_id, "items": session._checklist}


@app.put("/writing/{session_id}/checklist/{item_id}", summary="Atualiza status de requisito")
def writing_checklist_update(session_id: str, item_id: str, req: ChecklistUpdateRequest):
    session = _writing_sessions.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"Sessão '{session_id}' não encontrada")
    if not hasattr(session, "_checklist"):
        raise HTTPException(status_code=404, detail="Checklist não inicializado")
    for item in session._checklist:
        if item["id"] == item_id:
            item["status"] = req.status
            return {"success": True, "item": item}
    raise HTTPException(status_code=404, detail=f"Item '{item_id}' não encontrado")


@app.post("/writing/{session_id}/checklist/auto-review", summary="LLM revisa documento e atualiza checklist")
def writing_checklist_auto_review(session_id: str):
    from core.checklist_service import build_checklist, auto_review_checklist
    session = _writing_sessions.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"Sessão '{session_id}' não encontrada")

    if not hasattr(session, "_checklist"):
        session._checklist = build_checklist(session.edital_id)

    document = session.get_export()
    session._checklist = auto_review_checklist(session._checklist, document)
    return {"session_id": session_id, "items": session._checklist}


@app.get("/writing/{session_id}/document", summary="Retorna estado atual do documento")
def writing_get_document(session_id: str):
    session = _writing_sessions.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"Sessão '{session_id}' não encontrada")
    return session.get_document()


@app.put("/writing/{session_id}/section", summary="Salva conteúdo editado de uma seção")
def writing_save_section(session_id: str, req: WritingSectionSaveRequest):
    session = _writing_sessions.get(req.session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"Sessão '{req.session_id}' não encontrada")
    session.set_section_content(req.section_title, req.content)
    return {"success": True, "section_title": req.section_title}


@app.get("/writing/{session_id}/export", summary="Exporta documento completo como Markdown")
def writing_export(session_id: str):
    session = _writing_sessions.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"Sessão '{session_id}' não encontrada")
    return {"markdown": session.get_export(), "session_id": session_id}


@app.post("/writing/section-start", summary="Mensagem inicial para uma seção da proposta")
def writing_section_start(req: WritingSectionStartRequest):
    """
    Gera a mensagem de boas-vindas contextualizada para a seção selecionada.
    Chamado pelo frontend ao clicar em uma seção do checklist.
    """
    session = _writing_sessions.get(req.session_id)
    if session is None:
        raise HTTPException(
            status_code=404,
            detail=f"Sessão '{req.session_id}' não encontrada ou expirada",
        )
    starter = session.get_section_starter(req.section_title)
    return {"starter_message": starter, "section_title": req.section_title}


# =============================================================================
# ENDPOINTS — PROFILE EXTRACTION
# =============================================================================


@app.post("/profile/extract", summary="Extrai perfil da empresa a partir de URL")
def extract_profile(req: ProfileExtractRequest):
    """
    Recebe a URL do site da empresa, faz scraping e usa LLM para inferir
    os campos do CompanyProfile. Retorna perfil parcial + mapa de confiança.
    """
    url = req.url.strip()
    if not url.startswith(("http://", "https://")):
        raise HTTPException(status_code=422, detail="URL deve começar com http:// ou https://")

    result = ProfileExtractor().extract(url)

    return {
        "profile": {
            "nome": result.profile.nome,
            "cnpj": result.profile.cnpj,
            "tipo_entidade": result.profile.tipo_entidade,
            "one_liner": result.profile.one_liner,
            "problem_statement": result.profile.problem_statement,
            "solution_summary": result.profile.solution_summary,
            "descricao_atividades": result.profile.descricao_atividades,
            "portfolio_projetos": result.profile.portfolio_projetos,
            "tamanho_empresa": result.profile.tamanho_empresa,
            "faturamento_anual_faixa": result.profile.faturamento_anual_faixa,
            "localizacao": result.profile.localizacao,
            "capital_social": result.profile.capital_social,
            "certificacoes": result.profile.certificacoes,
            "trl": result.profile.trl,
            "equipe_resumo": result.profile.equipe_resumo,
            "tipos_financiamento_interesse": result.profile.tipos_financiamento_interesse,
            "uso_financiamento": result.profile.uso_financiamento,
            "valor_buscado": result.profile.valor_buscado,
        },
        "confidence": result.confidence,
        "source_title": result.source_title,
        "low_confidence": result.low_confidence,
        "error": result.error,
    }
