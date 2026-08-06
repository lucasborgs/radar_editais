"""core/kg/spike/graph_store.py — acesso SQL ao schema `kg_spike` (isolado).

Camada de armazenamento do spike (SPEC §7). Toda escrita é no schema `kg_spike`,
nunca no `public`. Acesso via psycopg (DATABASE_URL) — mesmo motivo de
`match_v3`/`company_chunks`: supabase-py corrompe colunas `vector`.

O schema é auto-criado por DDL idempotente (`init_schema`); para remover o
spike por completo basta `DROP SCHEMA kg_spike CASCADE`.
"""
from __future__ import annotations

import logging
import os
from typing import Any

import psycopg

logger = logging.getLogger(__name__)

SCHEMA = "kg_spike"


def get_dsn() -> str:
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise RuntimeError("DATABASE_URL não configurada — o spike precisa de acesso direto ao Postgres.")
    return dsn


def connect() -> psycopg.Connection:
    return psycopg.connect(get_dsn(), autocommit=True)


# ─────────────────────────────────────────────────────────────────────────────
# DDL idempotente
# ─────────────────────────────────────────────────────────────────────────────

_DDL = f"""
create schema if not exists {SCHEMA};

-- Espelho das entidades do gold (substâncias).
create table if not exists {SCHEMA}.nodes (
    id         text primary key,           -- <kind>:<native_id> (ex.: edital:finep:589)
    kind       text not null,
    native_id  text not null,
    name       text not null,
    description text not null default '',
    embedding  vector(1536)
);

-- Nós de qualidade (acidentes materializados): setor, tecnologia, estagio, uf,
-- mecanismo, faixa_trl.
create table if not exists {SCHEMA}.quality_nodes (
    id    text primary key,                -- <family>:<value> (ex.: setor:agro)
    family text not null,                  -- setor | tecnologia | estagio | uf | mecanismo | faixa_trl
    value text not null
);

-- Arestas. `type` SEM CHECK (cauda aberta — o vocabulário valida em aplicação).
create table if not exists {SCHEMA}.edges (
    id         bigint generated always as identity primary key,
    source_id  text not null,              -- node_id OU quality_node_id
    target_id  text not null,
    type       text not null,
    weight     double precision not null default 1.0,
    properties jsonb not null default '{{}}',
    source     text not null default 'deterministica_derivada',  -- origem da aresta (factual_catalogada | deterministica_derivada | similaridade_derivada)
    created_at timestamptz not null default now(),
    unique (source_id, target_id, type)
);
create index if not exists {SCHEMA}_idx_edges_source on {SCHEMA}.edges(source_id);
create index if not exists {SCHEMA}_idx_edges_target on {SCHEMA}.edges(target_id);
create index if not exists {SCHEMA}_idx_edges_type on {SCHEMA}.edges(type);

-- Comunidades (Louvain).
create table if not exists {SCHEMA}.communities (
    community_id text not null,
    node_id      text not null,
    primary key (community_id, node_id)
);

-- Vocabulário de predicados (seed aristotélico; `core` = percorrido pela
-- estrutura-consciente).
create table if not exists {SCHEMA}.predicates (
    predicate   text primary key,
    category    text not null,             -- categoria aristotélica (Relação/Qualidade/...)
    core        boolean not null default false,
    description text not null default ''
);
"""

# Seed aristotélico (SPEC §5). Fase 1 é determinística e usa apenas os `core`.
PREDICATES_SEED: list[tuple[str, str, bool, str]] = [
    ("operado_por", "Relação", True, "edital/programa → agência que opera"),
    ("subordinado_a", "Relação", True, "edital → programa âncora"),
    ("credenciada_por", "Relação", True, "ICT → agência que credencia"),
    ("exige_parceria_com", "Relação", True, "edital → ator cuja parceria é exigida"),
    ("similar_a", "Relação", True, "cosseno dos embeddings (threshold ~0.75)"),
    ("tem_setor", "Qualidade", True, "entidade → nó de setor"),
    ("tem_tecnologia", "Qualidade", True, "entidade → nó de tecnologia (folksonomia)"),
    ("usa_mecanismo", "Qualidade", True, "edital → mecanismo"),
    ("busca_estagio", "Posição", True, "investidor/programa → estágio alvo"),
    ("atua_em", "Posição", True, "empresa (efêmera) → setor/tema"),
    ("tem_uf", "Lugar", True, "entidade → UF"),
    ("tem_trl_faixa", "Quantidade", True, "edital → faixa TRL"),
    ("potencial_parceria", "Ação/Paixão", False, "dedução multi-salto (Fase 2): edital → ICT parceira viável"),
]


def init_schema() -> None:
    """Cria o schema `kg_spike` (idempotente). Chamado pelo ingest antes de
    qualquer escrita; seguro rodar múltiplas vezes."""
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(_DDL)
            cur.executemany(
                f"""
                insert into {SCHEMA}.predicates (predicate, category, core, description)
                values (%s, %s, %s, %s)
                on conflict (predicate) do nothing
                """,
                PREDICATES_SEED,
            )
    logger.info("kg_spike: schema e vocabulário prontos (%d predicados seed)", len(PREDICATES_SEED))


def reset() -> None:
    """Limpa o conteúdo do schema (mantém a estrutura). Uso em testes/debug."""
    with connect() as conn:
        with conn.cursor() as cur:
            for table in ("edges", "nodes", "quality_nodes", "communities"):
                cur.execute(f"truncate {SCHEMA}.{table} restart identity cascade")
    logger.info("kg_spike: conteúdo limpo")


def drop_schema() -> None:
    """Remove o spike por completo. Reversão total — NUNCA toca no `public`."""
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(f"drop schema if exists {SCHEMA} cascade")
    logger.info("kg_spike: schema %s removido", SCHEMA)


# ─────────────────────────────────────────────────────────────────────────────
# Leituras (consultadas por traverse/serialize/tools)
# ─────────────────────────────────────────────────────────────────────────────

def load_edges() -> list[dict[str, Any]]:
    """Todas as arestas do spike como dicts — a travessia em processo é barata
    (~2k arestas) e evita RPC/CTE, mesmo padrão do BFS de `entity_catalog`."""
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"select source_id, target_id, type, weight, properties, source from {SCHEMA}.edges"
            )
            cols = [d.name for d in cur.description]
            return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]


def load_nodes() -> list[dict[str, Any]]:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"select id, kind, native_id, name, description from {SCHEMA}.nodes"
            )
            cols = [d.name for d in cur.description]
            return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]


def load_quality_nodes() -> list[dict[str, Any]]:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"select id, family, value from {SCHEMA}.quality_nodes"
            )
            cols = [d.name for d in cur.description]
            return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]


def load_communities() -> dict[str, list[str]]:
    """{community_id: [node_ids]}."""
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"select community_id, node_id from {SCHEMA}.communities"
            )
            out: dict[str, list[str]] = {}
            for cid, nid in cur.fetchall():
                out.setdefault(cid, []).append(nid)
            return out
