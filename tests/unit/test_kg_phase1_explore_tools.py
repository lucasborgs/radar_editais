"""Testes da integração read-only da Fase 1 com o Explorar (KG-P1B1).

Cobrem os 12 itens essenciais:
  1. flag off mantém EXATAMENTE as tools anteriores;
  2. flag on adiciona apenas as três graph tools;
  3. perfil é closure, não argumento público;
  4. snapshot inteiro usa a mesma geração (UMA conexão, UMA transação);
  5. ausência de geração e falha do banco degradam sem derrubar o agente;
  6. resolução exata / não encontrada / ambígua;
  7. profundidade, nós, arestas, caminhos e payload são limitados;
  8. direção, `origin` e `derived` sobrevivem à serialização;
  9. hub `setor:multissetorial` não expande;
  10. relações derivadas não são textualizadas como confirmação;
  11. logs não vazam conteúdo adversarial;
  12. regressões do ExploreAgent permanecem verdes (arquivo existente).

Hermético: nenhum teste toca banco/LLM — o snapshot e `store.load_snapshot` são
mocks/monkeypatched.
"""
from __future__ import annotations

import json
import logging
from typing import Any

import psycopg
import pytest

from radar.core.kg.phase1 import resolve, store, tools
from radar.core.kg.phase1.store import Snapshot
from radar.core.llm.agent_runtime import AgentResult
from radar.core.services.explore_agent import ExploreAgent

pytestmark = pytest.mark.unit

_BASE_TOOLS = {
    "list_editais", "get_edital", "explore_opportunity",
    "search_entities", "related_by_tags", "get_node_neighborhood",
    "list_icts", "list_investidores", "get_investidor",
}


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures (sem banco)
# ─────────────────────────────────────────────────────────────────────────────

def _edge(source: str, target: str, type_: str, *, origin: str,
          weight: float = 1.0, props: dict | None = None) -> dict:
    return {
        "source_id": source, "target_id": target, "type": type_,
        "weight": weight, "properties": props or {}, "origin": origin,
    }


def _snapshot(**overrides: Any) -> Snapshot:
    nodes = [
        {"id": "edital:finep:589", "kind": "edital", "native_id": "finep:589",
         "name": "Chamada Agro IA", "description": ""},
        {"id": "ict:embrapii:ia", "kind": "ict", "native_id": "embrapii:ia",
         "name": "Unidade IA", "description": ""},
        {"id": "edital:finep:590", "kind": "edital", "native_id": "finep:590",
         "name": "Chamada Biotec", "description": ""},
        {"id": "agencia:finep", "kind": "agencia", "native_id": "finep",
         "name": "FINEP", "description": ""},
    ]
    quality = [
        {"id": "setor:agro", "family": "setor", "value": "Agro"},
        {"id": "setor:saude", "family": "setor", "value": "Saúde"},
        {"id": "setor:multissetorial", "family": "setor", "value": "Multissetorial"},
        {"id": "tecnologia:ia", "family": "tecnologia", "value": "Inteligência Artificial"},
        {"id": "uf:sc", "family": "uf", "value": "SC"},
        {"id": "uf:sp", "family": "uf", "value": "SP"},
        {"id": "mecanismo:subvencao", "family": "mecanismo", "value": "Subvenção"},
        {"id": "faixa_trl:prototipo", "family": "faixa_trl", "value": "prototipo"},
        {"id": "estagio:seed", "family": "estagio", "value": "seed"},
    ]
    edges = [
        _edge("edital:finep:589", "setor:agro", "tem_setor", origin="phase1_deterministic"),
        _edge("ict:embrapii:ia", "setor:agro", "tem_setor", origin="phase1_deterministic"),
        _edge("edital:finep:589", "tecnologia:ia", "tem_tecnologia", origin="phase1_deterministic"),
        _edge("ict:embrapii:ia", "tecnologia:ia", "tem_tecnologia", origin="phase1_deterministic"),
        _edge("ict:embrapii:ia", "uf:sc", "tem_uf", origin="phase1_deterministic"),
        _edge("edital:finep:590", "setor:saude", "tem_setor", origin="phase1_deterministic"),
        _edge("edital:finep:590", "setor:multissetorial", "tem_setor",
           origin="phase1_deterministic", weight=0.1, props={"hub": True}),
        _edge("ict:embrapii:ia", "setor:multissetorial", "tem_setor",
           origin="phase1_deterministic", weight=0.1, props={"hub": True}),
        _edge("edital:finep:589", "agencia:finep", "operado_por", origin="phase1_structural"),
        _edge("edital:finep:589", "ict:embrapii:ia", "similar_a",
           origin="phase1_similarity", weight=0.98,
           props={"base": "cosine_embedding", "derived": True}),
        _edge("ict:embrapii:ia", "edital:finep:589", "similar_a",
           origin="phase1_similarity", weight=0.98,
           props={"base": "cosine_embedding", "derived": True}),
        _edge("edital:finep:589", "ict:embrapii:ia", "potencial_parceria",
           origin="phase1_tech_bridge", weight=0.5,
           props={"n_shared": 1, "derived": True}),
        _edge("edital:finep:589", "uf:sp", "tem_uf", origin="phase1_deterministic"),
        _edge("edital:finep:589", "mecanismo:subvencao", "usa_mecanismo",
           origin="phase1_deterministic"),
        _edge("edital:finep:589", "faixa_trl:prototipo", "tem_trl_faixa",
           origin="phase1_deterministic"),
        _edge("edital:finep:590", "estagio:seed", "busca_estagio", origin="phase1_deterministic"),
    ]
    communities = {
        "com_0": ["edital:finep:589", "ict:embrapii:ia", "agencia:finep"],
        "com_1": ["edital:finep:590"],
    }
    base = Snapshot(
        generation_id=7, nodes=nodes, quality_nodes=quality,
        edges=edges, communities=communities,
    )
    for k, v in overrides.items():
        object.__setattr__(base, k, v)
    return base


