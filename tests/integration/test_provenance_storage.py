"""RT01-T04 — persistência gold aditiva (migration 042, spec
docs/specs/radar-data-trust-01-provenance.md §6/§9.1).

Prova que o schema aditivo está pronto para o dual-write de proveniência sem
introduzir nenhum consumidor ou produtor novo:

  - `entities.provenance` / `entity_relationships.provenance` (JSONB, default
    `{}`) fazem round-trip sem perda com um `FactProvenance` REAL
    (`radar.domain.provenance`);
  - registros sem provenance explícita ficam com o default `{}` — "legado"
    identificável, nunca `state=stated` implícito;
  - `match_chunks.document/page/silver_block_idx/source_hash` (nullable)
    aceitam NULL (chunk legado) e round-trip íntegro quando preenchidos;
  - o SQL literal de `gold._ENTITY_UPSERT` continua funcionando (upsert
    compatível) após a migration, executado 2x com os mesmos params.

Integração REAL contra o Postgres LOCAL (:54322), gated em runtime: pula com
mensagem clara se a conexão falhar. TODA sonda roda dentro de uma transação
revertida (`rollback()` no teardown do fixture, sempre) — zero resíduo no
banco, mesmo se um teste falhar no meio. Sem rede, sem LLM; psycopg direto.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone

import pytest

# A suíte comum (tests/conftest.py) zera DATABASE_URL por padrão (isolamento
# hermético) a menos que INTEGRATION_TARGET esteja setado. Este teste é gated
# por SONDA DE CONECTIVIDADE em runtime, não por presença de env var — cai
# para o DSN local padrão quando a env está ausente/vazia, exatamente o que
# `pytest tests/integration/test_provenance_storage.py` (sem env extra) faz.
DSN = os.environ.get("DATABASE_URL") or "postgresql://postgres:postgres@127.0.0.1:54322/postgres"


def _skip_reason() -> str | None:
    """Sonda de conectividade em runtime — sem side effects, sem deixar
    conexão aberta."""
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

from radar.core.kg import gold  # noqa: E402
from radar.domain.provenance import (  # noqa: E402
    EvidenceRef,
    FactProvenance,
    FactState,
    LocatorQuality,
    ProducerInfo,
    ProducerKind,
)

_DUMMY_EMB = [0.001] * 1536  # 1536d — mesma convenção de test_company_chunks_rls.py


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


def _real_fact_provenance() -> FactProvenance:
    """FactProvenance REAL (não um dict solto): producer.kind=adapter, um
    EvidenceRef com silver_source_hash no formato `"md5:<hex>"` (mesma
    convenção usada por evidence_resolver.py / RT01-T03)."""
    quote = "Poderao participar empresas brasileiras de pequeno porte."
    digest = hashlib.md5(quote.encode("utf-8")).hexdigest()
    evidence = EvidenceRef(
        source="finep",
        native_id="745",
        edital_id="finep:745",
        document="Edital.pdf",
        page=17,
        block_idx=143,
        section_path=["7. Elegibilidade", "7.2 Proponentes"],
        quote=quote,
        silver_source_hash=f"md5:{digest}",
        locator_quality=LocatorQuality.EXACT,
    )
    return FactProvenance(
        state=FactState.STATED,
        evidence_refs=[evidence],
        producer=ProducerInfo(kind=ProducerKind.ADAPTER, name="finep_adapter", version="1"),
        updated_at=datetime.now(timezone.utc),
    )


def _insert_minimal_entity(cur, native_id: str) -> str:
    """Insert mínimo que NÃO menciona `provenance` — a coluna deve cair no
    default `'{}'::jsonb` do schema, sem exigir que o caller a conheça."""
    cur.execute(
        """
        insert into public.entities (kind, source, native_id, name, description)
        values ('edital', 'test_source', %s, 'Edital de Teste RT01-T04', '')
        returning id, provenance
        """,
        (native_id,),
    )
    row = cur.fetchone()
    return row[0], row[1]


class TestEntitiesProvenanceDefault:
    def test_insert_without_provenance_gets_empty_default(self, pg_conn):
        cur = pg_conn.cursor()
        entity_id, provenance = _insert_minimal_entity(cur, "rt01t04:legacy-entity")
        assert provenance == {}, "registro sem provenance explícita deve ficar '{}'::jsonb (legado)"

        cur.execute("select provenance from public.entities where id = %s", (entity_id,))
        assert cur.fetchone()[0] == {}


class TestEntitiesProvenanceRoundTrip:
    def test_update_and_select_roundtrip_real_fact_provenance(self, pg_conn):
        cur = pg_conn.cursor()
        entity_id, _ = _insert_minimal_entity(cur, "rt01t04:roundtrip-entity")

        fp = _real_fact_provenance()
        payload = json.dumps(fp.model_dump(mode="json"), ensure_ascii=False)

        cur.execute(
            "update public.entities set provenance = %s::jsonb where id = %s",
            (payload, entity_id),
        )
        cur.execute("select provenance from public.entities where id = %s", (entity_id,))
        stored = cur.fetchone()[0]

        assert stored == fp.model_dump(mode="json")
        # round-trip completo: reconstrói o tipo real a partir do JSON lido do banco
        rebuilt = FactProvenance.model_validate(stored)
        assert rebuilt == fp


class TestEntityRelationshipsProvenanceRoundTrip:
    def test_relationship_provenance_roundtrip(self, pg_conn):
        cur = pg_conn.cursor()
        source_id, _ = _insert_minimal_entity(cur, "rt01t04:rel-source")
        target_id, _ = _insert_minimal_entity(cur, "rt01t04:rel-target")

        cur.execute(
            """
            insert into public.entity_relationships (source_id, target_id, type)
            values (%s, %s, 'operado_por')
            returning id, provenance
            """,
            (source_id, target_id),
        )
        rel_id, default_provenance = cur.fetchone()
        assert default_provenance == {}, "aresta sem provenance explícita deve ficar '{}'::jsonb"

        fp = _real_fact_provenance()
        payload = json.dumps(fp.model_dump(mode="json"), ensure_ascii=False)
        cur.execute(
            "update public.entity_relationships set provenance = %s::jsonb where id = %s",
            (payload, rel_id),
        )
        cur.execute("select provenance from public.entity_relationships where id = %s", (rel_id,))
        stored = cur.fetchone()[0]

        assert stored == fp.model_dump(mode="json")
        assert FactProvenance.model_validate(stored) == fp


class TestMatchChunksNewColumns:
    def test_insert_without_new_columns_all_null(self, pg_conn):
        cur = pg_conn.cursor()
        entity_id, _ = _insert_minimal_entity(cur, "rt01t04:chunk-legacy-entity")

        cur.execute(
            """
            insert into public.match_chunks (entity_id, idx, text, embedding)
            values (%s, 0, 'chunk legado sem coordenadas novas', %s::vector)
            returning document, page, silver_block_idx, source_hash
            """,
            (entity_id, gold._vec(_DUMMY_EMB)),
        )
        document, page, silver_block_idx, source_hash = cur.fetchone()
        assert document is None
        assert page is None
        assert silver_block_idx is None
        assert source_hash is None

    def test_insert_with_new_columns_roundtrip(self, pg_conn):
        cur = pg_conn.cursor()
        entity_id, _ = _insert_minimal_entity(cur, "rt01t04:chunk-new-entity")

        expected = {
            "document": "Edital.pdf",
            "page": 17,
            "silver_block_idx": 143,
            "source_hash": "md5:07e1eb514054a9979928ad6f6f824dc7",
        }
        cur.execute(
            """
            insert into public.match_chunks
              (entity_id, idx, text, embedding, document, page, silver_block_idx, source_hash)
            values (%s, 0, 'chunk com coordenadas de proveniencia', %s::vector, %s, %s, %s, %s)
            returning document, page, silver_block_idx, source_hash
            """,
            (
                entity_id,
                gold._vec(_DUMMY_EMB),
                expected["document"],
                expected["page"],
                expected["silver_block_idx"],
                expected["source_hash"],
            ),
        )
        document, page, silver_block_idx, source_hash = cur.fetchone()
        assert document == expected["document"]
        assert page == expected["page"]
        assert silver_block_idx == expected["silver_block_idx"]
        assert source_hash == expected["source_hash"]


class TestGoldEntityUpsertCompatibility:
    def test_entity_upsert_sql_literal_runs_twice_after_migration(self, pg_conn):
        """O SQL literal de `gold._ENTITY_UPSERT` (colunas nomeadas, sem
        `provenance`) continua funcionando após a migration aditiva — upsert
        idempotente por (source, native_id), mesmo com a coluna nova presente
        na tabela mas ausente da lista de colunas do INSERT."""
        cur = pg_conn.cursor()
        params = {
            "kind": "edital",
            "source": "rt01t04_test_source",
            "native_id": "rt01t04:upsert-compat",
            "name": "Edital de Teste — upsert compat",
            "description": "descricao de teste",
            "mecanismo": None,
            "formato": None,
            "setores": [],
            "tags": [],
            "status": None,
            "deadline": None,
            "uf": None,
            "ticket_min": None,
            "ticket_max": None,
            "constraints": json.dumps([]),
            "requisitos": [],
            "curated": False,
            "verificado": None,
            "metadata": json.dumps({}),
            "embedding": gold._vec(_DUMMY_EMB),
        }

        cur.execute(gold._ENTITY_UPSERT, params)
        id_first = cur.fetchone()[0]

        cur.execute(gold._ENTITY_UPSERT, params)
        id_second = cur.fetchone()[0]

        assert id_first == id_second, "upsert por (source, native_id) deve resolver para a mesma linha"

        # a linha existe, provenance permanece no default '{}' (o upsert não
        # menciona a coluna nova — comportamento aditivo intocado)
        cur.execute("select provenance from public.entities where id = %s", (id_first,))
        assert cur.fetchone()[0] == {}
