"""Testes do spike KG estrutura-consciente (src/radar/core/kg/spike).

Estratégia: funções PURAS de traverse/serialize testadas sem DB; tools e ingest
cobrem contratos mínimos com mocks. Nenhum teste toca o `public` nem exige
DATABASE_URL.
"""
from __future__ import annotations

import json

import pytest

from radar.core.kg.spike import extractor, match_boost, serialize, tools, traverse
from radar.core.kg.spike.ingest import (
    _insert_partnerships,
    _trl_faixa_id,
    _trl_range_from_constraints,
)

# ─────────────────────────────────────────────────────────────────────────────
# traverse
# ─────────────────────────────────────────────────────────────────────────────

def _edges() -> list[dict]:
    return [
        {"source_id": "a", "target_id": "setor:x", "type": "tem_setor", "weight": 1.0},
        {"source_id": "b", "target_id": "setor:x", "type": "tem_setor", "weight": 1.0},
        {"source_id": "c", "target_id": "agencia:f", "type": "operado_por", "weight": 1.0},
        {"source_id": "b", "target_id": "c", "type": "potencial_parceria", "weight": 0.8},
    ]


def test_bfs_edges_depth1():
    out = traverse.bfs_edges(_edges(), "b", depth=1)
    types = {e["type"] for e in out}
    assert "tem_setor" in types
    assert "potencial_parceria" in types
    assert all(e in _edges() for e in out)


def test_bfs_edges_depth2_includes_operado_por():
    out = traverse.bfs_edges(_edges(), "b", depth=2)
    assert any(e["type"] == "operado_por" for e in out)


def test_bfs_edges_cycle_safe():
    cyclic = _edges() + [
        {"source_id": "x", "target_id": "y", "type": "similar_a", "weight": 1.0},
        {"source_id": "y", "target_id": "x", "type": "similar_a", "weight": 1.0},
    ]
    out = traverse.bfs_edges(cyclic, "x", depth=3)
    assert sum(1 for e in out if e["type"] == "similar_a") == 1  # sem repetir ciclo


def test_bfs_edges_min_weight_excludes_hub():
    edges = _edges() + [
        {"source_id": "d", "target_id": "setor:multissetorial", "type": "tem_setor", "weight": 0.1, "properties": {"hub": True}},
        {"source_id": "setor:multissetorial", "target_id": "ict:hub", "type": "tem_setor", "weight": 0.1},
    ]
    out = traverse.bfs_edges(edges, "d", depth=2, min_weight=0.5)
    assert all(e.get("weight", 1.0) >= 0.5 for e in out)
    assert not any(e["target_id"] == "ict:hub" for e in out)


def test_find_paths_min_weight_skips_hub():
    edges = _edges() + [
        {"source_id": "b", "target_id": "setor:multissetorial", "type": "tem_setor", "weight": 0.1},
        {"source_id": "setor:multissetorial", "target_id": "ict:x", "type": "tem_setor", "weight": 0.1},
    ]
    assert traverse.find_paths(edges, "b", "ict:x", max_depth=3, min_weight=0.5) == []
    assert traverse.find_paths(edges, "b", "ict:x", max_depth=3)  # sem corte, alcança via hub


def test_reachable_within_min_weight():
    edges = _edges() + [
        {"source_id": "b", "target_id": "setor:multissetorial", "type": "tem_setor", "weight": 0.1},
        {"source_id": "setor:multissetorial", "target_id": "ict:x", "type": "tem_setor", "weight": 0.1},
    ]
    assert "ict:x" not in traverse.reachable_within(edges, "b", max_depth=2, min_weight=0.5)
    assert "ict:x" in traverse.reachable_within(edges, "b", max_depth=2)


def test_find_paths():
    paths = traverse.find_paths(_edges(), "b", "agencia:f", max_depth=4)
    assert paths, "deve existir caminho b→c→agencia:f"
    assert paths[0] == [("b", "potencial_parceria", "c"), ("c", "operado_por", "agencia:f")]


def test_find_paths_no_route():
    assert traverse.find_paths(_edges(), "a", "agencia:f", max_depth=2) == []