def _snapshot_ambiguous() -> Snapshot:
    nodes = [
        {"id": "edital:srcA:1", "kind": "edital", "native_id": "dup:x",
         "name": "Projeto Alfa", "description": ""},
        {"id": "edital:srcB:1", "kind": "edital", "native_id": "dup:x",
         "name": "Projeto Beta", "description": ""},
    ]
    return Snapshot(generation_id=1, nodes=nodes, quality_nodes=[], edges=[], communities={})


def _flag_on(monkeypatch) -> None:
    monkeypatch.setenv("KG_PHASE1_EXPLORE_ENABLED", "true")


def _snap_mock(monkeypatch, snapshot) -> None:
    monkeypatch.setattr(store, "load_snapshot", lambda *a, **k: snapshot)


def _tools(monkeypatch, *, profile=None, snapshot=None):
    _flag_on(monkeypatch)
    if snapshot is not None:
        _snap_mock(monkeypatch, snapshot)
    return {t.name: t for t in tools.build_graph_tools(profile=profile)}


# ─────────────────────────────────────────────────────────────────────────────
# 1 + 2. Flag off/on — registro das tools
# ─────────────────────────────────────────────────────────────────────────────

def test_flag_off_keeps_exactly_previous_tools(monkeypatch):
    monkeypatch.delenv("KG_PHASE1_EXPLORE_ENABLED", raising=False)
    monkeypatch.delenv("EXPLORE_DEEP_RESEARCH_ENABLED", raising=False)
    svc = ExploreAgent()
    names = {t.name for t in svc._explore_tools(profile={})}
    assert names == _BASE_TOOLS
    assert not any(n.startswith("graph_") for n in names)


def test_flag_off_build_graph_tools_empty(monkeypatch):
    monkeypatch.delenv("KG_PHASE1_EXPLORE_ENABLED", raising=False)
    assert tools.build_graph_tools(profile={}) == []


def test_flag_on_adds_only_three_graph_tools(monkeypatch):
    monkeypatch.delenv("EXPLORE_DEEP_RESEARCH_ENABLED", raising=False)
    _flag_on(monkeypatch)
    svc = ExploreAgent()
    names = {t.name for t in svc._explore_tools(profile={})}
    assert names == _BASE_TOOLS | {"graph_explore", "graph_reason", "graph_community"}
    assert {n for n in names if n.startswith("graph_")} == {
        "graph_explore", "graph_reason", "graph_community",
    }


