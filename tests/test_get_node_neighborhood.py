"""Fase A do Hipergrado Sprint 3 — leitura nativa do grafo (get_node_neighborhood).

Testa as funções puras (resolve_graph_nodes / neighborhood) com fixture em
memória — os arquivos hypergraphs/ são gitignored e não existem na CI, então a
lógica não pode depender do disco.
"""
from __future__ import annotations

from core.llm.agent_tools.explore_tools import neighborhood, resolve_graph_nodes

# Fixtures em v2 (Oportunidade/Ator/Conceito, ids, members-by-id). Requisito virou
# PROPRIEDADE (requisitos_texto) — não é mais nó/aresta; o guardrail de travessia
# serializa a propriedade no neighborhood.
GRAPHS = {
    "finep__589": {
        "format_version": 2,
        "nodes": [
            {
                "name": "Chamada FINEP-CDTI",
                "type": "Oportunidade",
                "kind": "edital",
                "aperture": "prazo",
                "id": "op:chamada-finep-cdti",
                "description": "Inovação tecnológica Brasil-Espanha.",
                "prazo": "30.06.2016",
                "status": "aberto",
                "edital_id": "589",
                "requisitos_texto": ["TRL 6"],
            },
            {"name": "Inteligência Artificial", "type": "Conceito", "dim": "tecnologia",
             "id": "con:inteligencia-artificial", "description": "IA embarcada"},
            {"name": "Inovação Tecnológica", "type": "Conceito", "dim": "tema", "id": "con:inovacao-tecnologica"},
            {"name": "Instituto Y", "type": "Ator", "kind": "ict", "id": "ator:instituto-y", "description": "parceiro"},
        ],
        "edges": [
            {
                "type": "abrange_tema",
                "members": ["op:chamada-finep-cdti", "con:inovacao-tecnologica", "con:inteligencia-artificial"],
                "description": "cobertura temática",
            },
            {
                "type": "parceria_com",
                "members": ["op:chamada-finep-cdti", "ator:instituto-y"],
                "description": "parceria obrigatória",
            },
        ],
    },
    "ict": {
        "format_version": 2,
        "nodes": [{"name": "Instituto X", "type": "Ator", "kind": "ict", "id": "ator:instituto-x", "description": "P&D em IA"}],
        "edges": [],
    },
}


def test_resolve_by_edital_id():
    res = resolve_graph_nodes(GRAPHS, "589")
    assert res, "deve resolver o edital pelo id"
    fk, node = res[0]
    assert fk == "finep__589"
    assert node["type"] == "Oportunidade"
    assert node["kind"] == "edital"


def test_resolve_by_name():
    res = resolve_graph_nodes(GRAPHS, "Inteligência Artificial")
    assert res[0][1]["type"] == "Conceito"
    assert res[0][1]["dim"] == "tecnologia"


def test_resolve_unknown_is_empty():
    assert resolve_graph_nodes(GRAPHS, "blockchain quântico") == []


def test_neighborhood_factual_props():
    out = neighborhood(GRAPHS, "589")
    assert "prazo 30.06.2016" in out
    assert "status aberto" in out
    assert "id 589" in out
    # guardrail de travessia: a propriedade requisitos_texto é serializada
    assert "requisitos: TRL 6" in out


def test_neighborhood_native_edges_with_types():
    out = neighborhood(GRAPHS, "589")
    # relações nativas e vizinhos rotulados por tipo (+kind quando houver)
    assert "abrange_tema" in out
    assert "parceria_com" in out
    assert "Inteligência Artificial (Conceito)" in out
    assert "Instituto Y (Ator/ict)" in out


def test_neighborhood_unknown_node():
    out = neighborhood(GRAPHS, "blockchain quântico")
    assert "Nenhum nó" in out


def test_depth2_expands_to_second_hop():
    # A partir de "Inteligência Artificial": depth=1 só pega a aresta abrange_tema;
    # depth=2 alcança "Instituto Y" via o nó-edital compartilhado → aresta parceria_com.
    d1 = neighborhood(GRAPHS, "Inteligência Artificial", depth=1)
    d2 = neighborhood(GRAPHS, "Inteligência Artificial", depth=2)
    assert "parceria_com" not in d1
    assert "parceria_com" in d2


# ── cross-source ──────────────────────────────────────────────────────────────

CROSS_GRAPHS = {
    "finep__589": {
        "format_version": 2,
        "nodes": [
            {"name": "Chamada FINEP-CDTI", "type": "Oportunidade", "kind": "edital",
             "id": "op:chamada-finep-cdti", "prazo": "30.06.2026", "status": "aberto", "edital_id": "589"},
            {"name": "Inteligência Artificial", "type": "Conceito", "dim": "tecnologia",
             "id": "con:inteligencia-artificial", "description": "IA embarcada"},
            {"name": "Inovação Tecnológica", "type": "Conceito", "dim": "tema", "id": "con:inovacao-tecnologica"},
        ],
        "edges": [
            {"type": "abrange_tema", "members": ["op:chamada-finep-cdti", "con:inovacao-tecnologica", "con:inteligencia-artificial"]},
        ],
    },
    "ict": {
        "format_version": 2,
        "nodes": [
            {"name": "Instituto X", "type": "Ator", "kind": "ict", "id": "ator:instituto-x", "description": "P&D em IA"},
            {"name": "Inteligência Artificial", "type": "Conceito", "dim": "tecnologia",
             "id": "con:inteligencia-artificial", "description": "IA geral"},
            {"name": "Robótica", "type": "Conceito", "dim": "tecnologia", "id": "con:robotica"},
        ],
        "edges": [
            {"type": "abrange_tema", "members": ["ator:instituto-x", "con:inteligencia-artificial"]},
            {"type": "abrange_tema", "members": ["ator:instituto-x", "con:robotica"]},
        ],
    },
}


def test_build_entity_index():
    from core.llm.agent_tools.explore_tools import build_entity_index, resolve_entity
    idx = build_entity_index(CROSS_GRAPHS)
    # (type, name) → [(file_key, node)] — o MESMO Conceito aparece nos dois subgrafos
    res = resolve_entity(idx, "Inteligência Artificial", "Conceito")
    fks = {fk for fk, _ in res}
    assert "finep__589" in fks
    assert "ict" in fks


def test_cross_source_edital_to_ict():
    from core.llm.agent_tools.explore_tools import build_entity_index
    idx = build_entity_index(CROSS_GRAPHS)
    # Edital → BFS → IA (Conceito) → cross_source → ICT (Ator)
    out = neighborhood(CROSS_GRAPHS, "Chamada FINEP-CDTI", depth=1,
                       cross_source=True, entity_index=idx)
    assert "[Conceito] em ict" in out
    assert "Instituto X (Ator/ict)" in out


def test_cross_source_default_off():
    # Sem cross_source, query a partir do edital não alcança ICTs
    out = neighborhood(CROSS_GRAPHS, "Chamada FINEP-CDTI", depth=1,
                       cross_source=False)
    assert "[Conceito] em ict" not in out
