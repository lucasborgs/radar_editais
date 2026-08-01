"""Testes da projeção de produção da Fase 1 do grafo (KG-P1A).

Cobrem APENAS os riscos essenciais (task KG-P1A):
  1. reconstrução determinística;
  2. idempotência (source_hash + skip);
  3. IDs estáveis;
  4. separação entre fatos estruturais, similaridade e heurística (origins);
  5. última geração saudável preservada após falha;
  6. geração incompleta invisível aos leitores;
  7. traversal limitado e sem ciclos infinitos;
  8. ausência de LLM/rede (e sem dependência da spike);
  9. migration e RLS conforme padrões do repositório;
  10. equivalência estrutural com fixture representativa da Fase 1 da spike.

Hermético: nenhum teste toca o `public`, nenhum exige DATABASE_URL (acesso ao
banco é mockado/monkeypatched).
"""
from __future__ import annotations

import ast
import copy
from collections import defaultdict
from pathlib import Path

import pytest

from radar.core.config import ROOT
from radar.core.kg import phase1
from radar.core.kg.phase1 import features, ingest, store, traverse


def _embed(*vals) -> str:
    return "[" + ",".join(repr(float(v)) for v in vals) + "]"


def _fixture_entities() -> list[dict]:
    return [
        {"id": "1", "kind": "edital", "source": "finep", "native_id": "finep:589",
         "name": "Chamada Agro IA", "description": "Edital de agro e IA",
         "setores": ["Agro", "Multissetorial"], "tecnologias_tags": ["Inteligência Artificial", "IoT"],
         "uf": "SP", "mecanismo": "subvencao",
         "constraints": [{"tipo": "trl", "op": "gte", "valor": 4}],
         "metadata": {"estagio_alvo": ["seed"]},
         "trl_range": (4, None), "embedding": _embed(1, 0, 0)},
        {"id": "2", "kind": "ict", "source": "embrapii", "native_id": "embrapii:ia",
         "name": "Unidade IA", "description": "ICT de IA",
         "setores": ["Agro"], "tecnologias_tags": ["Inteligência Artificial", "robótica"],
         "uf": "SC", "mecanismo": None, "constraints": [], "metadata": {},
         "trl_range": (None, None), "embedding": _embed(1, 0.2, 0.1)},
        {"id": "3", "kind": "edital", "source": "finep", "native_id": "finep:590",
         "name": "Chamada Biotec", "description": "Edital de biotecnologia",
         "setores": ["Saúde"], "tecnologias_tags": ["biotecnologia"],
         "uf": None, "mecanismo": "bolsa", "constraints": [], "metadata": {},
         "trl_range": (None, None), "embedding": _embed(0, 1, 0)},
        {"id": "4", "kind": "agencia", "source": "curadoria", "native_id": "finep",
         "name": "FINEP", "description": "Agência", "setores": [], "tecnologias_tags": [],
         "uf": None, "mecanismo": None, "constraints": [], "metadata": {},
         "trl_range": (None, None), "embedding": None},
    ]


def _fixture_rels() -> list[tuple[str, str, str]]:
    return [("1", "4", "operado_por")]


# ─────────────────────────────────────────────────────────────────────────────
# 1 + 3. Reconstrução determinística + IDs estáveis
# ─────────────────────────────────────────────────────────────────────────────

def test_build_rows_deterministic():
    n1, q1, e1 = ingest._build_rows(_fixture_entities(), _fixture_rels())
    n2, q2, e2 = ingest._build_rows(_fixture_entities(), _fixture_rels())
    assert n1 == n2
    assert q1 == q2
    assert e1 == e2


def test_stable_ids_and_trl_faixas():
    nodes, quality, _ = ingest._build_rows(_fixture_entities(), _fixture_rels())
    assert {n["id"] for n in nodes} == {
        "edital:finep:589", "ict:embrapii:ia", "edital:finep:590", "agencia:finep",
    }
    assert ingest._node_id("edital", "finep:589") == "edital:finep:589"
    assert ingest._trl_faixa_id(4, None) == ["faixa_trl:prototipo", "faixa_trl:industrial"]
    # ids de qualidade são determinísticos (deburr + lowercase)
    assert any(q["id"] == "tecnologia:inteligencia artificial" for q in quality)
    assert any(q["id"] == "setor:multissetorial" for q in quality)


