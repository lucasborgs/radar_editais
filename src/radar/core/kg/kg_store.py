"""Compatibilidade para o ledger da Descoberta e artefatos JSON legados.

O catálogo e o match ativos usam as tabelas gold SQL; este módulo não é mais a
fonte do knowledge graph. Há dois consumidores fora dos testes:

* ``radar.core.ingestion.opportunity_discovery`` persiste ``discovery_ledger`` e ainda consulta
  ``index.json`` para complementar a deduplicação por URL;
* ``radar.core.vocab_lint`` lê ``index.json``/``index_historico.json`` como corpus
  offline de evidências.

``load_icts`` e a chave ``icts`` não têm consumidor vivo fora dos testes, mas
permanecem enquanto o seam legado for removido como uma unidade.

Backends de LEITURA (env `KG_STORE_BACKEND`):
  • "file" (default)  — lê `data/knowledge_graph/*.json`. Dev + ETL local.
  • "postgres"        — lê a tabela `kg_artifacts` (JSONB) no Supabase.
                        Usado em produção para tornar o ledger durável.

ESCRITA (`save`): sempre grava o arquivo local (dev/inspeção e consumidores
em modo file) e, quando o Supabase está configurado, faz `upsert` na tabela
Falha de upsert com credencial presente PROPAGA: perder o ledger faria URLs já
avaliadas voltarem à triagem após um redeploy.

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

from radar.core.config import KNOWLEDGE_GRAPH_DIR

logger = logging.getLogger(__name__)

# key lógica -> nome do arquivo (modo file) / valor da coluna `key` (modo postgres)
_FILES: dict[str, str] = {
    "index": "index.json",
    "index_historico": "index_historico.json",
    "icts": "icts.json",
    # Estado operacional do pipeline (não-artefato do grafo, mas mesmo seam):
    # em prod o FS do worker é EFÊMERO — sem durabilidade aqui, a Descoberta
    # re-tria URLs já vistas a cada redeploy.
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
        from radar.core.infra.db import get_supabase_service
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
        from radar.core.infra.db import get_supabase_service
        get_supabase_service().table(_TABLE).upsert(
            {"key": key, "blob": blob}, on_conflict="key"
        ).execute()
        with _lock:
            _pg_cache[key] = (time.monotonic(), blob)
        logger.info("kg_store: %r publicado no Postgres", key)