def test_find_paths_contiguous_reverse_edge():
    edges = [
        {"source_id": "empresa:efemera", "target_id": "setor:agro", "type": "atua_em", "weight": 1.0},
        {"source_id": "edital:finep:783", "target_id": "setor:agro", "type": "tem_setor", "weight": 1.0},
    ]
    paths = traverse.find_paths(edges, "empresa:efemera", "edital:finep:783", max_depth=4)
    assert paths[0] == [
        ("empresa:efemera", "atua_em", "setor:agro"),
        ("setor:agro", "tem_setor", "edital:finep:783"),
    ], "passos devem seguir a direção PERCORRIDA (contíguos), não a direção original da aresta"


def test_reachable_within():
    assert traverse.reachable_within(_edges(), "b", max_depth=2) == {"b", "setor:x", "c", "agencia:f", "a"}


def test_filter_predicate():
    assert [e["type"] for e in traverse.filter_predicate(_edges(), "tem_setor")] == ["tem_setor", "tem_setor"]


# ─────────────────────────────────────────────────────────────────────────────
# seleção de caminhos internos (Bloco 3)
# ─────────────────────────────────────────────────────────────────────────────

def _rel(s: str, t: str, ty: str) -> dict:
    return {"source_id": s, "target_id": t, "type": ty, "weight": 1.0}


def test_select_internal_paths_shortest_before_longer():
    edges = [
        _rel("edital:x", "ict:a", "potencial_parceria"),
        _rel("edital:x", "setor:agro", "tem_setor"),
        _rel("setor:agro", "ict:c", "tem_setor"),
    ]
    paths = tools._select_internal_paths(edges, "edital:x", ["ict:a", "ict:c"], max_depth=3, min_weight=0.0)
    lens = [len(p) for p in paths]
    assert lens == sorted(lens)                      # mais curtos primeiro
    assert [p[-1][2] for p in paths] == ["ict:a", "ict:c"]  # 1 salto (ict:a) antes de 2 (ict:c)


def test_select_internal_paths_dedupes_duplicate_edges():
    e = _rel("edital:x", "ict:a", "potencial_parceria")
    paths = tools._select_internal_paths([e, dict(e)], "edital:x", ["ict:a"], max_depth=2, min_weight=0.0)
    assert len(paths) == 1


def test_select_internal_paths_tie_stable_by_dest_id():
    edges = [
        _rel("edital:x", "ict:z", "potencial_parceria"),
        _rel("edital:x", "ict:a", "potencial_parceria"),
        _rel("edital:x", "ict:m", "potencial_parceria"),
    ]
    paths = tools._select_internal_paths(edges, "edital:x", ["ict:z", "ict:a", "ict:m"], max_depth=2, min_weight=0.0)
    assert [p[0][2] for p in paths] == ["ict:a", "ict:m", "ict:z"]  # empate → ID do destino


def test_select_internal_paths_keeps_derived_edges():
    """Relações derivadas não são descartadas nem promovidas a fato."""
    edges = [
        _rel("edital:x", "ict:a", "similar_a"),
        _rel("edital:x", "setor:agro", "tem_setor"),
        _rel("setor:agro", "ict:c", "tem_setor"),
    ]
    paths = tools._select_internal_paths(edges, "edital:x", ["ict:a", "ict:c"], max_depth=3, min_weight=0.0)
    types = {p[0][1] for p in paths}
    assert "similar_a" in types
    assert len(paths) == 2


# ─────────────────────────────────────────────────────────────────────────────
# serialize
# ─────────────────────────────────────────────────────────────────────────────

_NODES = [
    {"id": "edital:x", "kind": "edital", "name": "Edital X", "native_id": "finep:1"},
    {"id": "ict:c", "kind": "ict", "name": "ICT C", "native_id": "embrapii:c"},
]
_QUALITY = [
    {"id": "setor:agro", "family": "setor", "value": "Agro"},
    {"id": "setor:ia", "family": "setor", "value": "IA"},
]
_SERIAL_EDGES = [
    {"source_id": "edital:x", "target_id": "setor:agro", "type": "tem_setor", "weight": 1.0,
     "source": "deterministica_derivada", "properties": {}},
    {"source_id": "edital:x", "target_id": "ict:c", "type": "potencial_parceria", "weight": 0.82,
     "source": "deterministica_derivada", "properties": {"n_shared": 2}},
]