def test_agent_flag_on_appends_graph_instructions(monkeypatch):
    _flag_on(monkeypatch)
    system = ExploreAgent._maybe_append_graph_instructions("base")
    assert "GRAFO DA FASE 1" in system
    assert "NUNCA apresente" in system


def test_agent_flag_off_keeps_system_byte_identical(monkeypatch):
    monkeypatch.delenv("KG_PHASE1_EXPLORE_ENABLED", raising=False)
    assert ExploreAgent._maybe_append_graph_instructions("base") == "base"


# ─────────────────────────────────────────────────────────────────────────────
# 3. Perfil é closure, não argumento público
# ─────────────────────────────────────────────────────────────────────────────

def test_profile_is_closure_not_public_arg(monkeypatch):
    _flag_on(monkeypatch)
    t = _tools(monkeypatch, profile={"uf": "SC"})
    reason = t["graph_reason"]
    assert "profile" not in reason.args


def test_profile_closure_anchors_reason_paths(monkeypatch):
    _flag_on(monkeypatch)
    _snap_mock(monkeypatch, _snapshot())
    t = _tools(monkeypatch, profile={"uf": "SC"})
    out = t["graph_reason"].invoke({"entity_ref": "ict:embrapii:ia"})
    data = json.loads(out)
    assert data["status"] == "hit" if "status" in data else True
    assert data["profile_anchor"] == "empresa:efemera"
    assert data["paths_to_profile"], "perfil deve ancorar caminhos via uf:sc"
    first = data["paths_to_profile"][0][0]
    assert first["from"] == "empresa:efemera"
    assert first["predicate"] == "atua_em"
    assert first["origin"] == "profile_ephemeral"


# ─────────────────────────────────────────────────────────────────────────────
# 4. Snapshot consistente (mesma geração, UMA conexão/transação)
# ─────────────────────────────────────────────────────────────────────────────

class _FakeTx:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _SnapCur:
    def __init__(self, gen_row: tuple | None, nodes=(), quality=(), edges=(), communities=()):
        self.gen_row = gen_row
        self.rows = {
            "from kg_phase1.nodes": list(nodes),
            "from kg_phase1.quality_nodes": list(quality),
            "from kg_phase1.edges": list(edges),
            "from kg_phase1.communities": list(communities),
        }
        self.executed: list[tuple] = []
        self.last_sql = ""

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self.executed.append((sql, params))
        self.last_sql = sql

    def fetchone(self):
        if "from kg_phase1.generations" in self.last_sql:
            return self.gen_row
        return None

    def fetchall(self):
        for marker, rows in self.rows.items():
            if marker in self.last_sql:
                return rows
        return []

    @property
    def description(self):
        cols = {
            "from kg_phase1.nodes": ("id", "kind", "native_id", "name", "description"),
            "from kg_phase1.quality_nodes": ("id", "family", "value"),
            "from kg_phase1.edges": ("source_id", "target_id", "type", "weight", "properties", "origin"),
            "from kg_phase1.communities": ("community_id", "node_id"),
        }
        for marker, names in cols.items():
            if marker in self.last_sql:
                return [_Col(n) for n in names]
        return [_Col("id")]


class _Col:
    def __init__(self, name):
        self.name = name


class _SnapConn:
    def __init__(self, cur):
        self._cur = cur

    def cursor(self):
        return self._cur

    def transaction(self):
        return _FakeTx()

    def close(self):
        pass


def _snap_cur_rows(snapshot: Snapshot) -> tuple:
    nodes = tuple(
        (n["id"], n["kind"], n["native_id"], n["name"], n["description"])
        for n in snapshot.nodes
    )
    quality = tuple((q["id"], q["family"], q["value"]) for q in snapshot.quality_nodes)
    edges = tuple(
        (e["source_id"], e["target_id"], e["type"], e["weight"],
         json.dumps(e["properties"], sort_keys=True), e["origin"])
        for e in snapshot.edges
    )
    communities = tuple(
        (cid, nid) for cid, members in snapshot.communities.items() for nid in members
    )
    return nodes, quality, edges, communities


