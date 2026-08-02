"""Testes de integração da projeção da Fase 1 do grafo (KG-P1A) — Supabase LOCAL.

Prova REAL com psycopg cobrindo os 10 itens da correção KG-P1A:

1. migration executável;
2. primeiro build cria geração saudável e corrente;
3. build sem mudança é pulado;
4. build forçado cria nova geração;
5. somente uma geração fica corrente;
6. leitores enxergam apenas a corrente;
7. falha controlada após iniciar uma nova geração provoca rollback;
8. a geração saudável anterior permanece corrente;
9. o ledger registra a falha de forma categórica e sanitizada;
10. vector, JSONB, FK, índice parcial e swap funcionam no Postgres real.

Exclusivamente LOCAL: nenhuma credencial real, nenhum Supabase remoto, nenhuma
rede, LLM ou Langfuse. A sentinela `public.environment_metadata` é validada
ANTES de qualquer escrita (`assert_database_target`). A projeção é derivada e
reconstruível — o fixture zera o schema `kg_phase1` e remove as entidades
sintéticas (source `kgp1a`) ao final.

Como rodar (requer `supabase start` + migration 048 aplicada):

    supabase migration up
    INTEGRATION_TARGET=local \
    DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:54322/postgres \
    ENVIRONMENT=test PYTHONPATH=src \
    /Users/lucasborges/radar_editais/.venv/bin/pytest -q tests/integration/test_kg_phase1_projection.py
"""
from __future__ import annotations

import json
import os
from urllib.parse import urlparse

import psycopg
import pytest

from radar.core.environment import assert_database_target
from radar.core.kg.phase1 import ingest, store

pytestmark = pytest.mark.integration


def _v1536(*vals) -> str:
    """Vetor de 1536 dims (coluna vector(1536)) — pista à esquerda, resto zero."""
    out = list(vals) + [0.0] * (1536 - len(vals))
    return "[" + ",".join(repr(float(x)) for x in out) + "]"


# Gold sintético da projeção — espelha a fixture unitária (cosseno e1↔e2=0.9759,
# Jaccard de tecnologia=1/3, hub multissetorial). source `kgp1a` = limpeza segura.
_SEED_ENTITIES = [
    {"kind": "edital", "source": "kgp1a", "native_id": "edital_1",
     "name": "Chamada Agro IA (KG-P1A)", "description": "Edital sintético de agro e IA",
     "setores": ["Agro", "Multissetorial"], "tecnologias_tags": ["Inteligência Artificial", "IoT"],
     "uf": "SP", "mecanismo": "subvencao",
     "constraints": [{"tipo": "trl", "op": "gte", "valor": 4}],
     "metadata": {"estagio_alvo": ["seed"]}, "embedding": _v1536(1, 0, 0)},
    {"kind": "ict", "source": "kgp1a", "native_id": "ict_1",
     "name": "Unidade IA (KG-P1A)", "description": "ICT sintética de IA",
     "setores": ["Agro"], "tecnologias_tags": ["Inteligência Artificial", "robótica"],
     "uf": "SC", "mecanismo": None, "constraints": [], "metadata": {},
     "embedding": _v1536(1, 0.2, 0.1)},
    {"kind": "edital", "source": "kgp1a", "native_id": "edital_2",
     "name": "Chamada Biotec (KG-P1A)", "description": "Edital sintético de biotecnologia",
     "setores": ["Saúde"], "tecnologias_tags": ["biotecnologia"],
     "uf": None, "mecanismo": "bolsa", "constraints": [], "metadata": {},
     "embedding": _v1536(0, 1, 0)},
    {"kind": "agencia", "source": "kgp1a", "native_id": "agencia_1",
     "name": "AGENCIA KG-P1A", "description": "Agência sintética",
     "setores": [], "tecnologias_tags": [], "uf": None, "mecanismo": None,
     "constraints": [], "metadata": {}, "embedding": None},
]


def _skip_reason() -> str | None:
    target = os.environ.get("INTEGRATION_TARGET", "").strip().lower()
    if target != "local":
        return "kg_phase1 — exige INTEGRATION_TARGET=local (Supabase local)"
    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn:
        return "kg_phase1 — DATABASE_URL ausente"
    host = (urlparse(dsn).hostname or "").lower()
    if host not in {"localhost", "127.0.0.1", "::1"}:
        return "kg_phase1 — DATABASE_URL não aponta para localhost (proibido remoto)"
    return None


def _seed_gold(conn) -> None:
    """Insere as entidades sintéticas + relação estrutural no gold local."""
    with conn.cursor() as cur:
        ids: dict[str, str] = {}
        for row in _SEED_ENTITIES:
            cur.execute(
                "insert into public.entities (kind, source, native_id, name, description, "
                "setores, tecnologias_tags, uf, mecanismo, constraints, metadata, embedding) "
                "values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s::vector) "
                "returning id",
                (row["kind"], row["source"], row["native_id"], row["name"], row["description"],
                 row["setores"], row["tecnologias_tags"], row["uf"], row["mecanismo"],
                 json.dumps(row["constraints"]), json.dumps(row["metadata"]), row["embedding"]),
            )
            ids[row["native_id"]] = cur.fetchone()[0]
        cur.execute(
            "insert into public.entity_relationships (source_id, target_id, type) "
            "values (%s, %s, 'operado_por')",
            (ids["edital_1"], ids["agencia_1"]),
        )