def test_serialize_subgraph_preserves_topology():
    sub = serialize.serialize_subgraph(
        "edital:x", _SERIAL_EDGES, _NODES, _QUALITY, depth=1,
        communities={"com_0": ["edital:x", "setor:agro"]},
    )
    assert sub["center"]["id"] == "edital:x"
    # Centro com estrutura completa (Bloco 2)
    assert sub["center"]["kind"] == "edital"
    assert sub["center"]["native_id"] == "finep:1"
    assert sub["center"]["name"] == "Edital X"
    # Aresta preserva extremidades, peso, classificação de origem e properties
    assert {
        "source_id": "edital:x", "target_id": "ict:c", "type": "potencial_parceria",
        "weight": 0.82, "source": "deterministica_derivada", "properties": {"n_shared": 2},
    } in sub["edges"]
    # Nós: entidade com kind/native_id/name; qualidade com family/value
    assert {"id": "ict:c", "kind": "ict", "native_id": "embrapii:c", "name": "ICT C"} in sub["nodes"]
    assert {"id": "setor:agro", "family": "setor", "value": "Agro"} in sub["nodes"]
    assert "com_0" in sub["communities"]
    kinds = {n.get("kind") or n.get("family") for n in sub["nodes"]}
    assert kinds == {"edital", "ict", "setor"}


def test_serialize_subgraph_max_nodes_keeps_consistency():
    """Sob `max_nodes`, nenhuma aresta aponta para nó ausente de `nodes`."""
    edges = [
        {"source_id": "edital:x", "target_id": f"ict:{i}", "type": "potencial_parceria",
         "weight": 0.8, "source": "deterministica_derivada", "properties": {}}
        for i in range(10)
    ]
    nodes = [
        {"id": "edital:x", "kind": "edital", "name": "Edital X", "native_id": "x"},
        *[
            {"id": f"ict:{i}", "kind": "ict", "name": f"ICT {i}", "native_id": f"ict:{i}"}
            for i in range(10)
        ],
    ]
    sub = serialize.serialize_subgraph("edital:x", edges, nodes, [], depth=1, max_nodes=4)
    present = {n["id"] for n in sub["nodes"]}
    assert len(present) <= 4
    assert "edital:x" in present  # seed sempre presente
    assert sub["edges"], "arestas internas ao recorte sobrevivem"
    for e in sub["edges"]:
        assert e["source_id"] in present and e["target_id"] in present


def test_serialize_subgraph_empty():
    sub = serialize.serialize_subgraph("edital:x", [], _NODES, _QUALITY, depth=1)
    assert sub["center"]["id"] == "edital:x"
    assert sub["edges"] == []


def test_enrich_paths_resolves_nodes_edges_source():
    edges = [
        {"source_id": "edital:finep:589", "target_id": "agencia:finep", "type": "operado_por",
         "weight": 1.0, "source": "factual_catalogada", "properties": {"provenance": True}},
        {"source_id": "edital:finep:589", "target_id": "setor:agro", "type": "tem_setor", "weight": 1.0},
    ]
    nodes = [
        {"id": "edital:finep:589", "kind": "edital", "native_id": "finep:589", "name": "Edital 589"},
        {"id": "agencia:finep", "kind": "agencia", "native_id": "agencia:finep", "name": "Finep"},
    ]
    quality = [{"id": "setor:agro", "family": "setor", "value": "Agro"}]
    paths = [
        [("edital:finep:589", "operado_por", "agencia:finep")],
        [("edital:finep:589", "tem_setor", "setor:agro")],
    ]
    rich = serialize.enrich_paths(paths, edges, nodes, quality)
    step = rich[0][0]
    assert step["source_node"] == {"id": "edital:finep:589", "kind": "edital", "name": "Edital 589"}
    assert step["target_node"] == {"id": "agencia:finep", "kind": "agencia", "name": "Finep"}
    assert step["predicate"] == "operado_por"
    assert step["weight"] == 1.0
    assert step["source"] == "factual_catalogada"          # classificação Bloco 1
    assert step["properties"] == {"provenance": True}
    qstep = rich[1][0]
    assert qstep["target_node"] == {"id": "setor:agro", "family": "setor", "value": "Agro"}
    assert qstep["source"] == ""                            # aresta sem `source` não inventa


