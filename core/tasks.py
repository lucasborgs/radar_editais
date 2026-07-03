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
import hashlib  # noqa: E402
import json  # noqa: E402
import logging  # noqa: E402
import os  # noqa: E402

from procrastinate import App, PsycopgConnector, RetryStrategy  # noqa: E402

from core.db import get_supabase_service  # noqa: E402
from core.logging_config import setup_logging  # noqa: E402
from core.notify import send_alert  # noqa: E402
from core.retrieval.chunker import chunk_from_blocks  # noqa: E402
from core.retrieval.embedder import EMBEDDING_MODEL, embed_texts  # noqa: E402
from core.retrieval.retriever import RETRIEVAL_EMBEDDING_COLUMN  # noqa: E402
from core.services.content_library import enrich_content  # noqa: E402
from core.structurer import build_or_load_structured_doc  # noqa: E402
from pipeline.adapters.base import get_adapter  # noqa: E402

setup_logging()
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

# Retry com backoff exponencial para as tasks UNITÁRIAS e idempotentes (spec
# hardening-pre-beta 4.1). O default do procrastinate é SEM retry — sem isto,
# uma falha transiente (timeout de LLM/DB) matava o job de vez. Espera 4^1=4s
# e 4^2=16s entre as 3 tentativas. Os wrappers de cron (run_daily_etl,
# discover_opportunities, synthesize_patterns_cron) NÃO têm retry de propósito:
# cron re-roda no dia seguinte e a falha vira alerta por e-mail (core/notify).
UNIT_TASK_RETRY = RetryStrategy(max_attempts=3, exponential_wait=4)


