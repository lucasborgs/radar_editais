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
    solution_summary: str = ""      # Como resolve (tecnologia/abordagem)
    descricao_atividades: str = ""  # Descrição completa das atividades
    portfolio_projetos: str = ""

    # ── Classificação ─────────────────────────────────────────────────────
    tamanho_empresa: str = ""        # MEI, ME, EPP, MEDIO, GRANDE
    capital_social: float | None = None

    # ── Perfil tecnológico ─────────────────────────────────────────────────
    trl: int | None = None       # Technology Readiness Level atual do projeto (1-9)
    equipe_resumo: str = ""

    # ── Intenção de financiamento ──────────────────────────────────────────
    tipos_financiamento_interesse: list[str] = field(default_factory=list)
    # Valores: "subvencao_nao_reembolsavel" | "credito_reembolsavel"
    #          | "matching_embrapii" | "pesquisa_colaborativa"

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
        if self.capital_social:
            parts.append(f"Capital Social: R$ {self.capital_social:,.2f}")
        if self.trl is not None:
            parts.append(f"TRL atual do projeto: {self.trl}")
        if self.one_liner:
            parts.append(f"\nProposta de valor: {self.one_liner}")
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

        return "\n".join(parts) if parts else "Perfil da empresa nao preenchido."

    def is_complete(self) -> bool:
        """Verifica se os campos mínimos para um match de qualidade estão preenchidos."""
        return bool(
            self.nome
            and self.tipo_entidade
            and self.tamanho_empresa
            and self.one_liner
            and self.solution_summary
            and self.descricao_atividades
            and self.trl is not None
            and self.tipos_financiamento_interesse
        )

    def completion_pct(self) -> int:
        """Retorna percentual de preenchimento do perfil."""
        fields_check = [
            bool(self.nome),
            bool(self.tipo_entidade),
            bool(self.one_liner),
            bool(self.solution_summary),
            bool(self.descricao_atividades),
            bool(self.tamanho_empresa),
            self.trl is not None,
            len(self.tipos_financiamento_interesse) > 0,
        ]
        return int(sum(fields_check) / len(fields_check) * 100)
