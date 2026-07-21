"""Backfill do corpus de RAG: chunka + embeda os editais do catálogo inteiro.

Companheiro one-time do cron `warm_edital_chunks` (eager chunking — reversão
operacional do lazy/PR #44; ver adendo em docs/historical/hardening-pre-beta.md).
Roda a MESMA task do runtime (`chunk_edital_task`) diretamente, sem worker,
então o gate de content_hash vale: editais já indexados e inalterados são
pulados sem custo. Re-rodar é sempre seguro.

Uso:
    python scripts/backfill_chunks.py                # catálogo inteiro
    python scripts/backfill_chunks.py --limit 2      # smoke test
    python scripts/backfill_chunks.py --status ABERTA
    python scripts/backfill_chunks.py --concurrency 3
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import time

from radar.core.environment import assert_database_target, load_environment_profile

load_environment_profile()

from radar.core.kg.entity_catalog import list_editais  # noqa: E402
from radar.core.tasks import chunk_edital_task  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("backfill_chunks")


async def _run(ids: list[str], concurrency: int) -> tuple[int, int]:
    sem = asyncio.Semaphore(concurrency)
    total, ok, failed = len(ids), 0, 0

    async def _one(eid: str) -> None:
        nonlocal ok, failed
        async with sem:
            t0 = time.time()
            try:
                await chunk_edital_task(eid)
                ok += 1
                logger.info("[%d/%d] %s ok em %.1fs", ok + failed, total, eid, time.time() - t0)
            except Exception as e:  # noqa: BLE001 — 1 edital quebrado não derruba o backfill
                failed += 1
                logger.error("[%d/%d] %s FALHOU: %s", ok + failed, total, eid, e)

    await asyncio.gather(*(_one(e) for e in ids))
    return ok, failed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=None, help="máx. de editais (smoke test)")
    parser.add_argument("--status", type=str, default=None, help="filtra por status (ex.: ABERTA)")
    parser.add_argument("--concurrency", type=int, default=3, help="editais em paralelo (default 3)")
    args = parser.parse_args()
    assert_database_target("RAG chunks backfill")

    cards = list_editais(status=args.status, limit=args.limit or 1000)
    ids = [c["id"] for c in cards]
    logger.info("backfill: %d editais no escopo (concurrency=%d)", len(ids), args.concurrency)

    t0 = time.time()
    ok, failed = asyncio.run(_run(ids, args.concurrency))
    logger.info("backfill concluído: %d ok, %d falhas, %.0fs", ok, failed, time.time() - t0)
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
