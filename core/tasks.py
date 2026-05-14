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
import re  # noqa: E402
from pathlib import Path  # noqa: E402

from procrastinate import App, PsycopgConnector  # noqa: E402

from config import FINEP_PDFS_DIR  # noqa: E402
from core.chunker import chunk_edital  # noqa: E402
from core.content_library import enrich_content  # noqa: E402
from core.db import get_supabase_service  # noqa: E402
from core.embedder import embed_texts  # noqa: E402

logger = logging.getLogger(__name__)

# Same list used by WritingSession to skip non-edital boilerplate PDFs.
# Filtro de boilerplate na ingestão de PDFs.
#
# Critério: cair fora se o PDF é (a) formulário em branco/minuta, (b) comunicação
# administrativa, (c) resultado/relatório de chamadas passadas, ou (d) material
# institucional sem regra técnica.
#
# `faq` e `tabela_com_requisitos` FORAM removidos da lista — FAQs trazem
# esclarecimentos oficiais da Finep úteis pra escrita, e tabelas de requisitos
# carregam valores e limites de compliance. Avaliação: 2026-05-13.
_SKIP_KEYWORDS = [
    "minuta", "declaracao", "carta_de_manifestacao",
    "apresentacao", "resultado", "oficio", "telas_fap",
    "orientacoes_para_apresentacao",
    "orientacoes_para_despesas", "relatorio_parcial", "ebook",
]


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

# =============================================================================
# PDF extraction — table-aware + header/footer cleanup
# =============================================================================
# Editais FINEP/Petrobras têm dois problemas crônicos:
#   1. Tabelas viram texto linearizado embaralhado (`pdfplumber.extract_text()`
#      caminha célula-por-célula sem preservar layout).
#   2. Cabeçalho institucional ("MINISTÉRIO DA…", "GOVERNO DO BRASIL…") e
#      rodapé de paginação ("Página X de N") aparecem em toda página,
#      poluindo chunks com tokens sem valor semântico.
#
# Solução:
#   • `_extract_page_with_tables` — detecta tabelas via `find_tables()`,
#     remove o bbox da extração de texto principal (via `page.filter`), e
#     re-anexa cada tabela como bloco Markdown ao final do texto da página.
#   • `_clean_edital_text` — descarta linhas conhecidas de header/footer.
#
# As tabelas em Markdown aparecem no `text` do chunk e são detectadas pelo
# campo `metadata.contem_tabela` em `core.chunker._detect_metadata` via
# busca pelo separador "|---" (que só existe em tabelas Markdown).

_HEADER_FOOTER_TERMS = (
    "MINISTÉRIO DA",
    "CIÊNCIA, TECNOLOGIA",
    "CIÊNCIA TECNOLOGIA",  # variante quando a vírgula some na extração
    "E INOVAÇÃO",
    "DO LADO DO POVO BRASILEIRO",
    "GOVERNO DO BRASIL",
)
_PAGINATION_RE = re.compile(r"^Página\s+\d+\s+de\s+\d+$")


def _table_to_markdown(rows: list[list]) -> str:
    """Converte uma tabela 2D (lista de listas, 1ª linha = header) em Markdown.

    Pula linhas inteiramente vazias. Pad/truncate de linhas curtas/longas
    pra largura do header — tabelas FINEP às vezes têm células mescladas
    que o pdfplumber reporta como linhas com colunas faltando.
    """
    if not rows or not rows[0]:
        return ""

    def cell(v) -> str:
        if v is None:
            return ""
        # Newlines em célula quebram o formato Markdown — colapsa em espaço.
        return str(v).strip().replace("\n", " ").replace("|", "\\|")

    n_cols = len(rows[0])
    header = "| " + " | ".join(cell(c) for c in rows[0]) + " |"
    sep = "| " + " | ".join("---" for _ in range(n_cols)) + " |"
    body: list[str] = []
    for row in rows[1:]:
        padded = (list(row) + [None] * n_cols)[:n_cols]
        if not any(cell(v) for v in padded):
            continue
        body.append("| " + " | ".join(cell(c) for c in padded) + " |")
    if not body:
        return ""
    return "\n".join([header, sep] + body)


