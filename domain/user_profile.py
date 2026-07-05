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

    # ── Elegibilidade organizacional ───────────────────────────────────────
    # Pares dos critérios DUROS que os editais filtram (EditalExtraction v2:
    # eligibility_constraints). Capturados agora para alinhar os dois lados; o
    # gate determinístico sobre eles entra na Fase 3 (hoje são sinal/contexto).
    uf: str = ""                       # UF/estado (elegibilidade geográfica)
    faturamento_anual: float | None = None   # receita (≠ capital_social)
    ano_fundacao: int | None = None    # ano de constituição (idade da empresa)
    data_constituicao: str = ""         # data de registro do CNPJ (AAAA-MM-DD), p/ regras de 12 meses

    # ── Perfil tecnológico ─────────────────────────────────────────────────
    trl: int | None = None       # Technology Readiness Level atual do projeto (1-9)
    equipe_resumo: str = ""

    # ── Intenção de financiamento ──────────────────────────────────────────
    tipos_financiamento_interesse: list[str] = field(default_factory=list)
    # Valores: "subvencao_nao_reembolsavel" | "pesquisa_colaborativa" | "capital_risco"
    # (escopo reduzido — spec mechanism-scope-decisions: credito_reembolsavel D1,
    #  investimento_direto D2 e matching_embrapii D3 saíram do eixo selecionável;
    #  capital_risco adicionado em 2026-07 reativando o eixo de investidor via perfil)

    # ── Perfil para capital privado / desafios (Q3/Q4) — opcionais ──────────
    # Não quebram o match de edital; alimentam o match de investidor (tese) e a
    # completude POR-QUADRANTE (perfil "completo p/ edital" ≠ "completo p/ fundo").
    estagio: str = ""                    # pre-seed | seed | serie-a (≠ TRL)
    mrr_arr: float | None = None         # tração financeira (receita recorrente)
    round_alvo_brl: float | None = None  # quanto capta (casa c/ investidor.ticket_range)
    cap_table_resumo: str = ""           # quem já investiu / equity disponível
    tracao_resumo: str = ""              # clientes, pilotos, LOIs (≠ portfolio_projetos)

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
        if self.faturamento_anual:
            parts.append(f"Faturamento anual: R$ {self.faturamento_anual:,.2f}")
        if self.uf:
            parts.append(f"UF: {self.uf}")
        if self.ano_fundacao:
            parts.append(f"Ano de fundação: {self.ano_fundacao}")
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

        # Capital privado / desafios (Q3/Q4) — só aparecem se preenchidos.
        if self.estagio:
            parts.append(f"Estágio: {self.estagio}")
        if self.mrr_arr:
            parts.append(f"MRR/ARR: R$ {self.mrr_arr:,.2f}")
        if self.round_alvo_brl:
            parts.append(f"Round alvo de captação: R$ {self.round_alvo_brl:,.2f}")
        if self.tracao_resumo:
            parts.append(f"\nTração:\n{self.tracao_resumo}")
        if self.cap_table_resumo:
            parts.append(f"\nCap table:\n{self.cap_table_resumo}")

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

    # Campos mínimos para INICIAR uma WritingSession (Fase 2 — gate determinístico).
    # Ordem canônica: identificação → classificação dura → descrição. A API
    # devolve `missing` ao front quando o gate barra (mesmo padrão de
    # request_user_info). Difere de is_complete() (que mira qualidade do match):
    # aqui o foco é o mínimo para a escrita arrancar com perfil + elegibilidade.
    _WRITING_MIN_FIELDS = (
        "nome", "tipo_entidade", "trl", "tamanho_empresa", "uf", "descricao_atividades",
    )

    def is_complete_for_writing(self) -> tuple[bool, list[str]]:
        """Gate (sem LLM) da criação de WritingSession.

        Retorna ``(ok, missing)``: ``ok`` é True quando todos os campos mínimos
        estão preenchidos; ``missing`` lista os ausentes na ordem canônica de
        ``_WRITING_MIN_FIELDS`` (vazio quando ok). ``trl`` conta como presente
        quando ``is not None`` (0 não é um TRL válido, então truthiness basta);
        os demais são strings e contam quando não-vazias.
        """
        def _present(field: str) -> bool:
            val = getattr(self, field)
            return val is not None if field == "trl" else bool(val)

        missing = [f for f in self._WRITING_MIN_FIELDS if not _present(f)]
        return (not missing, missing)

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