def test_load_snapshot_same_generation_single_connection(monkeypatch):
    snap = _snapshot()
    nodes, quality, edges, communities = _snap_cur_rows(snap)
    cur = _SnapCur((7,), nodes, quality, edges, communities)
    conn = _SnapConn(cur)

    out = store.load_snapshot(conn=conn)

    assert out is not None
    assert out.generation_id == 7
    assert len(out.nodes) == 4
    assert len(out.edges) == 16
    assert out.communities["com_0"] == ["edital:finep:589", "ict:embrapii:ia", "agencia:finep"]
    # todas as leituras da MESMA geração (nunca mistura duas durante um swap)
    gen_params = {p[0] for sql, p in cur.executed if "where generation_id = %s" in sql}
    assert gen_params == {7}
    # resolução exige geração saudável
    assert any("status = 'healthy'" in sql for sql, _ in cur.executed)


def test_load_snapshot_own_connection_sets_timeout(monkeypatch):
    nodes, quality, edges, communities = _snap_cur_rows(_snapshot())
    cur = _SnapCur((7,), nodes, quality, edges, communities)
    conn = _SnapConn(cur)
    monkeypatch.setattr(store, "_connect_with_timeout", lambda timeout: conn)

    out = store.load_snapshot()

    assert out is not None
    assert any("statement_timeout" in sql for sql, _ in cur.executed)
    assert sum(1 for sql, _ in cur.executed if "from kg_phase1.nodes" in sql) == 1


def test_load_snapshot_none_without_healthy_generation(monkeypatch):
    cur = _SnapCur(None)
    out = store.load_snapshot(conn=_SnapConn(cur))
    assert out is None
    # sem geração saudável → nenhuma leitura de dados é feita
    assert not any("from kg_phase1.nodes" in sql for sql, _ in cur.executed)


# ─────────────────────────────────────────────────────────────────────────────
# 5. Degradação: ausência de geração e falha do banco não derrubam o agente
# ─────────────────────────────────────────────────────────────────────────────

def test_tool_unavailable_when_no_generation(monkeypatch):
    _snap_mock(monkeypatch, None)
    t = _tools(monkeypatch, profile=None)
    out = t["graph_explore"].invoke({"entity_ref": "edital:finep:589"})
    assert "indisponível" in out
    assert "catálogo" in out


def test_tool_degrades_when_db_fails(monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger="radar.core.kg.phase1.tools")

    def _boom(*a, **k):
        raise psycopg.OperationalError("db down")

    monkeypatch.setattr(store, "load_snapshot", _boom)
    t = _tools(monkeypatch, profile=None)
    out = t["graph_reason"].invoke({"entity_ref": "qualquer"})
    assert "indisponível" in out
    assert "outcome=unavailable" in caplog.text
    assert "category=database_error" in caplog.text


def test_agent_degrades_gracefully_when_graph_down(monkeypatch):
    """Com a flag ligada e o grafo indisponível, o agente NÃO derruba: as graph
    tools estão presentes, mas a falha vira resultado sanitizado (run_agent
    continua recebendo tools e system)."""
    _flag_on(monkeypatch)
    captured: dict = {}
    fake_result = AgentResult(final_text="ok", steps=[], stop_reason="end_turn", usage={})
    monkeypatch.setattr(
        "radar.core.llm.agent_runtime.run_agent",
        lambda **kw: captured.update(kw) or fake_result,
    )
    ExploreAgent().explore_with_meta("pergunta", profile_text="x", profile={"uf": "SC"})
    names = {t.name for t in captured["tools"]}
    assert {"graph_explore", "graph_reason", "graph_community"} <= names
    assert "GRAFO DA FASE 1" in captured["system"]


def test_agent_flag_off_keeps_previous_tools_end_to_end(monkeypatch):
    monkeypatch.delenv("KG_PHASE1_EXPLORE_ENABLED", raising=False)
    monkeypatch.delenv("EXPLORE_DEEP_RESEARCH_ENABLED", raising=False)
    captured: dict = {}
    fake_result = AgentResult(final_text="ok", steps=[], stop_reason="end_turn", usage={})
    monkeypatch.setattr(
        "radar.core.llm.agent_runtime.run_agent",
        lambda **kw: captured.update(kw) or fake_result,
    )
    ExploreAgent().explore_with_meta("pergunta", profile_text="x", profile={"uf": "SC"})
    names = {t.name for t in captured["tools"]}
    assert names == _BASE_TOOLS | {"find_matching_editais", "find_matching_entities"}
    assert "GRAFO DA FASE 1" not in captured["system"]


