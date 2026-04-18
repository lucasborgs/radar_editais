"""
Radar Editais — FastAPI Backend v1 (FINEP-only)

Executar da raiz do projeto:
    uvicorn backend.api:app --reload --port 8000

Docs automáticos: http://localhost:8000/docs
"""

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional

from core.hybrid_match_service import HybridMatchService
from core.writing_session import WritingSession
from domain.user_profile import CompanyProfile as PyCompanyProfile

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
# SINGLETONS
# =============================================================================

kg_matcher = HybridMatchService()

# Session store em memória: {session_id: WritingSession}
_writing_sessions: dict[str, WritingSession] = {}

# =============================================================================
# SCHEMAS PYDANTIC
# =============================================================================


class CompanyProfileSchema(BaseModel):
    nome: str = ""
    cnpj: str = ""
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


class WritingTurnRequest(BaseModel):
    session_id: str
    user_message: str


# =============================================================================
# HELPER
# =============================================================================


def _to_py_profile(schema: CompanyProfileSchema) -> PyCompanyProfile:
    return PyCompanyProfile(
        nome=schema.nome,
        cnpj=schema.cnpj,
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
    return kg_matcher.get_stats()


@app.get("/editais", summary="Lista editais FINEP com filtros opcionais")
def list_editais(
    status: Optional[str] = Query(None, description="ABERTA | ENCERRADA | Desconhecido"),
    tema: Optional[str] = Query(None, description="Filtro por tema (substring)"),
    limit: int = Query(200, ge=1, le=500),
):
    return kg_matcher.list_editais(status=status, tema=tema, limit=limit)


@app.get("/editais/{edital_id}", summary="Card completo de um edital")
def get_edital(edital_id: str):
    edital = kg_matcher.get_edital_by_id(edital_id)
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
    return {"matches": kg_matcher.match(profile=profile, top_k=req.top_k)}


# =============================================================================
# ENDPOINTS — WRITING SESSION
# =============================================================================


@app.post("/writing/start", summary="Inicia sessão de escrita de proposta")
def writing_start(req: WritingStartRequest):
    """
    Cria uma sessão de escrita para o edital selecionado.
    Retorna session_id e títulos das seções disponíveis.
    """
    edital = kg_matcher.get_edital_by_id(req.edital_id)
    if edital is None:
        raise HTTPException(status_code=404, detail=f"Edital '{req.edital_id}' não encontrado")

    profile = _to_py_profile(req.profile)
    edital_url = str(edital.get("link") or "")
    session = WritingSession(edital_id=req.edital_id, profile=profile, edital_url=edital_url)
    _writing_sessions[session.session_id] = session

    return session.get_info()


@app.post("/writing/turn", summary="Turno da sessão de escrita")
def writing_turn(req: WritingTurnRequest):
    """
    Processa um turno da conversa de escrita.
    O Router LLM seleciona seções relevantes; o Writer LLM gera a resposta.
    """
    session = _writing_sessions.get(req.session_id)
    if session is None:
        raise HTTPException(
            status_code=404,
            detail=f"Sessão '{req.session_id}' não encontrada ou expirada",
        )
    return session.turn(req.user_message)
