"""Dependências compartilhadas pelos routers (extraído de backend/api.py).

Singletons de serviço + schema de perfil + helpers de carregamento que mais de
um router consome. Routers importam daqui; `backend/api.py` fica só com app,
middleware e wiring — nunca importe `backend.api` a partir de um router.
"""

from __future__ import annotations

from pydantic import BaseModel

from core.services.content_library import get_item
from core.services.explore_agent import ExploreAgent
from core.services.graph_service import GraphService
from core.services.hybrid_match_service import HybridMatchService
from domain.user_profile import CompanyProfile as PyCompanyProfile

# =============================================================================
# SINGLETONS
# =============================================================================

wiki_matcher = HybridMatchService()
graph_service = GraphService()
explore_agent = ExploreAgent()


# =============================================================================
# SCHEMA DE PERFIL (compartilhado por matching/writing/brief)
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
    # elegibilidade organizacional (espelha domain.CompanyProfile; o diff do
    # front-door propõe estes campos e eles precisam round-trippar pela API)
    uf: str = ""
    faturamento_anual: float | None = None
    ano_fundacao: int | None = None
    equipe_resumo: str = ""
    trl: int | None = None
    tipos_financiamento_interesse: list[str] = []
    # capital privado / desafios (Q3/Q4) — opcionais, alimentam o match de investidor
    estagio: str = ""
    mrr_arr: float | None = None
    round_alvo_brl: float | None = None
    cap_table_resumo: str = ""
    tracao_resumo: str = ""


def to_py_profile(schema: CompanyProfileSchema) -> PyCompanyProfile:
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
        uf=schema.uf,
        faturamento_anual=schema.faturamento_anual,
        ano_fundacao=schema.ano_fundacao,
        equipe_resumo=schema.equipe_resumo,
        trl=schema.trl,
        tipos_financiamento_interesse=schema.tipos_financiamento_interesse,
        estagio=schema.estagio,
        mrr_arr=schema.mrr_arr,
        round_alvo_brl=schema.round_alvo_brl,
        cap_table_resumo=schema.cap_table_resumo,
        tracao_resumo=schema.tracao_resumo,
    )


def load_library_items(db, workspace_id: str, item_ids: list[str]) -> list[dict]:
    if not item_ids:
        return []
    return [
        item for item_id in item_ids
        if (item := get_item(db, item_id, workspace_id)) is not None
    ]


def profile_from_workspace(db, workspace_id: str) -> PyCompanyProfile:
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
        "estagio", "mrr_arr", "round_alvo_brl", "cap_table_resumo", "tracao_resumo",
    }
    return PyCompanyProfile(**{k: v for k, v in raw.items() if k in allowed})
