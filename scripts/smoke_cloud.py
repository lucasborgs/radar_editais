"""Smoke test local-contra-cloud (pós-migração Supabase Cloud).

Carrega `.env` (LLM etc.) e sobrepõe com `.env.cloud` (os 5 valores do cloud),
sem imprimir segredo. Valida o que a migração de schema NÃO cobre:
  1. DB alcançável via DATABASE_URL (session pooler) + query — gotcha do pooler.
  2. supabase-py com service key lê `matching_weights` (chave válida + seed 017).
  3. anon key presente/decodificável (client inicializa).
  4. pgvector + tabelas-chave existem.
  5. DADO: `kg_artifacts` (editais) está VAZIO no cloud — o push só cria schema.

Uso: `.venv/bin/python scripts/smoke_cloud.py`  (exige `.env.cloud` na raiz).
Não escreve nada no cloud. Saída: PASS/FAIL por checagem + resumo.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
CLOUD_ENV = ROOT / ".env.cloud"

# Ordem importa: base primeiro, cloud sobrepõe os 5 do Supabase.
load_dotenv(ROOT / ".env")
if not CLOUD_ENV.exists():
    print(f"FALTA {CLOUD_ENV} — crie com os 5 valores do cloud (ver instruções).")
    sys.exit(2)
load_dotenv(CLOUD_ENV, override=True)

# Guard: garantir que estamos mesmo apontando pro cloud, não pro 127.0.0.1.
url = os.getenv("SUPABASE_URL", "")
if "127.0.0.1" in url or "localhost" in url:
    print(f"ABORT: SUPABASE_URL ainda é local ({url}). .env.cloud não sobrepôs.")
    sys.exit(2)

ok = 0
fail = 0


def check(name: str, fn):
    global ok, fail
    try:
        detail = fn()
        print(f"  PASS  {name}" + (f" — {detail}" if detail else ""))
        ok += 1
    except Exception as e:
        print(f"  FAIL  {name} — {type(e).__name__}: {e}")
        fail += 1


print(f"\nSmoke test contra {url}\n")


def _db_query():
    import psycopg
    dsn = os.environ["DATABASE_URL"]
    if ":6543" in dsn:
        return "⚠ porta 6543 (transaction pooler) — procrastinate LISTEN/NOTIFY quebra; use 5432"
    with psycopg.connect(dsn, connect_timeout=10) as conn, conn.cursor() as cur:
        cur.execute("select count(*) from information_schema.tables where table_schema='public'")
        n = cur.fetchone()[0]
        cur.execute("select max(version) from supabase_migrations.schema_migrations")
        ver = cur.fetchone()[0]
        return f"{n} tabelas public, última migration {ver}"


def _service_key_seed():
    from core.db import get_supabase_service
    resp = (get_supabase_service().table("matching_weights")
            .select("dimension, weight").is_("workspace_id", "null").execute())
    rows = {r["dimension"]: r["weight"] for r in (resp.data or [])}
    assert "elegibilidade_dura" in rows, f"seed 017 ausente; dims={list(rows)}"
    return f"{len(rows)} pesos globais (elegibilidade_dura={rows['elegibilidade_dura']})"


def _anon_key():
    import jwt
    tok = os.environ["SUPABASE_ANON_KEY"]
    claims = jwt.decode(tok, options={"verify_signature": False})
    return f"role={claims.get('role')}, ref={claims.get('ref', '?')}"


def _pgvector_tables():
    import psycopg
    with psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=10) as conn, conn.cursor() as cur:
        cur.execute("select extname from pg_extension where extname='vector'")
        assert cur.fetchone(), "extensão pgvector ausente"
        cur.execute("""select count(*) from information_schema.tables
                       where table_schema='public'
                         and table_name in ('edital_chunks','content_items','kg_artifacts',
                                            'workspaces','writing_sessions','matching_weights')""")
        return f"pgvector ok, {cur.fetchone()[0]}/6 tabelas-chave presentes"


def _editais_data_gap():
    """Demonstra o gap: kg_artifacts (editais) vazio no cloud vs local."""
    from core.db import get_supabase_service
    from core.kg import entity_catalog
    resp = get_supabase_service().table("kg_artifacts").select("key").execute()
    cloud_keys = [r["key"] for r in (resp.data or [])]
    local_n = entity_catalog.get_stats()["total_editais"]
    if not cloud_keys:
        return (f"⚠ kg_artifacts VAZIO no cloud (local tem {local_n} editais) — "
                f"rodar build com env cloud p/ popular antes dos testers")
    return f"kg_artifacts no cloud: {cloud_keys}"


check("1. DB via DATABASE_URL (pooler)", _db_query)
check("2. service key + seed matching_weights", _service_key_seed)
check("3. anon key decodificável", _anon_key)
check("4. pgvector + tabelas-chave", _pgvector_tables)
check("5. DADO: editais no cloud", _editais_data_gap)

print(f"\n{'='*50}\nRESULTADO: {ok} PASS / {fail} FAIL\n")
sys.exit(1 if fail else 0)
