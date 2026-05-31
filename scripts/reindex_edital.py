#!/usr/bin/env python3
"""
Reindex one or all FINEP editais into the `edital_chunks` table (RAG).

This wraps `core.tasks.chunk_edital_task` so operators can run it either
synchronously (the default — useful for one-offs and debugging) or via the
procrastinate queue (`--queue` — useful for backfilling many editais without
holding the CLI open).

Usage:
    python scripts/reindex_edital.py <edital_id>          # one edital, sync
    python scripts/reindex_edital.py --all                # all under bronze_data/finep_pdfs/
    python scripts/reindex_edital.py <edital_id> --queue  # enqueue via procrastinate
    python scripts/reindex_edital.py --all --force        # re-chunk even if rows already exist
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

# Project import bootstrap — pip install -e . provides this normally, but the
# script also works when run from a fresh checkout where the package isn't
# installed yet (CI dry-runs, etc.).
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config import FINEP_PDFS_DIR  # noqa: E402
from core.db import get_supabase_service  # noqa: E402
from core.tasks import chunk_edital_task  # noqa: E402

# Cost reference (May 2026): text-embedding-3-large is $0.13 / 1M tokens.
# At ~800 tokens/chunk and typically 30-80 chunks/edital → ~24k-64k tokens
# per indexing run = $0.003 - $0.008 per edital. Cheap enough that --force is
# safe; we only skip already-indexed editais to save on round-trips, not $.
EMBEDDING_COST_PER_1M_TOKENS = 0.13
TOKENS_PER_CHUNK_ESTIMATE = 800


def _has_existing_chunks(edital_id: str) -> bool:
    """Return True if the table already holds any row for this edital_id.

    Uses service-role because the worker that wrote them used service-role —
    keeping the read path consistent avoids confusion if RLS ever changes.
    """
    db = get_supabase_service()
    result = (
        db.table("edital_chunks")
        .select("id", head=True, count="exact")
        .eq("edital_id", edital_id)
        .execute()
    )
    return (result.count or 0) > 0


def _discover_editais() -> list[str]:
    """List edital_ids under FINEP_PDFS_DIR (one subdir per edital)."""
    if not FINEP_PDFS_DIR.exists():
        return []
    return sorted(d.name for d in FINEP_PDFS_DIR.iterdir() if d.is_dir())


async def _run_sync(edital_id: str, force: bool = False) -> None:
    """Invoke the task function directly in this process — no worker required."""
    # chunk_edital_task is a procrastinate-decorated coroutine; the wrapper
    # exposes the original callable via .func. Calling it directly skips
    # the queue entirely (we own the event loop here). `force` ignora o gate
    # de content_hash da task — sem isso, `--force` em um edital inalterado
    # seria silenciosamente pulado pelo próprio gate.
    fn = getattr(chunk_edital_task, "func", chunk_edital_task)
    await fn(edital_id=edital_id, force=force)


async def _enqueue(edital_id: str, force: bool = False) -> None:
    """Defer the job via procrastinate so a worker can pick it up."""
    from core.tasks import app as tasks_app
    async with tasks_app.open_async():
        await tasks_app.configure_task("chunk_edital").defer_async(
            edital_id=edital_id, force=force,
        )


def _count_chunks(edital_id: str) -> int:
    db = get_supabase_service()
    result = (
        db.table("edital_chunks")
        .select("id", head=True, count="exact")
        .eq("edital_id", edital_id)
        .execute()
    )
    return result.count or 0


async def _process_one(edital_id: str, queue: bool, force: bool) -> dict:
    """Run or enqueue indexing for one edital. Returns a status dict."""
    if not force and _has_existing_chunks(edital_id):
        return {"edital_id": edital_id, "status": "skipped (already indexed)"}

    pdf_dir = FINEP_PDFS_DIR / edital_id
    if not pdf_dir.exists():
        return {"edital_id": edital_id, "status": f"error: directory not found ({pdf_dir})"}

    try:
        if queue:
            await _enqueue(edital_id, force=force)
            return {"edital_id": edital_id, "status": "enqueued"}
        await _run_sync(edital_id, force=force)
        n = _count_chunks(edital_id)
        est_tokens = n * TOKENS_PER_CHUNK_ESTIMATE
        est_cost_usd = (est_tokens / 1_000_000) * EMBEDDING_COST_PER_1M_TOKENS
        return {
            "edital_id": edital_id,
            "status": f"indexed ({n} chunks, ~{est_tokens:,} tokens, ~${est_cost_usd:.4f})",
        }
    except Exception as e:
        return {"edital_id": edital_id, "status": f"error: {e}"}


async def _main_async(args: argparse.Namespace) -> int:
    targets: list[str]
    if args.all:
        targets = _discover_editais()
        if not targets:
            print(f"[reindex] Nenhum diretório de edital encontrado em {FINEP_PDFS_DIR}")
            return 1
    elif args.edital_id:
        targets = [args.edital_id]
    else:
        print("[reindex] Forneça <edital_id> ou --all", file=sys.stderr)
        return 2

    print(
        f"[reindex] {len(targets)} edital(is) — "
        f"mode={'queue' if args.queue else 'sync'}, force={args.force}"
    )

    failures = 0
    for eid in targets:
        result = await _process_one(eid, queue=args.queue, force=args.force)
        line = f"  • {result['edital_id']}: {result['status']}"
        print(line)
        if result["status"].startswith("error"):
            failures += 1

    print(f"[reindex] Concluído. Sucessos: {len(targets) - failures}, falhas: {failures}")
    return 0 if failures == 0 else 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Reindex FINEP editais into the edital_chunks table (RAG)."
    )
    parser.add_argument("edital_id", nargs="?", help="ID do edital a reindexar")
    parser.add_argument("--all", action="store_true", help="Reindexa todos os editais")
    parser.add_argument(
        "--queue",
        action="store_true",
        help="Enfileira via procrastinate ao invés de rodar inline",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-indexa mesmo se já houver chunks para o edital",
    )
    args = parser.parse_args()
    if not args.edital_id and not args.all:
        parser.error("forneça <edital_id> ou --all")
    return asyncio.run(_main_async(args))


if __name__ == "__main__":
    sys.exit(main())
