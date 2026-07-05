"""Testes da granularidade atômica de Conceitos (core/kg/split_concepts, PR-D).

Cobrem as funções PURAS (inventário, apply, word_count). A decomposição LLM
(propose_splits) fica fora da CI — mesmo padrão do propose_merges no PR-B.
"""
from __future__ import annotations

import copy

from core.kg.split_concepts import (
    _word_count,
    apply_splits,
    inventory_long_concepts,
)

_G = {
    "format_version": 2,
    "source_hash": "h",
    "nodes": [
        {"id": "op:edital-x", "type": "Oportunidade", "kind": "edital", "name": "Edital X"},
        {"id": "con:ia", "type": "Conceito", "dim": "tecnologia", "name": "IA"},
        {"id": "con:agricultura-baixo-carbono", "type": "Conceito", "dim": "tema",
         "name": "agricultura de baixo carbono e uso eficiente de recursos",
         "description": "práticas sustentáveis"},
        {"id": "con:saude-digital", "type": "Conceito", "dim": "aplicacao",
         "name": "saúde digital"},
    ],
    "edges": [
        {"type": "abrange_tema", "members": ["op:edital-x", "con:agricultura-baixo-carbono"]},
        {"type": "abrange_tema", "members": ["op:edital-x", "con:saude-digital"]},
        {"type": "viabiliza", "members": ["con:ia", "con:agricultura-baixo-carbono"]},
    ],
}

_G2 = {
    "format_version": 2,
    "source_hash": "h2",
    "nodes": [
        {"id": "con:agricultura-baixo-carbono", "type": "Conceito", "dim": "tema",
         "name": "agricultura de baixo carbono e uso eficiente de recursos"},
        {"id": "con:iot", "type": "Conceito", "dim": "tecnologia", "name": "Internet das Coisas"},
    ],
    "edges": [
        {"type": "viabiliza", "members": ["con:agricultura-baixo-carbono", "con:iot"]},
    ],
}


def test_word_count():
    assert _word_count("IA") == 1
    assert _word_count("saúde digital") == 2
    assert _word_count("agricultura de baixo carbono e uso eficiente de recursos") == 9


def test_inventory_finds_long_concepts():
    inv = inventory_long_concepts({"a": _G, "b": _G2}, max_words=5)
    assert "con:agricultura-baixo-carbono" in inv
    assert "con:ia" not in inv
    assert "con:saude-digital" not in inv
    assert "con:iot" not in inv


def test_inventory_aggregates_cross_file():
    inv = inventory_long_concepts({"a": _G, "b": _G2}, max_words=5)
    e = inv["con:agricultura-baixo-carbono"]
    assert e["fan_in"] == 2
    assert len(e["files"]) == 2
    assert e["word_count"] == 9


def test_apply_splits_creates_new_nodes():
    plan = {
        "con:agricultura-baixo-carbono": [
            {"nome": "agricultura de baixo carbono", "dim": "tema"},
            {"nome": "uso eficiente de recursos", "dim": "tema"},
        ],
    }
    graphs = {"a": copy.deepcopy(_G)}
    mod, st = apply_splits(graphs, plan)
    ids = {n["id"] for n in mod["a"]["nodes"]}
    assert "con:agricultura-baixo-carbono" not in ids  # original removido
    assert "con:agricultura-de-baixo-carbono" in ids    # split 1
    assert "con:uso-eficiente-de-recursos" in ids        # split 2
    assert "con:ia" in ids                                # intactos
    assert "con:saude-digital" in ids
    assert st["conceitos_split"] == 1
    assert st["conceitos_criados"] == 2