# ─────────────────────────────────────────────────────────────────────────────
# 2. Idempotência (source_hash + skip)
# ─────────────────────────────────────────────────────────────────────────────

def test_source_hash_stable_and_content_sensitive():
    h1 = ingest._source_hash(_fixture_entities(), _fixture_rels())
    h2 = ingest._source_hash(_fixture_entities(), _fixture_rels())
    assert h1 == h2
    mod = copy.deepcopy(_fixture_entities())
    mod[0]["tecnologias_tags"] = ["mudou"]
    assert ingest._source_hash(mod, _fixture_rels()) != h1
    emb_mod = copy.deepcopy(_fixture_entities())
    emb_mod[0]["embedding"] = _embed(1, 1, 0)
    assert ingest._source_hash(emb_mod, _fixture_rels()) != h1


def test_should_skip_decision():
    current = {"id": 5, "source_hash": "abc"}
    assert ingest._should_skip(current, "abc", True) is True
    assert ingest._should_skip(current, "xyz", True) is False
    assert ingest._should_skip(None, "abc", True) is False
    assert ingest._should_skip(current, "abc", False) is False


def test_build_tx_skips_when_unchanged(monkeypatch):
    entities, rels = _fixture_entities(), _fixture_rels()
    src_hash = ingest._source_hash(entities, rels)
    monkeypatch.setattr(ingest, "_load_gold", lambda cur: (entities, rels))
    monkeypatch.setattr(store, "current_generation", lambda conn: {"id": 5, "source_hash": src_hash})
    cur = _RecordingCur()
    out = ingest._build_tx(_TxConn(cur), skip_unchanged=True, run_communities=True)
    assert out["skipped"] is True
    assert out["generation"] == 5
    assert cur.executed == []  # nenhum insert/swap


# ─────────────────────────────────────────────────────────────────────────────
# 4. Separação: fatos estruturais × similaridade × heurística
# ─────────────────────────────────────────────────────────────────────────────

def test_origins_separate_facts_from_derived():
    _, _, edges = ingest._build_rows(_fixture_entities(), _fixture_rels())
    by_type: dict[str, list[dict]] = defaultdict(list)
    for e in edges:
        by_type[e["type"]].append(e)

    # fatos copiados do gold
    assert all(e["origin"] == "phase1_deterministic" for e in by_type["tem_setor"])
    assert all(e["origin"] == "phase1_deterministic" for e in by_type["tem_tecnologia"])
    # estruturais = cópia do entity_relationships
    struct = by_type["operado_por"]
    assert len(struct) == 1 and struct[0]["origin"] == "phase1_structural"
    # similar_a = DERIVADA de embeddings (nunca fato documental)
    assert all(e["origin"] == "phase1_similarity" for e in by_type["similar_a"])
    assert all(e["properties"]["derived"] is True for e in by_type["similar_a"])
    assert all(e["properties"]["base"] == "cosine_embedding" for e in by_type["similar_a"])
    # potencial_parceria = HEURÍSTICA (nunca fato documental)
    assert all(e["origin"] == "phase1_tech_bridge" for e in by_type["potencial_parceria"])
    assert all(e["properties"]["derived"] is True for e in by_type["potencial_parceria"])
    assert all(e["properties"]["n_shared"] >= 1 for e in by_type["potencial_parceria"])


# ─────────────────────────────────────────────────────────────────────────────
# 5 + 6. Falha preserva a saudável; leitores só veem a corrente
# ─────────────────────────────────────────────────────────────────────────────

def test_failure_preserves_last_healthy_and_records_failed(monkeypatch):
    captured: dict = {}
    monkeypatch.setattr(ingest, "_record_failure", lambda exc: captured.update(exc=exc))

    class _BoomError(Exception):
        pass

    class _FailingConn:
        def transaction(self):
            raise _BoomError("falhou no build")

        def close(self):
            pass

    monkeypatch.setattr(store, "connect", lambda: _FailingConn())
    with pytest.raises(_BoomError):
        ingest.build()
    assert isinstance(captured["exc"], _BoomError)


