"""Fase A do Hipergrado Sprint 3 — leitura nativa do grafo (get_node_neighborhood).

Testa as funções puras (resolve_graph_nodes / neighborhood) com fixture em
memória — os arquivos hypergraphs/ são gitignored e não existem na CI, então a
lógica não pode depender do disco.
"""
from __future__ import annotations

from core.llm.agent_tools.explore_tools import neighborhood, resolve_graph_nodes

GRAPHS = {
    "finep__589": {
        "nodes": [
            {
                "name": "Chamada FINEP-CDTI",
                "type": "Edital",
                "description": "Inovação tecnológica Brasil-Espanha.",
                "prazo": "30.06.2016",
                "status": "aberto",
                "valor": None,
                "edital_id": "589",
            },
            {"name": "Inteligência Artificial", "type": "Tecnologia", "description": "IA embarcada"},
            {"name": "Inovação Tecnológica", "type": "Tema"},
            {"name": "TRL 6", "type": "Requisito"},
        ],
        "edges": [
            {
                "type": "abrange_tema",
                "members": ["chamada finep-cdti", "inovação tecnológica", "inteligência artificial"],
                "description": "cobertura temática",
            },
            {
                "type": "exige",
                "members": ["chamada finep-cdti", "trl 6"],
                "description": "requisito de elegibilidade",
            },
        ],
    },
    "ict": {
        "nodes": [{"name": "Instituto X", "type": "ICT", "description": "P&D em IA"}],
        "edges": [],
    },
}


def test_resolve_by_edital_id():
    res = resolve_graph_nodes(GRAPHS, "589")
    assert res, "deve resolver o edital pelo id"
    fk, node = res[0]
    assert fk == "finep__589"
    assert node["type"] == "Edital"


def test_resolve_by_name():
    res = resolve_graph_nodes(GRAPHS, "Inteligência Artificial")
    assert res[0][1]["type"] == "Tecnologia"


def test_resolve_unknown_is_empty():
    assert resolve_graph_nodes(GRAPHS, "blockchain quântico") == []


def test_neighborhood_factual_props():
    out = neighborhood(GRAPHS, "589")
    assert "prazo 30.06.2016" in out
    assert "status aberto" in out
    assert "id 589" in out


def test_neighborhood_native_edges_with_types():
    out = neighborhood(GRAPHS, "589")
    # relações nativas e vizinhos rotulados por tipo
    assert "abrange_tema" in out
    assert "exige" in out
    assert "Inteligência Artificial (Tecnologia)" in out
    assert "TRL 6 (Requisito)" in out


def test_neighborhood_unknown_node():
    out = neighborhood(GRAPHS, "blockchain quântico")
    assert "Nenhum nó" in out


def test_depth2_expands_to_second_hop():
    # A partir de "Inteligência Artificial": depth=1 só pega a aresta abrange_tema;
    # depth=2 alcança "trl 6" via o nó-edital compartilhado → inclui a aresta `exige`.
    d1 = neighborhood(GRAPHS, "Inteligência Artificial", depth=1)
    d2 = neighborhood(GRAPHS, "Inteligência Artificial", depth=2)
    assert "exige" not in d1
    assert "exige" in d2
