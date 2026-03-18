"""
Radar Editais — FastAPI Backend

Envolve os serviços Python existentes (matching_engine, rag_service, analyst_agent)
em uma API REST consumida pelo frontend Next.js.

Executar da raiz do projeto:
    uvicorn backend.api:app --reload --port 8000

Docs automáticos: http://localhost:8000/docs
"""

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional

from core.matching_engine import MatchingEngine
from core.rag_service import RAGService
from core.writing_session import WritingSession
from domain.user_profile import CompanyProfile as PyCompanyProfile

# =============================================================================
# APP + CORS
# =============================================================================

app = FastAPI(
    title="Radar Editais API",
    description="Backend para descoberta e análise de editais de financiamento público",
    version="1.0.0",
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
# SINGLETONS — carregados uma vez no startup
# =============================================================================

engine = MatchingEngine()
rag = RAGService()

# Session store em memória: {session_id: WritingSession}
# Aceitável para MVP — para produção usar Redis/SQLite
_writing_sessions: dict[str, WritingSession] = {}

# =============================================================================
# SCHEMAS PYDANTIC
# =============================================================================


class CompanyProfileSchema(BaseModel):
    nome: str = ""
    cnpj: str = ""
    tipo_entidade: str = "empresa"  # empresa | startup | universidade | ICT
    one_liner: str = ""
    problem_statement: str = ""
    solution_summary: str = ""
    descricao_atividades: str = ""
    portfolio_projetos: str = ""
    tamanho_empresa: str = ""  # MEI | ME | EPP | MEDIO | GRANDE
    faturamento_anual_faixa: str = ""  # <500K | 500K-5M | 5M-50M | >50M
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
    top_k: int = 20
    min_score: float = 0
    status_filter: Optional[str] = "ABERTA"


class ChatRequest(BaseModel):
    query: str
    profile: Optional[CompanyProfileSchema] = None
    history: list[dict] = []


class AnalyzeRequest(BaseModel):
    edital_id: str
    profile: CompanyProfileSchema


class DraftRequest(BaseModel):
    edital_id: str
    profile: CompanyProfileSchema
    style: str = "formal"  # formal | consultivo | academico


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
    """Converte o schema Pydantic para o dataclass Python."""
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
# ENDPOINTS
# =============================================================================


@app.get("/", include_in_schema=False)
def root():
    return {"message": "Radar Editais API", "docs": "/docs"}


@app.get("/stats", summary="Estatísticas gerais do banco de editais")
def get_stats():
    """Retorna totais, distribuição por fonte e por status."""
    return engine.get_stats()


@app.get("/editais", summary="Lista editais com filtros opcionais")
def list_editais(
    status: Optional[str] = Query(
        None,
        description="Filtrar por status: ABERTA | ENCERRADA | FLUXO_CONTINUO | VERIFICAR",
    ),
    orgao: Optional[str] = Query(
        None,
        description="Filtrar por fonte: FAPESP | FINEP | BNDES | CNPQ | ...",
    ),
    limit: int = Query(200, ge=1, le=500, description="Máximo de resultados"),
):
    """
    Retorna a lista de editais enriquecidos.
    Use `?status=ABERTA` para filtrar apenas editais abertos.
    Use `?orgao=FAPESP` para filtrar por fonte.
    """
    return engine.list_editais(status=status, source=orgao, limit=limit)


@app.get("/editais/{edital_id}/sections", summary="Seções estruturais de um edital")
def get_edital_sections(edital_id: str):
    """
    Retorna os títulos e conteúdo das seções estruturais de um edital.
    Usado pelo Router LLM da feature Writing para selecionar seções relevantes.
    """
    from core.section_retriever import SectionRetriever
    retriever = SectionRetriever()
    if not retriever.exists(edital_id):
        raise HTTPException(
            status_code=404,
            detail=f"Section index não encontrado para '{edital_id}'. Execute pipeline/etl_section_index.py.",
        )
    return retriever.get_metadata(edital_id)


@app.get("/editais/{edital_id}", summary="Detalhes de um edital específico")
def get_edital(edital_id: str):
    """Retorna todos os campos de um edital pelo seu ID hexadecimal."""
    edital = engine.get_edital_by_id(edital_id)
    if edital is None:
        raise HTTPException(status_code=404, detail=f"Edital '{edital_id}' não encontrado")
    # Serializa valores numpy para tipos Python nativos
    return _serialize(edital)


@app.post("/match", summary="Rankeamento de editais por aderência ao perfil")
def match_editais(req: MatchRequest):
    """
    Recebe um perfil de empresa e retorna editais ordenados por score de aderência.
    Score 0-100 com breakdown por dimensão (temática, CNAE, porte, etc.).
    """
    profile = _to_py_profile(req.profile)
    return engine.match(
        profile=profile,
        top_k=req.top_k,
        min_score=req.min_score,
        status_filter=req.status_filter,
    )


@app.post("/chat", summary="Chat conversacional sobre editais")
def chat(req: ChatRequest):
    """
    Recebe uma pergunta e histórico, retorna resposta do assistente LLM
    com lista de editais relevantes.
    """
    profile = _to_py_profile(req.profile) if req.profile else None
    rag.profile = profile
    return rag.generate(
        query=req.query,
        history=req.history or [],
    )


@app.post("/analyze", summary="Análise de aderência de um edital específico")
def analyze_edital(req: AnalyzeRequest):
    """
    Análise detalhada (via LLM) da aderência de um edital ao perfil da empresa.
    Retorna score, pontos positivos, riscos e documentação faltante.
    """
    # Import lazy para não carregar o LLM no startup
    try:
        from agents.analyst_agent import AdherenceAnalyzer
    except ImportError:
        raise HTTPException(
            status_code=501,
            detail="Módulo analyst_agent não disponível",
        )

    edital = engine.get_edital_by_id(req.edital_id)
    if edital is None:
        raise HTTPException(status_code=404, detail=f"Edital '{req.edital_id}' não encontrado")

    edital_s = _serialize(edital)
    profile = _to_py_profile(req.profile)
    edital_metadata = {
        "title": edital_s.get("title"),
        "source": edital_s.get("source"),
        "deadline_date": edital_s.get("deadline_date"),
        "value_brl": edital_s.get("value_brl"),
        "category": edital_s.get("category"),
    }

    analyzer = AdherenceAnalyzer()
    return analyzer.calculate_score(
        edital_text=str(edital_s.get("description", "") or ""),
        company_profile=profile,
        edital_metadata=edital_metadata,
    )


@app.post("/draft", summary="Geração de rascunho de proposta técnica")
def draft_proposal(req: DraftRequest):
    """
    Gera um rascunho de proposta técnica em Markdown conectando as exigências
    do edital ao perfil e portfólio da empresa.
    """
    try:
        from agents.writer_agent import ProposalDrafter
    except ImportError:
        raise HTTPException(
            status_code=501,
            detail="Módulo writer_agent não disponível",
        )

    edital = engine.get_edital_by_id(req.edital_id)
    if edital is None:
        raise HTTPException(status_code=404, detail=f"Edital '{req.edital_id}' não encontrado")

    edital_s = _serialize(edital)
    profile = _to_py_profile(req.profile)

    edital_metadata = {
        "title": edital_s.get("title"),
        "source": edital_s.get("source"),
        "deadline_date": edital_s.get("deadline_date"),
        "value_brl": edital_s.get("value_brl"),
    }

    drafter = ProposalDrafter()
    return drafter.draft_proposal(
        company_profile=profile,
        edital_id=req.edital_id,
        edital_context=str(edital_s.get("description", "") or ""),
        style=req.style,
        edital_metadata=edital_metadata,
    )


# =============================================================================
# FEATURE WRITING — SESSÃO CONVERSACIONAL
# =============================================================================


@app.post("/writing/start", summary="Inicia sessão conversacional de escrita de proposta")
def writing_start(req: WritingStartRequest):
    """
    Cria uma sessão de escrita para o edital selecionado.
    Retorna session_id e títulos das seções disponíveis para o Router LLM.
    """
    edital = engine.get_edital_by_id(req.edital_id)
    if edital is None:
        raise HTTPException(status_code=404, detail=f"Edital '{req.edital_id}' não encontrado")

    profile = _to_py_profile(req.profile)
    edital_url = str(edital.get("url") or "")
    session = WritingSession(edital_id=req.edital_id, profile=profile, edital_url=edital_url)
    _writing_sessions[session.session_id] = session

    return session.get_info()


@app.post("/writing/turn", summary="Envia mensagem em sessão de escrita ativa")
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


# =============================================================================
# SERIALIZAÇÃO AUXILIAR
# =============================================================================


def _serialize(obj):
    """
    Converte recursivamente tipos numpy/pandas para tipos Python nativos,
    garantindo que a resposta seja serializável por JSON.
    """
    import numpy as np
    import pandas as pd

    if isinstance(obj, dict):
        return {k: _serialize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_serialize(v) for v in obj]
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        return None if np.isnan(obj) else float(obj)
    if isinstance(obj, float) and (obj != obj):  # NaN check
        return None
    try:
        if pd.isna(obj):
            return None
    except (TypeError, ValueError):
        pass
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    return obj
