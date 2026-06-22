"""Matchmaking ICT ↔ edital — sugere ICTs parceiras por afinidade temática.

ICTs Fase C, peça 2 (ver docs/spec_ict_phase_c.md). Determinístico, sem LLM: a
ponte é a interseção de macro-temas (`edital.themes ∩ ict.themes`, vocab §5.9),
construída na Fase A. `rank_partners` é puro (testável com fixtures); os loaders
têm cache por mtime, espelhando `core.services.kg_match_service`.

Uso (peça 3, tool do Explorador):
    from core.ict_match import find_partners
    for p in find_partners("finep:769", k=5):
        ...  # p.name, p.themes_match, p.contact, p.url
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from core.kg import kg_store

logger = logging.getLogger(__name__)


@dataclass
class PartnerSuggestion:
    """Uma ICT candidata a parceira de um edital."""
    id: str
    name: str
    kind: str
    score: int                       # nº de macro-temas em comum
    themes_match: list[str]          # quais temas casaram
    contact: dict = field(default_factory=dict)
    url: str = ""
    brings_cofinancing: bool = False  # arranjo aporta recurso não-reembolsável (§6.1.2)


# =============================================================================
# Lógica pura (testável sem arquivos)
# =============================================================================

def rank_partners(
    edital_themes: list[str], icts: list[dict], k: int = 5
) -> list[PartnerSuggestion]:
    """Ranqueia ICTs por overlap de macro-tema com o edital.

    Score = |edital.themes ∩ ict.themes|. Desempate: nº de areas_raw (proxy de
    abrangência), depois nome (estável). Sem interseção → fora do ranking. Edital
    sem tema → lista vazia (não força match ruim — coerente com o normalizador).
    """
    themes = set(edital_themes)
    if not themes:
        return []

    scored: list[PartnerSuggestion] = []
    for ict in icts:
        match = themes & set(ict.get("themes", []))
        if not match:
            continue
        scored.append(PartnerSuggestion(
            id=ict.get("id", ""),
            name=ict.get("name", ""),
            kind=ict.get("kind", ""),
            score=len(match),
            themes_match=sorted(match),
            contact=ict.get("contact", {}),
            url=ict.get("url", ""),
            brings_cofinancing=ict.get("brings_cofinancing", False),
        ))

    scored.sort(key=lambda p: (-p.score, -_areas_len(icts, p.id), p.name))
    return scored[:k]


def _areas_len(icts: list[dict], ict_id: str) -> int:
    for ict in icts:
        if ict.get("id") == ict_id:
            return len(ict.get("areas_raw", []))
    return 0


def edital_entry(edital_id: str) -> dict | None:
    """Entry do edital no index.json. None se não existir."""
    index = kg_store.load_index()
    for e in index.get("editais", []):
        if e.get("id") == edital_id:
            return e
    return None


def edital_themes(edital_id: str) -> list[str] | None:
    """Temas de um edital do index.json. None se o edital não existir."""
    e = edital_entry(edital_id)
    return None if e is None else e.get("themes", [])


def find_partners(edital_id: str, k: int = 5) -> list[PartnerSuggestion]:
    """ICTs candidatas a parceiras de um edital. [] se edital inexistente, sem
    tema, ou sem ICT de tema compatível."""
    themes = edital_themes(edital_id)
    if themes is None:
        logger.info("find_partners: edital %s não encontrado no índice", edital_id)
        return []
    icts = kg_store.load_icts()
    return rank_partners(themes, icts, k=k)
