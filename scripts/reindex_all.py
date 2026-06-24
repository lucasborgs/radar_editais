#!/usr/bin/env python3
"""
Reindex editais de TODAS as fontes no `edital_chunks` (RAG).

Lê os IDs do index.json (editais vigentes) e chama `chunk_edital_task`
para cada um. O `chunk_edital_task` é source-agnostic — despacha para o
adapter correto (finep/fapesp/web/fapesc) via prefixo do edital_id.

Difere de `reindex_edital.py --all` que só descobre IDs via FINEP_PDFS_DIR.

Usage:
    python scripts/reindex_all.py                   # todas as fontes, sync
    python scripts/reindex_all.py --source fapesp   # só uma fonte
    python scripts/reindex_all.py --force           # re-indexa mesmo sem mudança
    python scripts/reindex_all.py --queue           # enfileira via procrastinate
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config import KNOWLEDGE_GRAPH_DIR  # noqa: E402
from core.db import get_supabase_service  # noqa: E402
from core.tasks import chunk_edital_task  # noqa: E402

INDEX_FILE = KNOWLEDGE_GRAPH_DIR / "index.json"


def _load_ids(source_filter: str | None) -> list[str]:
    data = json.loads(INDEX_FILE.read_text(encoding="utf-8"))
    ids = [e["id"] for e in data.get("editais", []) if e.get("id")]
    if source_filter:
        ids = [eid for eid in ids if eid.startswith(f"{source_filter}:")]
    return sorted(ids)


def _has_existing_chunks(db, edital_id: str) -> bool:
    result = (
        db.table("edital_chunks")
        .select("id", head=True, count="exact")
        .eq("edital_id", edital_id)
        .execute()
    )
    return (result.count or 0) > 0


def _count_chunks(db, edital_id: str) -> int:
    result = (
        db.table("edital_chunks")
        .select("id", head=True, count="exact")
        .eq("edital_id", edital_id)
        .execute()
    )
    return result.count or 0


async def _run_sync(edital_id: str, force: bool) -> None:
    fn = getattr(chunk_edital_task, "func", chunk_edital_task)
    await fn(edital_id=edital_id, force=force)


async def _enqueue(edital_id: str, force: bool) -> None:
    from core.tasks import app as tasks_app
    async with tasks_app.open_async():
        await tasks_app.configure_task("chunk_edital").defer_async(
            edital_id=edital_id, force=force,
        )


async def _process_one(db, edital_id: str, queue: bool, force: bool) -> dict:
    if not force and _has_existing_chunks(db, edital_id):
        return {"edital_id": edital_id, "status": "skipped (already indexed)"}
    try:
        if queue:
            await _enqueue(edital_id, force=force)
            return {"edital_id": edital_id, "status": "enqueued"}
        await _run_sync(edital_id, force=force)
        n = _count_chunks(db, edital_id)
        return {"edital_id": edital_id, "status": f"indexed ({n} chunks)"}
    except Exception as e:
        return {"edital_id": edital_id, "status": f"error: {e}"}


async def _main_async(args: argparse.Namespace) -> int:
    targets = _load_ids(args.source)
    if not targets:
        print(f"[reindex_all] Nenhum edital encontrado em {INDEX_FILE}")
        return 1

    db = get_supabase_service()
    mode = "queue" if args.queue else "sync"
    filter_label = f" (source={args.source})" if args.source else ""
    print(f"[reindex_all] {len(targets)} edital(is){filter_label} — mode={mode}, force={args.force}")

    failures = 0
    for eid in targets:
        result = await _process_one(db, eid, queue=args.queue, force=args.force)
        print(f"  • {result['edital_id']}: {result['status']}")
        if result["status"].startswith("error"):
            failures += 1

    print(f"[reindex_all] Concluído. Sucessos: {len(targets) - failures}, falhas: {failures}")
    return 0 if failures == 0 else 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Reindex editais de todas as fontes no edital_chunks (RAG)."
    )
    parser.add_argument(
        "--source", metavar="SOURCE",
        help="Filtra por fonte (ex: finep, fapesp, web). Sem filtro = todas.",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Re-indexa mesmo se já houver chunks (ignora gate de content_hash).",
    )
    parser.add_argument(
        "--queue", action="store_true",
        help="Enfileira via procrastinate ao invés de rodar inline.",
    )
    args = parser.parse_args()
    return asyncio.run(_main_async(args))


if __name__ == "__main__":
    sys.exit(main())
