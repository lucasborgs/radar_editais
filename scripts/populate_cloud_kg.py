"""Popula o `kg_artifacts` do Supabase Cloud com os editais (FINEP, FAPESP) — rebuild limpo.

Pós-migração: o push de schema não levou dado. Este driver rebuilda o índice a
partir do BRONZE existente (sem re-scrape) EXCLUINDO o discovery web (respeita o
isolamento de prod) e faz upsert no cloud via `kg_store.save`.

Carrega `.env` + `.env.cloud` (override) para apontar o `kg_store.save` ao cloud.
Faz backup do index local (que contém itens web) antes de reescrevê-lo limpo.

Uso: `.venv/bin/python scripts/populate_cloud_kg.py`
"""
from __future__ import annotations

import shutil
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
cloud = ROOT / ".env.cloud"
if not cloud.exists():
    print(f"FALTA {cloud}")
    sys.exit(2)
load_dotenv(cloud, override=True)

import os  # noqa: E402

if "127.0.0.1" in os.getenv("SUPABASE_URL", "") or not os.getenv("SUPABASE_SERVICE_KEY"):
    print("ABORT: env não aponta pro cloud (SUPABASE_URL/SERVICE_KEY).")
    sys.exit(2)

# Backup do index local (tem itens web) antes do rebuild limpo sobrescrever.
from config import KNOWLEDGE_GRAPH_DIR  # noqa: E402

stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
for name in ("index.json", "index_historico.json"):
    src = KNOWLEDGE_GRAPH_DIR / name
    if src.exists():
        bak = src.with_suffix(f".json.{stamp}.bak")
        shutil.copy2(src, bak)
        print(f"backup: {src.name} → {bak.name}")

# Por default exclui a fonte `web` do build (isolamento de prod). Com
# `--include-web` (decisão 2026-06-11: torneira aberta em prod) o bronze de
# web entra no push — os itens vão rotulados `provisorio` (badge na UI).
import pipeline.build_knowledge_graph as bkg  # noqa: E402

include_web = "--include-web" in sys.argv

if not include_web:
    _orig_load_bronze = bkg.load_bronze
    bkg.load_bronze = (  # noqa: E731 — override pontual do driver
        lambda source=bkg._DEFAULT_SOURCE: [] if source == "web" else _orig_load_bronze(source)
    )

scope = "FINEP+FAPESP+web" if include_web else "FINEP+FAPESP (sem web)"
print(f"\nRebuild {scope} → upsert cloud ({os.getenv('SUPABASE_URL')})\n")
bkg.main()  # rebuilda do bronze, salva local + upsert kg_artifacts no cloud

# Verificação: lê de volta do cloud o que acabou de subir.
print("\n--- verificação no cloud ---")
os.environ["KG_STORE_BACKEND"] = "postgres"
import core.kg.kg_store as kg_store  # noqa: E402

kg_store._pg_cache.clear()
idx = kg_store.load_index()
editais = idx.get("editais", [])
print(f"kg_artifacts['index'] no cloud: {len(editais)} editais vigentes")
fontes: dict[str, int] = {}
for e in editais:
    fontes[e.get("source", "?")] = fontes.get(e.get("source", "?"), 0) + 1
print(f"por fonte: {fontes}")
esperadas = ("finep", "fapesp", "web") if include_web else ("finep", "fapesp")
if any(f not in esperadas for f in fontes):
    print(f"⚠ ATENÇÃO: fonte inesperada no índice (esperadas: {esperadas})")