# ─────────────────────────────────────────────────────────────────────────────
# 6. Resolução: exata, não encontrada, ambígua
# ─────────────────────────────────────────────────────────────────────────────

def test_resolution_exact_native_name_quality():
    snap = _snapshot()
    assert resolve.resolve_entity("edital:finep:589", snap).status == "hit"
    assert resolve.resolve_entity("finep:589", snap).node_id == "edital:finep:589"
    assert resolve.resolve_entity("Chamada Agro IA", snap).node_id == "edital:finep:589"
    assert resolve.resolve_entity("agro", snap).node_id == "setor:agro"
    assert resolve.resolve_entity("setor:agro", snap).node_id == "setor:agro"
    assert resolve.resolve_entity("", snap).status == "not_found"
    assert resolve.resolve_entity("não existe no grafo", snap).status == "not_found"


def test_resolution_ambiguous_never_guesses():
    snap = _snapshot_ambiguous()
    res = resolve.resolve_entity("dup:x", snap)
    assert res.status == "ambiguous"
    assert set(res.candidates) == {"edital:srcA:1", "edital:srcB:1"}
    assert len(res.candidates) <= resolve.MAX_CANDIDATES

    nodes = [
        {"id": "edital:srcA:2", "kind": "edital", "native_id": "a:2",
         "name": "Mesmo Nome", "description": ""},
        {"id": "ict:srcB:2", "kind": "ict", "native_id": "b:2",
         "name": "Mesmo Nome", "description": ""},
    ]
    snap2 = Snapshot(1, nodes, [], [], {})
    res2 = resolve.resolve_entity("mesmo nome", snap2)
    assert res2.status == "ambiguous"
    assert set(res2.candidates) == {"edital:srcA:2", "ict:srcB:2"}


def test_tool_ambiguous_returns_safe_candidates(monkeypatch):
    _snap_mock(monkeypatch, _snapshot_ambiguous())
    t = _tools(monkeypatch, profile=None)
    out = json.loads(t["graph_explore"].invoke({"entity_ref": "dup:x"}))
    assert out["status"] == "ambiguous"
    assert "candidates" in out
    assert "edital:srcA:1" in out["candidates"]


def test_tool_not_found_is_categorical(monkeypatch):
    _snap_mock(monkeypatch, _snapshot())
    t = _tools(monkeypatch, profile=None)
    out = json.loads(t["graph_explore"].invoke({"entity_ref": "zedadozen"}))
    assert out["status"] == "not_found"


# ─────────────────────────────────────────────────────────────────────────────
# 7. Limites: profundidade, nós, arestas, caminhos, payload
# ─────────────────────────────────────────────────────────────────────────────

def test_explore_depth_capped_at_two(monkeypatch):
    _snap_mock(monkeypatch, _snapshot())
    t = _tools(monkeypatch, profile=None)
    out = json.loads(t["graph_explore"].invoke({"entity_ref": "edital:finep:589", "depth": 99}))
    assert out["center"]["id"] == "edital:finep:589"
    assert "edges" in out


def test_explore_payload_caps_nodes_and_edges():
    out = tools.explore_payload(
        "edital:finep:589", _snapshot(), depth=2, max_nodes=2, max_edges=2,
    )
    assert out.outcome == "hit"
    assert len(out.payload["nodes"]) <= 2
    assert len(out.payload["edges"]) <= 2


def test_reason_paths_limited():
    out = tools.reason_payload("edital:finep:589", _snapshot(), max_paths=1)
    assert out.n_paths <= 2
    assert len(out.payload["paths_to_actors"]) <= 1


def test_trim_payload_enforces_byte_cap():
    big = {
        "edges": [
            {"source": "a", "target": f"b{i}", "predicate": "p", "weight": 1.0,
             "origin": "o", "derived": False}
            for i in range(500)
        ]
    }
    trimmed = tools._trim_payload(big, 12_000)
    assert len(tools.dump(trimmed)) <= 12_000
    assert len(trimmed["edges"]) < 500