def _clean_edital_text(text: str) -> str:
    """Remove cabeçalho institucional e rodapé de paginação."""
    if not text:
        return ""
    out: list[str] = []
    for line in text.split("\n"):
        stripped = line.strip()
        if _PAGINATION_RE.match(stripped):
            continue
        if any(term in stripped for term in _HEADER_FOOTER_TERMS):
            continue
        out.append(line)
    cleaned = "\n".join(out)
    # Colapsa 3+ newlines em 2 (evita blank-line storms após a limpeza).
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def _extract_page_with_tables(page) -> str:
    """Extrai texto da página tratando tabelas como blocos Markdown separados.

    Estratégia:
        1. `find_tables()` localiza tabelas (bbox + dados).
        2. `page.filter(predicate)` produz uma página virtual sem os
           caracteres dentro de qualquer bbox de tabela — `extract_text`
           dessa página dá só o texto fora das tabelas, sem o "embaralhado".
        3. Cada tabela é renderizada como bloco Markdown e anexada ao
           final do texto da página.

    Fallback (se `page.filter` falhar): extract_text() normal — aceita
    duplicação do conteúdo da tabela em vez de quebrar a extração.
    """
    tables = page.find_tables()
    if not tables:
        return page.extract_text() or ""

    bboxes = [t.bbox for t in tables]

    def _outside_tables(obj) -> bool:
        for (x0, top, x1, bottom) in bboxes:
            if (obj["x0"] >= x0 and obj["x1"] <= x1
                    and obj["top"] >= top and obj["bottom"] <= bottom):
                return False
        return True

    try:
        main_text = page.filter(_outside_tables).extract_text() or ""
    except Exception as e:
        logger.debug("page.filter falhou (%s), caindo pra extract_text padrão", e)
        main_text = page.extract_text() or ""

    md_blocks: list[str] = []
    for t in tables:
        try:
            md = _table_to_markdown(t.extract() or [])
        except Exception as e:
            logger.debug("table.extract() falhou: %s", e)
            continue
        if md:
            md_blocks.append(md)

    if md_blocks:
        return main_text + "\n\n" + "\n\n".join(md_blocks)
    return main_text


def _extract_pdf_sync(pdf_path: Path) -> str:
    """Read a PDF and return its concatenated text. Empty string on failure.

    Mirrors WritingSession._extract_pdf — kept duplicated here to avoid a
    circular dependency (writing_session imports may grow heavy).
    """
    try:
        import pdfplumber
        pages: list[str] = []
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                raw = _extract_page_with_tables(page)
                cleaned = _clean_edital_text(raw)
                if cleaned:
                    pages.append(cleaned)
        # Junta com \n\n pra preservar limites de parágrafo entre páginas —
        # importante pro fallback `_split_oversize` no chunker.
        return "\n\n".join(pages)
    except Exception as e:
        logger.warning("chunk_edital_task: erro ao extrair %s: %s", pdf_path.name, e)
        return ""


# =============================================================================
# Version dedup: keep only the latest version of FAQ and Edital regulamento
# =============================================================================
# Editais FINEP vêm com múltiplas versões do FAQ (`FAQ.pdf`, `FAQ_Versão_2.pdf`,
# `FAQ_Versão_4_-_atualizado_em_06_04_2026.pdf`, …) e múltiplas rerratificações
# do regulamento (`Edital.pdf`, `Rerratificação_-_Edital_rerratificado.pdf`,
# `2ª_Rerratificação_-_Edital_rerratificado.pdf`, …). Versões antigas estão
# SUPERSEDED pela mais recente — chunkar todas polui o retrieval com texto que
# pode contradizer o vigente.
#
# Heurística deliberadamente conservadora: só agrupamos quando temos certeza.
#   • Group `__faq__`: stem começa com "faq" OU contém tokens "perguntas" +
#     "frequentes".
#   • Group `__edital__`: tem token "edital" E (token "rerratificado" OU stem
#     é exatamente "edital").
#   • Qualquer outro arquivo (anexos, avisos, comunicados, orientações, …)
#     é sua própria fonte — não há tentativa de agrupar.
#
# Recency: maior número de rerratificação > rerratificação sem número (= 1ª)
#          > original. Pra FAQ: número de versão * 1000 + data DDMMYYYY.
#
# Notar: a comparação por token usa `re.split` em separadores comuns
# (`_-. \s`) porque `\b` (word boundary) NÃO funciona entre underscore e
# letra — `_` é word-char em regex.

