"""
procrastinate App and task definitions.

Workers run with:

    python -m procrastinate --app=radar.core.tasks.app worker

Implements ADR M8 (procrastinate replaces pg-boss for background jobs). The
worker uses the service-role Supabase client (via radar.core.infra.db.get_supabase_service)
because it has no request context — see ADR M7. Tasks MUST therefore enforce
tenant boundaries explicitly when relevant (e.g. by scoping queries with
workspace_id).

Note on the connector: procrastinate ships an async-capable PsycopgConnector
(psycopg 3 with asyncio) by default — there is no separate `asyncpg` extra in
upstream procrastinate. PsycopgConnector is the right choice for our async
event loop.
"""
from __future__ import annotations

# Carrega o perfil ANTES de importar procrastinate — o connector lê DATABASE_URL
# no import time. Quando o worker roda via `python -m procrastinate ...`,
# nada antes do nosso código carrega o env. Deve ser o primeiro import.
from radar.core.environment import assert_runtime_environment, load_environment_profile

load_environment_profile()

import asyncio  # noqa: E402
import hashlib  # noqa: E402
import json  # noqa: E402
import logging  # noqa: E402
import os  # noqa: E402
import uuid  # noqa: E402
from typing import TYPE_CHECKING  # noqa: E402

from procrastinate import App, PsycopgConnector, RetryStrategy  # noqa: E402
from pydantic import ValidationError  # noqa: E402

from radar.core.infra.db import get_supabase_service  # noqa: E402
from radar.core.infra.logging_config import setup_logging  # noqa: E402
from radar.core.infra.notify import send_alert  # noqa: E402
from radar.core.ingestion import early_dedup  # noqa: E402
from radar.core.ingestion.structurer import build_or_load_structured_doc  # noqa: E402
from radar.core.retrieval.chunker import CHUNKER_VERSION, chunk_from_blocks  # noqa: E402
from radar.core.retrieval.embedder import EMBEDDING_MODEL, embed_texts  # noqa: E402
from radar.core.retrieval.retriever import RETRIEVAL_EMBEDDING_COLUMN  # noqa: E402
from radar.core.services.content_library import enrich_content  # noqa: E402
from radar.core.services.cron_operations import finish_cron, safe_error, start_cron  # noqa: E402
from radar.pipeline.adapters.base import get_adapter  # noqa: E402

if TYPE_CHECKING:  # noqa: E402
    from radar.domain.source_bundle import SourceBundle

setup_logging()
logger = logging.getLogger(__name__)
assert_runtime_environment("background worker")

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
    """Gera embedding com o modelo configurado em `EMBEDDING_MODEL` (1536d).

    Embeds: title + summary + content (concatenados; importante manter title
    no início porque carrega sinal forte). Roda após enrich_content_task —
    summary já está populado e contribui para a representação.

    Idempotente: re-rodar simplesmente sobrescreve o vetor.
    """
    from radar.core.retrieval.embedder import embed_query  # import local para evitar custo ao boot

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