def _truncate_projection(cur) -> None:
    cur.execute(
        "truncate kg_phase1.generations, kg_phase1.nodes, kg_phase1.quality_nodes, "
        "kg_phase1.edges, kg_phase1.communities cascade"
    )


@pytest.fixture
def kg_db():
    """Conexão com o Postgres local, schema `kg_phase1` zerado e gold sintético
    semeado. Sempre valida a sentinela local ANTES de escrever."""
    reason = _skip_reason()
    if reason:
        pytest.skip(reason)
    assert_database_target("kg_phase1 integration test")
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        with conn.cursor() as cur:
            _truncate_projection(cur)
            cur.execute("delete from public.entities where source = 'kgp1a'")
        conn.commit()
        _seed_gold(conn)
        conn.commit()
        yield conn
        with conn.cursor() as cur:
            cur.execute("delete from public.entities where source = 'kgp1a'")
            _truncate_projection(cur)
        conn.commit()


@pytest.mark.skipif(_skip_reason() is not None, reason=_skip_reason() or "")
class TestKgPhase1ProjectionLocal:
    def test_migration_applied_and_objects_exist(self, kg_db):
        """(1) migration executável: schema, tabelas e índice parcial existem."""
        with kg_db.cursor() as cur:
            for table in ("generations", "nodes", "quality_nodes", "edges", "communities"):
                cur.execute("select to_regclass(%s)", (f"kg_phase1.{table}",))
                assert cur.fetchone()[0] is not None, f"kg_phase1.{table} ausente"
            cur.execute(
                "select count(*) from pg_indexes where schemaname='kg_phase1' "
                "and indexname='kg_phase1_generations_one_current'"
            )
            assert cur.fetchone()[0] == 1
            cur.execute(
                "select count(*) from information_schema.columns "
                "where table_schema='kg_phase1' and table_name='edges' and column_name='origin'"
            )
            assert cur.fetchone()[0] == 1

    def test_first_build_creates_healthy_current_generation(self, kg_db):
        """(2) primeiro build → geração `healthy` e `is_current=true`."""
        out = ingest.build(run_communities=True)
        assert out["skipped"] is False
        with kg_db.cursor() as cur:
            cur.execute(
                "select status, is_current, source_hash, counts from kg_phase1.generations "
                "where id = %s",
                (out["generation"],),
            )
            status, is_current, source_hash, counts = cur.fetchone()
        assert status == "healthy"
        assert is_current is True
        assert source_hash == out["source_hash"]
        assert counts["nodes"] >= 4
        assert counts["edges"] > 0
        assert counts["communities"] >= 1

    def test_build_without_change_is_skipped(self, kg_db):
        """(3) build sem mudança no gold → pulado (mesma geração corrente)."""
        first = ingest.build(run_communities=False)
        second = ingest.build(run_communities=False)
        assert second["skipped"] is True
        assert second["generation"] == first["generation"]

    def test_forced_build_creates_new_generation_and_single_current(self, kg_db):
        """(4)+(5) build forçado cria nova geração e EXATAMENTE UMA é corrente."""
        first = ingest.build(run_communities=False)
        second = ingest.build(skip_unchanged=False, run_communities=False)
        assert second["generation"] != first["generation"]
        with kg_db.cursor() as cur:
            cur.execute("select id from kg_phase1.generations where is_current")
            current_ids = [r[0] for r in cur.fetchall()]
        assert current_ids == [second["generation"]]

    def test_readers_see_only_current_generation(self, kg_db):
        """(6) leitores resolvem a corrente; a geração antiga não vaza."""
        first = ingest.build(run_communities=False)
        second = ingest.build(skip_unchanged=False, run_communities=False)
        cur_gen = store.current_generation(conn=kg_db)
        assert cur_gen["id"] == second["generation"]
        nodes = store.load_nodes(conn=kg_db)
        assert nodes  # não-vazio
        assert store.load_edges(conn=kg_db)
        # a antiga ainda é consultável por id, mas NÃO é a corrente
        old_nodes = store.load_nodes(generation_id=first["generation"], conn=kg_db)
        assert old_nodes == nodes
        assert store.current_generation(conn=kg_db)["id"] != first["generation"]

    def test_load_snapshot_real_pg_no_syntax_error(self, kg_db):
        """(KG-P1B-1 achado 1) `load_snapshot()` SEM injetar conexão, contra o
        Postgres REAL: a geração corrente e TODOS os componentes carregam e o
        timeout não causa erro de sintaxe (set_config parametrizado)."""
        out = ingest.build(run_communities=True)
        snap = store.load_snapshot()
        assert snap is not None
        assert snap.generation_id == out["generation"]
        assert snap.nodes, "nós devem carregar"
        assert snap.quality_nodes, "quality nodes devem carregar"
        assert snap.edges, "arestas devem carregar"
        assert snap.communities, "comunidades devem carregar"
        # os ids lidos pertencem à geração corrente (nunca mistura gerações)
        with kg_db.cursor() as cur:
            cur.execute(
                "select id, kind, native_id, name from kg_phase1.nodes "
                "where generation_id = %s order by id",
                (snap.generation_id,),
            )
            expected = [dict(zip(("id", "kind", "native_id", "name"), r, strict=True))
                        for r in cur.fetchall()]
        assert len(snap.nodes) == len(expected)
        assert all(n["id"] in {e["id"] for e in expected} for n in snap.nodes)

    def test_failure_rolls_back_and_keeps_previous_healthy(self, kg_db, monkeypatch):
        """(7)+(8)+(9) falha após iniciar a nova geração → rollback, saudável
        anterior corrente, ledger `failed` categórico e sanitizado."""
        healthy = ingest.build(run_communities=False)
        secret = (
            "postgresql://user:pass@db.internal:5432/radar SEGREDO_BRUTO "
            "https://evil.example/leak SELECT * FROM entities WHERE 1=1 trecho confidencial"
        )

        def _boom(cur, generation_id, edges):
            raise RuntimeError(secret)

        monkeypatch.setattr(ingest, "_insert_edges", _boom)
        with pytest.raises(RuntimeError):
            ingest.build(skip_unchanged=False, run_communities=False)

        # geração saudável anterior permanece corrente
        cur_gen = store.current_generation(conn=kg_db)
        assert cur_gen["id"] == healthy["generation"]

        with kg_db.cursor() as cur:
            cur.execute(
                "select error from kg_phase1.generations where status = 'failed' "
                "order by id desc limit 1"
            )
            error_field = cur.fetchone()[0]
            cur.execute("select count(*) from kg_phase1.generations where status = 'building'")
            n_building = cur.fetchone()[0]
            cur.execute(
                "select count(*) from kg_phase1.generations "
                "where status = 'failed' and error = 'unexpected_error:RuntimeError'"
            )
            n_categorical_failed = cur.fetchone()[0]

        assert error_field == "unexpected_error:RuntimeError"
        assert n_building == 0  # a linha 'building' rolou junto com a transação
        assert n_categorical_failed == 1
        for marker in ("postgresql://", "user:pass", "SEGREDO_BRUTO", "https://",
                       "evil.example", "SELECT", "confidencial"):
            assert marker not in error_field

    def test_vector_jsonb_fk_partial_index_and_swap_on_real_pg(self, kg_db):
        """(10) vector, JSONB, FK, índice parcial e swap funcionam no Postgres real."""
        out = ingest.build(run_communities=True)
        gen_id = out["generation"]
        with kg_db.cursor() as cur:
            # vector(1536): embedding armazenado e legível
            cur.execute(
                "select embedding::text from kg_phase1.nodes "
                "where generation_id = %s and id = 'edital:edital_1'",
                (gen_id,),
            )
            emb = cur.fetchone()[0]
            assert emb is not None
            assert emb.startswith("[") and len(emb.split(",")) == 1536
            # JSONB: counts e properties
            cur.execute("select counts from kg_phase1.generations where id = %s", (gen_id,))
            counts = cur.fetchone()[0]
            assert isinstance(counts, dict) and counts["nodes"] > 0
            cur.execute(
                "select properties from kg_phase1.edges "
                "where generation_id = %s and type = 'similar_a' limit 1",
                (gen_id,),
            )
            props = cur.fetchone()[0]
            assert isinstance(props, dict) and props.get("derived") is True
            # comunidades persistidas (FK real para generations)
            cur.execute(
                "select count(*) from kg_phase1.communities where generation_id = %s", (gen_id,)
            )
            assert cur.fetchone()[0] > 0

        # FK real: nó de geração inexistente viola a FK
        with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
            with pytest.raises(psycopg.errors.ForeignKeyViolation):
                with conn.transaction():
                    conn.execute(
                        "insert into kg_phase1.nodes (generation_id, id, kind, native_id, name) "
                        "values (999999, 'x', 'x', 'x', 'x')"
                    )

        # índice parcial: não deixa duas gerações correntes coexistirem. O build
        # já deixou uma corrente — zera is_current antes do probe.
        with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
            with conn.transaction():
                conn.execute("update kg_phase1.generations set is_current = false where is_current")
                conn.execute(
                    "insert into kg_phase1.generations (status, is_current, build_version) "
                    "values ('healthy', true, 'probe')"
                )
            with pytest.raises(psycopg.errors.UniqueViolation):
                with conn.transaction():
                    conn.execute(
                        "insert into kg_phase1.generations (status, is_current, build_version) "
                        "values ('healthy', true, 'probe2')"
                    )
