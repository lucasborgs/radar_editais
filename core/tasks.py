"""
procrastinate App and task definitions.

Workers run with:

    python -m procrastinate --app=core.tasks.app worker

Implements ADR M8 (procrastinate replaces pg-boss for background jobs). The
worker uses the service-role Supabase client (via core.db.get_supabase_service)
because it has no request context — see ADR M7. Tasks MUST therefore enforce
tenant boundaries explicitly when relevant (e.g. by scoping queries with
workspace_id).

Note on the connector: procrastinate ships an async-capable PsycopgConnector
(psycopg 3 with asyncio) by default — there is no separate `asyncpg` extra in
upstream procrastinate. PsycopgConnector is the right choice for our async
event loop.
"""
from __future__ import annotations

# Carrega .env ANTES de importar procrastinate — o connector lê DATABASE_URL
# no import time. Quando o worker roda via `python -m procrastinate ...`,
# nada antes do nosso código carrega o env. Deve ser o primeiro import.
from dotenv import load_dotenv

load_dotenv()

import asyncio  # noqa: E402
import json  # noqa: E402
import logging  # noqa: E402
import os  # noqa: E402

from procrastinate import App, PsycopgConnector  # noqa: E402

from core.chunker import chunk_from_blocks  # noqa: E402
from core.content_library import enrich_content  # noqa: E402
from core.db import get_supabase_service  # noqa: E402
from core.embedder import embed_texts  # noqa: E402
from core.structurer import build_or_load_structured_doc  # noqa: E402
from pipeline.adapters.base import get_adapter  # noqa: E402

logger = logging.getLogger(__name__)

# Same list used by WritingSession to skip non-edital boilerplate PDFs.
# Descoberta de PDFs e dedup de versão são L1 (FINEP-específico) — vivem em
# pipeline/adapters/finep.py. Aqui (L3) tasks.py consome Documento Canônico.


def _build_app() -> App:
    """Build the procrastinate App singleton using DATABASE_URL from env."""
    dsn = os.getenv("DATABASE_URL")
    if not dsn:
        # Allow import without DATABASE_URL (e.g. for unit tests that monkey-patch
        # tasks). The connector will fail loudly the first time it tries to open
        # a connection if DATABASE_URL is still missing.
        logger.warning(
            "DATABASE_URL not set; procrastinate App initialized without a DSN. "
            "Set DATABASE_URL before opening the App or running the worker."
        )
        connector = PsycopgConnector()
    else:
        connector = PsycopgConnector(conninfo=dsn)
    return App(connector=connector)


app = _build_app()


@app.task(name="enrich_content", queue="default")
async def enrich_content_task(item_id: str) -> None:
    """Enrich a content_items row via LLM and persist summary / key_facts / themes.

    Runs against the service-role Supabase client (bypasses RLS). Tenant safety
    is preserved because we only touch the single row identified by `item_id`
    and never reveal cross-tenant data via the row payload.

    If the row no longer exists (e.g. user deleted it before the worker picked
    the job up), we log and return without raising — there's nothing to do and
    a retry won't change the outcome. Errors raised by `enrich_content` itself
    are allowed to propagate so procrastinate can apply its retry/backoff
    policy.
    """
    db = get_supabase_service()

    # supabase-py is sync; wrap in to_thread so we don't block the event loop.
    fetch = await asyncio.to_thread(
        lambda: db.table("content_items")
        .select("id, workspace_id, content")
        .eq("id", item_id)
        .maybe_single()
        .execute()
    )
    row = fetch.data if fetch else None
    if not row:
        logger.info("enrich_content_task: item_id=%s not found (skipping)", item_id)
        return

    content = row.get("content") or ""
    enriched = await asyncio.to_thread(enrich_content, content)

    await asyncio.to_thread(
        lambda: db.table("content_items")
        .update(
            {
                "summary": enriched["summary"],
                "key_facts": enriched["key_facts"],
                "themes": enriched["themes"],
                "importance_score": enriched["importance_score"],
                "enrichment_status": "done",
            }
        )
        .eq("id", item_id)
        .execute()
    )
    logger.info(
        "enrich_content_task: item_id=%s enriched (importance=%d)",
        item_id, enriched["importance_score"],
    )

    # Encadeia embedding (Fase 2 #15) — gera o vetor para retrieval multi-critério.
    async with app.open_async():
        await app.configure_task("embed_content").defer_async(item_id=item_id)


