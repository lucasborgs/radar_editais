"""Export Obsidian do quadrante investidor (Q3) — pós-migração hipergrado.

O investidor liga-se ao grafo pela PONTE DO NÓ tema (aresta `financia` em
hypergraphs/investidores.json): no Graph View isso conecta o quadrante de
entidades (quem investe) ao de eventos (oportunidades), via o eixo de tema
compartilhado. Desde a migração p/ hipergrados (Sprint 3, commit 07916303c) essa
ponte não é mais um parâmetro bespoke de `tema_note` — é resolvida genericamente
por `_edge_neighbors` (mesma trilha de qualquer aresta cross-source) e escrita
como `## Conexões via arestas` na nota. Estes testes travam (a) `_edge_neighbors`
só segue arestas onde a entidade de fato é membro — não todas as arestas do
grafo, (b) a nota de investidor/tema renderiza o conteúdo esperado.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.export_to_obsidian import _edge_neighbors, _investidor_note, _tema_note  # noqa: E402


def _edital(eid="finep:1", title="Chamada X", status="ABERTA"):
    return {"id": eid, "title": title, "status": status}


def test_investidor_note_renders_editais():
    note = _investidor_note("Indicator Capital", [_edital()], "radar-editais")
    assert "Investidor: Indicator Capital" in note
    assert "**1 editais** com participação." in note
    assert "[[radar-editais/editais/finep_1|Chamada X]]" in note
    assert "  - investidor" in note


def test_investidor_note_sem_editais_nao_quebra():
    note = _investidor_note("Indicator Capital", [], "radar-editais")
    assert "**0 editais** com participação." in note


def test_tema_note_renders_editais():
    note = _tema_note("tecnologias digitais e conectividade", [_edital()], "radar-editais")
    assert "Tema: tecnologias digitais e conectividade" in note
    assert "[[radar-editais/editais/finep_1|Chamada X]]" in note


def test_edge_neighbors_follows_only_edges_where_entity_is_member():
    """A ponte investidor->tema: só segue arestas `financia` que citam o próprio
    investidor, não arestas de OUTROS investidores no mesmo hipergrafo (bug real
    corrigido — antes coletava membros de toda aresta do grafo)."""
    graph = {
        "nodes": [],
        "edges": [
            {
                "type": "financia",
                "members": ["indicator capital", "tecnologias digitais e conectividade", "materiais"],
            },
            {
                "type": "financia",
                "members": ["vox capital", "saúde e ciências da vida"],
            },
        ],
    }
    occurrences = [("investidores", {"name": "Indicator Capital", "type": "Investidor"})]
    neighbors = _edge_neighbors("indicator capital", occurrences, {"investidores": graph})

    assert neighbors == {"tecnologias digitais e conectividade", "materiais"}
    assert "vox capital" not in neighbors
    assert "saúde e ciências da vida" not in neighbors


def test_edge_neighbors_bridges_tema_back_to_investidor():
    """Simétrico: a nota do tema também deve enxergar o investidor de volta."""
    graph = {
        "nodes": [],
        "edges": [
            {
                "type": "financia",
                "members": ["indicator capital", "materiais"],
            },
        ],
    }
    occurrences = [("investidores", {"name": "materiais", "type": "Tema"})]
    neighbors = _edge_neighbors("materiais", occurrences, {"investidores": graph})

    assert neighbors == {"indicator capital"}


def test_edge_neighbors_no_edges_for_entity_returns_empty():
    graph = {
        "nodes": [],
        "edges": [{"type": "financia", "members": ["vox capital", "saúde e ciências da vida"]}],
    }
    occurrences = [("investidores", {"name": "Antler", "type": "Investidor"})]
    neighbors = _edge_neighbors("antler", occurrences, {"investidores": graph})

    assert neighbors == set()
