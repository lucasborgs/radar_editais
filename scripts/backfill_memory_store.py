"""Backfill dos reflection_insights ativos para o LangGraph Store (Etapa 5).

Embeda todos os insights ativos (deactivated_at IS NULL) de TODOS os workspaces no
PostgresStore (namespace por workspace_id), para que a recuperação semântica da
WritingSession encontre o corpus já existente — sem isto, o Store só ganha insights
gerados APÓS o deploy da Etapa 5.

Idempotente: `store.put(key=id)` sobrescreve; rodar de novo re-embeda (custo de
embedding, mas resultado igual). Usa o client service-role (lê cross-workspace,
bypassa RLS) — a leitura é intencional e administrativa.

Embeddings OS por env (EMBEDDING_BACKEND=sentence_transformers → zero token OpenAI).

    python scripts/backfill_memory_store.py

Requer DATABASE_URL (Store real) + acesso ao Supabase. Sem DATABASE_URL o runtime usa
InMemoryStore (efêmero) e o backfill não persiste — aborta com mensagem.
"""
from __future__ import annotations

from dotenv import load_dotenv

load_dotenv()

import logging  # noqa: E402
import os  # noqa: E402
import sys  # noqa: E402
from collections import defaultdict  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("backfill_memory_store")


def main() -> int:
    if not os.environ.get("DATABASE_URL"):
        logger.error("DATABASE_URL ausente — Store seria InMemory (efêmero). Abortando.")
        return 1

    from core.db import get_supabase_service
    from core.llm.agent_graph import memory_put, shutdown_writing_runtime

    db = get_supabase_service()
    try:
        rows = (
            db.table("reflection_insights")
            .select("id, workspace_id, insight, level")
            .is_("deactivated_at", "null")
            .execute()
        ).data or []

        by_ws: dict[str, list[dict]] = defaultdict(list)
        for r in rows:
            ws = r.get("workspace_id")
            if ws and r.get("insight"):
                by_ws[str(ws)].append(r)

        total = 0
        for ws, items in by_ws.items():
            for r in items:
                memory_put(ws, str(r["id"]), r["insight"], level=r.get("level"))
                total += 1
            logger.info("workspace=%s: %d insight(s) embeddado(s)", ws, len(items))

        logger.info("Backfill concluído: %d insight(s) em %d workspace(s).", total, len(by_ws))
        return 0
    finally:
        shutdown_writing_runtime()


if __name__ == "__main__":
    sys.exit(main())
