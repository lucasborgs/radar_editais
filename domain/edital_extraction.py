"""Schema tipado de extração de edital — o contrato entre a extração-LLM e o
scoring determinístico.

Materializa em código o que `docs/spec_extraction_schema.md` (Fase 1) definiu:
  • campos DECISÃO carregam abstenção explícita — `Extracted[T]` com
    `state ∈ {stated, inferred, absent}` + `evidence` (trecho da fonte). "Não
    consta" vira estado conhecido, não meio-crédito cego.
  • campos CONTEXTO são `valor | None` simples — alimentam o Stage 2 semântico /
    a escrita (que pega profundidade via RAG) / display, e toleram ruído.

A extração-LLM (Fase 3) emite uma instância deste modelo via structured outputs;
o scoring (Fase 3) lê os campos DECISÃO e aplica a política de `absent` validada
(excluir do gate, elegibilidade normalizada pelos presentes). Aqui só definimos o
CONTRATO — sem lógica de scoring nem chamadas de modelo.
"""
from __future__ import annotations

from enum import Enum
from typing import Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class FieldState(str, Enum):
    """Origem do valor de um campo DECISÃO."""
    STATED = "stated"      # explícito na fonte
    INFERRED = "inferred"  # deduzido pela LLM (confiança menor)
    ABSENT = "absent"      # não consta — a LLM deve abster, não inventar


class Extracted(BaseModel, Generic[T]):
    """Um campo DECISÃO: valor + de onde veio + evidência textual."""
    value: T | None = None
    state: FieldState = FieldState.ABSENT
    evidence: str | None = None  # trecho/citação da fonte; None quando absent

    @property
    def is_present(self) -> bool:
        """Campo presente = tem base para decidir (não-absent e com valor)."""
        return self.state is not FieldState.ABSENT and self.value is not None


def absent() -> Extracted:
    """Factory de campo ausente (default dos campos DECISÃO)."""
    return Extracted(state=FieldState.ABSENT)


class TrlRange(BaseModel):
    min: int | None = None
    max: int | None = None


class EditalExtraction(BaseModel):
    """Extração canônica de um edital, agnóstica de fonte.

    Proveniência (`source`, `native_id`) + campos DECISÃO (com abstenção) +
    campos CONTEXTO (valor|None) + temporais (pipeline próprio em core/temporal).
    """
    # --- proveniência ---
    source: str
    native_id: str

    # --- DECISÃO (alimentam elegibilidade/ranking determinístico) ---
    eligible_entities: Extracted[list[str]] = Field(default_factory=absent)
    themes: Extracted[list[str]] = Field(default_factory=absent)
    eligible_sectors: Extracted[list[str]] = Field(default_factory=absent)
    trl_range: Extracted[TrlRange] = Field(default_factory=absent)
    mechanism: Extracted[str] = Field(default_factory=absent)
    # Exceção (spec §decisões): ausência tem default de domínio ("não exige") no
    # scoring — não entra na política de exclusão-por-absent. Ainda assim é
    # extraível com abstenção, para o eval medir e o scoring decidir.
    counterpart_required: Extracted[bool] = Field(default_factory=absent)

    # --- CONTEXTO (Stage 2 semântico / escrita / display; tolera ruído) ---
    title: str | None = None
    objective: str | None = None
    key_requirements: list[str] = Field(default_factory=list)

    # --- TEMPORAL (vigência é tratada por core/temporal.py) ---
    status: str | None = None
    deadline: str | None = None


# Campos DECISÃO sujeitos à política de exclusão-por-absent (spec §decisões).
# counterpart_required é DECISÃO mas fica de fora (default de domínio).
DECISION_FIELDS: tuple[str, ...] = (
    "eligible_entities",
    "themes",
    "eligible_sectors",
    "trl_range",
    "mechanism",
)

# Substantivos cuja ausência TOTAL → sem base de elegibilidade → provisório/HITL.
SUBSTANTIVE_DECISION_FIELDS: tuple[str, ...] = (
    "eligible_entities",
    "themes",
    "trl_range",
    "mechanism",
)

CONTEXT_FIELDS: tuple[str, ...] = ("title", "objective", "key_requirements")

__all__ = [
    "FieldState",
    "Extracted",
    "absent",
    "TrlRange",
    "EditalExtraction",
    "DECISION_FIELDS",
    "SUBSTANTIVE_DECISION_FIELDS",
    "CONTEXT_FIELDS",
]
