"""Schema tipado de extração de edital — o contrato entre a extração-LLM e o
scoring determinístico (v2).

Princípios:
  • campos DECISÃO carregam abstenção (`Extracted[T]` com `state` + `evidence`).
  • campos CONTEXTO são `valor | None` (alimentam Stage 2 / escrita; toleram ruído).
  • `evidence` deve ser SUBSTRING VERBATIM da fonte (âncora de auditoria/anti-alucinação).
  • `inferred` é PROIBIDO em GATE_FIELDS — o scoring trata inferred≡absent lá
    (gate determinístico sobre inferência é contraditório). Permitido em contexto.
  • a canonicalização de `themes` (vocab fechado) é passo SEGUINTE, não da extração:
    aqui guardamos o termo CRU verbatim; `canonicalize_themes` roda depois.

Sem lógica de scoring nem chamadas de modelo — só o CONTRATO.
"""
from __future__ import annotations

from enum import Enum
from typing import Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class FieldState(str, Enum):
    """Origem do valor de um campo DECISÃO."""
    STATED = "stated"      # explícito na fonte (evidence = substring verbatim)
    INFERRED = "inferred"  # deduzido — PROIBIDO em GATE_FIELDS (scoring o trata como absent)
    ABSENT = "absent"      # não consta — a LLM deve abster, não inventar


class Extracted(BaseModel, Generic[T]):
    """Um campo DECISÃO: valor + de onde veio + evidência textual verbatim."""
    value: T | None = None
    state: FieldState = FieldState.ABSENT
    evidence: str | None = None  # substring exata da fonte; None quando absent

    @property
    def is_present(self) -> bool:
        """Presente = base para decidir (não-absent e com valor)."""
        return self.state is not FieldState.ABSENT and self.value is not None


def absent() -> Extracted:
    """Factory de campo ausente (default dos campos DECISÃO)."""
    return Extracted(state=FieldState.ABSENT)


class TrlRange(BaseModel):
    min: int | None = None
    max: int | None = None


class Counterpart(BaseModel):
    """Contrapartida: não basta exigir; o % decide viabilidade (5% ≠ 50%)."""
    required: bool | None = None
    percentage: float | None = None  # None = % não informado


class FundingAmount(BaseModel):
    """Faixa financiável do projeto (contexto p/ escrita e decisão de aplicar)."""
    min: float | None = None
    max: float | None = None


class EligibilityConstraint(BaseModel):
    """Restrição organizacional dura (região, idade, faturamento, CNAE, consórcio…).

    Capturada ESTRUTURADA (não enterrada em key_requirements), com abstenção. NÃO
    entra no gate determinístico ainda — vira sinal de contexto/HITL até o
    CompanyProfile ter o campo-par para comparar. Ver BACKLOG (perfil é o gargalo).
    """
    type: str                      # ex.: "region" | "company_age" | "revenue" | "cnae" | "consortium"
    description: str               # o requisito em texto curto
    state: FieldState = FieldState.STATED
    evidence: str | None = None


class EditalExtraction(BaseModel):
    """Extração canônica de um edital, agnóstica de fonte (v2)."""
    # --- proveniência ---
    source: str
    native_id: str
    # Discriminador do tipo de oportunidade-EVENTO. Os 3
    # tipos-evento compartilham este schema/pipeline; só os campos opcionais abaixo
    # ramificam. ENTIDADES (como investidor) usam os schemas diretos do gold.
    opportunity_type: str = "edital"   # "edital" | "desafio" | "programa"

    # --- DECISÃO (alimentam elegibilidade/ranking determinístico) ---
    eligible_entities: Extracted[list[str]] = Field(default_factory=absent)
    themes: Extracted[list[str]] = Field(default_factory=absent)  # CRU verbatim; canoniza depois
    trl_range: Extracted[TrlRange] = Field(default_factory=absent)
    mechanism: Extracted[str] = Field(default_factory=absent)
    counterpart: Extracted[Counterpart] = Field(default_factory=absent)
    # Flag (não-gate): o edital exige parceria com ICT? Liga o matchmaking de ICT,
    # NÃO desqualifica (filosofia: ajudamos a achar a ICT).
    requires_ict_partner: Extracted[bool] = Field(default_factory=absent)

    # --- CONTEXTO (Stage 2 semântico / escrita; tolera ruído) ---
    title: str | None = None
    objective: str | None = None
    key_requirements: list[str] = Field(default_factory=list)
    funding_amount: FundingAmount | None = None
    project_duration_months: int | None = None
    # Restrições organizacionais estruturadas (capturar agora, gate quando o
    # perfil tiver o lado oposto).
    eligibility_constraints: list[EligibilityConstraint] = Field(default_factory=list)

    # --- CONTEXTO específico de DESAFIO (Q2 open innovation), opcional ---
    empresa_ancora: str | None = None       # quem traz a dor (operador/corporate)
    poc_scope: str | None = None            # escopo do piloto/PoC esperado
    # --- CONTEXTO específico de PROGRAMA (Q4 aceleração/incubação), opcional ---
    modelo_participacao: str | None = None  # "equity" | "no-equity"
    beneficios: list[str] = Field(default_factory=list)  # capital, mentoria, espaço

    # status/deadline NÃO vivem aqui: o pipeline temporal (core/temporal.py) é o
    # dono (SSOT) — o PDF lido na extração fica stale com retificações.


# Campos DECISÃO (estruturados, com abstenção).
DECISION_FIELDS: tuple[str, ...] = (
    "eligible_entities",
    "themes",
    "trl_range",
    "mechanism",
    "counterpart",
    "requires_ict_partner",
)

# GATE_FIELDS: sujeitos à exclusão-por-absent E à proibição de `inferred`.
# Fora: counterpart (default de domínio "não exige") e requires_ict_partner (flag).
GATE_FIELDS: tuple[str, ...] = (
    "eligible_entities",
    "themes",
    "trl_range",
    "mechanism",
)

CONTEXT_FIELDS: tuple[str, ...] = (
    "title",
    "objective",
    "key_requirements",
    "funding_amount",
    "project_duration_months",
    "eligibility_constraints",
    "empresa_ancora",
    "poc_scope",
    "modelo_participacao",
    "beneficios",
)

__all__ = [
    "FieldState",
    "Extracted",
    "absent",
    "TrlRange",
    "Counterpart",
    "FundingAmount",
    "EligibilityConstraint",
    "EditalExtraction",
    "DECISION_FIELDS",
    "GATE_FIELDS",
    "CONTEXT_FIELDS",
]
