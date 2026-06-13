"""Export Obsidian do quadrante investidor (Q3) — Fase 1.

O investidor liga-se ao grafo pela PONTE DO NÓ tema (tese_themes ⊆ tema_vocab,
WIKI.md §6.1.3): no Graph View a aresta vem do wikilink `[[temas/…]]`. Estes
testes travam (a) o wikilink de tese_theme, (b) o fallback de generalista sem
tema, (c) a listagem de investidores na nota de tema.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.export_to_obsidian import investidor_note, tema_note  # noqa: E402


def _inv(**kw):
    base = {
        "id": "investidor:indicator-capital",
        "name": "Indicator Capital",
        "tese": "VC de deep-tech B2B.",
        "tese_themes": ["tecnologias digitais e conectividade"],
        "tese_keywords": ["deep-tech", "b2b"],
        "setores": ["multissetorial"],
        "estagio_alvo": ["seed", "serie-a"],
        "lead_follow": "lead",
        "generalista": False,
        "fund_status": "ativo",
        "site": "https://indicator.capital",
    }
    base.update(kw)
    return base


def test_investidor_note_links_tese_themes_to_tema_nodes():
    """tese_theme vira wikilink pro nó de tema compartilhado — a ponte Q3↔evento."""
    note = investidor_note(_inv())
    assert "[[radar-editais/temas/tecnologias-digitais-e-conectividade|" in note
    assert "investidor_id: investidor:indicator-capital" in note
    assert "  - investidor" in note
    # tags de filtro do node_type (não criam aresta, mas seguem o schema)
    assert "  - estagio/seed" in note
    assert "  - setor/multissetorial" in note


def test_investidor_note_generalista_has_no_theme_bridge():
    """Generalista (tese_themes=[]) não inventa aresta temática — explicita o porquê."""
    note = investidor_note(_inv(tese_themes=[], generalista=True))
    assert "[[radar-editais/temas/" not in note
    assert "generalista" in note.lower()


def test_tema_note_lists_investidores_section():
    """A nota de tema é a ponte 3-way: lista editais, ICTs E investidores."""
    note = tema_note(
        "tecnologias digitais e conectividade", [], {},
        investidores_for_theme=[_inv()],
    )
    assert "## Investidores" in note
    assert "[[radar-editais/investidores/investidor-indicator-capital|Indicator Capital]]" in note


def test_tema_note_omits_investidores_section_when_empty():
    note = tema_note("saúde e ciências da vida", [], {})
    assert "## Investidores" not in note
