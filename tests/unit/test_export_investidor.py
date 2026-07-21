"""Export Obsidian do quadrante investidor (Q3) — catálogo gold (v3).

Desde a migração para o gold (PR-C v3) o hipergrado morreu: não há mais nós/
arestas nem `_edge_neighbors`. O investidor liga-se ao grafo pela PONTE DE TEMA —
`_entity_note` recebe `themes` (setores/tecnologias da entidade gold) e emite
wikilinks para as notas de tema, que o Graph View renderiza como aresta ao
quadrante de eventos (editais que compartilham o mesmo tema). Estes testes travam
(a) a nota de investidor renderiza editais + a ponte de tema, (b) o caso sem
editais não quebra.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from scripts.export_to_obsidian import _entity_note  # noqa: E402

pytestmark = pytest.mark.unit


def _edital(eid="finep:1", title="Chamada X", status="ABERTA"):
    return {"id": eid, "title": title, "status": status}


def test_investidor_note_renders_editais():
    note = _entity_note("Indicator Capital", "💼", "Investidor", "investidor", [_edital()], "radar-editais")
    assert "Investidor: Indicator Capital" in note
    assert "**1 editais** relacionados." in note
    assert "[[radar-editais/editais/finep_1|Chamada X]]" in note
    assert "  - investidor" in note


def test_investidor_note_sem_editais_nao_quebra():
    note = _entity_note("Indicator Capital", "💼", "Investidor", "investidor", [], "radar-editais")
    assert "**0 editais** relacionados." in note


def test_investidor_note_bridges_to_temas():
    """A ponte investidor→tema (v3): a nota do investidor linka os temas dele, e o
    Graph View conecta ao quadrante de eventos pelo tema compartilhado."""
    note = _entity_note(
        "Indicator Capital", "💼", "Investidor", "investidor", [],
        "radar-editais",
        themes=["tecnologias digitais e conectividade", "materiais"],
    )
    assert "## Temas" in note
    assert "[[radar-editais/temas/tecnologias-digitais-e-conectividade|tecnologias digitais e conectividade]]" in note
    assert "[[radar-editais/temas/materiais|materiais]]" in note


def test_entity_note_without_themes_has_no_temas_section():
    note = _entity_note("Vox Capital", "💼", "Investidor", "investidor", [], "radar-editais")
    assert "## Temas" not in note


def test_tema_note_renders_editais():
    note = _entity_note("tecnologias digitais e conectividade", "🏷️", "Tema", "tema", [_edital()], "radar-editais")
    assert "Tema: tecnologias digitais e conectividade" in note
    assert "[[radar-editais/editais/finep_1|Chamada X]]" in note