@app.task(name="embed_content", queue="default")
async def embed_content_task(item_id: str) -> None:
    """Gera embedding text-embedding-3-large (1536d) para um content_item.

    Embeds: title + summary + content (concatenados; importante manter title
    no início porque carrega sinal forte). Roda após enrich_content_task —
    summary já está populado e contribui para a representação.

    Idempotente: re-rodar simplesmente sobrescreve o vetor.
    """
    from core.embedder import embed_query  # import local para evitar custo ao boot

    db = get_supabase_service()

    fetch = await asyncio.to_thread(
        lambda: db.table("content_items")
        .select("id, title, content, summary")
        .eq("id", item_id)
        .maybe_single()
        .execute()
    )
    row = fetch.data if fetch else None
    if not row:
        logger.info("embed_content_task: item_id=%s not found (skipping)", item_id)
        return

    title = row.get("title") or ""
    summary = row.get("summary") or ""
    content = (row.get("content") or "")[:6000]  # ~75% do budget de 8k tokens
    text_to_embed = f"{title}\n\n{summary}\n\n{content}".strip()

    if not text_to_embed:
        logger.warning("embed_content_task: item_id=%s sem conteúdo, pulando", item_id)
        return

    vector = await asyncio.to_thread(embed_query, text_to_embed)

    await asyncio.to_thread(
        lambda: db.table("content_items")
        .update({"embedding": vector})
        .eq("id", item_id)
        .execute()
    )
    logger.info("embed_content_task: item_id=%s embedded (1536d)", item_id)


# =============================================================================
# Reflection (Fase 2 #17)
# =============================================================================

@app.task(name="reflect_workspace", queue="default")
async def reflect_workspace_task(workspace_id: str) -> None:
    """Gera reflexão sobre outcomes do workspace e persiste em reflection_insights.

    Trigger atual: on-demand via POST /me/reflect ou agendamento manual.
    Trigger futuro (ADR §4.3): a cada 5 outcomes acumulados em application_log,
    ouvir via Postgres NOTIFY ou cron periódico procrastinate.
    """
    from core.reflection_service import reflect_workspace

    db = get_supabase_service()
    result = await asyncio.to_thread(reflect_workspace, db, workspace_id)
    logger.info(
        "reflect_workspace_task: workspace=%s outcomes=%d obs=%d patterns=%d skip=%s",
        workspace_id,
        result["outcomes_considered"],
        result["observations_inserted"],
        result["patterns_inserted"],
        result.get("skipped_reason"),
    )


# =============================================================================
# RAG indexing
# =============================================================================

# Extração de PDF (table-aware, descoberta, dedup de versão) é L1 FINEP —
# vive em pipeline/adapters/finep.py via o Source Adapter (WIKI.md §12).


def _build_chunks_for_edital(edital_id: str) -> list[dict]:
    """Pipeline da Retrieval gold (L3b, §12): Source Adapter → Documento
    Canônico → structurer/silver → chunk_from_blocks.

    Síncrono — chamar via asyncio.to_thread. Falha silenciosa do structurer
    (silver vazio) → retorna [] e o caller limpa as linhas antigas, conforme
    o contrato §11.4 ("falha LLM → B não indexa").
    """
    adapter = get_adapter("finep")
    documents = adapter.to_documents(edital_id)
    if not documents:
        logger.warning(
            "chunk_edital_task: adapter não retornou conteúdo p/ edital=%s",
            edital_id,
        )
        return []

    blocks = build_or_load_structured_doc("finep", edital_id, documents)
    if not blocks:
        logger.warning(
            "chunk_edital_task: silver vazio p/ edital=%s (structurer falhou)",
            edital_id,
        )
        return []

    chunks = chunk_from_blocks(blocks)
    for global_idx, chunk in enumerate(chunks):
        chunk["chunk_index"] = global_idx
    return chunks


