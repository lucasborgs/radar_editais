"""Testes do apply mecânico da higiene de Conceitos (core/kg/canonicalize, PR3).

Cobrem só a metade PURA (inventário, compilação do canon, canonicalize_graph,
macro_temas) — as funções propose-* usam LLM/embeddings e ficam fora da CI
(mesmo padrão do hyper_extractor)."""
from __future__ import annotations

from core.kg.canonicalize import (
    CanonStats,
    apply_macro_temas,
    build_canon,
    canonicalize_graph,
    corpus_stats,
    inventory_concepts,
)

# Subgrafo v2 sintético: 1 edital, 1 ICT, 4 Conceitos (1 ruído-geografia,
# 1 ex-Entidade legítimo, 2 duplicatas semânticas ML/aprendizado de máquina).
_G = {
    "format_version": 2,
    "source_hash": "h",
    "proveniencia": {},
    "nodes": [
        {"id": "op:edital-x", "type": "Oportunidade", "kind": "edital", "name": "Edital X"},
        {"id": "ator:senai", "type": "Ator", "kind": "ict", "name": "SENAI"},
        {"id": "con:brasil", "type": "Conceito", "dim": "tema", "name": "Brasil"},
        {"id": "con:bioinsumos", "type": "Conceito", "dim": "tema", "name": "bioinsumos",
         "origem": "entidade_v1"},
        {"id": "con:ml", "type": "Conceito", "dim": "tecnologia", "name": "ML",
         "description": "sigla"},
        {"id": "con:aprendizado-de-maquina", "type": "Conceito", "dim": "tecnologia",
         "name": "aprendizado de máquina", "description": "descrição bem mais longa aqui"},
    ],
    "edges": [
        {"type": "abrange_tema", "members": ["op:edital-x", "con:brasil"]},
        {"type": "abrange_tema", "members": ["op:edital-x", "con:bioinsumos"]},
        {"type": "viabiliza", "members": ["ator:senai", "con:ml", "con:aprendizado-de-maquina"]},
    ],
}

_CANON = build_canon(
    validation_plan={
        "con:brasil": {"veredicto": "descartar", "categoria": "geografia"},
        "con:bioinsumos": {"veredicto": "manter", "promote": True},
        "con:ml": {"veredicto": "manter"},
        "con:aprendizado-de-maquina": {"veredicto": "manter"},
    },
    merge_plan={
        "clusters": [
            {
                "members": ["con:ml", "con:aprendizado-de-maquina"],
                "score_medio": 0.91,
                "grupos": [
                    {"membros": ["con:ml", "con:aprendizado-de-maquina"],
                     "nome_canonico": "aprendizado de máquina", "dim": "tecnologia"},
                ],
            }
        ]
    },
)


def test_build_canon_compiles_plans():
    assert _CANON["discards"] == {"con:brasil": "geografia"}
    assert _CANON["promotions"] == ["con:bioinsumos"]
    # os DOIS membros do grupo apontam para a mesma forma canônica
    assert _CANON["aliases"]["con:ml"]["name"] == "aprendizado de máquina"
    assert _CANON["aliases"]["con:aprendizado-de-maquina"]["name"] == "aprendizado de máquina"


def test_canonicalize_graph_applies_canon():
    st = CanonStats()
    g2 = canonicalize_graph(_G, _CANON, stats=st)
    ids = {n["id"] for n in g2["nodes"]}

    # descarte: geografia some do grafo e da aresta (aresta degenerada cai)
    assert "con:brasil" not in ids
    assert st.discarded == 1 and st.dropped_edges == 1

    # promoção: ex-Entidade perde a marca (entra na afinidade)
    bio = next(n for n in g2["nodes"] if n["id"] == "con:bioinsumos")
    assert "origem" not in bio

    # merge: ML e aprendizado de máquina viram UM nó com id canônico,
    # ficando a descrição mais longa; a aresta re-aponta sem duplicar membro
    assert "con:ml" not in ids
    ml = next(n for n in g2["nodes"] if n["id"] == "con:aprendizado-de-maquina")
    assert ml["name"] == "aprendizado de máquina"
    assert ml["description"] == "descrição bem mais longa aqui"
    assert st.deduped == 1
    via = next(e for e in g2["edges"] if e["type"] == "viabiliza")
    assert via["members"] == ["ator:senai", "con:aprendizado-de-maquina"]

    # não-Conceito passam intactos
    assert {"op:edital-x", "ator:senai"} <= ids


def test_canonicalize_graph_is_idempotent():
    g2 = canonicalize_graph(_G, _CANON)
    g3 = canonicalize_graph(g2, _CANON)
    assert g3 == g2