@app.task(name="enrich_content", queue="default", retry=UNIT_TASK_RETRY)
async def enrich_content_task(item_id: str) -> None:
    """Enrich a content_items row via LLM and persist summary / key_facts / themes.

    Runs against the service-role Supabase client (bypasses RLS). Tenant safety
    is preserved because we only touch the single row identified by `item_id`
    and never reveal cross-tenant data via the row payload.

    If the row no longer exists (e.g. user deleted it before the worker picked
    the job up), we log and return without raising — there's nothing to do and
    a retry won't change the outcome. Erros levantados pelo próprio
    `enrich_content` propagam e o procrastinate re-tenta com backoff
    exponencial (retry=UNIT_TASK_RETRY no decorator desta task).
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
    # NÃO usar `async with app.open_async()` aqui: a task roda DENTRO do worker,
    # que já mantém o connector pool aberto. Reabrir + sair do `with` fecha o
    # pool e quebra a próxima persistência de status do worker (AppNotOpen).
    # Callers fora do worker (API: create_item/update_item) é que precisam
    # abrir o app antes do defer_async.
    await app.configure_task("embed_content").defer_async(item_id=item_id)


@app.task(name="embed_content", queue="default", retry=UNIT_TASK_RETRY)
async def embed_content_task(item_id: str) -> None:
    """Gera embedding text-embedding-3-large (1536d) para um content_item.

    Embeds: title + summary + content (concatenados; importante manter title
    no início porque carrega sinal forte). Roda após enrich_content_task —
    summary já está populado e contribui para a representação.

    Idempotente: re-rodar simplesmente sobrescreve o vetor.
    """
    from core.retrieval.embedder import embed_query  # import local para evitar custo ao boot

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


@app.task(name="build_company_hypergraph", queue="default", retry=UNIT_TASK_RETRY)
async def build_company_hypergraph_task(workspace_id: str) -> None:
    """(Re)constrói o hipergrado durável da empresa a partir do corpus já no DB.

    Orquestra corpus → extração → completude → persistência via
    `core.services.company_corpus.build_company_hypergraph` (Sprint 2). Essa
    chamada é SÍncrona e bloqueia (1 LLM call + embeddings), então a envolvemos
    em `asyncio.to_thread` para não travar o event loop do worker.

    Roda contra o cliente service-role (sem request context) — a fronteira de
    tenant é o próprio `workspace_id`. Exceções propagam e o procrastinate
    re-tenta com backoff exponencial (retry=UNIT_TASK_RETRY, como nas demais
    tasks unitárias).
    """
    from core.services.company_corpus import build_company_hypergraph

    db = get_supabase_service()

    logger.info("build_company_hypergraph_task: workspace_id=%s iniciando", workspace_id)
    result = await asyncio.to_thread(build_company_hypergraph, db, workspace_id)
    logger.info(
        "build_company_hypergraph_task: workspace_id=%s concluído "
        "(nós=%d, docs=%d, completude=%.2f)",
        workspace_id,
        len(result["nodes"]),
        result["n_docs"],
        result["completude"],
    )


# =============================================================================
# Reflection (Fase 2 #17)
# =============================================================================

@app.task(name="reflect_workspace", queue="default", retry=UNIT_TASK_RETRY)
async def reflect_workspace_task(workspace_id: str) -> None:
    """Gera reflexão sobre outcomes do workspace e persiste em reflection_insights.

    Triggers:
      - Automático: cada transição de status para outcome (submetida/aprovada/
        reprovada) em PUT /applications/{id}/status enfileira esta task.
      - On-demand: POST /me/reflect ou agendamento manual.

    A task self-gateia em MIN_OUTCOMES_FOR_REFLECTION (pula se há poucos
    outcomes) e supersede o lote de insights anterior, então é seguro
    enfileirar a cada outcome.
    """
    from core.reflection_service import reflect_workspace

    db = get_supabase_service()
    result = await asyncio.to_thread(reflect_workspace, db, workspace_id)
    logger.info(
        "reflect_workspace_task: workspace=%s outcomes=%d obs=%d skip=%s",
        workspace_id,
        result["outcomes_considered"],
        result["observations_inserted"],
        result.get("skipped_reason"),
    )


@app.task(name="synthesize_patterns", queue="default", retry=UNIT_TASK_RETRY)
async def synthesize_patterns_task(workspace_id: str) -> None:
    """Sintetiza padrões (level 2) a partir do corpus de observações (level 1).

    Etapa de longo prazo, separada de reflect_workspace: lê as observações
    factuais (level 1) acumuladas e ativas do workspace e destila padrões
    interpretativos (level 2) + weight_suggestions, sem zerar o corpus de
    level 1.

    Triggers:
      - Periódico: cron semanal (domingo 05:00 UTC) para todos os workspaces
        ativos (ver synthesize_patterns_cron).
      - On-demand: POST /me/synthesize.

    Self-gateia em MIN_LEVEL1_FOR_SYNTHESIS (pula se há poucas observações
    ativas), então é seguro enfileirar livremente.
    """
    from core.reflection_service import synthesize_patterns

    db = get_supabase_service()
    result = await asyncio.to_thread(synthesize_patterns, db, workspace_id)
    logger.info(
        "synthesize_patterns_task: workspace=%s level1=%d patterns=%d auto_applied=%d skip=%s",
        workspace_id,
        result["level1_considered"],
        result["patterns_inserted"],
        len(result.get("auto_applied") or []),
        result.get("skipped_reason"),
    )


@app.periodic(cron="0 5 * * 0")
@app.task(name="synthesize_patterns_cron", queue="default")
async def synthesize_patterns_cron(timestamp: int) -> None:
    """Cron semanal (domingo 05:00 UTC): enfileira síntese de padrões para
    todos os workspaces ativos.

    Lê os ids de workspaces e enfileira `synthesize_patterns_task` para cada um.
    A própria task self-gateia (pula workspaces com poucas observações ativas),
    então enfileirar todos é barato. `timestamp` vem do procrastinate periodic
    (UNIX epoch).
    """
    db = get_supabase_service()

    fetch = await asyncio.to_thread(
        lambda: db.table("workspaces").select("id").execute()
    )
    workspace_ids = [row["id"] for row in (fetch.data or []) if row.get("id")]
    logger.info(
        "synthesize_patterns_cron: enfileirando síntese para %d workspaces (timestamp=%s)",
        len(workspace_ids), timestamp,
    )
    for ws_id in workspace_ids:
        try:
            await app.configure_task("synthesize_patterns").defer_async(
                workspace_id=ws_id,
            )
        except Exception as e:
            logger.warning(
                "synthesize_patterns_cron: falha ao enfileirar ws=%s: %s", ws_id, e,
            )


# =============================================================================
# Higiene do runtime agêntico — purge de checkpoints LangGraph (hardening PR6.1)
# =============================================================================

def _purge_stale_checkpoints(retention_days: int) -> dict[str, int]:
    """Deleta do schema agent_memory os threads de checkpoint cujo ÚLTIMO
    checkpoint é mais antigo que `retention_days`. Síncrono — rodar via to_thread.

    O timestamp vem do próprio checkpoint (JSONB, key `ts`, ISO-8601): o schema
    do AsyncPostgresSaver não tem coluna de data. O thread_id é
    {workspace_id}:{session_id}:{turn} — cada turno é um thread permanente que
    só é relido em resume de interrupt DENTRO do próprio turno, então threads
    velhos são lixo puro (F9: nada mais deletava esses rows).
    """
    import psycopg

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise RuntimeError("purge_checkpoints: DATABASE_URL não configurada")

    counts: dict[str, int] = {"threads": 0}
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        try:
            cur.execute(
                """
                SELECT thread_id
                FROM agent_memory.checkpoints
                GROUP BY thread_id
                HAVING max((checkpoint->>'ts')::timestamptz)
                       < now() - make_interval(days => %s)
                """,
                (retention_days,),
            )
        except psycopg.errors.UndefinedTable:
            # Ambiente fresco: o setup() do Saver ainda não criou as tabelas.
            logger.info("purge_checkpoints: agent_memory.checkpoints não existe — no-op")
            return counts
        stale = [row[0] for row in cur.fetchall()]
        if not stale:
            return counts
        counts["threads"] = len(stale)
        # Ordem filho→pai (writes/blobs antes de checkpoints) por clareza; não há
        # FK entre elas no schema do langgraph, mas mantém o invariante "nunca
        # existe write/blob órfão de checkpoint" mesmo num crash no meio.
        for table in ("checkpoint_writes", "checkpoint_blobs", "checkpoints"):
            cur.execute(
                f"DELETE FROM agent_memory.{table} WHERE thread_id = ANY(%s)",  # noqa: S608 — nome de tabela é literal do código
                (stale,),
            )
            counts[table] = cur.rowcount
    return counts


@app.periodic(cron="0 6 * * 0")
@app.task(name="purge_agent_checkpoints", queue="default")
async def purge_agent_checkpoints(timestamp: int) -> None:
    """Cron semanal (domingo 06:00 UTC): purga do schema agent_memory os threads
    de checkpoint mais antigos que CHECKPOINT_RETENTION_DAYS (default 30).

    Sem retry de propósito (padrão dos wrappers de cron): falha re-roda na
    semana seguinte. Sem DATABASE_URL (dev/teste com InMemorySaver) é no-op.
    `timestamp` vem do procrastinate periodic (UNIX epoch).
    """
    if not os.environ.get("DATABASE_URL"):
        logger.info("purge_agent_checkpoints: DATABASE_URL ausente — no-op")
        return
    retention = int(os.getenv("CHECKPOINT_RETENTION_DAYS", "30"))
    counts = await asyncio.to_thread(_purge_stale_checkpoints, retention)
    logger.info(
        "purge_agent_checkpoints: %d threads purgados (retention=%dd, timestamp=%s): %s",
        counts.get("threads", 0), retention, timestamp, counts,
    )


# =============================================================================
# RAG indexing
# =============================================================================

# Extração de PDF (table-aware, descoberta, dedup de versão) é L1 FINEP —
# vive em pipeline/adapters/finep.py via o Source Adapter (WIKI.md §12).


def _build_chunks_for_edital(edital_id: str) -> list[dict]:
    """Pipeline da Retrieval gold (L3b, §12): Source Adapter → Documento
    Canônico → structurer/silver → chunk_from_blocks.

    `edital_id` chega prefixado (`finep:782`). Source vem do prefixo; adapter
    e structurer recebem o native_id (eles operam em escopo de fonte).

    Síncrono — chamar via asyncio.to_thread. Falha silenciosa do structurer
    (silver vazio) → retorna [] e o caller limpa as linhas antigas, conforme
    o contrato §11.4 ("falha LLM → B não indexa").
    """
    from core.kg import source_docs  # noqa: PLC0415
    from core.kg.edital_id import native_id_of, source_of  # noqa: PLC0415

    source = source_of(edital_id)
    native = native_id_of(edital_id)

    # Documento Canônico DURÁVEL-primeiro (robustez contra o disco efêmero do
    # worker — spec docs/specs/durable-source-docs.md). O disco vira só
    # cache/fallback. Se o durável faltar mas o disco tiver o bronze (ex.: logo
    # após scrape, ou edital pré-feature), backfilla o durável (self-healing).
    documents = source_docs.load(edital_id)
    if not documents:
        adapter = get_adapter(source)
        documents = adapter.to_documents(native)
        if documents:
            source_docs.save(edital_id, source, documents)
    if not documents:
        logger.warning(
            "chunk_edital_task: sem conteúdo-fonte p/ edital=%s "
            "(durável vazio e disco sem bronze — redeploy efêmero?)",
            edital_id,
        )
        return []

    blocks = build_or_load_structured_doc(source, native, documents)
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


def _index_is_current(db, edital_id: str, content_hash: str, n_chunks: int) -> bool:
    """Gate de re-indexação: True se o índice existente está COMPLETO e com o
    mesmo conteúdo (pula o re-embed — corta custo OpenAI, requisito 3).

    Duas condições, ambas necessárias:
      1. O `content_hash` do marcador (metadata do chunk_index=0, gravado SÓ
         após o último batch — ver chunk_edital_task) bate com o hash do
         conteúdo recém-gerado.
      2. `count(*)` das rows do edital == `n_chunks` esperado. Como o hash é
         md5 dos próprios textos, hash igual ⇒ mesma lista de chunks ⇒ o count
         atual serve de expectativa. Pega indexação parcial (run antiga que
         morreu no meio dos batches, deleção manual) — inclusive as legadas,
         anteriores ao marcador-no-fim.

    False = reindexar (nunca indexado, conteúdo mudou, ou índice incompleto).
    Qualquer erro de lookup também retorna False — na dúvida, reindexa.
    """
    try:
        res = (
            db.table("edital_chunks")
            .select("metadata")
            .eq("edital_id", edital_id)
            .eq("chunk_index", 0)
            .maybe_single()
            .execute()
        )
        row = res.data if res else None
        if not (row and isinstance(row.get("metadata"), dict)):
            return False
        if row["metadata"].get("content_hash") != content_hash:
            return False
        cnt = (
            db.table("edital_chunks")
            .select("id", count="exact")
            .eq("edital_id", edital_id)
            .execute()
        )
        return (getattr(cnt, "count", None) or 0) == n_chunks
    except Exception as e:
        logger.debug("chunk_edital_task: lookup do gate falhou p/ %s: %s", edital_id, e)
        return False


@app.task(name="chunk_edital", queue="default", retry=UNIT_TASK_RETRY)
async def chunk_edital_task(edital_id: str, force: bool = False) -> None:
    """Index one edital: PDFs → chunks → embeddings → upsert into edital_chunks.

    Idempotent: deletes any existing rows for this edital_id before inserting
    the fresh batch. Re-runnable without manual cleanup.

    Gate de conteúdo (requisito 3): após o ÚLTIMO batch inserido, grava um
    marcador `{content_hash, n_chunks}` no metadata do chunk_index=0. Em
    re-runs, se o hash bater E o count(*) estiver íntegro, pula o re-embed —
    só editais cujo conteúdo mudou pagam OpenAI. O marcador vem por último de
    propósito: uma run que morre no meio dos inserts deixa o índice SEM
    marcador → a próxima run reindexa em vez de aceitar o parcial como pronto.
    `force=True` ignora o gate (caminho manual `reindex_edital.py --force`).

    Error handling:
      - Extraction failure on one PDF logs and is skipped (the others proceed).
      - Falhas de embedding/DB propagam e o procrastinate re-tenta com backoff
        exponencial (retry=UNIT_TASK_RETRY no decorator desta task).
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

    # Gate: se o conteúdo não mudou E o índice está completo, não re-embeda.
    content_hash = hashlib.md5("\x00".join(texts).encode("utf-8", "ignore")).hexdigest()
    if not force:
        current = await asyncio.to_thread(
            _index_is_current, db, edital_id, content_hash, len(chunks)
        )
        if current:
            logger.info(
                "chunk_edital_task: edital=%s inalterado e íntegro (hash=%s) — skip re-embed",
                edital_id, content_hash[:8],
            )
            return

    logger.info(
        "chunk_edital_task: edital=%s gerou %d chunks, embedando…",
        edital_id, len(chunks),
    )
    # Contextual Retrieval (Anthropic): embeda contexto+corpo; a coluna `text`
    # segue com o corpo original. Desligável por env; gateado pelo content_hash
    # acima (só editais que mudaram re-contextualizam). Ver core/contextual_retrieval.
    from core.contextual_retrieval import contextualize_chunks
    embed_inputs = await asyncio.to_thread(contextualize_chunks, chunks)
    embeddings = await asyncio.to_thread(embed_texts, embed_inputs)

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
            RETRIEVAL_EMBEDDING_COLUMN: emb,
            # SEM content_hash aqui — o marcador de conclusão é gravado no
            # chunk 0 só depois do último batch (ver abaixo).
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

    # 3. Marcador de conclusão — POR ÚLTIMO. Só existe se todos os batches
    #    entraram; uma run que morreu no meio deixa o índice sem marcador e a
    #    próxima run reindexa (gate `_index_is_current` retorna False).
    marker_meta = {
        **(chunks[0].get("metadata") or {}),
        "content_hash": content_hash,
        "n_chunks": len(rows),
    }
    await asyncio.to_thread(
        lambda: db.table("edital_chunks")
        .update({"metadata": marker_meta})
        .eq("edital_id", edital_id)
        .eq("chunk_index", 0)
        .execute()
    )

    logger.info(
        "chunk_edital_task: edital=%s indexado com %d chunks (%s → coluna %s)",
        edital_id, len(rows), EMBEDDING_MODEL, RETRIEVAL_EMBEDDING_COLUMN,
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
            emb = r[RETRIEVAL_EMBEDDING_COLUMN]
            # pgvector textual literal — '[v1,v2,...]'.
            vec_literal = "[" + ",".join(f"{x:.7f}" for x in emb) + "]"
            meta_literal = json.dumps(r.get("metadata") or {})
            # {RETRIEVAL_EMBEDDING_COLUMN} é valor de código (env interna), não input.
            cur.execute(
                f"""
                INSERT INTO public.edital_chunks
                    (edital_id, chunk_index, text, section, source_file, page_range, {RETRIEVAL_EMBEDDING_COLUMN}, metadata)
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
# Para ativar mais fontes: implemente um BaseScraper concreto e registre em SCRAPER_REGISTRY.


def _build_all_silver() -> int:
    """Materializa o silver (structurer) de todos os editais vigentes, durable-first.

    Passo EXPLÍCITO do pipeline. Antes o silver nascia como EFEITO COLATERAL do
    etl_process (síntese de wiki) — que a migração hipergrado vai remover. Aqui o
    silver vira dependência própria do hipergrado E do RAG, não da wiki. Sem LLM.
    Enumera pelo índice (como persist_all_current); tolerante a falha por-edital.
    """
    from core.kg import kg_store, source_docs  # noqa: PLC0415
    from core.kg.edital_id import native_id_of, source_of  # noqa: PLC0415
    from pipeline.adapters.base import get_adapter  # noqa: PLC0415

    # NOTA: enumera via load_all_hypergraphs (hipergrado) — migração coordenada.
    # source_docs.persist_all_current migrou junto no mesmo PR (Phase 3).
    # Quando o índice + build_knowledge_graph forem removidos, TODOS os consumidores
    # restantes (hybrid_match_service, explore_agent, eval/matching…) migram juntos.
    editais = []
    for fk in kg_store.load_all_hypergraphs():
        if "__" not in fk:
            continue
        source, _, native = fk.partition("__")
        editais.append({"id": f"{source}:{native}"})
    n = 0
    for e in editais:
        eid = e.get("id")
        if not eid:
            continue
        try:
            source = source_of(eid)
            native = native_id_of(eid)
            docs = source_docs.load(eid) or get_adapter(source).to_documents(native)
            if docs and build_or_load_structured_doc(source, native, docs):
                n += 1
        except Exception:
            logger.warning("_build_all_silver: falha em %s", eid, exc_info=True)
    return n


@app.periodic(cron="0 3 * * *")
@app.task(name="run_daily_etl", queue="etl")
async def run_daily_etl_task(timestamp: int) -> None:
    """Cron diário (3am UTC): roda scrapers ativos e dispara chunking.

    Wrapper fino de `_run_daily_etl` com o contrato de alerta (spec
    hardening-pre-beta 4.3): falha TOTAL do cron → 1 e-mail de alerta +
    re-raise (o job consta como failed no procrastinate). O cron NÃO tem
    retry= de propósito — re-roda no dia seguinte; o alerta é o mecanismo
    de visibilidade. Falhas de ETAPA (parciais) são agregadas e alertadas
    dentro de `_run_daily_etl` — máx. 1 e-mail por run em qualquer caminho.

    O argumento `timestamp` vem do procrastinate periodic e representa o
    instante agendado da execução (UNIX epoch).
    """
    try:
        await _run_daily_etl(timestamp)
    except Exception as e:
        # send_alert nunca levanta (contrato de core/notify) — o alerta não
        # pode mascarar nem substituir a falha original.
        send_alert(
            "[radar] run_daily_etl: falha total do cron",
            f"run_daily_etl (timestamp={timestamp}) abortou com exceção:\n\n{e!r}",
        )
        raise


async def _run_daily_etl(timestamp: int) -> None:
    """Corpo do ETL diário (ver run_daily_etl_task para o contrato de alerta).

    Para cada fonte em pipeline.extractors.SCRAPER_REGISTRY:
      1. Roda o scraper (captura novos editais em data/bronze/)
      2. Falhas tipadas (TimeoutError, ParseError, etc.) viram rows em
         pipeline_errors via log_pipeline_error
      3. Para cada edital novo detectado, enfileira chunk_edital_task
         (não roda inline — workers picam de outra fila)

    Falhas de scraper/etapa não derrubam a run (cada bloco tem try/except
    próprio), mas são acumuladas em `step_errors` e viram UM e-mail agregado
    ao final (spec hardening-pre-beta 4.3).
    """
    from core.pipeline_errors import (
        PipelineError,
        classify_requests_error,
        log_pipeline_error,
    )

    logger.info("run_daily_etl_task: iniciando (timestamp=%s)", timestamp)

    # Import tardio para evitar custo no boot do worker
    from pipeline.extractors import SCRAPER_REGISTRY

    # Falhas parciais da run (scraper por fonte + etapas pós-scraping).
    # Não derrubam a run; viram 1 e-mail AGREGADO ao final.
    step_errors: list[str] = []

    total_new = 0
    for source_key, cfg in SCRAPER_REGISTRY.items():
        cls = cfg["cls"]
        # Slug canônico do registry (chave) é o source para prefixo §12.
        # `display_name` (ex: "FINEP") é só pra log/UX, não pra prefixo.
        source = cfg.get("source", source_key)
        display_name = cfg.get("display_name", source_key)
        try:
            scraper = cls(**cfg.get("kwargs", {}))
            results = await asyncio.to_thread(scraper.extract)
            new_count = len(results) if results else 0
            total_new += new_count
            logger.info(
                "run_daily_etl_task: %s — %d resultados", display_name, new_count,
            )

            # Chunking é LAZY (spec docs/specs/lazy-chunking.md): o ETL NÃO chunka
            # mais todo edital scraped. Os chunks (RAG fino) nascem sob demanda
            # quando o usuário engaja o edital (prefetch no brief / writing-start).
            # O cron segue só scrape + build KG (tier grosso: navegação/match).

        except PipelineError as e:
            await asyncio.to_thread(
                log_pipeline_error,
                source=source, error=e,
                context={"stage": "scraper"},
            )
            logger.warning("run_daily_etl_task: %s falhou (%s): %s",
                          display_name, e.category, e)
            step_errors.append(f"scraper {display_name} [{e.category}]: {e}")
        except Exception as raw_err:
            # Genérico: tenta classificar (HTTPError → ParseError/Timeout, etc.)
            typed = classify_requests_error(raw_err)
            await asyncio.to_thread(
                log_pipeline_error,
                source=source, error=typed,
                context={"stage": "scraper", "original_exception": type(raw_err).__name__},
            )
            logger.warning(
                "run_daily_etl_task: %s falhou (classificado como %s): %s",
                display_name, typed.category, raw_err,
            )
            step_errors.append(
                f"scraper {display_name} [{typed.category}]: {raw_err}"
            )

    # -------------------------------------------------------------------
    # Pós-scraping: reconstruir índice e sintetizar wiki pages.
    # Antes esses dois passos eram CLI manuais — o cron só enfileirava
    # chunk_edital. Resultado: vigência, prazos e os campos sintetizados
    # (objective/key_requirements) ficavam congelados entre execuções
    # manuais. Aqui fechamos o ciclo (requisito 3 + gap de vigência P0).
    # -------------------------------------------------------------------

    # 1) Índice vigentes/histórico a partir do bronze recém-salvo.
    #    Pure-Python (sem LLM) — barato e idempotente.
    try:
        from pipeline import build_knowledge_graph  # noqa: PLC0415
        await asyncio.to_thread(build_knowledge_graph.main)
        logger.info("run_daily_etl_task: índice reconstruído (vigentes + histórico)")
    except Exception as e:
        logger.error("run_daily_etl_task: falha ao reconstruir índice: %s", e)
        step_errors.append(f"reconstrução do índice: {e}")

    # 1b) Persiste o Documento Canônico durável (§12.3) enquanto o bronze
    #     recém-scraped está no disco. O FS do worker é EFÊMERO: sem isto, o
    #     próximo redeploy apaga o bronze e o chunk_edital (lazy) produz 0 chunks.
    #     Barato (extração já é eager; sem LLM). Spec: docs/specs/durable-source-docs.md.
    try:
        from core.kg.source_docs import persist_all_current  # noqa: PLC0415
        n_docs = await asyncio.to_thread(persist_all_current)
        logger.info("run_daily_etl_task: %d documentos-fonte persistidos (durável)", n_docs)
    except Exception as e:
        logger.error("run_daily_etl_task: falha ao persistir documentos-fonte: %s", e)
        step_errors.append(f"persistência do Documento Canônico: {e}")

    # 1c) Silver EXPLÍCITO: materializa data/silver/structured_docs/*.jsonl de
    #     todos os editais vigentes (structurer, durable-first). Antes isto era
    #     EFEITO COLATERAL do etl_process (síntese de wiki) — que a migração
    #     hipergrado remove. Passo próprio aqui DESACOPLA o silver da wiki:
    #     hipergrado (1d) e RAG dependem do silver, não da wiki. Sem LLM.
    try:
        n_silver = await asyncio.to_thread(_build_all_silver)
        logger.info("run_daily_etl_task: silver materializado p/ %d editais", n_silver)
    except Exception as e:
        logger.error("run_daily_etl_task: falha ao materializar silver: %s", e)
        step_errors.append(f"materialização do silver: {e}")

    # 1d) Hipergrado por edital + catálogos (arquitetura hipergrado, Sprint 0).
    #     Depende do silver de 1c — NÃO da wiki. Roda EM PARALELO à wiki (nada
    #     removido ainda). Skip por hash: só re-extrai o que mudou. Precisa de
    #     OPENAI_API_KEY.
    if os.getenv("OPENAI_API_KEY"):
        try:
            from core.retrieval.hyper_extractor import build_all_hypergraphs  # noqa: PLC0415
            counts = await asyncio.to_thread(build_all_hypergraphs)
            logger.info("run_daily_etl_task: hipergrado — %s", counts)
        except Exception as e:
            logger.error("run_daily_etl_task: falha ao construir hipergrado: %s", e)
            step_errors.append(f"construção do hipergrado: {e}")
    else:
        logger.warning("run_daily_etl_task: sem OPENAI_API_KEY — hipergrado pulado")

    # 2) Síntese de wiki pages. O etl_process tem cache próprio por hash de
    #    (metadata + silver) — só chama o LLM para editais que mudaram, então
    #    rodar todo dia é barato. Guardamos a API key para não cair no getpass
    #    (que travaria o worker headless).
    wiki_backend = os.getenv("WIKI_SYNTH_BACKEND", "gemini")
    key_ok = (
        os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        if wiki_backend == "gemini"
        else os.getenv("OPENAI_API_KEY")
    )
    if key_ok:
        try:
            from pipeline import etl_process  # noqa: PLC0415
            await asyncio.to_thread(lambda: etl_process.main(backend=wiki_backend))
            logger.info("run_daily_etl_task: wiki pages sintetizadas (backend=%s)", wiki_backend)
        except Exception as e:
            logger.error("run_daily_etl_task: falha na síntese de wiki pages: %s", e)
            step_errors.append(f"síntese de wiki pages: {e}")
    else:
        logger.warning(
            "run_daily_etl_task: sem API key p/ WIKI_SYNTH_BACKEND=%s — síntese de wiki pulada",
            wiki_backend,
        )

    # 3) Regenera o vault Obsidian a partir do índice unificado. É a MESMA fonte
    #    de dados do grafo do frontend (GET /graph lê OBSIDIAN_VAULT_DIR), então
    #    isto atualiza Obsidian e frontend de uma vez. Agnóstico à fonte e sem
    #    duplicar editais (nomes/wikilinks colon-free consistentes + dedup no
    #    get_graph). `scripts` é importável como namespace package a partir da raiz.
    try:
        from config import OBSIDIAN_VAULT_DIR  # noqa: PLC0415
        from scripts.export_to_obsidian import export as export_obsidian  # noqa: PLC0415
        OBSIDIAN_VAULT_DIR.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(export_obsidian, OBSIDIAN_VAULT_DIR)
        logger.info("run_daily_etl_task: vault Obsidian / grafo regenerado")
    except Exception as e:
        logger.error("run_daily_etl_task: falha ao regenerar grafo Obsidian: %s", e)
        step_errors.append(f"regeneração do grafo Obsidian: {e}")

    # 4) Alerta de fonte parada: bronze de alguma fonte registrada sem arquivo
    #    novo há mais que o threshold (scraper quebrado retornando vazio, cron
    #    capenga). O try/except por fonte acima captura exceções, mas um scraper
    #    que "funciona" e não grava nada escaparia — este check pega pelo efeito.
    try:
        from pipeline.health_check import check_sources_freshness  # noqa: PLC0415
        for r in await asyncio.to_thread(check_sources_freshness):
            if r["status"] == "STALE":
                logger.error(
                    "run_daily_etl_task: fonte %s estagnada — bronze mais "
                    "recente tem %.1f dias", r["source"], r["age_days"],
                )
                step_errors.append(
                    f"fonte {r['source']} estagnada (bronze mais recente tem "
                    f"{r['age_days']:.1f} dias)"
                )
    except Exception as e:
        logger.warning("run_daily_etl_task: check de frescor das fontes falhou: %s", e)
        step_errors.append(f"check de frescor das fontes: {e}")

    logger.info("run_daily_etl_task: concluído (total=%d novos)", total_new)

    # Alerta AGREGADO das falhas parciais (spec hardening-pre-beta 4.3): a run
    # concluiu, mas alguma(s) etapa(s) falhou(aram). UM e-mail com o resumo —
    # sem spam por etapa. A falha total (exceção não capturada) é alertada no
    # wrapper run_daily_etl_task, nunca nos dois caminhos ao mesmo tempo.
    if step_errors:
        send_alert(
            f"[radar] run_daily_etl: {len(step_errors)} falha(s) de etapa",
            "A run diária do ETL concluiu com falhas parciais "
            f"(timestamp={timestamp}, {total_new} editais novos):\n\n"
            + "\n".join(f"- {err}" for err in step_errors),
        )


# =============================================================================
# Descoberta — busca livre por termos (torneira automática da fonte web)
# =============================================================================
# Procrastinate periodic: cron diário 04:00 UTC (após o ETL das 03:00). Roda a
# busca livre (Tavily), grava os achados em web_raw/ como `provisorio`, enfileira
# o chunking de cada um e reconstrói o índice. É a Opção A (WIKI.md §12.4): a
# Descoberta não tem pipeline próprio — alimenta a MESMA fonte `web` que a seed
# list manual. Requer TAVILY_API_KEY + chave LLM; sem elas, degrada para no-op.


@app.periodic(cron="0 4 * * *")
@app.task(name="discover_opportunities", queue="etl")
async def discover_opportunities_task(timestamp: int) -> None:
    """Cron diário (4am UTC): busca livre → staging (discovered_opportunities, pending).

    Os registros ficam em staging até aprovação humana via POST /promote.
    Chunking e entrada no KG só acontecem após promoção — ver promote_discovered_opportunity.

    `timestamp` vem do procrastinate periodic (instante agendado, UNIX epoch).

    Falha total → 1 e-mail de alerta + re-raise (spec hardening-pre-beta 4.3).
    Sem retry= de propósito: o cron re-roda no dia seguinte.
    """
    from core.opportunity_discovery import discover_opportunities  # noqa: PLC0415

    logger.info("discover_opportunities_task: iniciando (timestamp=%s)", timestamp)

    try:
        records = await asyncio.to_thread(discover_opportunities, write=True)
    except Exception as e:
        # send_alert nunca levanta (contrato de core/notify) — o alerta não
        # pode mascarar nem substituir a falha original.
        send_alert(
            "[radar] discover_opportunities: falha total do cron",
            f"discover_opportunities (timestamp={timestamp}) abortou com "
            f"exceção:\n\n{e!r}",
        )
        raise
    logger.info(
        "discover_opportunities_task: %d oportunidades → staging (aguardam gate humano)",
        len(records),
    )

    logger.info("discover_opportunities_task: concluído")


# ============================================================================
# Warm-up do corpus RAG — eager chunking (reversão operacional do lazy/PR #44)
# ============================================================================
# Racional (adendo eager na spec hardening-pre-beta): o catálogo é pequeno
# (~30 editais) e o gate de content_hash do chunk_edital torna re-runs quase
# gratuitos — só editais novos/alterados pagam LLM. Manter o corpus inteiro
# indexado elimina o cold-start bloqueante do POST /writing/start no primeiro
# engajamento de um edital. O ensure-at-start segue como rede de segurança
# (vira no-op com o índice quente). Backfill manual: scripts/backfill_chunks.py.


@app.periodic(cron="0 5 * * *")
@app.task(name="warm_edital_chunks", queue="etl")
async def warm_edital_chunks_task(timestamp: int) -> None:
    """Cron diário (05:00 UTC, depois do ETL 03:00 e da descoberta 04:00):
    enfileira `chunk_edital` para TODO edital do catálogo.

    Defere 1 job por edital em vez de rodar inline: o worker aplica o
    paralelismo dele e uma falha isolada não afeta os demais. `timestamp`
    vem do procrastinate periodic (UNIX epoch).
    """
    from core.kg.hypergraph_catalog import list_editais  # noqa: PLC0415

    cards = list_editais(limit=1000)
    queued = 0
    for card in cards:
        try:
            await app.configure_task("chunk_edital").defer_async(
                edital_id=card["id"],
            )
            queued += 1
        except Exception as e:
            logger.warning(
                "warm_edital_chunks: falha ao enfileirar %s: %s", card.get("id"), e,
            )
    logger.info(
        "warm_edital_chunks: %d/%d editais enfileirados (timestamp=%s)",
        queued, len(cards), timestamp,
    )
