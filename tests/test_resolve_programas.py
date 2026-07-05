"""Testes da resolução de programas (core/kg/resolve_programas, KG v2 resíduos PR-C).

Cobrem as funções PURAS (inventário, clusterização por embedding, build_canon,
apply, queue) — a adjudicação LLM (resolve_clusters com LLM) fica fora da CI
(mesmo padrão do propose_merges em test_canonicalize). O clustering usa
embeddings reais (função pura, sem LLM).
"""
from __future__ import annotations

from core.kg.resolve_programas import (
    _is_obvious_trash,
    _UnionFind,
    apply,
    build_canon,
    corpus_programa_stats,
    inventory_programas,
    queue_unresolved,
)

# Hipergrados sintéticos com nós programa
_G1 = {
    "format_version": 2,
    "source_hash": "h1",
    "nodes": [
        {"id": "op:edital-x", "type": "Oportunidade", "kind": "edital",
         "name": "Edital X"},
        {"id": "op:programa-pipe", "type": "Oportunidade", "kind": "programa",
         "name": "Programa PIPE", "description": "descrição do PIPE"},
        {"id": "op:finep-startup", "type": "Oportunidade", "kind": "programa",
         "name": "Finep Startup"},
        {"id": "op:lixo-programa", "type": "Oportunidade", "kind": "programa",
         "name": "programa"},
    ],
    "edges": [
        {"type": "pertence_a", "members": ["op:edital-x", "op:programa-pipe"]},
        {"type": "abrange_tema", "members": ["op:edital-x", "op:finep-startup"]},
        {"type": "abrange_tema", "members": ["op:edital-x", "op:lixo-programa"]},
    ],
}

_G2 = {
    "format_version": 2,
    "source_hash": "h2",
    "nodes": [
        {"id": "op:edital-y", "type": "Oportunidade", "kind": "edital", "name": "Edital Y"},
        {"id": "op:programa-pipe-jt", "type": "Oportunidade", "kind": "programa",
         "name": "PIPE-JT – Fase 1"},
        {"id": "op:globalstars-v2", "type": "Oportunidade", "kind": "programa",
         "name": "Globalstars"},
    ],
    "edges": [],
}


def test_inventory_finds_program_nodes():
    inv = inventory_programas({"finep__x": _G1, "fapesp__y": _G2})
    assert "programa pipe" in inv
    assert "finep startup" in inv
    assert "globalstars" in inv
    # Lixo óbvio entra no inventário
    assert "programa" in inv


def test_inventory_aggregates_names():
    g = {
        "format_version": 2,
        "nodes": [
            {"id": "op:pipe-a", "type": "Oportunidade", "kind": "programa",
             "name": "Programa PIPE"},
            {"id": "op:pipe-b", "type": "Oportunidade", "kind": "programa",
             "name": "Programa PIPE"},
        ],
        "edges": [],
    }
    inv = inventory_programas({"a": _G1, "b": g})
    assert inv["programa pipe"]["fan_in"] == 2
    assert len(inv["programa pipe"]["file_keys"]) == 2
    # 3 node_ids: op:programa-pipe (de _G1) + op:pipe-a + op:pipe-b (de g)
    assert len(inv["programa pipe"]["node_ids"]) == 3


def test_inventory_picks_longest_description():
    g_short = {
        "format_version": 2,
        "nodes": [
            {"id": "op:pipe", "type": "Oportunidade", "kind": "programa",
             "name": "Programa PIPE", "description": "curta"},
        ],
        "edges": [],
    }
    g_long = {
        "format_version": 2,
        "nodes": [
            {"id": "op:pipe2", "type": "Oportunidade", "kind": "programa",
             "name": "Programa PIPE", "description": "descrição mais longa e completa"},
        ],
        "edges": [],
    }
    inv = inventory_programas({"a": g_short, "b": g_long})
    assert inv["programa pipe"]["descricao"] == "descrição mais longa e completa"


def test_corpus_stats():
    s = corpus_programa_stats({"a": _G1})
    assert s["total_programa_nodes"] == 3
    assert s["unique_names"] == 3  # Programa PIPE, Finep Startup, programa


def test_build_canon_compiles_resolutions():
    resolutions = [
        {
            "canon_key": "programa pipe",
            "canon_name": "Programa PIPE",
            "membros": ["programa pipe", "pipe"],
            "status": "curado",
            "registry_id": "programa:pipe-fapesp",
            "registry_name": "Programa PIPE",
        },
        {
            "canon_key": "finep startup",
            "canon_name": "Finep Startup",
            "membros": ["finep startup"],
            "status": "promovido_auto",
            "registry_id": "programa:finep-startup",
            "registry_name": "Finep Startup",
        },
    ]
    canon = build_canon(resolutions)
    assert canon["version"] == 1
    assert "programa pipe" in canon["aliases"]
    assert canon["aliases"]["programa pipe"]["status"] == "curado"
    assert canon["curados"] == ["programa:pipe-fapesp"]
    assert canon["promovidos_auto"] == ["programa:finep-startup"]