@app.task(name="chunk_edital", queue="default")
async def chunk_edital_task(edital_id: str) -> None:
    """Index one edital: PDFs → chunks → embeddings → upsert into edital_chunks.

    Idempotent: deletes any existing rows for this edital_id before inserting
    the fresh batch. Re-runnable without manual cleanup.

    Error handling:
      - Extraction failure on one PDF logs and is skipped (the others proceed).
      - Embedding/DB failures propagate so procrastinate can retry with
        exponential backoff (configured at the worker level).
    """
    db = get_supabase_service()

    chunks = await asyncio.to_thread(_build_chunks_for_edital, edital_id)
    if not chunks:
        logger.info(
            "chunk_edital_task: edital=%s não produziu chunks (sem PDFs ou texto vazio)",
            edital_id,
        )
        # Still clear stale rows so callers can rely on the invariant
        # "indexed run completed → DB reflects the current PDFs."
        await asyncio.to_thread(
            lambda: db.table("edital_chunks").delete().eq("edital_id", edital_id).execute()
        )
        return

    texts = [c["text"] for c in chunks]
    logger.info(
        "chunk_edital_task: edital=%s gerou %d chunks, embedando…",
        edital_id, len(chunks),
    )
    embeddings = await asyncio.to_thread(embed_texts, texts)

    if len(embeddings) != len(chunks):
        raise RuntimeError(
            f"chunk_edital_task: mismatch embeddings ({len(embeddings)}) vs "
            f"chunks ({len(chunks)}) para edital={edital_id}"
        )

    rows = [
        {
            "edital_id": edital_id,
            "chunk_index": c["chunk_index"],
            "text": c["text"],
            "section": c.get("section"),
            "source_file": c.get("source_file"),
            "page_range": c.get("page_range"),
            "embedding": emb,
            "metadata": c.get("metadata") or {},
        }
        for c, emb in zip(chunks, embeddings, strict=True)
    ]

    # 1. Delete existing rows for idempotency.
    await asyncio.to_thread(
        lambda: db.table("edital_chunks").delete().eq("edital_id", edital_id).execute()
    )

    # 2. Insert new chunks. supabase-py serialises the embedding list to JSON,
    #    which PostgREST forwards to pgvector — accepted as a vector literal.
    #    If the volume per call gets large, split into pages of ~200 rows so
    #    we don't blow past the PostgREST request size limit (~1MB default).
    BATCH = 200  # noqa: N806
    for start in range(0, len(rows), BATCH):
        page = rows[start:start + BATCH]
        try:
            await asyncio.to_thread(
                lambda p=page: db.table("edital_chunks").insert(p).execute()
            )
        except Exception as e:
            # Fall back to direct psycopg if supabase-py choked on the vector
            # column for any reason. We loop row-by-row in the fallback so
            # we still get a partial commit if only some rows are problematic.
            logger.warning(
                "chunk_edital_task: insert via supabase-py falhou (%s); "
                "tentando psycopg direto.", e,
            )
            await asyncio.to_thread(_insert_chunks_psycopg, page)

    logger.info(
        "chunk_edital_task: edital=%s indexado com %d chunks (text-embedding-3-large)",
        edital_id, len(rows),
    )


