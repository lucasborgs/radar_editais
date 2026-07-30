"""Contrato HTTP canônico do perfil da empresa.

Este módulo é neutro: não importa API, banco ou serviços. O payload é usado
pelos routers e os nomes são reutilizados pelo loader do perfil de domínio.
Campos desconhecidos são rejeitados explicitamente; chaves legadas já
persistidas continuam preservadas pelo endpoint de PUT.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

PROFILE_FIELD_NAMES: tuple[str, ...] = (
    "nome",
    "cnpj",
    "url_site",
    "tipo_entidade",
    "one_liner",
    "solution_summary",
    "descricao_atividades",
    "portfolio_projetos",
    "estilo_escrita",
    "tamanho_empresa",
    "capital_social",
    "uf",
    "faturamento_anual",
    "ano_fundacao",
    "equipe_resumo",
    "trl",
    "tipos_financiamento_interesse",
    "estagio",
    "mrr_arr",
    "round_alvo_brl",
    "cap_table_resumo",
    "tracao_resumo",
)


class CompanyProfilePayload(BaseModel):
    """Payload completo aceito por ``PUT /me/profile`` e Explore."""

    model_config = ConfigDict(extra="forbid")

    nome: str = ""
    cnpj: str = ""
    url_site: str = ""
    tipo_entidade: str = "empresa"
    one_liner: str = ""
    solution_summary: str = ""
    descricao_atividades: str = ""
    portfolio_projetos: str = ""
    estilo_escrita: str = ""
    tamanho_empresa: str = ""
    capital_social: float | None = None
    uf: str = ""
    faturamento_anual: float | None = None
    ano_fundacao: int | None = None
    equipe_resumo: str = ""
    trl: int | None = None
    tipos_financiamento_interesse: list[str] = Field(default_factory=list)
    estagio: str = ""
    mrr_arr: float | None = None
    round_alvo_brl: float | None = None
    cap_table_resumo: str = ""
    tracao_resumo: str = ""