def test_apply_splits_reataches_edges():
    plan = {
        "con:agricultura-baixo-carbono": [
            {"nome": "agricultura de baixo carbono", "dim": "tema"},
            {"nome": "uso eficiente de recursos", "dim": "tema"},
        ],
    }
    graphs = {"a": copy.deepcopy(_G)}
    mod, _ = apply_splits(graphs, plan)
    edges = mod["a"]["edges"]
    novo_id1 = "con:agricultura-de-baixo-carbono"
    novo_id2 = "con:uso-eficiente-de-recursos"
    # Aresta abrange_tema agora tem 3 membros (op + ambos splits)
    abrange = [e for e in edges if e["type"] == "abrange_tema"]
    assert any(
        set(e["members"]) == {"op:edital-x", novo_id1, novo_id2}
        for e in abrange
    )
    # Aresta viabiliza da IA também reatada
    via = [e for e in edges if e["type"] == "viabiliza"]
    assert any(
        set(e["members"]) == {"con:ia", novo_id1, novo_id2}
        for e in via
    )


def test_apply_splits_cross_file():
    plan = {
        "con:agricultura-baixo-carbono": [
            {"nome": "agricultura de baixo carbono", "dim": "tema"},
            {"nome": "uso eficiente de recursos", "dim": "tema"},
        ],
    }
    graphs = {"a": copy.deepcopy(_G), "b": copy.deepcopy(_G2)}
    mod, _ = apply_splits(graphs, plan)
    for fk in ("a", "b"):
        ids = {n["id"] for n in mod[fk]["nodes"]}
        assert "con:agricultura-baixo-carbono" not in ids
        assert "con:agricultura-de-baixo-carbono" in ids


def test_apply_splits_same_id_across_files():
    """Split cria o mesmo id novo em cada arquivo."""
    plan = {
        "con:agricultura-baixo-carbono": [
            {"nome": "agricultura de baixo carbono", "dim": "tema"},
        ],
    }
    graphs = {"a": copy.deepcopy(_G), "b": copy.deepcopy(_G2)}
    mod, st = apply_splits(graphs, plan)
    assert st["conceitos_criados"] == 2  # um por arquivo
    for fk in ("a", "b"):
        ids = {n["id"] for n in mod[fk]["nodes"]}
        assert "con:agricultura-de-baixo-carbono" in ids


def test_apply_splits_preserves_unaffected_nodes():
    plan = {
        "con:agricultura-baixo-carbono": [
            {"nome": "agricultura de baixo carbono", "dim": "tema"},
        ],
    }
    graphs = {"a": copy.deepcopy(_G)}
    mod, _ = apply_splits(graphs, plan)
    assert any(n["id"] == "con:ia" for n in mod["a"]["nodes"])
    assert any(n["id"] == "con:saude-digital" for n in mod["a"]["nodes"])
    assert any(n["id"] == "op:edital-x" for n in mod["a"]["nodes"])


def test_apply_splits_no_plan_is_passthrough():
    graphs = {"a": copy.deepcopy(_G)}
    mod, st = apply_splits(graphs, {})
    assert mod["a"]["nodes"] == _G["nodes"]
    assert st["conceitos_split"] == 0


def test_apply_splits_preserves_edge_after_rename():
    """Split de conceito com aresta externa → aresta mantida com novo id."""
    g = {
        "format_version": 2,
        "nodes": [
            {"id": "con:unico", "type": "Conceito", "dim": "tema",
             "name": "conceito único e exclusivo e especializado"},
            {"id": "op:e", "type": "Oportunidade", "kind": "edital", "name": "E"},
        ],
        "edges": [
            {"type": "abrange_tema", "members": ["op:e", "con:unico"]},
        ],
    }
    plan = {
        "con:unico": [{"nome": "conceito único", "dim": "tema"}],
    }
    graphs = {"a": g}
    mod, st = apply_splits(graphs, plan)
    # Aresta mantida com novo id (op:e + con:conceito-unico)
    assert len(mod["a"]["edges"]) == 1
    e = mod["a"]["edges"][0]
    assert set(e["members"]) == {"op:e", "con:conceito-unico"}
    # Nenhuma aresta removida (todas re-apeadas com sucesso)
    assert st["arestas_removidas"] == 0
