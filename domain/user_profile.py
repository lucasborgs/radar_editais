"""
Perfil da Empresa (Radar de Editais)

Estrutura os dados da empresa do usuario para comparacao
com editais (Contexto A vs Contexto B).
"""

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class CompanyProfile:
    """Perfil estruturado da empresa para analise de aderencia."""

    # ── Identificação ──────────────────────────────────────────────────────
    nome: str = ""
    cnpj: str = ""
    url_site: str = ""
    tipo_entidade: str = "empresa"  # "empresa" | "startup" | "universidade" | "ICT"

    # ── Descrição da empresa ───────────────────────────────────────────────
    one_liner: str = ""             # "Desenvolvemos X para resolver Y em Z" (1 frase)
    problem_statement: str = ""     # Problema que a empresa resolve
    solution_summary: str = ""      # Como resolve (tecnologia/abordagem)
    descricao_atividades: str = ""  # Descrição completa das atividades
    portfolio_projetos: str = ""

    # ── Classificação ─────────────────────────────────────────────────────
    tamanho_empresa: str = ""        # MEI, ME, EPP, MEDIO, GRANDE
    faturamento_anual_faixa: str = ""  # "<500K" | "500K-5M" | "5M-50M" | ">50M"
    localizacao: str = ""
    capital_social: float | None = None
    certificacoes: list[str] = field(default_factory=list)

    # ── Perfil tecnológico ─────────────────────────────────────────────────
    trl: int | None = None       # Technology Readiness Level atual do projeto (1-9)
    equipe_resumo: str = ""

    # ── Intenção de financiamento ──────────────────────────────────────────
    tipos_financiamento_interesse: list[str] = field(default_factory=list)
    # Valores: "subvencao_nao_reembolsavel" | "credito_reembolsavel"
    #          | "matching_embrapii" | "pesquisa_colaborativa"
    uso_financiamento: list[str] = field(default_factory=list)
    # Valores: "P&D_interno" | "contratacao" | "equipamento"
    #          | "prototipagem" | "internacionalizacao" | "marketing"
    valor_buscado: float | None = None  # Valor em BRL que busca

    def to_context(self) -> str:
        """Gera texto de contexto para uso em prompts LLM."""
        parts = []

        if self.nome:
            parts.append(f"Empresa: {self.nome}")
        if self.cnpj:
            parts.append(f"CNPJ: {self.cnpj}")
        if self.tipo_entidade:
            parts.append(f"Tipo de entidade: {self.tipo_entidade}")
        if self.tamanho_empresa:
            parts.append(f"Porte: {self.tamanho_empresa}")
        if self.faturamento_anual_faixa:
            parts.append(f"Faturamento anual: {self.faturamento_anual_faixa}")
        if self.localizacao:
            parts.append(f"Localidade: {self.localizacao}")
        if self.capital_social:
            parts.append(f"Capital Social: R$ {self.capital_social:,.2f}")
        if self.certificacoes:
            parts.append(f"Certificacoes: {', '.join(self.certificacoes)}")
        if self.trl is not None:
            parts.append(f"TRL atual do projeto: {self.trl}")
        if self.one_liner:
            parts.append(f"\nProposta de valor: {self.one_liner}")
        if self.problem_statement:
            parts.append(f"\nProblema resolvido:\n{self.problem_statement}")
        if self.solution_summary:
            parts.append(f"\nSolucao/Tecnologia:\n{self.solution_summary}")
        if self.descricao_atividades:
            parts.append(f"\nAtividades:\n{self.descricao_atividades}")
        if self.portfolio_projetos:
            parts.append(f"\nPortfolio de Projetos:\n{self.portfolio_projetos}")
        if self.equipe_resumo:
            parts.append(f"\nEquipe:\n{self.equipe_resumo}")
        if self.tipos_financiamento_interesse:
            parts.append(f"\nTipos de financiamento desejados: {', '.join(self.tipos_financiamento_interesse)}")
        if self.uso_financiamento:
            parts.append(f"Uso previsto do recurso: {', '.join(self.uso_financiamento)}")
        if self.valor_buscado is not None:
            parts.append(f"Valor buscado: R$ {self.valor_buscado:,.2f}")

        return "\n".join(parts) if parts else "Perfil da empresa nao preenchido."

    def is_complete(self) -> bool:
        """Verifica se os campos mínimos para um match de qualidade estão preenchidos."""
        return bool(
            self.nome
            and self.tipo_entidade
            and self.tamanho_empresa
            and self.descricao_atividades
            and self.trl is not None
            and self.tipos_financiamento_interesse
        )

    def completion_pct(self) -> int:
        """Retorna percentual de preenchimento do perfil."""
        fields_check = [
            bool(self.nome),
            bool(self.cnpj),
            bool(self.tipo_entidade),
            bool(self.one_liner),
            bool(self.descricao_atividades),
            bool(self.portfolio_projetos),
            bool(self.tamanho_empresa),
            bool(self.localizacao),
            self.capital_social is not None,
            len(self.certificacoes) > 0,
            bool(self.equipe_resumo),
            self.trl is not None,
            len(self.tipos_financiamento_interesse) > 0,
            len(self.uso_financiamento) > 0,
            self.valor_buscado is not None,
        ]
        return int(sum(fields_check) / len(fields_check) * 100)
