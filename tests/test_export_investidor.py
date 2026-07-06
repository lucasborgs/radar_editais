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

from scripts.export_to_obsidian import _edge_neighbors, _entity_note  # noqa: E402


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


def test_tema_note_renders_editais():
    note = _entity_note("tecnologias digitais e conectividade", "🏷️", "Tema", "tema", [_edital()], "radar-editais")
    assert "Tema: tecnologias digitais e conectividade" in note
    assert "[[radar-editais/editais/finep_1|Chamada X]]" in note


def test_edge_neighbors_follows_only_edges_where_entity_is_member():
    """A ponte investidor->tema: só segue arestas `financia` que citam o próprio
    investidor, não arestas de OUTROS investidores no mesmo hipergrafo (bug real
    corrigido — antes coletava membros de toda aresta do grafo). `members` são
    ids (resolvidos via `nodes`), não nomes."""
    graph = {
        "nodes": [
            {"id": "ator:indicator_capital", "name": "Indicator Capital"},
            {"id": "con:tecnologias_digitais", "name": "tecnologias digitais e conectividade"},
            {"id": "con:materiais", "name": "materiais"},
            {"id": "ator:vox_capital", "name": "Vox Capital"},
            {"id": "con:saude", "name": "saúde e ciências da vida"},
        ],
        "edges": [
            {
                "type": "financia",
                "members": ["ator:indicator_capital", "con:tecnologias_digitais", "con:materiais"],
            },
            {
                "type": "financia",
                "members": ["ator:vox_capital", "con:saude"],
            },
        ],
    }
    occurrences = [("investidores", {"id": "ator:indicator_capital", "name": "Indicator Capital", "type": "Investidor"})]
    neighbors = _edge_neighbors("ator:indicator_capital", occurrences, {"investidores": graph})

    assert neighbors == {"tecnologias digitais e conectividade", "materiais"}
    assert "Vox Capital" not in neighbors
    assert "saúde e ciências da vida" not in neighbors


def test_edge_neighbors_bridges_tema_back_to_investidor():
    """Simétrico: a nota do tema também deve enxergar o investidor de volta."""
    graph = {
        "nodes": [
            {"id": "ator:indicator_capital", "name": "Indicator Capital"},
            {"id": "con:materiais", "name": "materiais"},
        ],
        "edges": [
            {
                "type": "financia",
                "members": ["ator:indicator_capital", "con:materiais"],
            },
        ],
    }
    occurrences = [("investidores", {"id": "con:materiais", "name": "materiais", "type": "Tema"})]
    neighbors = _edge_neighbors("con:materiais", occurrences, {"investidores": graph})

    assert neighbors == {"Indicator Capital"}


def test_edge_neighbors_no_edges_for_entity_returns_empty():
    graph = {
        "nodes": [
            {"id": "ator:vox_capital", "name": "Vox Capital"},
            {"id": "con:saude", "name": "saúde e ciências da vida"},
            {"id": "ator:antler", "name": "Antler"},
        ],
        "edges": [{"type": "financia", "members": ["ator:vox_capital", "con:saude"]}],
    }
    occurrences = [("investidores", {"id": "ator:antler", "name": "Antler", "type": "Investidor"})]
    neighbors = _edge_neighbors("ator:antler", occurrences, {"investidores": graph})

    assert neighbors == set()
