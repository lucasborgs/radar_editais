"""RT01-T05 — dual-write real das colunas de proveniência (finep, migration
042, spec docs/specs/radar-data-trust-01-provenance.md §6).

Prova que o CÓDIGO de dual-write (`gold._upsert_entity`, `gold._upsert_rel`,
`gold._replace_match_chunks`) grava de fato os valores esperados no Postgres
local, além do round-trip de schema já coberto por T04
(`tests/integration/test_provenance_storage.py`):

  - `_upsert_entity` grava `provenance` não vazia quando passada;
  - re-upsert (`on conflict`) com `provenance='{}'` NÃO apaga a proveniência
    já gravada (guard anti-clobber do `_ENTITY_UPSERT`);
  - `_upsert_rel` grava `provenance` só no INSERT (`on conflict do nothing`
    preserva a aresta existente, `provenance` incluída);
  - `_replace_match_chunks` persiste as 4 colunas novas
    (`document`/`page`/`silver_block_idx`/`source_hash`) quando o dict do
    chunk as carrega.

Integração REAL contra o Postgres LOCAL (:54322), gated em runtime (mesmo
padrão de `test_provenance_storage.py`): pula com mensagem clara se a conexão
falhar. TODA sonda roda dentro de uma transação revertida (`rollback()` no
teardown do fixture, sempre) — zero resíduo no banco, mesmo se um teste
falhar no meio. Sem rede, sem LLM; psycopg direto, `gold._upsert_entity`/
`_upsert_rel`/`_replace_match_chunks` chamados diretamente (não via
`ingest_all`).
"""
from __future__ import annotations

import os

import pytest

# Mesmo gate por sonda de conectividade de test_provenance_storage.py — a
# suíte comum (tests/conftest.py) zera DATABASE_URL por padrão.
DSN = os.environ.get("DATABASE_URL") or "postgresql://postgres:postgres@127.0.0.1:54322/postgres"


def _skip_reason() -> str | None:
    try:
        import psycopg

        with psycopg.connect(DSN, connect_timeout=3) as conn:
            with conn.cursor() as cur:
                cur.execute("select 1")
                cur.fetchone()
        return None
    except Exception as e:  # noqa: BLE001 — qualquer falha de conectividade é motivo de skip
        return f"Postgres local ({DSN}) não respondeu: {e!r}"


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(_skip_reason() is not None, reason=_skip_reason() or ""),
]

import psycopg  # noqa: E402  (só importa quando o gate passa)

from radar.core.kg import gold, provenance_writer  # noqa: E402
from radar.domain.provenance import FactProvenance  # noqa: E402

_DUMMY_EMB = [0.001] * 1536


@pytest.fixture
def pg_conn():
    """Conexão dedicada, transação nunca commitada — SEMPRE revertida no
    teardown, mesmo se o teste falhar/lançar. Zero resíduo garantido."""
    conn = psycopg.connect(DSN)
    conn.autocommit = False
    try:
        yield conn
    finally:
        conn.rollback()
        conn.close()


def _insert_minimal_entity(cur, native_id: str, provenance: dict | None = None) -> str:
    import json

    cur.execute(
        """
        insert into public.entities (kind, source, native_id, name, description, provenance)
        values ('edital', 'test_source', %s, 'Edital de Teste RT01-T05', '', %s::jsonb)
        returning id
        """,
        (native_id, json.dumps(provenance or {})),
    )
    return cur.fetchone()[0]


class TestUpsertEntityWritesProvenance:
    def test_upsert_entity_persists_non_empty_provenance(self, pg_conn):
        cur = pg_conn.cursor()
        prov = provenance_writer.build_edital_fact_provenance(
            status="aberta", mecanismo="subvencao", constraints=[], requisitos_texto=[],
            blocks=[], source="finep", native_id="rt01t05-upsert", edital_id="finep:rt01t05-upsert",
            silver_source_hash=None, tagger_model="gpt-4o-mini", constraints_model="gpt-4o-mini",
        )
        eid = gold._upsert_entity(
            cur, kind="edital", source="finep", native_id="rt01t05:upsert-entity",
            name="Edital dual-write", description="", provenance=prov, embedding=_DUMMY_EMB,
        )
        cur.execute("select provenance from public.entities where id = %s", (eid,))
        stored = cur.fetchone()[0]
        assert stored == prov
        for _path, payload in stored.items():
            FactProvenance.model_validate(payload)