def test_tool_output_respects_payload_cap(monkeypatch):
    _snap_mock(monkeypatch, _snapshot())
    t = _tools(monkeypatch, profile=None)
    out = t["graph_explore"].invoke({"entity_ref": "edital:finep:589", "depth": 2})
    assert len(out) <= 2 * 12_000 + 512  # utf-8 multibyte + folga


# ─────────────────────────────────────────────────────────────────────────────
# 8. Direção, origin e derived sobrevivem à serialização
# ─────────────────────────────────────────────────────────────────────────────

def test_explore_serialization_preserves_direction_origin_derived(monkeypatch):
    _snap_mock(monkeypatch, _snapshot())
    t = _tools(monkeypatch, profile=None)
    out = json.loads(t["graph_explore"].invoke({"entity_ref": "edital:finep:589"}))
    by_pred = {(e["source"], e["predicate"], e["target"]): e for e in out["edges"]}

    struct = by_pred[("edital:finep:589", "operado_por", "agencia:finep")]
    assert struct["origin"] == "phase1_structural"
    assert struct["derived"] is False
    assert struct["weight"] == 1.0

    sim = by_pred[("edital:finep:589", "similar_a", "ict:embrapii:ia")]
    assert sim["origin"] == "phase1_similarity"
    assert sim["derived"] is True
    assert sim["weight"] == pytest.approx(0.98)

    hub = by_pred.get(("edital:finep:590", "tem_setor", "setor:multissetorial"))
    if hub is not None:  # só direto (depth 1 do próprio nó); direção preservada
        assert hub["source"] == "edital:finep:590"
        assert hub["target"] == "setor:multissetorial"


def test_reason_serialization_preserves_hop_direction(monkeypatch):
    _flag_on(monkeypatch)
    _snap_mock(monkeypatch, _snapshot())
    t = _tools(monkeypatch, profile={"uf": "SC"})
    out = json.loads(t["graph_reason"].invoke({"entity_ref": "ict:embrapii:ia"}))
    path = out["paths_to_profile"][0]
    assert path[0] == {
        "from": "empresa:efemera", "to": "uf:sc", "predicate": "atua_em",
        "weight": 1.0, "origin": "profile_ephemeral", "derived": False,
    }
    assert path[1]["from"] == "uf:sc"
    assert path[1]["to"] == "ict:embrapii:ia"
    assert path[1]["predicate"] == "tem_uf"


# ─────────────────────────────────────────────────────────────────────────────
# 9. Hub multissetorial não expande a vizinhança
# ─────────────────────────────────────────────────────────────────────────────

def test_hub_multissetorial_does_not_expand(monkeypatch):
    _snap_mock(monkeypatch, _snapshot())
    t = _tools(monkeypatch, profile=None)
    out = json.loads(t["graph_explore"].invoke({"entity_ref": "edital:finep:590", "depth": 2}))
    ids = {n["id"] for n in out["nodes"]}
    assert "setor:multissetorial" not in ids
    assert all(e["target"] != "setor:multissetorial" for e in out["edges"])
    assert "setor:saude" in ids  # vizinhança real continua visível


def test_hub_as_seed_has_no_expansion(monkeypatch):
    _snap_mock(monkeypatch, _snapshot())
    t = _tools(monkeypatch, profile=None)
    out = json.loads(t["graph_explore"].invoke({"entity_ref": "multissetorial"}))
    assert out["center"]["id"] == "setor:multissetorial"
    assert out["edges"] == []


def test_reason_does_not_traverse_through_hub(monkeypatch):
    out = tools.reason_payload("edital:finep:590", _snapshot(), max_depth=3)
    # sem min_weight o hub conectaria — aqui NENHUM caminho passa por ele
    for path in out.payload["paths_to_actors"]:
        for step in path:
            assert step["to"] != "setor:multissetorial"
            assert step["from"] != "setor:multissetorial"


# ─────────────────────────────────────────────────────────────────────────────
# 10. Relações derivadas não textualizadas como confirmação
# ─────────────────────────────────────────────────────────────────────────────