def test_community_helpers_resolve_members_and_qualities():
    by_id = serialize.build_node_map(
        [
            {"id": "ict:a", "kind": "ict", "native_id": "embrapii:a", "name": "ICT A"},
            {"id": "edital:x", "kind": "edital", "native_id": "finep:x", "name": "Edital X"},
        ],
        [
            {"id": "tecnologia:ia", "family": "tecnologia", "value": "IA"},
            {"id": "setor:agro", "family": "setor", "value": "Agro"},
        ],
    )
    members = serialize.community_members(["ict:a", "tecnologia:ia", "nope"], by_id)
    assert {"id": "ict:a", "native_id": "embrapii:a", "kind": "ict", "name": "ICT A"} in members
    assert {"id": "tecnologia:ia", "family": "tecnologia", "value": "IA"} in members
    assert {"id": "nope"} in members  # nunca inventa campos inexistentes

    shared = serialize.shared_quality_payloads({"tecnologia:ia": 3, "setor:agro": 2, "nope": 1}, by_id)
    assert {"id": "tecnologia:ia", "family": "tecnologia", "value": "IA", "member_count": 3} in shared
    missing = [s for s in shared if s["id"] == "nope"][0]
    assert missing == {"id": "nope", "family": "", "value": "", "member_count": 1}


def test_paths_to_prose_and_dump():
    paths = [[("b", "potencial_parceria", "c"), ("c", "operado_por", "agencia:f")]]
    prose = serialize.paths_to_prose(paths)
    assert "b -[potencial_parceria]-> c" in prose
    dumped = serialize.dump({"center": {"id": "x"}, "edges": []})
    assert isinstance(dumped, str)


# ─────────────────────────────────────────────────────────────────────────────
# ingest (funções puras — sem DB)
# ─────────────────────────────────────────────────────────────────────────────

def test_trl_range_from_constraints():
    assert _trl_range_from_constraints(None) == (None, None)
    assert _trl_range_from_constraints([{"op": "gte", "tipo": "trl", "valor": 3}]) == (3, None)
    assert _trl_range_from_constraints([
        {"op": "gte", "tipo": "trl", "valor": 3},
        {"op": "lte", "tipo": "trl", "valor": 6},
    ]) == (3, 6)
    assert _trl_range_from_constraints([
        {"op": "in", "tipo": "porte", "valor": ["me"]},
    ]) == (None, None)  # ignora constraints não-TRL


def test_trl_faixa_id_overlap():
    assert _trl_faixa_id(1, 9) == ["faixa_trl:pesquisa", "faixa_trl:prototipo", "faixa_trl:industrial"]
    assert _trl_faixa_id(4, 6) == ["faixa_trl:prototipo"]
    assert _trl_faixa_id(None, None) == []


def test_insert_partnerships_shared_technology():
    entities = [
        {"id": 1, "kind": "edital", "native_id": "finep:1", "tecnologias_tags": ["IA", "IoT"]},
        {"id": 2, "kind": "edital", "native_id": "finep:2", "tecnologias_tags": ["biotecnologia"]},
        {"id": 3, "kind": "ict", "native_id": "x", "tecnologias_tags": ["IA", "robótica"]},
        {"id": 4, "kind": "ict", "native_id": "y", "tecnologias_tags": ["biotecnologia", "IA"]},
    ]
    executed: list[tuple] = []

    class _Cur:
        def execute(self, sql, params):
            executed.append(params)

    n = _insert_partnerships(_Cur(), entities)
    # edital finep:1 × ict x (compartilham IA) e × ict y (IA); finep:2 × ict y (biotecnologia)
    assert n == 3
    sources = {(e[0], e[1]) for e in executed}
    assert ("edital:finep:1", "ict:x") in sources
    assert ("edital:finep:1", "ict:y") in sources
    assert ("edital:finep:2", "ict:y") in sources
    assert all(e[2] == "potencial_parceria" for e in executed)
    assert all(0.0 < e[3] <= 1.0 for e in executed)


