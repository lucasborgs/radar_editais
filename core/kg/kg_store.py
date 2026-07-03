"""core/kg_store.py — fonte única dos artefatos do knowledge graph.

Centraliza leitura/escrita dos blobs do grafo (`index.json`,
`index_historico.json`, `icts.json`). Todos os consumidores lêem via
esta camada, nunca diretamente do disco.

Backends de LEITURA (env `KG_STORE_BACKEND`):
  • "file" (default)  — lê `data/knowledge_graph/*.json`. Dev + ETL local.
  • "postgres"        — lê a tabela `kg_artifacts` (JSONB) no Supabase.
                        Caminho de produção: o request-path deixa de depender
                        de arquivos locais.

ESCRITA (`save`): sempre grava o arquivo local (dev/inspeção e consumidores
em modo file) e, quando o Supabase está configurado, faz `upsert` na tabela
— assim um `run_all.py` local publica o dado para a prod sem redeploy. Falha
de upsert com credencial presente PROPAGA (publicar dado não pode falhar em
silêncio).

Caching:
  • modo file     — invalida por mtime (espelha o comportamento legado: um
                    rebuild do ETL é refletido sem reiniciar o processo).
  • modo postgres — TTL curto (env `KG_STORE_TTL`, default 60s) para não
                    bater no banco a cada request (mesmo espírito do cache de
                    `matching_weights`, ADR A5).
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time

from config import KNOWLEDGE_GRAPH_DIR
from core.kg.migrate_v2 import migrate_to_v2

logger = logging.getLogger(__name__)

# key lógica -> nome do arquivo (modo file) / valor da coluna `key` (modo postgres)
_FILES: dict[str, str] = {
    "index": "index.json",
    "index_historico": "index_historico.json",
    "icts": "icts.json",
    "investidores": "investidores.json",
    "programas": "programas.json",
    # Estado operacional do pipeline (não-artefato do grafo, mas mesmo seam):
    # em prod o FS do worker é EFÊMERO — sem durabilidade aqui, cada redeploy
    # re-sintetiza toda wiki (rate limit) e a Descoberta re-tria URLs já vistas.
    "discovery_ledger": ".discovery_ledger.json",
}

_TABLE = "kg_artifacts"
_PG_TTL = float(os.getenv("KG_STORE_TTL", "60"))

_lock = threading.Lock()
_file_cache: dict[str, tuple[float, dict]] = {}   # key -> (mtime, data)
_pg_cache: dict[str, tuple[float, dict]] = {}     # key -> (monotonic_fetched_at, data)


def _check_key(key: str) -> None:
    if key not in _FILES:
        raise KeyError(f"kg_store: artefato desconhecido {key!r} (conhecidos: {list(_FILES)})")


def _pg_configured() -> bool:
    return bool(os.getenv("SUPABASE_URL") and os.getenv("SUPABASE_SERVICE_KEY"))


# ---------------------------------------------------------------------------
# Leitura
# ---------------------------------------------------------------------------

def _load_file(key: str) -> dict | None:
    path = KNOWLEDGE_GRAPH_DIR / _FILES[key]
    if not path.exists():
        return None
    mtime = path.stat().st_mtime
    with _lock:
        cached = _file_cache.get(key)
        if cached is not None and cached[0] == mtime:
            return cached[1]
    data = json.loads(path.read_text(encoding="utf-8"))
    with _lock:
        _file_cache[key] = (mtime, data)
    return data


def _load_pg(key: str) -> dict | None:
    now = time.monotonic()
    with _lock:
        cached = _pg_cache.get(key)
        if cached is not None and (now - cached[0]) < _PG_TTL:
            return cached[1]
    try:
        from core.db import get_supabase_service
        resp = (
            get_supabase_service()
            .table(_TABLE)
            .select("blob")
            .eq("key", key)
            .limit(1)
            .execute()
        )
    except Exception as e:
        logger.warning("kg_store[postgres]: falha ao ler %r: %s", key, e)
        return None
    rows = resp.data or []
    if not rows:
        return None
    data = rows[0]["blob"]
    with _lock:
        _pg_cache[key] = (now, data)
    return data


def load(key: str, default: dict | None = None) -> dict:
    """Carrega um artefato do grafo. `default` é devolvido quando ausente."""
    _check_key(key)
    backend = os.getenv("KG_STORE_BACKEND", "file").lower()
    if backend == "postgres":
        data = _load_pg(key)
        if data is None:
            # Em prod a tabela DEVE estar populada; ausência é erro operacional.
            # Logamos alto e degradamos para o arquivo local (cobre transição/dev).
            logger.warning(
                "kg_store[postgres]: %r ausente na tabela %s — caindo para arquivo local",
                key, _TABLE,
            )
            data = _load_file(key)
    else:
        data = _load_file(key)
    if data is None:
        return {} if default is None else default
    return data


def load_index(*, historico: bool = False) -> dict:
    """Índice de editais (default seguro `{"editais": []}` quando ausente)."""
    return load("index_historico" if historico else "index", default={"editais": []})


def load_icts() -> list[dict]:
    """Lista de ICTs do `icts.json` (a chave `icts` do blob)."""
    return load("icts", default={}).get("icts", [])


def load_investidores() -> list[dict]:
    """Lista de investidores (Q3) do `investidores.json` — diretório CURADO à mão
    (espelha load_icts). Entidade fora do ciclo de edital, fora do index.json."""
    return load("investidores", default={}).get("investidores", [])


def load_programas() -> list[dict]:
    """Lista de programas recorrentes do `programas.json` — diretório CURADO à mão
    (espelha load_investidores). Entidade fora do ciclo de edital, fora do index.json."""
    return load("programas", default={}).get("programas", [])





# ---------------------------------------------------------------------------
# Hypergraphs (subgrafo N-ário por edital — Hyper-Extract)
# ---------------------------------------------------------------------------
# Paralelo EXATO das wiki pages acima, mesmo motivo: em modo file são arquivos
# POR-EDITAL (KNOWLEDGE_GRAPH_DIR/hypergraphs/{file_key}.json, file_key =
# "{source}__{native}"), mas o FS do worker no Railway é EFÊMERO — todo redeploy
# perderia os subgrafos. Em postgres vivem num único blob kg_artifacts
# key='hypergraphs' = {file_key: {source_hash, nodes, edges}}, durável.
_HYPERGRAPH_KEY = "hypergraphs"
_HYPERGRAPHS_DIR = KNOWLEDGE_GRAPH_DIR / "hypergraphs"


def _load_hypergraph_blob_pg() -> dict:
    """Blob {file_key: hypergraph} do Postgres, cacheado (TTL). {} se ausente."""
    now = time.monotonic()
    with _lock:
        cached = _pg_cache.get(_HYPERGRAPH_KEY)
        if cached is not None and (now - cached[0]) < _PG_TTL:
            return cached[1]
    try:
        from core.db import get_supabase_service
        resp = (
            get_supabase_service()
            .table(_TABLE).select("blob").eq("key", _HYPERGRAPH_KEY).limit(1).execute()
        )
    except Exception as e:
        logger.warning("kg_store[postgres]: falha ao ler blob hypergraphs: %s", e)
        return {}
    rows = resp.data or []
    blob = rows[0]["blob"] if rows else {}
    with _lock:
        _pg_cache[_HYPERGRAPH_KEY] = (now, blob)
    return blob


def load_hypergraph(file_key: str) -> dict | None:
    """Hypergraph (subgrafo N-ário) de um edital — None se ausente.

    postgres → do blob `hypergraphs`; file → arquivo por-edital. Em postgres, se
    faltar no blob, cai pro arquivo (cobre transição/dev). Retorna o dict
    {format_version, source_hash, proveniencia, nodes, edges}. Single source para
    os consumidores no request-path.

    UPGRADE-ON-READ (PR1 KG v2): normaliza para o formato v2 (`migrate_to_v2`) —
    blobs/arquivos ainda em v1 e extrações frescas (o extractor só emite v2 no
    PR2) são migrados em memória, para que os leitores nunca vejam v1. Idempotente
    e barato para grafos já-v2.
    """
    if os.getenv("KG_STORE_BACKEND", "file").lower() == "postgres":
        graph = _load_hypergraph_blob_pg().get(file_key)
        if graph is not None:
            return migrate_to_v2(graph)
    path = _HYPERGRAPHS_DIR / f"{file_key}.json"
    if path.exists():
        try:
            return migrate_to_v2(json.loads(path.read_text(encoding="utf-8")))
        except Exception as e:
            logger.warning("kg_store: falha ao ler hypergraph %s: %s", file_key, e)
    return None


def save_hypergraphs(graphs: dict[str, dict]) -> None:
    """MERGE de {file_key: hypergraph} no blob `hypergraphs` do Postgres (se configurado).

    Os arquivos por-edital são escritos pelo produtor (Hyper-Extract); aqui só
    persistimos o blob durável p/ prod. MERGE (não substitui) para que runs
    parciais não apaguem os demais subgrafos. No-op sem Supabase (modo file puro).
    """
    if not graphs or not _pg_configured():
        return
    from core.db import get_supabase_service
    merged = {**_load_hypergraph_blob_pg(), **graphs}
    get_supabase_service().table(_TABLE).upsert(
        {"key": _HYPERGRAPH_KEY, "blob": merged}, on_conflict="key"
    ).execute()
    with _lock:
        _pg_cache[_HYPERGRAPH_KEY] = (time.monotonic(), merged)
    logger.info("kg_store: %d hypergraphs publicados (blob total=%d)", len(graphs), len(merged))


def load_all_hypergraphs() -> dict[str, dict]:
    """Todos os subgrafos do ecossistema como {file_key: hypergraph}.

    postgres → blob `hypergraphs`, completado pelos arquivos locais ausentes
    (mesma transição de `load_hypergraph`); file → arquivos por-edital no disco.
    Single source para os consumidores que varrem o grafo inteiro (vizinhança,
    catálogo, match). Não cacheia além do TTL do blob PG.

    UPGRADE-ON-READ (PR1 KG v2): cada subgrafo é normalizado para v2 via
    `migrate_to_v2` (idempotente) — ver `load_hypergraph`.
    """
    out: dict[str, dict] = {}
    if os.getenv("KG_STORE_BACKEND", "file").lower() == "postgres":
        out.update(_load_hypergraph_blob_pg())
    for p in sorted(_HYPERGRAPHS_DIR.glob("*.json")):
        if p.stem in out:
            continue
        try:
            out[p.stem] = json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning("kg_store: falha ao ler hypergraph %s: %s", p.stem, e)
    return {fk: migrate_to_v2(g) for fk, g in out.items()}


# ---------------------------------------------------------------------------
# Escrita (ETL)
# ---------------------------------------------------------------------------

def save(key: str, blob: dict) -> None:
    """Persiste um artefato: arquivo local SEMPRE + upsert no Postgres se
    o Supabase estiver configurado. Falha do upsert (com credencial presente)
    propaga — publicar dado para prod não pode falhar em silêncio."""
    _check_key(key)
    path = KNOWLEDGE_GRAPH_DIR / _FILES[key]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(blob, indent=2, ensure_ascii=False), encoding="utf-8")
    with _lock:
        _file_cache[key] = (path.stat().st_mtime, blob)

    if _pg_configured():
        from core.db import get_supabase_service
        get_supabase_service().table(_TABLE).upsert(
            {"key": key, "blob": blob}, on_conflict="key"
        ).execute()
        with _lock:
            _pg_cache[key] = (time.monotonic(), blob)
        logger.info("kg_store: %r publicado no Postgres", key)