@app.task(name="compute_match_verdicts", queue="default", retry=UNIT_TASK_RETRY)
async def compute_match_verdicts_task(
    workspace_id: str, items: list[dict], profile: dict,
) -> None:
    """Computa e cacheia os vereditos LLM do top-K do radar (Estágio 3 do v3).

    `items` = misses do último refresh (`[{oportunidade_id, excerpts}]`, ≤ K),
    com os trechos que justificaram o match. A task re-serializa cada ficha do
    corpus ATUAL (linha de `entities` via snapshot do match_v3) e recomputa o
    `input_hash` (mesma função do router) — se o corpus mudou entre o defer e a
    execução, o hash gravado reflete o corpus novo, que é o que o próximo
    request também verá (cache-hit correto, nunca stale servido como fresco).

    Fail-open POR PAR: `compute_verdict` devolve None em erro (o card fica sem
    veredito); um par ruim não derruba os demais. Exceções de infra (DB/fila)
    propagam e o procrastinate re-tenta com backoff — o upsert é idempotente.
    """
    from radar.core.services import match_verdict as mv

    db = get_supabase_service()

    prepared: list[tuple[str, str, list[dict], str]] = []  # (oid, serialized, excerpts, hash)
    for item in items or []:
        prep = await asyncio.to_thread(mv.serialize_for_verdict, item)
        if prep is None:
            logger.info("compute_match_verdicts: item sem oportunidade resolvível — pulado: %s", item)
            continue
        oid, serialized = prep
        excerpts = item.get("excerpts") or []
        prepared.append((oid, serialized, excerpts,
                         mv.verdict_input_hash(serialized, profile, excerpts)))

    # Segunda visita = zero chamadas: pares já gravados com o MESMO hash saem aqui
    # (o defer duplicado é possível — queueing_lock só cobre jobs ainda na fila).
    wanted = {oid: h for oid, _, _, h in prepared}
    hits = await asyncio.to_thread(mv.get_cached_verdicts, db, workspace_id, wanted)
    misses = [p for p in prepared if p[0] not in hits]

    async def _one(oid: str, serialized: str, excerpts: list[dict], h: str) -> bool:
        verdict = await asyncio.to_thread(mv.compute_verdict, serialized, profile, excerpts)
        if verdict is None:
            return False
        await asyncio.to_thread(
            mv.upsert_verdict, db, workspace_id, oid, h, verdict, mv._verdict_model()
        )
        return True

    results = await asyncio.gather(
        *(_one(*p) for p in misses), return_exceptions=True
    )
    n_ok = sum(1 for r in results if r is True)
    n_err = sum(1 for r in results if isinstance(r, Exception))
    for r in results:
        if isinstance(r, Exception):
            logger.warning("compute_match_verdicts: par falhou: %s", r)
    logger.info(
        "compute_match_verdicts: workspace=%s pares=%d cache_hit=%d computados=%d falhas=%d",
        workspace_id, len(prepared), len(hits), n_ok, n_err,
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
      - Manual: enfileiramento operacional da task.

    A task self-gateia em MIN_OUTCOMES_FOR_REFLECTION (pula se há poucos
    outcomes) e supersede o lote de insights anterior, então é seguro
    enfileirar a cada outcome.

    F6 (D3): short-circuit defensivo sob AUTO_MEMORY_WRITE=0 (default), antes
    de importar reflection_service.
    """
    from radar.core.reflection_service import _auto_memory_write_enabled
    if not _auto_memory_write_enabled():
        logger.info(
            "reflect_workspace_task: AUTO_MEMORY_WRITE=0 — no-op (ws=%s). "
            "Escrita congelada (F6/D3); leitura intacta.", workspace_id,
        )
        return

    from radar.core.reflection_service import reflect_workspace

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
      - Manual: enfileiramento operacional da task.

    Self-gateia em MIN_LEVEL1_FOR_SYNTHESIS (pula se há poucas observações
    ativas), então é seguro enfileirar livremente.

    F6 (D3): short-circuit defensivo sob AUTO_MEMORY_WRITE=0 (default), antes
    de importar reflection_service.
    """
    from radar.core.reflection_service import _auto_memory_write_enabled
    if not _auto_memory_write_enabled():
        logger.info(
            "synthesize_patterns_task: AUTO_MEMORY_WRITE=0 — no-op (ws=%s). "
            "Escrita congelada (F6/D3); leitura intacta.", workspace_id,
        )
        return

    from radar.core.reflection_service import synthesize_patterns

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

    F6 (D3): sob AUTO_MEMORY_WRITE=0 (default), short-circuit sem nem buscar
    os workspaces — evita N no-op enqueue (defensivo sobre o gate do worker).
    """
    from radar.core.reflection_service import _auto_memory_write_enabled
    if not _auto_memory_write_enabled():
        logger.info(
            "synthesize_patterns_cron: AUTO_MEMORY_WRITE=0 — no-op (ts=%d). "
            "Escrita congelada (F6/D3).", timestamp,
        )
        return
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
    do AsyncPostgresSaver não tem coluna de data.

    SEMÂNTICA (Item 3, TASK 5 — thread-por-SESSÃO): o modelo antigo era
    thread-por-TURNO (`{ws}:{session}:{turn}`), onde um thread velho era LIXO PURO
    (turno morto). Agora o thread é `{ws}:{session}` e é a **memória viva da sessão**
    — todos os turnos acumulam nele. Consequência:
      • DORMÊNCIA vs LIXO: o critério `max(ts) < now - retention` distingue os dois
        por construção — um turno recente em QUALQUER sessão empurra o `max(ts)` pra
        frente, então uma sessão só-dormente (tocada há < retention) NUNCA é purgada;
        só sessões ABANDONADAS (sem nenhum turno há > retention) são reclamadas.
      • `retention_days` deixou de ser "TTL de lixo de turno" e passou a ser "por
        quanto tempo guardamos a memória-de-agente de uma sessão dormante antes de
        reclamá-la". Default subido 30→90 (conservador — sessão retornável).
      • GRACIOSO: purgar a thread apaga só o CONTEXTO do agente; `session_turns`
        (persist_turn) preserva o histórico de EXIBIÇÃO do usuário (Decisão 4). Uma
        sessão purgada e reaberta re-semeia do zero, não perde o registro de produto.
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
    de checkpoint mais antigos que CHECKPOINT_RETENTION_DAYS (default 90 — janela de
    memória de sessão DORMENTE, não lixo de turno; ver _purge_stale_checkpoints).

    Sem retry de propósito (padrão dos wrappers de cron): falha re-roda na
    semana seguinte. Sem DATABASE_URL (dev/teste com InMemorySaver) é no-op.
    `timestamp` vem do procrastinate periodic (UNIX epoch).
    """
    if not os.environ.get("DATABASE_URL"):
        logger.info("purge_agent_checkpoints: DATABASE_URL ausente — no-op")
        return
    retention = int(os.getenv("CHECKPOINT_RETENTION_DAYS", "90"))
    counts = await asyncio.to_thread(_purge_stale_checkpoints, retention)
    logger.info(
        "purge_agent_checkpoints: %d threads purgados (retention=%dd, timestamp=%s): %s",
        counts.get("threads", 0), retention, timestamp, counts,
    )


# =============================================================================
# RAG indexing
# =============================================================================

# Extração de PDF (table-aware, descoberta, dedup de versão) é L1 FINEP —
# vive em pipeline/adapters/finep.py via o Source Adapter (docs/domain/schema.md §12).


def _save_fapesc_bundle_if_available(source: str, native: str) -> SourceBundle | None:
    """Persiste o bundle FAPESC antes da projeção canônica, sem bloquear o ETL."""
    if source != "fapesc":
        return None
    from radar.core.kg import source_bundles  # noqa: PLC0415
    from radar.core.kg.source_bundle_projection import current_complete_bundle  # noqa: PLC0415
    from radar.core.kg.source_bundles import BundleStorageError  # noqa: PLC0415
    from radar.pipeline.adapters.fapesc import build_source_bundle  # noqa: PLC0415

    try:
        bundle = build_source_bundle(native)
    except ValidationError:
        logger.warning(
            "fapesc bundle: bundle inválido para %s (type=%s)",
            native,
            ValidationError.__name__,
        )
        return None
    if bundle is None:
        return None
    try:
        if source_bundles.save(bundle) is not True:
            return None
        return current_complete_bundle(bundle)
    except BundleStorageError:
        logger.warning(
            "fapesc bundle: falha best-effort para %s (type=%s)",
            native, BundleStorageError.__name__,
        )
        return None


def _build_chunks_for_edital(edital_id: str) -> list[dict]:
    """Pipeline da Retrieval gold (L3b, §12): Source Adapter → Documento
    Canônico → structurer/silver → chunk_from_blocks.

    `edital_id` chega prefixado (`finep:782`). Source vem do prefixo; adapter
    e structurer recebem o native_id (eles operam em escopo de fonte).

    Síncrono — chamar via asyncio.to_thread. Falha silenciosa do structurer
    (silver vazio) → retorna [] e o caller limpa as linhas antigas, conforme
    o contrato §11.4 ("falha LLM → B não indexa").
    """
    from radar.core.kg import source_docs  # noqa: PLC0415
    from radar.core.kg.edital_id import native_id_of, source_of  # noqa: PLC0415
    from radar.core.kg.source_bundle_projection import (
        attach_bundle_metadata_to_documents,  # noqa: PLC0415
    )

    source = source_of(edital_id)
    native = native_id_of(edital_id)

    # Conteúdo recém-coletado prevalece quando está no disco; o durável é o
    # fallback de redeploy/lazy indexing. Isso evita usar por mais uma run uma
    # versão normativa antiga que ainda esteja persistida.
    documents = get_adapter(source).to_documents(native)
    if documents:
        documents = attach_bundle_metadata_to_documents(
            documents,
            _save_fapesc_bundle_if_available(source, native),
        )
        source_docs.save(edital_id, source, documents)
    else:
        documents = source_docs.load(edital_id)
    if not documents:
        logger.warning(
            "chunk_edital_task: sem conteúdo-fonte p/ edital=%s "
            "(durável vazio e disco sem bronze — redeploy efêmero?)",
            edital_id,
        )
        return []

    active = source_docs.active_documents(documents)
    blocks = build_or_load_structured_doc(source, native, active)
    if not blocks:
        logger.warning(
            "chunk_edital_task: silver vazio p/ edital=%s (structurer falhou)",
            edital_id,
        )
        return []

    chunks = chunk_from_blocks(blocks)
    # Lineage (RT01 §6.2): `active` é o Documento Canônico REALMENTE usado
    # neste chunking (chegou até aqui só se não-vazio — ver early-return
    # acima). Hash do lote inteiro, não por-documento: mesmo espírito de
    # `_index_is_current` (conteúdo agregado determina a versão do índice).
    canonical_content_hash = f"md5:{source_docs.canonical_hash(active)}"
    for global_idx, chunk in enumerate(chunks):
        matched_docs = [
            entry for entry in active if entry.get("doc_name") == chunk.get("source_file")
        ]
        bundle_lineage = {}
        if len(matched_docs) == 1:
            metadata = matched_docs[0].get("metadata") or {}
            if metadata.get("bundle_hash") and metadata.get("content_hash"):
                bundle_lineage = {
                    "bundle_hash": metadata["bundle_hash"],
                    "content_hash": metadata["content_hash"],
                }
        chunk["chunk_index"] = global_idx
        chunk["metadata"] = {
            **(chunk.get("metadata") or {}),
            "canonical_content_hash": canonical_content_hash,
            "chunker_version": CHUNKER_VERSION,
            **bundle_lineage,
        }
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
        current_hash = row["metadata"].get("index_content_hash") or row["metadata"].get("content_hash")
        if current_hash != content_hash:
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
    marcador `{index_content_hash, n_chunks}` no metadata do chunk_index=0. Em
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
        from radar.core.services.discovery_promotion import mark_by_edital  # noqa: PLC0415
        mark_by_edital(edital_id, "rag_ready", "failed", error="nenhum chunk foi produzido")
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
            from radar.core.services.discovery_promotion import mark_by_edital  # noqa: PLC0415
            mark_by_edital(edital_id, "rag_ready", "ready")
            return

    logger.info(
        "chunk_edital_task: edital=%s gerou %d chunks, embedando…",
        edital_id, len(chunks),
    )
    # Contextual Retrieval (Anthropic): embeda contexto+corpo; a coluna `text`
    # segue com o corpo original. Desligável por env; gateado pelo content_hash
    # acima (só editais que mudaram re-contextualizam). Ver core/contextual_retrieval.
    from radar.core import contextual_retrieval
    # Decisão lida UMA vez: env não muda no meio da task, e contextualize_chunks
    # reavalia a mesma condição internamente (pure function do env — consistente).
    context_enabled = contextual_retrieval.is_enabled()
    context_version = (
        {"model": contextual_retrieval.effective_model()} if context_enabled else None
    )
    embed_inputs = await asyncio.to_thread(contextual_retrieval.contextualize_chunks, chunks)
    embeddings = await asyncio.to_thread(embed_texts, embed_inputs)

    if len(embeddings) != len(chunks):
        raise RuntimeError(
            f"chunk_edital_task: mismatch embeddings ({len(embeddings)}) vs "
            f"chunks ({len(chunks)}) para edital={edital_id}"
        )

    def _row_metadata(c: dict) -> dict:
        meta = dict(c.get("metadata") or {})
        if context_version is not None:
            meta["context_version"] = context_version
        return meta

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
            "metadata": _row_metadata(c),
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
    marker_meta = {**rows[0]["metadata"], "index_content_hash": content_hash, "n_chunks": len(rows)}
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
    from radar.core.services.discovery_promotion import mark_by_edital  # noqa: PLC0415
    mark_by_edital(edital_id, "rag_ready", "ready")


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
# ETL diário
# =============================================================================
# Procrastinate periodic: cron diário 03:00 UTC roda scrapers ativos, materializa
# silver/gold, persiste documentos canônicos e atualiza o vault. Falhas são tipadas e
# persistidas em pipeline_errors para retry e diagnóstico por categoria.
#
# Para mudar o horário: ajuste o cron expression (formato padrão crontab).
# Para ativar mais fontes: implemente um BaseScraper concreto e registre em SCRAPER_REGISTRY.


# Mapa: chave do SCRAPER_REGISTRY → (source_key do canal, mode).
# O scraper `web` é mapeado como `web_curated` (curated_web).
_SOURCE_RUN_MAP: dict[str, tuple[str, str]] = {
    "finep": ("finep", "dedicated"),
    "fapesp": ("fapesp", "dedicated"),
    "fapesc": ("fapesc", "dedicated"),
    "web": ("web_curated", "curated_web"),
}

# PipelineError.category → reason_code canônico de source_runs.
_PIPELINE_CATEGORY_TO_REASON: dict[str, str] = {
    "timeout": "timeout",
    "parse_error": "parse_error",
    "schema_violation": "parse_error",
    "llm_refusal": "provider_error",
    "duplicate": "empty_result",
    "unknown": "unknown",
}


def _build_all_silver() -> dict[str, object]:
    """Persiste o canônico fresco e materializa Silver dos editais scraped.

    Passo EXPLÍCITO do pipeline v3 (spec docs/specs/v3-unified.md §10): bronze →
    adapter → silver (structurer). Enumera direto do BRONZE (fonte autoritativa dos
    scrapers), sem depender de artefato derivado (hipergrado/gold). O silver é a
    ENTRADA do `ingest_all` (gold) e do RAG de escrita. Sem LLM; tolerante a falha
    por-edital."""
    from radar.core.kg import gold, source_docs  # noqa: PLC0415
    from radar.pipeline.adapters.base import get_adapter  # noqa: PLC0415

    result: dict[str, object] = {
        "changed_ids": [],
        "by_source": {},
        "unchanged": 0,
        "changed": 0,
        "silver_built": 0,
        "silver_skipped": 0,
        "step_errors": 0,
    }
    changed_ids = result["changed_ids"]
    by_source = result["by_source"]
    for source, native in gold.iter_bronze_editais():
        eid = f"{source}:{native}"
        source_stats = by_source.setdefault(source, {
            "unchanged": 0, "changed": 0, "silver_built": 0,
            "silver_skipped": 0, "step_errors": 0,
        })
        try:
            fingerprint = early_dedup.input_fingerprint(source, native)
            if fingerprint and early_dedup.can_skip_silver(source, native, fingerprint):
                result["unchanged"] += 1
                result["silver_skipped"] += 1
                source_stats["unchanged"] += 1
                source_stats["silver_skipped"] += 1
                logger.info("early dedup: %s unchanged; silver/gold skipped", eid)
                continue

            result["changed"] += 1
            source_stats["changed"] += 1
            fresh = get_adapter(source).to_documents(native)
            if fresh:
                _save_fapesc_bundle_if_available(source, native)
                source_docs.save(eid, source, fresh)
            docs = fresh or source_docs.load(eid)
            active = source_docs.active_documents(docs or [])
            blocks = (
                build_or_load_structured_doc(source, native, active, force=True)
                if active else []
            )
            if active and blocks:
                result["silver_built"] += 1
                source_stats["silver_built"] += 1
                changed_ids.append(eid)
                if fresh and fingerprint and not early_dedup.persist_fingerprint(source, native, fingerprint):
                    result["step_errors"] += 1
                    source_stats["step_errors"] += 1
            # Empty/absent input is not a materialization error by itself.  We
            # deliberately leave the fingerprint absent so the next run fails
            # open and gets another chance to materialize it.
        except Exception:
            result["step_errors"] += 1
            source_stats["step_errors"] += 1
            logger.warning("_build_all_silver: falha em %s", eid, exc_info=True)
    return result


def _normalize_silver_result(value: object) -> dict[str, object]:
    """Keep the ETL boundary tolerant of legacy/test Silver producers."""
    if isinstance(value, dict):
        return value
    built = value if isinstance(value, int) and value >= 0 else 0
    return {
        "changed_ids": [],
        "by_source": {},
        "unchanged": 0,
        "changed": built,
        "silver_built": built,
        "silver_skipped": 0,
        "step_errors": 0,
    }


@app.periodic(cron="0 3 * * *")
@app.task(name="run_daily_etl", queue="etl")
async def run_daily_etl_task(timestamp: int) -> None:
    """Cron diário (3am UTC): atualiza fontes, silver, gold e artefatos derivados.

    Wrapper fino de `_run_daily_etl` com o contrato de alerta (spec
    hardening-pre-beta 4.3): falha TOTAL do cron → 1 e-mail de alerta +
    re-raise (o job consta como failed no procrastinate). O cron NÃO tem
    retry= de propósito — re-roda no dia seguinte; o alerta é o mecanismo
    de visibilidade. Falhas de ETAPA (parciais) são agregadas e alertadas
    dentro de `_run_daily_etl` — máx. 1 e-mail por run em qualquer caminho.

    O argumento `timestamp` vem do procrastinate periodic e representa o
    instante agendado da execução (UNIX epoch).
    """
    db = None
    run_id = None
    try:
        db = get_supabase_service()
        run_id = start_cron(db, task="run_daily_etl", scheduled_at=timestamp)
    except Exception:
        logger.warning("run_daily_etl: cron ledger unavailable", exc_info=False)
    try:
        summary = await _run_daily_etl(timestamp)
    except Exception as e:
        # send_alert nunca levanta (contrato de core/notify) — o alerta não
        # pode mascarar nem substituir a falha original.
        send_alert(
            "[radar] run_daily_etl: falha total do cron",
            f"run_daily_etl (timestamp={timestamp}) falhou: {safe_error(e)}",
        )
        if db is not None and run_id:
            finish_cron(db, run_id=run_id, status="failed", last_step="etl", error=e)
        raise
    else:
        if db is not None and run_id:
            finish_cron(
                db, run_id=run_id, status=summary["status"],
                last_step=summary["last_step"], counters=summary["counters"],
                error=summary.get("error"),
            )


async def _run_daily_etl(timestamp: int) -> dict[str, object]:
    """Corpo do ETL diário (ver run_daily_etl_task para o contrato de alerta).

    Para cada fonte em radar.pipeline.extractors.SCRAPER_REGISTRY:
      1. Roda o scraper (captura novos editais em data/bronze/)
      2. Falhas tipadas (TimeoutError, ParseError, etc.) viram rows em
         pipeline_errors via log_pipeline_error
      3. Após os scrapers, converge os documentos em silver → gold e persiste
         o Documento Canônico durável

    Falhas de scraper/etapa não derrubam a run (cada bloco tem try/except
    próprio), mas são acumuladas em `step_errors` e viram UM e-mail agregado
    ao final (spec hardening-pre-beta 4.3).
    """
    from radar.core.infra.pipeline_errors import (
        PipelineError,
        classify_requests_error,
        log_pipeline_error,
    )

    async def record_pipeline_error(*args, **kwargs) -> None:
        """Telemetry is best-effort and must not replace the source failure."""
        try:
            await asyncio.to_thread(log_pipeline_error, *args, **kwargs)
        except Exception:
            logger.warning("run_daily_etl: telemetry de erro indisponível", exc_info=True)

    logger.info("run_daily_etl_task: iniciando (timestamp=%s)", timestamp)

    # Import tardio para evitar custo no boot do worker
    from radar.pipeline.extractors import SCRAPER_REGISTRY

    # Telemetria: batch_id único para todos os canais desta rodada.
    batch_id = str(uuid.uuid4())
    db = None
    try:
        db = get_supabase_service()
    except Exception:
        logger.warning("run_daily_etl: falha ao conectar DB para telemetria", exc_info=True)

    from radar.core.services.source_runs import finish_run, start_run  # noqa: PLC0415

    # Falhas parciais da run (scraper por fonte + etapas pós-scraping).
    # Não derrubam a run; viram 1 e-mail AGREGADO ao final.
    step_errors: list[str] = []
    last_step = "start"
    silver_result: dict[str, object] = {
        "changed_ids": [],
        "by_source": {},
        "unchanged": 0,
        "changed": 0,
        "silver_built": 0,
        "silver_skipped": 0,
        "step_errors": 0,
    }
    n_gold = n_docs = 0
    gold_counts: dict[str, int] = {}
    pending_source_runs: list[tuple[str, int, str]] = []

    total_new = 0
    for source_key, cfg in SCRAPER_REGISTRY.items():
        cls = cfg["cls"]
        # Slug canônico do registry (chave) é o source para prefixo §12.
        # `display_name` (ex: "FINEP") é só pra log/UX, não pra prefixo.
        source = cfg.get("source", source_key)
        display_name = cfg.get("display_name", source_key)

        # Telemetria: abrir run antes da coleta (best-effort).
        run_id = None
        coverage_info = _SOURCE_RUN_MAP.get(source_key)
        if coverage_info is not None and db is not None:
            cov_source_key, cov_mode = coverage_info
            try:
                run_id = await asyncio.to_thread(
                    start_run, db,
                    batch_id=batch_id,
                    source_key=cov_source_key,
                    mode=cov_mode,
                )
            except Exception:
                logger.warning(
                    "run_daily_etl: start_run falhou (best-effort) source=%s",
                    source_key, exc_info=True,
                )

        try:
            last_step = f"scraper:{source_key}"
            scraper = cls(**cfg.get("kwargs", {}))
            results = await asyncio.to_thread(scraper.extract)
            new_count = len(results) if results else 0
            total_new += new_count
            logger.info(
                "run_daily_etl_task: %s — %d resultados", display_name, new_count,
            )

            # Finalizamos depois do silver para registrar também os contadores
            # do gate antecipado no mesmo source_run.
            if run_id:
                pending_source_runs.append((run_id, new_count, coverage_info[0]))

            # O cron das 03:00 não produz edital_chunks. O corpus de escrita é
            # aquecido pelo cron dedicado das 05:00 e também garantido por
            # ensure/prefetch quando o usuário engaja um edital.

        except PipelineError as e:
            await record_pipeline_error(
                source=source, error=e,
                context={"stage": "scraper"},
            )
            logger.warning("run_daily_etl_task: %s falhou (%s): %s",
                          display_name, e.category, e)
            step_errors.append(f"scraper {display_name} [{e.category}]: {e}")

            # Telemetria: finalizar run com falha (best-effort).
            if run_id:
                reason = _PIPELINE_CATEGORY_TO_REASON.get(e.category, "unknown")
                try:
                    await asyncio.to_thread(
                        finish_run, db,
                        run_id=run_id,
                        status="failed",
                        records_observed=0,
                        error_count=1,
                        reason_code=reason,
                    )
                except Exception:
                    logger.warning(
                        "run_daily_etl: finish_run falhou (best-effort) source=%s",
                        source_key, exc_info=True,
                    )

        except Exception as raw_err:
            # Genérico: tenta classificar (HTTPError → ParseError/Timeout, etc.)
            typed = classify_requests_error(raw_err)
            await record_pipeline_error(
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

            # Telemetria: finalizar run com falha (best-effort).
            if run_id:
                reason = _PIPELINE_CATEGORY_TO_REASON.get(typed.category, "unknown")
                try:
                    await asyncio.to_thread(
                        finish_run, db,
                        run_id=run_id,
                        status="failed",
                        records_observed=0,
                        error_count=1,
                        reason_code=reason,
                    )
                except Exception:
                    logger.warning(
                        "run_daily_etl: finish_run falhou (best-effort) source=%s",
                        source_key, exc_info=True,
                    )

    # -------------------------------------------------------------------
    # Pós-scraping (pipeline v3 — spec docs/specs/v3-unified.md §10):
    #   bronze → adapter/autoridade → Documento Canônico durável → silver
    #   (structurer) → ingest_all() (gold + embeddings) → vault Obsidian.
    # O produtor legado (hipergrado / hyper-extract) foi removido: o catálogo e
    # o match vêm das tabelas gold (entities/match_chunks, migration 036).
    # -------------------------------------------------------------------

    # 1) CANÔNICO + SILVER: persiste primeiro o documento recém-scraped e então
    #    materializa data/silver/structured_docs/*.jsonl. É a ENTRADA
    #    do ingest_all (gold) e do RAG de escrita. Sem LLM.
    try:
        last_step = "silver"
        silver_result = _normalize_silver_result(
            await asyncio.to_thread(_build_all_silver)
        )
        logger.info(
            "run_daily_etl_task: silver — changed=%d unchanged=%d built=%d skipped=%d",
            silver_result["changed"], silver_result["unchanged"],
            silver_result["silver_built"], silver_result["silver_skipped"],
        )
    except Exception as e:
        logger.error("run_daily_etl_task: falha ao materializar silver: %s", e)
        step_errors.append(f"materialização do silver: {e}")
    if silver_result["step_errors"]:
        step_errors.append(
            f"gate/materialização silver: {silver_result['step_errors']} erro(s)"
        )

    # Source runs ficam terminais somente agora, para que o ledger contenha o
    # resultado do scraper e do gate Silver da mesma rodada.
    for source_run_id, observed, source in pending_source_runs:
        metrics = silver_result["by_source"].get(source, {
            "unchanged": 0, "changed": 0, "silver_built": 0,
            "silver_skipped": 0, "step_errors": 0,
        })
        try:
            await asyncio.to_thread(
                finish_run, db,
                run_id=source_run_id,
                status="succeeded",
                records_observed=observed,
                error_count=0,
                metrics=metrics,
            )
        except Exception:
            logger.warning("run_daily_etl: finish_run falhou (best-effort) source=%s",
                           source, exc_info=True)

    # 2) Gold INCREMENTAL: ingest_all() popula entities/match_chunks a partir do
    #    silver de (1) + catálogos versionados (data/silver/{investidores,
    #    programas}.json + bronze EMBRAPII). Diff por source_hash: só re-processa
    #    edital alterado (2 chamadas LLM leves/edital + embeddings). Precisa de
    #    OPENAI_API_KEY (tagger/constraints/embeddings) e DATABASE_URL (gold._dsn).
    if os.getenv("OPENAI_API_KEY") and os.getenv("DATABASE_URL"):
        try:
            last_step = "gold"
            from radar.core.kg.gold import ingest_all  # noqa: PLC0415
            changed_ids = list(silver_result["changed_ids"])
            gold_sources = ["investidor", "programa", "ict"]
            if changed_ids:
                gold_sources.append("edital")
            counts = await asyncio.to_thread(
                ingest_all, sources=gold_sources, edital_ids=changed_ids,
            )
            gold_counts = {
                key: int(value) for key, value in counts.items()
                if isinstance(value, int)
            }
            n_gold = gold_counts.get("edital", 0)
            logger.info(
                "run_daily_etl_task: gold — edital_ids=%d processed=%d skipped=%d counts=%s",
                len(changed_ids), n_gold, gold_counts.get("skipped", 0), counts,
            )
        except Exception as e:
            logger.error("run_daily_etl_task: falha ao ingerir gold: %s", e)
            step_errors.append(f"ingestão gold (ingest_all): {e}")
    else:
        logger.warning(
            "run_daily_etl_task: sem OPENAI_API_KEY/DATABASE_URL — ingestão gold pulada",
        )

    # 3) Rede de segurança: persiste documentos de editais que já existiam no
    #    catálogo mas não foram enumerados pelo bronze da run. O caminho normal
    #    já persistiu conteúdo fresco em (1).
    try:
        last_step = "source_docs"
        from radar.core.kg.source_docs import persist_all_current  # noqa: PLC0415
        n_docs = await asyncio.to_thread(persist_all_current)
        logger.info("run_daily_etl_task: %d documentos-fonte persistidos (durável)", n_docs)
    except Exception as e:
        logger.error("run_daily_etl_task: falha ao persistir documentos-fonte: %s", e)
        step_errors.append(f"persistência do Documento Canônico: {e}")

    # 4) Regenera o vault Obsidian a partir das tabelas gold (entity_catalog +
    #    entity_relationships). Uso pessoal (Graph View); sem consumidor no app.
    #    `scripts` é importável como namespace package a partir da raiz.
    try:
        last_step = "obsidian"
        from radar.core.config import OBSIDIAN_VAULT_DIR  # noqa: PLC0415
        from scripts.export_to_obsidian import run as export_obsidian  # noqa: PLC0415
        OBSIDIAN_VAULT_DIR.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(export_obsidian, OBSIDIAN_VAULT_DIR)
        logger.info("run_daily_etl_task: vault Obsidian / grafo regenerado")
    except Exception as e:
        logger.error("run_daily_etl_task: falha ao regenerar grafo Obsidian: %s", e)
        step_errors.append(f"regeneração do grafo Obsidian: {e}")

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
    return {
        "status": "partial" if step_errors else "succeeded",
        "last_step": last_step,
        "counters": {
            "records_observed": total_new,
            "records_new": total_new,
            "unchanged": silver_result["unchanged"],
            "changed": silver_result["changed"],
            "silver_built": silver_result["silver_built"],
            "silver_skipped": silver_result["silver_skipped"],
            "gold_processed": n_gold,
            "gold_skipped": gold_counts.get("skipped", 0),
            "silver": silver_result["silver_built"],
            "gold": n_gold,
            "source_docs": n_docs,
            "step_errors": len(step_errors),
        },
        "error": step_errors[0] if step_errors else None,
    }


@app.task(name="ingest_promoted_edital", queue="etl")
async def ingest_promoted_edital_task(edital_id: str) -> None:
    """Ingesta um edital promovido (gate admin do discovery) no gold pelo MESMO
    caminho do ETL diário: silver (structurer) → `ingest_all` incremental.

    Enfileirado por POST /discovered-opportunities/{id}/promote quando há bronze
    imediato (edital_link PDF). Sem isto, o promovido entraria só no RAG
    (chunk_edital) e nunca no catálogo/match. `ingest_all(sources=["edital"])` é
    incremental (skip por source_hash): só o edital novo paga LLM/embeddings."""
    from radar.core.kg import gold, source_docs  # noqa: PLC0415
    from radar.core.kg.edital_id import native_id_of, source_of  # noqa: PLC0415
    from radar.pipeline.adapters.base import get_adapter  # noqa: PLC0415

    source = source_of(edital_id)
    native = native_id_of(edital_id)
    fresh = get_adapter(source).to_documents(native)
    if fresh:
        _save_fapesc_bundle_if_available(source, native)
        source_docs.save(edital_id, source, fresh)
    docs = source_docs.active_documents(fresh or source_docs.load(edital_id) or [])
    if not docs or not build_or_load_structured_doc(source, native, docs):
        logger.warning("ingest_promoted_edital: silver vazio p/ %s — abortado", edital_id)
        from radar.core.services.discovery_promotion import mark_by_edital  # noqa: PLC0415
        mark_by_edital(edital_id, "silver_ready", "failed", error="documento silver vazio")
        mark_by_edital(edital_id, "radar_ready", "failed", error="documento silver vazio")
        return
    if not (os.getenv("OPENAI_API_KEY") and os.getenv("DATABASE_URL")):
        logger.warning(
            "ingest_promoted_edital: sem OPENAI_API_KEY/DATABASE_URL — gold pulado p/ %s",
            edital_id,
        )
        from radar.core.services.discovery_promotion import mark_by_edital  # noqa: PLC0415
        mark_by_edital(edital_id, "silver_ready", "ready")
        mark_by_edital(edital_id, "radar_ready", "failed", error="ambiente gold não configurado")
        return
    counts = await asyncio.to_thread(gold.ingest_all, sources=["edital"])
    logger.info("ingest_promoted_edital: %s ingerido no gold — %s", edital_id, counts)
    from radar.core.services.discovery_promotion import mark_by_edital  # noqa: PLC0415
    mark_by_edital(edital_id, "silver_ready", "ready")
    mark_by_edital(edital_id, "radar_ready", "ready")


@app.task(name="fetch_discovery_promotion", queue="etl", retry=UNIT_TASK_RETRY)
async def fetch_discovery_promotion_task(promotion_run_id: str) -> None:
    """Busca UMA URL já aprovada para o retry explícito de ``fetch``.

    Reusa o WebScraper e o mesmo bronze/adaptador ``web``. Não usa Crawl4AI,
    não mexe em fontes dedicadas e não aceita URL vinda do cliente.
    """
    from radar.core.services import discovery_promotion  # noqa: PLC0415
    from radar.pipeline.extractors.web import WebScraper  # noqa: PLC0415

    db = get_supabase_service()
    result = db.table("discovery_promotion_runs").select("*").eq("id", promotion_run_id).maybe_single().execute()
    run = result.data if result else None
    if not run or run.get("route") != "web_source":
        logger.warning("fetch_discovery_promotion: run inválido %s", promotion_run_id)
        return
    opp_result = (db.table("discovered_opportunities").select("url,title")
                  .eq("id", run["discovered_opportunity_id"]).maybe_single().execute())
    opp = opp_result.data if opp_result else None
    if not opp:
        discovery_promotion.update_stage(db, run, "bronze_ready", "failed", error="oportunidade ausente")
        return
    scraper = WebScraper(max_urls=1)
    row = await asyncio.to_thread(scraper._fetch_one, opp["url"], opp.get("title"))
    if not row:
        discovery_promotion.update_stage(db, run, "bronze_ready", "failed", error="coleta não retornou conteúdo")
        return
    await asyncio.to_thread(scraper._save, [row], "web_promoted_fetch")
    edital_id = f"web:{row['url_hash']}"
    run = discovery_promotion.set_edital_id(db, run, edital_id)
    discovery_promotion.update_stage(db, run, "bronze_ready", "ready", artifact={"edital_id": edital_id})
    discovery_promotion.update_stage(db, run, "silver_ready", "running", artifact={"edital_id": edital_id})
    discovery_promotion.update_stage(db, run, "radar_ready", "running", artifact={"edital_id": edital_id})
    discovery_promotion.update_stage(db, run, "rag_ready", "running", artifact={"edital_id": edital_id})
    await app.configure_task("chunk_edital").defer_async(edital_id=edital_id)
    await app.configure_task("ingest_promoted_edital").defer_async(edital_id=edital_id)


# =============================================================================
# Descoberta — busca livre por termos (torneira automática da fonte web)
# =============================================================================
# Procrastinate periodic: cron diário 04:00 UTC (após o ETL das 03:00). Roda a
# busca livre (Tavily), grava os achados em web_raw/ como `provisorio`, enfileira
# o chunking de cada um e reconstrói o índice. É a Opção A (docs/domain/schema.md §12.4): a
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
    from radar.core.ingestion.opportunity_discovery import discover_opportunities  # noqa: PLC0415

    db = None
    run_id = None
    try:
        db = get_supabase_service()
        run_id = start_cron(db, task="discover_opportunities", scheduled_at=timestamp)
    except Exception:
        logger.warning("discover_opportunities: cron ledger unavailable", exc_info=False)
    logger.info("discover_opportunities_task: iniciando (timestamp=%s)", timestamp)

    try:
        records = await asyncio.to_thread(discover_opportunities, write=True)
    except Exception as e:
        # send_alert nunca levanta (contrato de core/notify) — o alerta não
        # pode mascarar nem substituir a falha original.
        send_alert(
            "[radar] discover_opportunities: falha total do cron",
            f"discover_opportunities (timestamp={timestamp}) falhou: {safe_error(e)}",
        )
        if db is not None and run_id:
            finish_cron(db, run_id=run_id, status="failed", last_step="discovery", error=e)
        raise
    logger.info(
        "discover_opportunities_task: %d oportunidades → staging (aguardam gate humano)",
        len(records),
    )

    logger.info("discover_opportunities_task: concluído")
    if db is not None and run_id:
        finish_cron(db, run_id=run_id, status="succeeded", last_step="staging", counters={"records_staged": len(records)})


# ============================================================================
# Warm-up do corpus RAG — aquecimento periódico, complementar ao sob demanda
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
    from radar.core.kg.entity_catalog import list_editais  # noqa: PLC0415

    db = None
    run_id = None
    try:
        db = get_supabase_service()
        run_id = start_cron(db, task="warm_edital_chunks", scheduled_at=timestamp)
    except Exception:
        logger.warning("warm_edital_chunks: cron ledger unavailable", exc_info=False)
    try:
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
        if db is not None and run_id:
            finish_cron(db, run_id=run_id, status="succeeded" if queued == len(cards) else "partial",
                        last_step="enqueue_chunk_edital", counters={"editais": len(cards), "queued": queued})
    except Exception as e:
        if db is not None and run_id:
            finish_cron(db, run_id=run_id, status="failed", last_step="list_editais", error=e)
        raise