def test_insert_partnerships_no_overlap_no_edges():
    entities = [
        {"id": 1, "kind": "edital", "native_id": "finep:1", "tecnologias_tags": ["IA"]},
        {"id": 2, "kind": "ict", "native_id": "x", "tecnologias_tags": ["biotecnologia"]},
    ]
    executed: list[tuple] = []

    class _Cur:
        def execute(self, sql, params):
            executed.append(params)

    assert _insert_partnerships(_Cur(), entities) == 0
    assert executed == []


# ─────────────────────────────────────────────────────────────────────────────
# tools (flag KG_SPIKE_ENABLED)
# ─────────────────────────────────────────────────────────────────────────────

def test_tools_disabled_by_default(monkeypatch):
    monkeypatch.delenv("KG_SPIKE_ENABLED", raising=False)
    assert "desabilitado" in tools.graph_explore("edital:x")


def test_build_spike_tools_flag_off_returns_empty(monkeypatch):
    monkeypatch.delenv("KG_SPIKE_ENABLED", raising=False)
    assert tools.build_spike_tools() == []


def test_build_spike_tools_flag_on_returns_wrapped_tools(monkeypatch):
    monkeypatch.setenv("KG_SPIKE_ENABLED", "1")
    wrapped = tools.build_spike_tools()
    assert len(wrapped) == 3
    assert {t.name for t in wrapped} == {"graph_explore", "graph_reason", "graph_community"}
    assert all(hasattr(t, "invoke") for t in wrapped)


def test_graph_community_disabled_by_default(monkeypatch):
    monkeypatch.delenv("KG_SPIKE_ENABLED", raising=False)
    assert "desabilitado" in tools.graph_community("com_0")


def test_resolve_community_aliases():
    communities = {"com_0": ["a"], "com_11": ["b"]}
    assert tools._resolve_community("com_11", communities) == "com_11"
    assert tools._resolve_community("comunidade:11", communities) == "com_11"
    assert tools._resolve_community("11", communities) == "com_11"
    assert tools._resolve_community("nope", communities) is None
    assert tools._resolve_community("", communities) is None


def test_profile_edges_only_links_existing_quality_nodes():
    quality = [{"id": "setor:agro", "family": "setor", "value": "Agro"}]
    edges, start = tools._profile_edges(
        {"setores": ["Agro", "ia"], "tema": ["AGRO"], "tecnologias": ["IA"]}, quality,
    )
    assert start == "empresa:efemera"
    targets = [e["target_id"] for e in edges]
    assert targets == ["setor:agro"]  # "Agro" e tema "AGRO" casam o mesmo nó → 1 aresta (dedup)
    assert "tecnologia:ia" not in targets  # nó de tecnologia inexistente → sem aresta
    assert all(e["source_id"] == "empresa:efemera" for e in edges)
    assert all(e["type"] == "atua_em" for e in edges)


def test_profile_edges_no_matching_node_no_edge():
    assert tools._profile_edges({"setores": ["agro"]}, []) == ([], "empresa:efemera")


# ─────────────────────────────────────────────────────────────────────────────
# extractor — Fase 2 (resolução object_ref, gate de evidência, roteamento)
# ─────────────────────────────────────────────────────────────────────────────

def test_resolve_object_ref_by_native_and_name():
    by_native = {"finep:589": "edital:finep:589", "embrapii:ceia-ufg": "ict:embrapii:ceia-ufg"}
    by_name = {"edital:finep:589": "edital:finep:589", "ict ceia-ufg": "ict:embrapii:ceia-ufg", "agro": "setor:agro"}
    assert extractor.resolve_object_ref("finep:589", by_native, by_name) == "edital:finep:589"
    assert extractor.resolve_object_ref("ICT CEIA-UFG", by_native, by_name) == "ict:embrapii:ceia-ufg"
    assert extractor.resolve_object_ref("Agro", by_native, by_name) == "setor:agro"
    assert extractor.resolve_object_ref("edital:finep:589", by_native, by_name) == "edital:finep:589"