def _insert_chunks_psycopg(rows: list[dict]) -> None:
    """Direct psycopg insert as a fallback when supabase-py mishandles the
    vector column. DATABASE_URL must be set."""
    import psycopg

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise RuntimeError(
            "chunk_edital_task fallback: DATABASE_URL não configurada"
        )

    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        for r in rows:
            emb = r["embedding"]
            # pgvector textual literal — '[v1,v2,...]'.
            vec_literal = "[" + ",".join(f"{x:.7f}" for x in emb) + "]"
            meta_literal = json.dumps(r.get("metadata") or {})
            cur.execute(
                """
                INSERT INTO public.edital_chunks
                    (edital_id, chunk_index, text, section, source_file, page_range, embedding, metadata)
                VALUES (%s, %s, %s, %s, %s, %s, %s::vector, %s::jsonb)
                """,
                (
                    r["edital_id"], r["chunk_index"], r["text"],
                    r.get("section"), r.get("source_file"), r.get("page_range"),
                    vec_literal, meta_literal,
                ),
            )


# =============================================================================
# Daily ETL — Pipeline #28
# =============================================================================
# Procrastinate periodic: cron diário 03:00 UTC roda scrapers ativos +
# enfileira chunk_edital para editais novos. Falhas são tipadas e
# persistidas em pipeline_errors (taxonomia ADR §3.7).
#
# Para mudar o horário: ajuste o cron expression (formato padrão crontab).
# Para ativar mais fontes: adicione em SCRAPER_REGISTRY (não em INACTIVE_SCRAPERS).


@app.periodic(cron="0 3 * * *")
@app.task(name="run_daily_etl", queue="etl")
async def run_daily_etl_task(timestamp: int) -> None:
    """Cron diário (3am UTC): roda scrapers ativos e dispara chunking.

    Para cada fonte em pipeline.extractors.SCRAPER_REGISTRY:
      1. Roda o scraper (captura novos editais em bronze_data/)
      2. Falhas tipadas (TimeoutError, ParseError, etc.) viram rows em
         pipeline_errors via log_pipeline_error
      3. Para cada edital novo detectado, enfileira chunk_edital_task
         (não roda inline — workers picam de outra fila)

    O argumento `timestamp` vem do procrastinate periodic e representa o
    instante agendado da execução (UNIX epoch).
    """
    from core.pipeline_errors import (
        PipelineError,
        classify_requests_error,
        log_pipeline_error,
    )

    logger.info("run_daily_etl_task: iniciando (timestamp=%s)", timestamp)

    # Import tardio para evitar custo no boot do worker
    from pipeline.extractors import SCRAPER_REGISTRY

    total_new = 0
    for source_name, cfg in SCRAPER_REGISTRY.items():
        cls = cfg["cls"]
        try:
            scraper = cls(**cfg.get("kwargs", {}))
            results = await asyncio.to_thread(scraper.extract)
            new_count = len(results) if results else 0
            total_new += new_count
            logger.info(
                "run_daily_etl_task: %s — %d resultados", source_name, new_count,
            )

            # Enfileira chunk_edital_task para cada edital com PDFs novos
            for item in (results or []):
                edital_id = item.get("chamada_id") or item.get("id")
                if not edital_id:
                    continue
                try:
                    await app.configure_task("chunk_edital").defer_async(
                        edital_id=str(edital_id),
                    )
                except Exception as enqueue_err:
                    logger.warning(
                        "Falha ao enfileirar chunk_edital para %s/%s: %s",
                        source_name, edital_id, enqueue_err,
                    )

        except PipelineError as e:
            await asyncio.to_thread(
                log_pipeline_error,
                source=source_name, error=e,
                context={"stage": "scraper"},
            )
            logger.warning("run_daily_etl_task: %s falhou (%s): %s",
                          source_name, e.category, e)
        except Exception as raw_err:
            # Genérico: tenta classificar (HTTPError → ParseError/Timeout, etc.)
            typed = classify_requests_error(raw_err)
            await asyncio.to_thread(
                log_pipeline_error,
                source=source_name, error=typed,
                context={"stage": "scraper", "original_exception": type(raw_err).__name__},
            )
            logger.warning(
                "run_daily_etl_task: %s falhou (classificado como %s): %s",
                source_name, typed.category, raw_err,
            )

    logger.info("run_daily_etl_task: concluído (total=%d novos)", total_new)