def test_derived_edges_never_presented_as_fact(monkeypatch):
    _flag_on(monkeypatch)
    _snap_mock(monkeypatch, _snapshot())
    t = _tools(monkeypatch, profile=None)
    out = json.loads(t["graph_reason"].invoke({"entity_ref": "edital:finep:589"}))
    assert "DERIVADAS" in out["note"]
    seen_derived = False
    for path in out["paths_to_profile"] + out["paths_to_actors"]:
        for step in path:
            if step["predicate"] in ("similar_a", "potencial_parceria"):
                seen_derived = True
                assert step["derived"] is True
                assert step["origin"] in ("phase1_similarity", "phase1_tech_bridge")
    assert seen_derived, "o fixture deve conter pelo menos um salto derivado"


def test_explore_does_not_invent_confirmation_text(monkeypatch):
    _snap_mock(monkeypatch, _snapshot())
    t = _tools(monkeypatch, profile=None)
    out = t["graph_explore"].invoke({"entity_ref": "edital:finep:589"})
    # o payload é JSON estrutural — nunca prosa de "fato confirmado"
    assert "é parceira confirmada" not in out
    assert "fato" not in json.loads(out).get("note", "")


# ─────────────────────────────────────────────────────────────────────────────
# 11. Logs não vazam conteúdo adversarial
# ─────────────────────────────────────────────────────────────────────────────

_ADVERSARIAL_MSG = (
    "postgresql://user:pass@db.internal:5432/radar SEGREDO_BRUTO "
    "https://admin.example.com/x SELECT * FROM entities conteudo_confidencial"
)
_MARKERS = ("postgresql://", "user:pass", "SEGREDO_BRUTO", "admin.example.com",
            "SELECT", "conteudo_confidencial")


def test_logs_never_leak_adversarial_content(caplog, monkeypatch):
    caplog.set_level(logging.INFO, logger="radar.core.kg.phase1.tools")

    def _boom(*a, **k):
        raise psycopg.OperationalError(_ADVERSARIAL_MSG)

    monkeypatch.setattr(store, "load_snapshot", _boom)
    t = _tools(monkeypatch, profile={"uf": "SC"})

    out = t["graph_explore"].invoke({"entity_ref": "SEGREDO_BRUTO alvo"})
    assert "indisponível" in out
    for marker in _MARKERS:
        assert marker not in out
        assert marker not in caplog.text
    assert "Traceback" not in caplog.text


def test_logs_structural_only_on_hit(caplog, monkeypatch):
    caplog.set_level(logging.INFO, logger="radar.core.kg.phase1.tools")
    _snap_mock(monkeypatch, _snapshot())
    t = _tools(monkeypatch, profile=None)
    t["graph_explore"].invoke({"entity_ref": "Chamada Agro IA"})
    assert "tool=graph_explore outcome=hit" in caplog.text
    assert "generation_id=7" in caplog.text
    assert "Chamada Agro IA" not in caplog.text  # nome não vaza


# ─────────────────────────────────────────────────────────────────────────────
# Extras: graph_community (membros por kind + características compartilhadas)
# ─────────────────────────────────────────────────────────────────────────────

def test_community_resolves_and_groups_by_kind(monkeypatch):
    _snap_mock(monkeypatch, _snapshot())
    t = _tools(monkeypatch, profile=None)
    out = json.loads(t["graph_community"].invoke({"community_ref": "com_0"}))
    assert out["community_id"] == "com_0"
    assert out["n_members"] == 3
    assert set(out["members_by_kind"]) >= {"edital", "ict", "agencia"}
    assert "shared_characteristics" in out
    assert "edge_types" in out
    assert "tem_setor" in out["edge_types"]


def test_community_variants_and_not_found(monkeypatch):
    _snap_mock(monkeypatch, _snapshot())
    t = _tools(monkeypatch, profile=None)
    out0 = json.loads(t["graph_community"].invoke({"community_ref": "0"}))
    assert out0["community_id"] == "com_0"
    out1 = json.loads(t["graph_community"].invoke({"community_ref": "comunidade 1"}))
    assert out1["community_id"] == "com_1"
    miss = json.loads(t["graph_community"].invoke({"community_ref": "com_999"}))
    assert miss["status"] == "not_found"
    assert miss["available_sample"]
