"""Fixture de eval local — copia o corpus dos editais do golden cloud → local.

CONTRATO DE SEGURANÇA:
  • cloud é READ-ONLY (só SELECT/COPY TO STDOUT); toda escrita é no Postgres LOCAL.
  • idempotente: apaga as linhas-alvo no local e recopia (re-executável à vontade).
  • NÃO toca a identidade de eval (user auth.users + workspace) — essa é fixture
    de schema em supabase/seed.sql (sobrevive a `supabase db reset`). Este script
    é só o CORPUS (chunks + source_docs), que é grande demais p/ seed.sql.

Por quê existe: o gate de writing (writing_v2) usa finep:769/774, cujo corpus só
existe em cloud. A regra é rodar eval SEMPRE contra o Postgres local (:54322) —
ver memória feedback_eval_runs_local. Este script materializa esse corpus local.

Uso:
    python scripts/seed_eval_corpus.py
Requer no .env: DATABASE_URL de cloud (pooler). O local é fixo em :54322.
Usa COPY (formato texto do Postgres) → tipos vector/jsonb copiados fielmente.
"""
from __future__ import annotations

import sys

import psycopg

LOCAL = "postgresql://postgres:postgres@127.0.0.1:54322/postgres"
EDITAL_IDS = ("finep:769", "finep:774")
# literal seguro p/ o COPY (COPY não aceita placeholders); ids são constantes nossas
_ID_LITERAL = "(" + ",".join(f"'{e}'" for e in EDITAL_IDS) + ")"
TABLES = ("edital_source_docs", "edital_chunks")


def _cloud_dsn() -> str:
    for line in open(".env"):
        if line.startswith("DATABASE_URL=") and "pooler" in line:
            return line.split("=", 1)[1].strip().strip('"')
    raise SystemExit("DATABASE_URL de cloud (pooler) não encontrada no .env")


def _cols(cur, table: str) -> list[str]:
    # Exclui colunas GERADAS (ex.: edital_chunks.text_search tsvector) — COPY não
    # aceita inserir nelas; são recomputadas pelo Postgres a partir das demais.
    cur.execute(
        "select column_name from information_schema.columns "
        "where table_name=%s and table_schema='public' "
        "and is_generated <> 'ALWAYS' order by ordinal_position",
        (table,),
    )
    return [r[0] for r in cur.fetchall()]


def _copy_table(cloud, local, table: str) -> None:
    """Copia (por edital_id) uma tabela de corpus cloud→local, idempotente."""
    with local.cursor() as lc:
        collist = _cols(lc, table)
    clist = ", ".join(f'"{c}"' for c in collist)
    with cloud.cursor() as ccur, local.cursor() as lcur:
        lcur.execute(f"delete from {table} where edital_id = ANY(%s)", (list(EDITAL_IDS),))
        sel = f"COPY (SELECT {clist} FROM {table} WHERE edital_id IN {_ID_LITERAL}) TO STDOUT"
        with ccur.copy(sel) as cout, lcur.copy(f"COPY {table} ({clist}) FROM STDIN") as cin:
            for data in cout:
                cin.write(data)
    local.commit()


def _copy_edital_entities(cloud, local) -> None:
    """Copia os NÓS DE EDITAL do KG (tabela `entities`, keyed por native_id) —
    fonte do 'source card' do agente (core.kg.entity_catalog.get_edital). Sem
    isso o card fica vazio e o agente floora (saved≈0) — foi o artefato de
    ambiente do 1º A/B local. Só os nós de edital: os alvos das relations
    (agências/programas) degradam gracioso e colidem com curadoria já local
    (chave única source,native_id), então não os copiamos."""
    with local.cursor() as lc:
        collist = _cols(lc, "entities")  # exclui a coluna gerada `type`
    clist = ", ".join(f'"{c}"' for c in collist)
    with cloud.cursor() as ccur, local.cursor() as lcur:
        lcur.execute(f"delete from entities where native_id IN {_ID_LITERAL} and kind='edital'")
        sel = (
            f"COPY (SELECT {clist} FROM entities "
            f"WHERE native_id IN {_ID_LITERAL} AND kind='edital') TO STDOUT"
        )
        with ccur.copy(sel) as cout, lcur.copy(f"COPY entities ({clist}) FROM STDIN") as cin:
            for data in cout:
                cin.write(data)
    local.commit()


def main() -> int:
    cloud_dsn = _cloud_dsn()
    with psycopg.connect(cloud_dsn, connect_timeout=20) as cloud, \
            psycopg.connect(LOCAL) as local:
        for table in TABLES:
            _copy_table(cloud, local, table)
        _copy_edital_entities(cloud, local)  # nós de edital do KG → source card
        # verificação
        with local.cursor() as lc:
            for eid in EDITAL_IDS:
                lc.execute("select count(*) from edital_chunks where edital_id=%s", (eid,))
                ch = lc.fetchone()[0]
                lc.execute("select count(*) from edital_source_docs where edital_id=%s", (eid,))
                sd = lc.fetchone()[0]
                lc.execute(
                    "select vector_dims(embedding) from edital_chunks "
                    "where edital_id=%s and embedding is not null limit 1",
                    (eid,),
                )
                row = lc.fetchone()
                dim = row[0] if row else "NULL"
                lc.execute(
                    "select count(*) from entities where native_id=%s and kind='edital'", (eid,),
                )
                node = lc.fetchone()[0]
                print(f"  {eid}: chunks={ch} source_docs={sd} embedding_dim={dim} kg_node={node}")
                if not ch or dim != 1536 or not node:
                    print(f"AVISO: {eid} incompleto (chunks={ch}, dim={dim}, kg_node={node})")
    print("seed_eval_corpus: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