_FAQ_VERSION_RE = re.compile(r"vers[aã]o[_\s\-]*(\d+)")
_FAQ_DATE_RE = re.compile(r"(\d{2})[_\s\-]?(\d{2})[_\s\-]?(\d{4})")
_EDITAL_RERR_NUM_RE = re.compile(r"(\d+)[ªº°][_\s\-]*rerratifica")
_FILENAME_TOKEN_RE = re.compile(r"[_\s\-.]+")


def _version_info(stem: str) -> tuple[str | None, int]:
    """Classifica um PDF num grupo de versionamento + score de recência.

    Retorna `(group, recency)`. `group=None` significa "não-versionado" —
    deve ser preservado como-está. Recency é monotônico: maior = mais novo.
    """
    s = stem.lower()
    tokens = [t for t in _FILENAME_TOKEN_RE.split(s) if t]
    if not tokens:
        return (None, 0)

    # FAQ group
    is_faq = (tokens[0] == "faq"
              or ("perguntas" in tokens and "frequentes" in tokens))
    if is_faq:
        recency = 0
        m = _FAQ_VERSION_RE.search(s)
        if m:
            recency += int(m.group(1)) * 1000
        m = _FAQ_DATE_RE.search(s)
        if m:
            d, mo, y = m.groups()
            recency += int(y) * 10000 + int(mo) * 100 + int(d)
        return ("__faq__", recency)

    # Edital regulamento group
    has_edital_tok = "edital" in tokens
    has_rerr_tok = any("rerratificad" in t for t in tokens)
    if has_edital_tok and (has_rerr_tok or s == "edital"):
        recency = 0
        m = _EDITAL_RERR_NUM_RE.search(s)
        if m:
            recency = int(m.group(1)) * 1000
        elif has_rerr_tok:
            recency = 500  # 1ª rerratificação sem número explícito
        # Edital.pdf original fica com recency 0 (perde pra qualquer rerratificação).
        return ("__edital__", recency)

    return (None, 0)


def _filter_to_latest_versions(pdfs: list[Path]) -> list[Path]:
    """Filtra uma lista de PDFs mantendo só a versão mais recente de cada
    grupo de versionamento conhecido. Arquivos não-versionados passam livres.
    """
    groups: dict[str, list[tuple[int, Path]]] = {}
    keep: list[Path] = []
    for pdf in pdfs:
        group, recency = _version_info(pdf.stem)
        if group is None:
            keep.append(pdf)
            continue
        groups.setdefault(group, []).append((recency, pdf))

    for group, items in groups.items():
        items.sort(key=lambda x: x[0], reverse=True)
        winner = items[0][1]
        keep.append(winner)
        for _, loser in items[1:]:
            logger.info(
                "chunk_edital_task: versão antiga descartada (group=%s, vencedor=%s): %s",
                group, winner.name, loser.name,
            )

    # Preserva a ordem alfabética original — facilita reprodutibilidade dos chunk_index.
    return sorted(keep)


def _build_chunks_for_edital(edital_id: str) -> list[dict]:
    """Walk FINEP_PDFS_DIR/<edital_id>, extract+chunk each PDF, return a
    globally-renumbered chunk list. Pure synchronous worker — call from a
    thread via asyncio.to_thread."""
    pdf_dir = FINEP_PDFS_DIR / edital_id
    if not pdf_dir.exists():
        logger.warning("chunk_edital_task: diretório não encontrado: %s", pdf_dir)
        return []

    # 1. Lista PDFs e filtra boilerplate por keyword.
    candidates = [p for p in sorted(pdf_dir.glob("*.pdf"))
                  if not any(kw in p.stem.lower() for kw in _SKIP_KEYWORDS)]

    # 2. Mantém só a versão mais recente de cada grupo (FAQ, Edital regulamento).
    candidates = _filter_to_latest_versions(candidates)

    # 3. Chunk cada PDF sobrevivente.
    all_chunks: list[dict] = []
    for pdf_path in candidates:
        text = _extract_pdf_sync(pdf_path)
        if not text.strip():
            continue
        pdf_chunks = chunk_edital(text, source_file=pdf_path.name)
        # chunk_edital uses local indexing — we reassign globally below.
        all_chunks.extend(pdf_chunks)

    # Global chunk_index across all PDFs of this edital.
    for global_idx, chunk in enumerate(all_chunks):
        chunk["chunk_index"] = global_idx
    return all_chunks


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