def test_canonicalize_graph_retypes_ator():
    canon = build_canon(
        {"con:bioinsumos": {"veredicto": "ator", "ator_kind": "corporate"}}, None,
    )
    g2 = canonicalize_graph(_G, canon)
    node = next(n for n in g2["nodes"] if n["name"] == "bioinsumos")
    assert node["type"] == "Ator" and node["kind"] == "corporate"
    assert node["id"] == "ator:bioinsumos" and "dim" not in node and "origem" not in node
    # a aresta segue o novo id
    edge = next(e for e in g2["edges"] if e["type"] == "abrange_tema"
                and "ator:bioinsumos" in e["members"])
    assert edge["members"] == ["op:edital-x", "ator:bioinsumos"]


def test_inventory_aggregates_cross_file():
    g_b = {
        "format_version": 2,
        "nodes": [{"id": "con:ml", "type": "Conceito", "dim": "tema", "name": "ML",
                   "description": "descrição mais longa no arquivo B"}],
        "edges": [],
    }
    inv = inventory_concepts({"a": _G, "b": g_b})
    ml = inv["con:ml"]
    assert ml["fan_in"] == 2
    # dim = moda (empate 1-1 resolve pelo mais comum retornado pelo Counter);
    # description = a mais longa entre as instâncias
    assert ml["description"] == "descrição mais longa no arquivo B"
    assert inv["con:bioinsumos"]["ex_entidade"] is True


def test_corpus_stats_fan_in():
    s = corpus_stats({"a": _G})
    assert s["conceitos_ids_unicos"] == 4
    assert s["fan_in_medio"] == 1.0
    assert s["conceitos_ex_entidade"] == 1


def test_apply_macro_temas():
    graphs = {"a": {"nodes": [dict(n) for n in _G["nodes"]], "edges": _G["edges"]}}
    counts = apply_macro_temas(graphs, {"a::op:edital-x": ["agro - bioeconomia e alimentos"]})
    assert counts == {"a": 1}
    op = next(n for n in graphs["a"]["nodes"] if n["id"] == "op:edital-x")
    assert op["macro_temas"] == ["agro - bioeconomia e alimentos"]


def test_canonicalize_fresh_graph_replays_persisted_canon(monkeypatch):
    # O ingest replayia o canon persistido (kg_store `concept_canon`) de forma
    # determinística; llm_new=False não chama LLM nenhum.
    from core.kg import kg_store
    from core.kg.canonicalize import canonicalize_fresh_graph

    monkeypatch.setattr(kg_store, "load", lambda key, default=None: _CANON)
    g2 = canonicalize_fresh_graph(_G, file_key="finep__novo", llm_new=False)
    ids = {n["id"] for n in g2["nodes"]}
    assert "con:brasil" not in ids                      # descarte replayado
    assert "con:aprendizado-de-maquina" in ids          # merge replayado
    assert "con:ml" not in ids


def test_canonicalize_fresh_graph_without_canon_is_passthrough(monkeypatch):
    from core.kg import kg_store
    from core.kg.canonicalize import canonicalize_fresh_graph

    monkeypatch.setattr(kg_store, "load", lambda key, default=None: default)
    g2 = canonicalize_fresh_graph(_G, file_key="finep__novo", llm_new=False)
    assert g2 == _G


def test_build_canon_records_validated_ids():
    # `validated` = todos os ids julgados + os alvos canônicos dos merges —
    # o ingest usa isso p/ só validar Conceitos inéditos.
    assert set(_CANON["validated"]) == {
        "con:brasil", "con:bioinsumos", "con:ml", "con:aprendizado-de-maquina",
    }


def test_bfs_degree_cap_prioritizes_entity_edges():
    # Super-nó com 30 arestas: o cap limita a expansão e as arestas que tocam
    # Oportunidade/Ator entram primeiro (guardrail de travessia do PR3).
    from core.llm.agent_tools.explore_tools import _bfs_subgraph

    nodes = [{"id": "con:hub", "type": "Conceito", "dim": "tema", "name": "hub"}]
    edges = []
    for i in range(25):  # 25 arestas Conceito-Conceito (baixa prioridade)
        nodes.append({"id": f"con:n{i}", "type": "Conceito", "dim": "tema", "name": f"n{i}"})
        edges.append({"type": "aplica_em", "members": ["con:hub", f"con:n{i}"]})
    for i in range(5):  # 5 arestas com Oportunidade (alta prioridade)
        nodes.append({"id": f"op:e{i}", "type": "Oportunidade", "kind": "edital", "name": f"e{i}"})
        edges.append({"type": "abrange_tema", "members": ["op:e" + str(i), "con:hub"]})
    graph = {"nodes": nodes, "edges": edges}
    idx = {n["id"]: n for n in nodes}

    collected, visited = _bfs_subgraph(graph, idx, "con:hub", depth=1, max_edges=100, degree_cap=10)
    assert len(collected) == 10  # cap por nó, não as 30
    # as 5 arestas de entidade vêm primeiro
    assert all(e["type"] == "abrange_tema" for e in collected[:5])