def test_swap_not_executed_on_partial_failure(monkeypatch):
    """Falha no meio do build → o swap (is_current) NUNCA roda → a última
    geração saudável permanece corrente (rollback da transação)."""
    entities, rels = _fixture_entities(), _fixture_rels()
    monkeypatch.setattr(ingest, "_load_gold", lambda cur: (entities, rels))
    monkeypatch.setattr(store, "current_generation", lambda conn: None)

    class _BoomCur(_RecordingCur):
        def executemany(self, sql, params):
            if "insert into kg_phase1.edges" in sql:
                raise RuntimeError("db down")
            self.executed.append((sql, params))

    cur = _BoomCur()
    with pytest.raises(RuntimeError):
        ingest._build_tx(_TxConn(cur), skip_unchanged=True, run_communities=True)
    sqls = [s for s, _ in cur.executed]
    assert not any("is_current" in s for s in sqls)


def test_build_tx_swap_is_last_statement(monkeypatch):
    """Swap atômico = última operação da transação: is_current primeiro, healthy
    por último. Nada é escrito depois do swap."""
    entities, rels = _fixture_entities(), _fixture_rels()
    monkeypatch.setattr(ingest, "_load_gold", lambda cur: (entities, rels))
    monkeypatch.setattr(store, "current_generation", lambda conn: None)
    cur = _RecordingCur()
    out = ingest._build_tx(_TxConn(cur), skip_unchanged=True, run_communities=True)
    assert out["skipped"] is False
    assert out["nodes"] == 4
    sqls = [s for s, _ in cur.executed]
    assert "is_current = (id = %s)" in sqls[-2]
    assert "status = 'healthy'" in sqls[-1]
    # geração nova é inserida no início da transação
    assert "insert into kg_phase1.generations (status, build_version" in sqls[0]


def test_current_generation_filters_is_current(monkeypatch):
    cur = _StoreCur((7, "healthy", "kg-phase1-v1", "abc", "{}", "", None, None))
    monkeypatch.setattr(store, "connect", lambda: _StoreConn(cur))
    gen = store.current_generation()
    assert gen["id"] == 7
    # A ÚNICA linha observável é `is_current = true` — `building`/`failed` nunca
    # entram (a troca só marca a geração saudável, no commit do build).
    assert any("where is_current = true" in s for s in cur.executed)


def test_current_generation_none_when_no_current(monkeypatch):
    cur = _StoreCur(None)
    monkeypatch.setattr(store, "connect", lambda: _StoreConn(cur))
    assert store.current_generation() is None


def test_store_reads_empty_without_generation(monkeypatch):
    monkeypatch.setattr(store, "current_generation", lambda conn=None: None)
    monkeypatch.setattr(store, "connect", lambda: _DummyConn())
    assert store.load_nodes() == []
    assert store.load_edges() == []
    assert store.load_communities() == {}


# ─────────────────────────────────────────────────────────────────────────────
# 7. Traversal limitado e sem ciclos infinitos
# ─────────────────────────────────────────────────────────────────────────────

def test_traversal_limited_and_cycle_safe():
    edges = [
        {"source_id": "a", "target_id": "setor:x", "type": "tem_setor", "weight": 1.0},
        {"source_id": "b", "target_id": "setor:x", "type": "tem_setor", "weight": 1.0},
        {"source_id": "b", "target_id": "c", "type": "potencial_parceria", "weight": 0.8},
        {"source_id": "c", "target_id": "agencia:f", "type": "operado_por", "weight": 1.0},
        {"source_id": "x", "target_id": "y", "type": "similar_a", "weight": 1.0},
        {"source_id": "y", "target_id": "x", "type": "similar_a", "weight": 1.0},
    ]
    out = traverse.bfs_edges(edges, "x", depth=3)
    assert sum(1 for e in out if e["type"] == "similar_a") == 1  # ciclo não repete
    assert len(out) <= len(edges)
    paths = traverse.find_paths(edges, "b", "agencia:f", max_depth=4)
    assert paths[0] == [("b", "potencial_parceria", "c"), ("c", "operado_por", "agencia:f")]
    assert "z" not in traverse.reachable_within(edges, "a", max_depth=2)