def test_resolve_object_ref_none_for_literal_or_unknown():
    by_native = {"finep:589": "edital:finep:589"}
    by_name = {"agro": "setor:agro"}
    assert extractor.resolve_object_ref(None, by_native, by_name) is None
    assert extractor.resolve_object_ref("", by_native, by_name) is None
    assert extractor.resolve_object_ref("R$ 1.000.000", by_native, by_name) is None
    assert extractor.resolve_object_ref("  ", by_native, by_name) is None


def test_promote_requires_gate_evidences(monkeypatch):
    """Predicado com < gate subjects fica core=false; com >= gate vira true."""
    counts = {
        "opera": 1,                       # 1 subject → não promove
        "potencial_parceria": 3,          # 3 subjects → promove
    }
    edge_counts = [("potencial_parceria", 1), ("investe_em", 1)]  # +1 subject distinto cada

    class _Cur:
        def __init__(self):
            self.queries: list[tuple] = []
            self._stage = 0

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, sql, params=None):
            self.queries.append((sql, params))
            if "from kg_spike.extraction_candidates" in sql and "group by" in sql:
                self._stage = 1
            elif "source='fase2_llm'" in sql and "group by" in sql:
                self._stage = 2

        def fetchall(self):
            if self._stage == 1:
                return list(counts.items())
            if self._stage == 2:
                return edge_counts
            return []

    class _Conn:
        def cursor(self):
            return _Cur()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(extractor, "connect", lambda: _Conn())
    out = extractor.promote(gate=3)
    assert "potencial_parceria" in out["promoted"]  # 3 candidatos + 1 aresta = 4 ≥ 3
    assert "opera" not in out["promoted"]           # 1 < 3
    assert "investe_em" not in out["promoted"]      # só 1 aresta, sem candidatos
    assert out["n_promoted"] == 1


def test_promote_empty_counts(monkeypatch):
    class _Cur:
        _stage = 1

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, sql, params=None):
            if "source='fase2_llm'" in sql and "group by" in sql:
                self._stage = 2

        def fetchall(self):
            return []

    class _Conn:
        def cursor(self):
            return _Cur()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(extractor, "connect", lambda: _Conn())
    assert extractor.promote(gate=3) == {"promoted": [], "n_promoted": 0, "gate": 3}


def test_insert_triples_routes_resolved_to_edges_and_literal_to_candidates(monkeypatch):
    """object_ref resolvido → edges (fase2_llm); literal/não-resolvido →
    extraction_candidates. Auto-edge (subject==target) também vai p/ candidatos."""
    executed: list[tuple] = []

    class _Cur:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, sql, params):
            executed.append((sql, params))

    class _Conn:
        def cursor(self):
            return _Cur()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(extractor, "connect", lambda: _Conn())
    triples = [
        {"predicate": "potencial_parceria", "object_ref": "ICT CEIA-UFG", "evidence": "E1"},
        {"predicate": "exige_parceria_com", "object_ref": None, "object_literal": "ICT", "evidence": "E2"},
        {"predicate": "auto_ref", "object_ref": "edital:finep:589", "evidence": "E3"},
    ]
    e, c = extractor._insert_triples(
        triples, subject_id="edital:finep:589",
        by_native={"finep:589": "edital:finep:589"},
        by_name={"ict ceia-ufg": "ict:embrapii:ceia-ufg"},
        source_hash="h", model="m",
    )
    assert (e, c) == (1, 2)
    edges_sql = [s for s, _ in executed if "insert into kg_spike.edges" in s]
    assert len(edges_sql) == 1
    cand_sql = [s for s, _ in executed if "insert into kg_spike.extraction_candidates" in s]
    assert len(cand_sql) == 2


