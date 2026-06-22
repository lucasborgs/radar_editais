"""Schema tipado de um INVESTIDOR (fundo/anjo/corporate venture) — kind_class=entidade.

Espelha o padrão de `domain/edital_extraction.py` (`Extracted[T]`/`absent` para os
campos de DECISÃO de tese), mas é uma ENTIDADE, não um evento:
  • NÃO tem status/deadline (não flui por core/temporal.py).
  • NÃO tem GATE_FIELDS (fundo não desqualifica por CNPJ — match é alinhamento de
    tese, soft).
  • Identidade própria `investidor:<slug>`, artefato próprio `investidores.json`
    (espelha icts.json), populado por CURADORIA MANUAL (~30-50 fundos).

Só o CONTRATO — sem scoring,
sem chamadas de modelo.
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from domain.edital_extraction import Extracted, absent


class TicketRange(BaseModel):
    """Faixa de cheque do investidor (R$). Casa com CompanyProfile.round_alvo_brl."""
    min_brl: float | None = None
    max_brl: float | None = None


class InvestorEntity(BaseModel):
    """Entidade investidor. id_format: `investidor:<slug>` (ex.: investidor:kptl)."""
    # --- proveniência ---
    source: str = "investidor"
    native_id: str                      # slug, ex.: "kptl"
    name: str

    # --- TESE (alimenta o match de tese — Stage 2 GraphRAG, spec §3.8) ---
    tese: str | None = None             # texto livre da tese de investimento
    # temas canônicos (MESMA representação de edital.themes) — é a ponte
    # investidor↔edital no grafo (interseção por slug, como a ICT).
    tese_themes: Extracted[list[str]] = Field(default_factory=absent)
    setores: Extracted[list[str]] = Field(default_factory=absent)       # ponte setor
    estagio_alvo: Extracted[list[str]] = Field(default_factory=absent)  # pre-seed|seed|serie-a
    ticket_range: Extracted[TicketRange] = Field(default_factory=absent)
    lead_follow: str | None = None      # "lead" | "follow" | "ambos"

    # --- CONTEXTO (escrita do pitch + display + semente da Camada B induzida) ---
    portfolio: list[str] = Field(default_factory=list)        # empresas investidas
    co_investidores: list[str] = Field(default_factory=list)  # syndication → semente induzida
    site: str | None = None
    contato: dict | None = None


# Campos de TESE usados pelo match de entidade (sem gate — nada elimina).
THESIS_FIELDS: tuple[str, ...] = (
    "tese_themes",
    "setores",
    "estagio_alvo",
    "ticket_range",
)

__all__ = ["TicketRange", "InvestorEntity", "THESIS_FIELDS"]
