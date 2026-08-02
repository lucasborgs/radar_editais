"""core/kg/phase1/store.py — acesso SQL à projeção da Fase 1 do grafo.

Leitura do schema `kg_phase1` (gerações + nós + qualidade + arestas +
comunidades). A geração corrente é a ÚNICA linha com `is_current = true`; a
troca é atômica no commit do build (ingest.py) — leitores NUNCA observam uma
geração incompleta (linha `building`/`failed` nunca é `is_current`).

Acesso via psycopg (DATABASE_URL), mesmo motivo do gold/match: supabase-py
corrompe colunas `vector`. Sem LLM e sem rede fora do Postgres.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any

import psycopg

logger = logging.getLogger(__name__)

SCHEMA = "kg_phase1"

# Timeout curto e EXPLÍCITO do snapshot (evita bloquear o Explorar se o Postgres
# pendurar): statement_timeout + connect_timeout em UMA leitura única.
SNAPSHOT_TIMEOUT_SECONDS = 2.0


@dataclass(frozen=True)
class Snapshot:
    """Cópia consistente de UMA geração — nunca mistura duas durante um swap.

    `generation_id` fixado na resolução; nós/qualidade/arestas/comunidades são
    lidos DA MESMA geração, em UMA conexão, dentro de UMA transação."""
    generation_id: int
    nodes: list[dict[str, Any]]
    quality_nodes: list[dict[str, Any]]
    edges: list[dict[str, Any]]
    communities: dict[str, list[str]] = field(default_factory=dict)


def get_dsn() -> str:
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise RuntimeError(
            "DATABASE_URL não configurada — a projeção precisa de acesso direto ao Postgres."
        )
    return dsn


def connect() -> psycopg.Connection:
    return psycopg.connect(get_dsn())


def _cols(cur) -> list[str]:
    return [d.name for d in cur.description]


def _resolve_generation(conn: psycopg.Connection, generation_id: int | None) -> int | None:
    """Geração alvo: a passada, ou a corrente quando não informada."""
    if generation_id is not None:
        return generation_id
    gen = current_generation(conn)
    return gen["id"] if gen else None


def current_generation(conn: psycopg.Connection | None = None) -> dict[str, Any] | None:
    """Geração saudável corrente — a única observável pelos leitores."""
    own = conn is None
    if own:
        conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"select id, status, build_version, source_hash, counts, error, "
                f"started_at, finished_at from {SCHEMA}.generations "
                f"where is_current = true"
            )
            row = cur.fetchone()
            if row is None:
                return None
            return dict(zip(_cols(cur), row, strict=True))
    finally:
        if own:
            conn.close()


def generations(conn: psycopg.Connection | None = None, *, limit: int = 10) -> list[dict[str, Any]]:
    """Histórico recente do ledger (mais recente primeiro)."""
    own = conn is None
    if own:
        conn = connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"select id, status, is_current, build_version, source_hash, counts, "
                f"error, started_at, finished_at from {SCHEMA}.generations "
                f"order by id desc limit %s",
                (int(limit),),
            )
            return [dict(zip(_cols(cur), r, strict=True)) for r in cur.fetchall()]
    finally:
        if own:
            conn.close()


def load_nodes(
    generation_id: int | None = None, *, conn: psycopg.Connection | None = None
) -> list[dict[str, Any]]:
    """Nós da geração (corrente por default)."""
    own = conn is None
    if own:
        conn = connect()
    try:
        gen = _resolve_generation(conn, generation_id)
        if gen is None:
            return []
        with conn.cursor() as cur:
            cur.execute(
                f"select id, kind, native_id, name, description from {SCHEMA}.nodes "
                f"where generation_id = %s order by id",
                (gen,),
            )
            return [dict(zip(_cols(cur), r, strict=True)) for r in cur.fetchall()]
    finally:
        if own:
            conn.close()


def get_node(
    node_id: str, generation_id: int | None = None, *, conn: psycopg.Connection | None = None
) -> dict[str, Any] | None:
    """Um nó por id (na geração corrente por default)."""
    own = conn is None
    if own:
        conn = connect()
    try:
        gen = _resolve_generation(conn, generation_id)
        if gen is None:
            return None
        with conn.cursor() as cur:
            cur.execute(
                f"select id, kind, native_id, name, description from {SCHEMA}.nodes "
                f"where generation_id = %s and id = %s",
                (gen, node_id),
            )
            row = cur.fetchone()
            if row is None:
                return None
            return dict(zip(_cols(cur), row, strict=True))
    finally:
        if own:
            conn.close()


def load_quality_nodes(
    generation_id: int | None = None, *, conn: psycopg.Connection | None = None
) -> list[dict[str, Any]]:
    """Nós de qualidade da geração (corrente por default)."""
    own = conn is None
    if own:
        conn = connect()
    try:
        gen = _resolve_generation(conn, generation_id)
        if gen is None:
            return []
        with conn.cursor() as cur:
            cur.execute(
                f"select id, family, value from {SCHEMA}.quality_nodes "
                f"where generation_id = %s order by id",
                (gen,),
            )
            return [dict(zip(_cols(cur), r, strict=True)) for r in cur.fetchall()]
    finally:
        if own:
            conn.close()


def load_edges(
    generation_id: int | None = None, *, conn: psycopg.Connection | None = None
) -> list[dict[str, Any]]:
    """Todas as arestas da geração (corrente por default). A travessia em
    processo é barata (~2k arestas) e evita RPC/CTE — mesmo padrão do BFS de
    `entity_catalog`."""
    return query_edges(generation_id=generation_id, conn=conn)


def query_edges(
    generation_id: int | None = None,
    *,
    source_id: str | None = None,
    target_id: str | None = None,
    type: str | None = None,
    origin: str | None = None,
    conn: psycopg.Connection | None = None,
) -> list[dict[str, Any]]:
    """Arestas da geração filtradas por source/target/type/origin."""
    own = conn is None
    if own:
        conn = connect()
    try:
        gen = _resolve_generation(conn, generation_id)
        if gen is None:
            return []
        sql = (
            f"select source_id, target_id, type, weight, properties, origin "
            f"from {SCHEMA}.edges where generation_id = %s"
        )
        params: list[Any] = [gen]
        for col, val in (("source_id", source_id), ("target_id", target_id),
                         ("type", type), ("origin", origin)):
            if val is not None:
                sql += f" and {col} = %s"
                params.append(val)
        sql += " order by source_id, target_id, type"
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return [dict(zip(_cols(cur), r, strict=True)) for r in cur.fetchall()]
    finally:
        if own:
            conn.close()


def load_communities(
    generation_id: int | None = None, *, conn: psycopg.Connection | None = None
) -> dict[str, list[str]]:
    """{community_id: [node_ids]} da geração (corrente por default)."""
    own = conn is None
    if own:
        conn = connect()
    try:
        gen = _resolve_generation(conn, generation_id)
        if gen is None:
            return {}
        with conn.cursor() as cur:
            cur.execute(
                f"select community_id, node_id from {SCHEMA}.communities "
                f"where generation_id = %s order by community_id, node_id",
                (gen,),
            )
            out: dict[str, list[str]] = {}
            for cid, nid in cur.fetchall():
                out.setdefault(cid, []).append(nid)
            return out
    finally:
        if own:
            conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# Snapshot CONSISTENTE (KG-P1B): UMA geração, UMA conexão, UMA transação,
# timeout explícito. A base dos graph tools read-only do Explorar.
# ─────────────────────────────────────────────────────────────────────────────

def _connect_with_timeout(timeout: float) -> psycopg.Connection:
    """Conexão com timeouts curtos e explícitos (connect + statement).

    `statement_timeout` na própria conexão garante que uma leitura pendurada
    (lentidão/lock do Postgres) NÃO bloqueie o Explorar além do teto."""
    secs = max(1.0, float(timeout))
    ms = max(1, int(secs * 1000))
    return psycopg.connect(
        get_dsn(),
        connect_timeout=max(1, int(secs)),
        options=f"-c statement_timeout={ms}",
    )


def _fetch_nodes(cur, generation_id: int) -> list[dict[str, Any]]:
    cur.execute(
        f"select id, kind, native_id, name, description from {SCHEMA}.nodes "
        f"where generation_id = %s order by id",
        (generation_id,),
    )
    return [dict(zip(_cols(cur), r, strict=True)) for r in cur.fetchall()]


def _fetch_quality_nodes(cur, generation_id: int) -> list[dict[str, Any]]:
    cur.execute(
        f"select id, family, value from {SCHEMA}.quality_nodes "
        f"where generation_id = %s order by id",
        (generation_id,),
    )
    return [dict(zip(_cols(cur), r, strict=True)) for r in cur.fetchall()]


def _fetch_edges(cur, generation_id: int) -> list[dict[str, Any]]:
    cur.execute(
        f"select source_id, target_id, type, weight, properties, origin "
        f"from {SCHEMA}.edges where generation_id = %s "
        f"order by source_id, target_id, type",
        (generation_id,),
    )
    return [dict(zip(_cols(cur), r, strict=True)) for r in cur.fetchall()]


def _fetch_communities(cur, generation_id: int) -> dict[str, list[str]]:
    cur.execute(
        f"select community_id, node_id from {SCHEMA}.communities "
        f"where generation_id = %s order by community_id, node_id",
        (generation_id,),
    )
    out: dict[str, list[str]] = {}
    for cid, nid in cur.fetchall():
        out.setdefault(cid, []).append(nid)
    return out


def load_snapshot(
    *,
    conn: psycopg.Connection | None = None,
    timeout: float = SNAPSHOT_TIMEOUT_SECONDS,
) -> Snapshot | None:
    """Snapshot CONSISTENTE da geração corrente e saudável.

    - resolve a ÚNICA geração `is_current = true AND status = 'healthy'`;
    - carrega nós, quality nodes, arestas e comunidades DESSA mesma geração
      (nunca mistura duas gerações — inclusive durante um swap: o swap só
      desmarca `is_current`, nunca apaga gerações, então o `generation_id`
      resolvido permanece íntegro);
    - UMA conexão e UMA transação (visão consistente);
    - `None` quando não existe geração saudável (estado pré-primeiro-build) —
      nunca um dado parcial;
    - timeout curto e explícito via `statement_timeout`/`connect_timeout`."""
    own = conn is None
    if own:
        conn = _connect_with_timeout(timeout)
    try:
        with conn.transaction():
            with conn.cursor() as cur:
                if own:
                    cur.execute(
                        "set local statement_timeout = %s",
                        (max(1, int(timeout * 1000)),),
                    )
                cur.execute(
                    f"select id from {SCHEMA}.generations "
                    f"where is_current = true and status = 'healthy' "
                    f"order by id desc limit 1"
                )
                row = cur.fetchone()
                if row is None:
                    return None
                generation_id = int(row[0])
                nodes = _fetch_nodes(cur, generation_id)
                quality_nodes = _fetch_quality_nodes(cur, generation_id)
                edges = _fetch_edges(cur, generation_id)
                communities = _fetch_communities(cur, generation_id)
        return Snapshot(
            generation_id=generation_id,
            nodes=nodes,
            quality_nodes=quality_nodes,
            edges=edges,
            communities=communities,
        )
    finally:
        if own:
            conn.close()