def test_extract_llm_filters_guarded_predicates(monkeypatch):
    """Guard de qualidade: categoria aristotélica e placeholder `_X` nunca
    entram no grafo (arestas/candidatos)."""
    content = json.dumps({"triples": [
        {"predicate": "financia", "object_ref": "Finep", "evidence": "E1"},
        {"predicate": "quantidade", "object_literal": "50", "evidence": "E2"},
        {"predicate": "exige_setor_X", "object_literal": "agro", "evidence": "E3"},
        {"predicate": "posição", "object_literal": "FIP", "evidence": "E4"},
    ]})
    resp = type("r", (), {"choices": [type("c", (), {"message": type("m", (), {"content": content})()})()]})()

    class _Client:
        def __init__(self):
            self.chat = type("ch", (), {"completions": type("cm", (), {"create": staticmethod(lambda **kw: resp)})()})()
    out = extractor._extract_llm(_Client(), "m", "edital:x", "texto")
    assert [t["predicate"] for t in out] == ["financia"]
    assert extractor._is_guarded_predicate("quantidade")
    assert extractor._is_guarded_predicate("exige_setor_X")
    assert extractor._is_guarded_predicate("posição")
    assert not extractor._is_guarded_predicate("financia")


def test_extract_llm_fail_open(monkeypatch):
    class _Client:
        def chat(self):
            return self

        def completions(self):
            return self

        def create(self, **kw):
            raise RuntimeError("boom")

    assert extractor._extract_llm(_Client(), "m", "edital:x", "texto") == []


def test_manual_extract_cli_flag():
    # Garantia de contrato: --promote-only existe e não extrai.
    import subprocess
    import sys

    out = subprocess.run(
        [sys.executable, "-m", "radar.core.kg.spike.extractor", "--help"],
        capture_output=True, text=True,
    )
    assert out.returncode == 0
    assert "--promote-only" in out.stdout


# ─────────────────────────────────────────────────────────────────────────────
# match_boost (fator estrutural — célula A/B do match)
# ─────────────────────────────────────────────────────────────────────────────

def test_structural_factors_boosts_neighbors_of_seeds(monkeypatch):
    class _Graph:
        probe = ("fake",)
        neighbors = {
            "edital:a": [("edital:b", 0.8)],
            "edital:b": [("edital:a", 0.8), ("edital:c", 0.6)],
        }

    monkeypatch.setattr(match_boost, "_get_similar", lambda: _Graph())
    factors = match_boost.structural_factors({"edital:a"}, alpha=0.05)
    assert factors["edital:b"] == pytest.approx(1 + 0.05 * 0.8, rel=1e-6)
    assert "edital:c" not in factors  # c é vizinho de b, não de um seed


def test_structural_factors_excludes_seeds_themselves(monkeypatch):
    """Seeds (matches já fortes) não recebem boost — só seus vizinhos."""
    class _Graph:
        probe = ("fake",)
        neighbors = {
            "edital:a": [("edital:b", 0.9)],
            "edital:b": [("edital:a", 0.9)],
        }

    monkeypatch.setattr(match_boost, "_get_similar", lambda: _Graph())
    factors = match_boost.structural_factors({"edital:a", "edital:b"}, alpha=0.05)
    assert factors == {}  # ambos são seeds → ninguém é liftado


def test_structural_factors_takes_max_when_multiple_seeds(monkeypatch):
    class _Graph:
        probe = ("fake",)
        neighbors = {
            "edital:a": [("edital:x", 0.9)],
            "edital:b": [("edital:x", 0.5)],
        }

    monkeypatch.setattr(match_boost, "_get_similar", lambda: _Graph())
    factors = match_boost.structural_factors({"edital:a", "edital:b"}, alpha=0.05)
    assert factors["edital:x"] == pytest.approx(1 + 0.05 * 0.9, rel=1e-6)


def test_structural_factors_fail_open(monkeypatch):
    monkeypatch.setattr(match_boost, "_get_similar", lambda: None)
    assert match_boost.structural_factors({"edital:a"}) == {}


def test_structural_factors_no_seeds(monkeypatch):
    class _Graph:
        probe = ("fake",)
        neighbors = {}

    monkeypatch.setattr(match_boost, "_get_similar", lambda: _Graph())
    assert match_boost.structural_factors(set()) == {}