def test_traversal_min_weight_skips_hub():
    edges = [
        {"source_id": "b", "target_id": "setor:multissetorial", "type": "tem_setor", "weight": 0.1},
        {"source_id": "setor:multissetorial", "target_id": "ict:x", "type": "tem_setor", "weight": 0.1},
    ]
    assert traverse.find_paths(edges, "b", "ict:x", max_depth=3, min_weight=0.5) == []
    assert traverse.find_paths(edges, "b", "ict:x", max_depth=3)  # sem corte, alcança via hub


# ─────────────────────────────────────────────────────────────────────────────
# 8. Ausência de LLM/rede — e sem dependência da spike em runtime
# ─────────────────────────────────────────────────────────────────────────────

_FORBIDDEN_IMPORTS = {
    "openai", "anthropic", "langchain", "requests", "httpx", "urllib", "aiohttp",
    "radar.core.llm", "radar.core.retrieval.embedder", "radar.core.kg.spike",
}


def test_no_llm_no_network_no_spike_imports():
    pkg_dir = Path(phase1.__file__).parent
    assert pkg_dir.is_dir()
    checked = 0
    for path in sorted(pkg_dir.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imports: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                imports.add(node.module or "")
        bad = imports & _FORBIDDEN_IMPORTS
        assert not bad, f"{path.name}: imports proibidos {sorted(bad)}"
        checked += 1
    assert checked >= 5  # __init__, store, ingest, traverse, features


# ─────────────────────────────────────────────────────────────────────────────
# 9. Migration e RLS conforme padrões do repositório
# ─────────────────────────────────────────────────────────────────────────────

def test_migration_048_schema_and_rls_patterns():
    migration = ROOT / "supabase/migrations/048_kg_phase1.sql"
    assert migration.is_file()
    text = migration.read_text(encoding="utf-8")
    assert "create schema if not exists kg_phase1" in text
    for table in ("generations", "nodes", "quality_nodes", "edges", "communities"):
        assert f"create table if not exists kg_phase1.{table}" in text
    assert "where is_current" in text                       # índice parcial 1-corrente
    assert "check (origin in (" in text                     # CHECK fechado das 4 origens
    for origin in ("phase1_deterministic", "phase1_structural",
                   "phase1_similarity", "phase1_tech_bridge"):
        assert origin in text
    assert text.count("enable row level security") == 5     # todas as tabelas
    assert text.count("for select to authenticated using (true)") == 5  # padrão gold 036


# ─────────────────────────────────────────────────────────────────────────────
# 10. Equivalência estrutural com fixture representativa da Fase 1 da spike
# ─────────────────────────────────────────────────────────────────────────────

def test_equivalence_fixture_mirrors_spike_phase1():
    """A projeção reproduz o comportamento validado na spike (SPEC §8): hub
    multissetorial com peso baixo, similar_a por cosseno ≥0.75 top-10,
    potencial_parceria por Jaccard de tecnologia, estruturais copiadas."""
    nodes, quality, edges = ingest._build_rows(_fixture_entities(), _fixture_rels())
    assert len(nodes) == 4
    assert len(edges) == 20  # 16 determinísticas + 1 estrutural + 2 similar + 1 parceria
    by_type: dict[str, list[dict]] = defaultdict(list)
    for e in edges:
        by_type[e["type"]].append(e)

    # hub multissetorial: peso 0.1 (não expande vizinhança)
    hub = [e for e in by_type["tem_setor"] if e["target_id"] == "setor:multissetorial"]
    assert len(hub) == 1
    assert hub[0]["weight"] == pytest.approx(0.1)
    assert hub[0]["properties"] == {"hub": True}

    # similar_a: só o par e1↔e2 (cosseno 0.9759 ≥ 0.75), simétrico, peso = cosseno
    sims = {(e["source_id"], e["target_id"]): e for e in by_type["similar_a"]}
    assert set(sims) == {
        ("edital:finep:589", "ict:embrapii:ia"),
        ("ict:embrapii:ia", "edital:finep:589"),
    }
    assert sims[("edital:finep:589", "ict:embrapii:ia")]["weight"] == pytest.approx(0.9759, abs=1e-3)

    # potencial_parceria: Jaccard dos conjuntos de tecnologia = 1/3 (compartilham 1 de 3)
    parcs = by_type["potencial_parceria"]
    assert len(parcs) == 1
    assert (parcs[0]["source_id"], parcs[0]["target_id"]) == ("edital:finep:589", "ict:embrapii:ia")
    assert parcs[0]["weight"] == pytest.approx(1 / 3, abs=1e-3)
    assert parcs[0]["properties"]["n_shared"] == 1

    # estruturais copiadas do entity_relationships
    assert [e["target_id"] for e in by_type["operado_por"]] == ["agencia:finep"]

    # TRL overlap: constraint gte 4 → prototipo + industrial (nunca pesquisa)
    trl = {e["target_id"] for e in by_type["tem_trl_faixa"]}
    assert trl == {"faixa_trl:prototipo", "faixa_trl:industrial"}

    # sem sobreposição de tecnologia → sem aresta de parceria (postura honesta)
    assert not any(e["source_id"].endswith("finep:590") for e in by_type["potencial_parceria"])


# ─────────────────────────────────────────────────────────────────────────────
# Features (comunidades/centralidade — "quando disponíveis")
# ─────────────────────────────────────────────────────────────────────────────

def test_detect_communities_deterministic_and_empty():
    _, _, edges = ingest._build_rows(_fixture_entities(), _fixture_rels())
    c1 = features.detect_communities(edges)
    c2 = features.detect_communities(edges)
    assert c1 == c2
    assert features.detect_communities([]) == []
    stats = features.node_stats(edges)
    assert stats and all("degree" in v for v in stats.values())
    assert features.node_stats([]) == {}


def test_sanitize_error_redacts_dsn():
    msg = ingest._sanitize_error(
        RuntimeError("não conectou em postgresql://user:pass@host:5432/db")
    )
    assert "postgresql://" not in msg
    assert "<redacted>" in msg
    assert ingest._sanitize_error(RuntimeError("")) == "RuntimeError"


# ─────────────────────────────────────────────────────────────────────────────
# Dummies para os fluxos de transação/leitura (sem banco)
# ─────────────────────────────────────────────────────────────────────────────

class _Col:
    def __init__(self, name):
        self.name = name


class _RecordingCur:
    """Cursor fake: registra SQL/params; fetchone devolve o id da geração."""

    def __init__(self, generation_id: int = 7):
        self.executed: list[tuple] = []
        self.generation_id = generation_id
        self.last_sql = ""

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, sql, params=None):
        self.executed.append((sql, params))
        self.last_sql = sql

    def executemany(self, sql, params):
        self.executed.append((sql, params))

    def fetchone(self):
        if "returning id" in self.last_sql:
            return (self.generation_id,)
        return None

    def fetchall(self):
        return []

    @property
    def description(self):
        return [_Col("id"), _Col("status")]


class _TxConn:
    """Conexão fake com contexto de transação (commit/rollback contados)."""

    def __init__(self, cur):
        self._cur = cur
        self.commits = 0
        self.rollbacks = 0

    def cursor(self):
        return self._cur

    def transaction(self):
        return _Tx(self)

    def close(self):
        pass


class _Tx:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is not None:
            self.conn.rollbacks += 1
        else:
            self.conn.commits += 1
        return False


class _StoreCur:
    def __init__(self, row):
        self._row = row
        self.executed: list[str] = []
        self.last_sql = ""

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, sql, params=None):
        self.executed.append(sql)
        self.last_sql = sql

    def fetchone(self):
        return self._row

    @property
    def description(self):
        return [_Col(n) for n in (
            "id", "status", "build_version", "source_hash", "counts",
            "error", "started_at", "finished_at",
        )]


class _StoreConn:
    def __init__(self, cur):
        self._cur = cur

    def cursor(self):
        return self._cur

    def close(self):
        pass


class _DummyConn:
    def cursor(self):
        return _StoreCur(None)

    def close(self):
        pass