def test_apply_resolves_and_removes_trash():
    graphs = {"a": dict(_G1)}
    resolutions = [
        {
            "canon_key": "programa pipe",
            "canon_name": "Programa PIPE",
            "membros": ["programa pipe"],
            "status": "curado",
            "registry_id": "programa:pipe-fapesp",
            "registry_name": "Programa PIPE",
        },
        {
            "canon_key": "finep startup",
            "canon_name": "Finep Startup",
            "membros": ["finep startup"],
            "status": "curado",
            "registry_id": "programa:finep-startup",
            "registry_name": "Finep Startup",
        },
    ]
    canon = build_canon(resolutions)
    graphs_mod, stats = apply(graphs, canon)

    # Nós programa resolvidos foram removidos (viram arestas)
    ids = {n["id"] for n in graphs_mod["a"]["nodes"]}
    assert "op:programa-pipe" not in ids
    # "Finep Startup" tem id=op:finep-startup, igual ao target → mantido (já canônico)
    assert "op:finep-startup" in ids
    assert "op:lixo-programa" not in ids  # lixo descartado

    # Arestas pertence_a: só p/ quem mudou de id
    edges = graphs_mod["a"]["edges"]
    pertence = [e for e in edges if e["type"] == "pertence_a"]
    assert len(pertence) == 1  # só PIPE (Finep Startup já está no id canônico)

    # Edital e arestas restantes intactos
    assert "op:edital-x" in ids
    assert stats["resolvidos"] == 1  # só PIPE (já que Finep Startup manteve o id)
    assert stats["descartados_lixo"] == 1


def test_apply_on_graph_without_program_nodes_is_passthrough():
    g = {
        "format_version": 2,
        "nodes": [{"id": "op:e", "type": "Oportunidade", "kind": "edital",
                   "name": "Edital"}],
        "edges": [],
    }
    graphs = {"a": dict(g)}
    canon = build_canon([])
    graphs_mod, stats = apply(graphs, canon)
    assert graphs_mod["a"]["nodes"] == g["nodes"]


def test_apply_is_idempotent():
    graphs = {"a": dict(_G1)}
    # Remove lixo só
    resolutions = [
        {
            "canon_key": "programa pipe", "canon_name": "Programa PIPE",
            "membros": ["programa pipe"], "status": "curado",
            "registry_id": "programa:pipe-fapesp", "registry_name": "Programa PIPE",
        },
    ]
    canon = build_canon(resolutions)
    mod1, _ = apply(graphs, canon)
    mod2, _ = apply(mod1, canon)
    assert mod1 == mod2


def test_queue_unresolved_filters_promovidos():
    resolutions = [
        {"canon_key": "a", "status": "curado", "registry_id": "prog:a", "membros": ["a"]},
        {"canon_key": "b", "status": "promovido_auto", "registry_id": "prog:b", "membros": ["b"]},
        {"canon_key": "c", "status": "curado", "registry_id": "prog:c", "membros": ["c"]},
        {"canon_key": "d", "status": "promovido_auto", "registry_id": "prog:d", "membros": ["d"]},
    ]
    fila = queue_unresolved(resolutions)
    assert len(fila) == 2
    assert all(r["status"] == "promovido_auto" for r in fila)


def test_union_find():
    uf = _UnionFind()
    uf.union("a", "b")
    uf.union("c", "d")
    uf.union("b", "c")
    assert uf.find("a") == uf.find("d")


def test_is_obvious_trash():
    assert _is_obvious_trash("programa") is True
    assert _is_obvious_trash("Programa") is True
    assert _is_obvious_trash("Programa PIPE") is False
    assert _is_obvious_trash("") is False


# ── Fix 2026-07-05: chave determinística de identidade de programa ──────────────

def test_program_key_collapses_trivial_variants():
    from core.kg.resolve_programas import _program_key

    assert _program_key("ROTA2030") == _program_key("Rota 2030")           # letra-dígito
    assert _program_key("Projeto Rota 2030") == _program_key("rota 2030")  # prefixo genérico
    assert _program_key("Programa Centelha") == _program_key("Centelha")   # prefixo genérico
    assert _program_key("Programa") == ""                                  # só prefixo → vazia
    # programas distintos NÃO colidem
    assert _program_key("Centelha") != _program_key("Tecnova")
    assert _program_key("PIPE") != _program_key("PITE")