class TestUpsertEntityAntiClobberGuard:
    def test_reupsert_with_empty_provenance_does_not_clear_existing(self, pg_conn):
        cur = pg_conn.cursor()
        prov = provenance_writer.build_status_provenance("aberta").model_dump(mode="json")
        eid1 = gold._upsert_entity(
            cur, kind="edital", source="finep", native_id="rt01t05:anticlobber",
            name="v1", description="", provenance={"status": prov}, embedding=_DUMMY_EMB,
        )
        cur.execute("select provenance from public.entities where id = %s", (eid1,))
        assert cur.fetchone()[0] == {"status": prov}

        # re-upsert do MESMO (source, native_id) sem passar provenance
        # (default '{}' em _upsert_entity) — guard anti-clobber deve manter
        # a proveniência já gravada, não apagá-la.
        eid2 = gold._upsert_entity(
            cur, kind="edital", source="finep", native_id="rt01t05:anticlobber",
            name="v2", description="", embedding=_DUMMY_EMB,
        )
        assert eid1 == eid2
        cur.execute("select name, provenance from public.entities where id = %s", (eid1,))
        name, provenance = cur.fetchone()
        assert name == "v2", "campos normais continuam sendo sobrescritos pelo upsert"
        assert provenance == {"status": prov}, "provenance NÃO deve ser apagada por um re-upsert vazio"

    def test_reupsert_with_non_empty_provenance_replaces(self, pg_conn):
        cur = pg_conn.cursor()
        prov_v1 = {"status": provenance_writer.build_status_provenance("aberta").model_dump(mode="json")}
        prov_v2 = {"mecanismo": provenance_writer.build_mecanismo_provenance().model_dump(mode="json")}
        eid1 = gold._upsert_entity(
            cur, kind="edital", source="finep", native_id="rt01t05:replace",
            name="v1", description="", provenance=prov_v1, embedding=_DUMMY_EMB,
        )
        eid2 = gold._upsert_entity(
            cur, kind="edital", source="finep", native_id="rt01t05:replace",
            name="v2", description="", provenance=prov_v2, embedding=_DUMMY_EMB,
        )
        assert eid1 == eid2
        cur.execute("select provenance from public.entities where id = %s", (eid1,))
        assert cur.fetchone()[0] == prov_v2, "provenance não vazia substitui integralmente (não faz merge)"


class TestUpsertRelWritesProvenanceOnCreation:
    def test_upsert_rel_persists_provenance_on_insert(self, pg_conn):
        cur = pg_conn.cursor()
        source_id = _insert_minimal_entity(cur, "rt01t05:rel-source")
        target_id = _insert_minimal_entity(cur, "rt01t05:rel-target")

        prov = provenance_writer.build_operado_por_provenance().model_dump(mode="json")
        gold._upsert_rel(cur, source_id, target_id, "operado_por", provenance=prov)

        cur.execute(
            "select provenance from public.entity_relationships "
            "where source_id=%s and target_id=%s and type='operado_por'",
            (source_id, target_id),
        )
        stored = cur.fetchone()[0]
        assert stored == prov
        assert FactProvenance.model_validate(stored).state == "inferred"

    def test_upsert_rel_existing_edge_not_updated_by_second_call(self, pg_conn):
        """`on conflict do nothing` — uma aresta já criada não é atualizada
        por uma segunda chamada, mesmo com provenance diferente (spec da
        task: "edge existente não atualiza")."""
        cur = pg_conn.cursor()
        source_id = _insert_minimal_entity(cur, "rt01t05:rel-source-2")
        target_id = _insert_minimal_entity(cur, "rt01t05:rel-target-2")

        prov1 = provenance_writer.build_operado_por_provenance().model_dump(mode="json")
        gold._upsert_rel(cur, source_id, target_id, "operado_por", provenance=prov1)

        prov2 = provenance_writer.build_subordinado_a_provenance().model_dump(mode="json")
        gold._upsert_rel(cur, source_id, target_id, "operado_por", provenance=prov2)

        cur.execute(
            "select provenance from public.entity_relationships "
            "where source_id=%s and target_id=%s and type='operado_por'",
            (source_id, target_id),
        )
        rows = cur.fetchall()
        assert len(rows) == 1, "on conflict do nothing não deve duplicar a aresta"
        assert rows[0][0] == prov1, "segunda chamada não deve atualizar a provenance da aresta existente"


class TestReplaceMatchChunksPersistsCoords:
    def test_replace_match_chunks_writes_new_columns(self, pg_conn):
        cur = pg_conn.cursor()
        entity_id = _insert_minimal_entity(cur, "rt01t05:chunk-entity")

        chunk_hash = "md5:07e1eb514054a9979928ad6f6f824dc7"
        chunks = [
            {
                "section_path": ["1. Objeto"], "kind": "paragraph", "text": "bloco 1",
                "document": "Edital.pdf", "page": 1, "silver_block_idx": 3, "source_hash": chunk_hash,
            },
            {
                "section_path": ["2. Elegibilidade"], "kind": "paragraph", "text": "bloco 2",
                # sem coordenadas (simula chunk cujo helper devolveu {} — legado dentro do mesmo edital)
            },
        ]
        n = gold._replace_match_chunks(cur, entity_id, chunks, [_DUMMY_EMB, _DUMMY_EMB])
        assert n == 2

        cur.execute(
            "select idx, document, page, silver_block_idx, source_hash "
            "from public.match_chunks where entity_id = %s order by idx",
            (entity_id,),
        )
        rows = cur.fetchall()
        assert rows[0] == (0, "Edital.pdf", 1, 3, chunk_hash)
        assert rows[1] == (1, None, None, None, None)

    def test_replace_match_chunks_deletes_previous_rows(self, pg_conn):
        cur = pg_conn.cursor()
        entity_id = _insert_minimal_entity(cur, "rt01t05:chunk-replace")

        gold._replace_match_chunks(
            cur, entity_id,
            [{"section_path": [], "kind": None, "text": "v1", "document": "A.pdf", "page": 1,
              "silver_block_idx": 0, "source_hash": "md5:aaa"}],
            [_DUMMY_EMB],
        )
        gold._replace_match_chunks(
            cur, entity_id,
            [{"section_path": [], "kind": None, "text": "v2"}],
            [_DUMMY_EMB],
        )
        cur.execute(
            "select text, document, source_hash from public.match_chunks where entity_id = %s",
            (entity_id,),
        )
        rows = cur.fetchall()
        assert rows == [("v2", None, None)]
