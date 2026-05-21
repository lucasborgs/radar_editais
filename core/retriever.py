"""
Hybrid retrieval over edital_chunks.

Two retrievers, fused via Reciprocal Rank Fusion (RRF, k=60):
  • Dense: cosine similarity over the `embedding` column (HNSW index)
  • Sparse: tsvector FTS over `text_search` (GIN index, Portuguese)

ADR A3 (RAG for WritingSession only) — this module MUST NOT be imported by
matching code. M9: matching uses summary-level embeddings, not chunks.

SQL backend
-----------
We use psycopg directly via DATABASE_URL for the retrieval queries. Reasons:
  1. supabase-py / PostgREST does not expose the `<=>` cosine-distance
     operator nor pgvector parameter binding cleanly. Wrapping pgvector calls
     in a server-side function is feasible but adds a deployment step we
     don't need yet.
  2. The `edital_chunks` table is public-readable for any authenticated user
     (see migration 004 RLS policy `edital_chunks_read_authenticated`). There
     is no per-row tenant filter to enforce — a direct DB connection from
     the backend is equivalent in trust.
  3. psycopg is already installed (procrastinate dependency).

The `db: Client` argument is accepted for forward compatibility (if we move
to a stored function later) but is currently unused.
"""
from __future__ import annotations

import logging
import os
import re
from typing import Any

from core.embedder import embed_query
from supabase import Client

logger = logging.getLogger(__name__)

DEFAULT_TOP_K = 5
DEFAULT_FTS_WEIGHT = 0.3    # ver "Tuning do default" no docstring de `retrieve_chunks`
DEFAULT_MAX_PER_SOURCE = 2  # diversidade: nº máx de chunks do mesmo PDF no top-K
_CANDIDATE_LIMIT = 20       # how many we pull from each retriever before fusion
_RRF_CONSTANT = 60          # classic RRF k

# Stopwords PT-BR para filtrar query antes do FTS. Lista enxuta — só palavras
# super-comuns que nunca acrescentam sinal (artigos, preposições, conjunções
# básicas, verbos de ligação). NÃO filtra palavras de domínio (`edital`,
# `proposta`, etc.) — essas têm informação mesmo sendo frequentes.
_PT_STOPWORDS: frozenset[str] = frozenset({
    "a", "o", "as", "os", "um", "uma", "uns", "umas",
    "de", "do", "da", "dos", "das", "no", "na", "nos", "nas",
    "em", "por", "para", "com", "sem", "sob", "sobre",
    "e", "ou", "mas", "que", "se", "como", "qual", "quais",
    "ao", "aos", "à", "às", "pelo", "pela", "pelos", "pelas",
    "é", "são", "ser", "está", "estão", "foi", "ter", "tem",
    "este", "esta", "estes", "estas", "esse", "essa", "esses", "essas",
    "isto", "isso", "também", "muito", "mais", "menos", "quando", "onde",
    "tipo", "tipos", "todo", "toda", "todos", "todas",
})

# Captura sequências de letras PT-BR (com acentos). Despreza dígitos por escolha:
# uma query como "Art. 12" deve casar pela palavra "art" mesmo sem o "12" — e
# misturar dígitos no tsquery aumenta chance de falso positivo (datas, IDs).
_WORD_RE = re.compile(r"[a-záéíóúâêîôûãõç]+", re.IGNORECASE)


def _build_or_tsquery(query: str, max_terms: int = 8, min_len: int = 3) -> str:
    """Converte query natural em string de `to_tsquery` com OR (`|`).

    Por que existir: `plainto_tsquery` faz AND lógico — se UM termo da
    query (ex.: "subvenção") não está no corpus, retorna zero hits. Em
    queries longas com decoradores ("Qual o…?", "Que tipo de…?"), isso é
    frequente. OR resolve preservando recall; o ranking via `ts_rank`
    continua dando peso aos chunks com mais termos casados.

    Retorna string vazia se não houver termos válidos após filtragem
    (caller deve detectar e pular o FTS).
    """
    if not query:
        return ""
    seen: set[str] = set()
    keepers: list[str] = []
    for word in _WORD_RE.findall(query.lower()):
        if len(word) < min_len or word in _PT_STOPWORDS or word in seen:
            continue
        seen.add(word)
        keepers.append(word)
        if len(keepers) >= max_terms:
            break
    return " | ".join(keepers)


def _dedup_by_source(
    ranked: list[tuple[str, float]],
    by_id: dict[str, dict],
    k: int,
    max_per_source: int,
) -> list[dict]:
    """Top-K com diversidade por source_file.

    Caminha por `ranked` (já em ordem decrescente de score) e mantém no
    máximo `max_per_source` chunks por arquivo. Pode retornar menos que K
    se o corpus não tem fontes suficientes — preferimos retornar pouco e
    diverso a encher o top com duplicatas.
    """
    if max_per_source <= 0:
        max_per_source = len(ranked)  # disable dedup
    seen: dict[str, int] = {}
    out: list[dict] = []
    for cid, score in ranked:
        src = by_id[cid].get("source_file") or "__unknown__"
        if seen.get(src, 0) >= max_per_source:
            continue
        seen[src] = seen.get(src, 0) + 1
        out.append({**by_id[cid], "score": score})
        if len(out) >= k:
            break
    return out


# =============================================================================
# DB CONNECTION
# =============================================================================

def _get_dsn() -> str:
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise RuntimeError(
            "DATABASE_URL não configurada — retriever precisa acesso direto ao Postgres."
        )
    return dsn


def _vector_literal(vec: list[float]) -> str:
    """pgvector accepts a textual literal of the form '[0.1,0.2,...]'."""
    # Use repr-free serialization for speed; floats are well-formed JSON numbers.
    return "[" + ",".join(f"{x:.7f}" for x in vec) + "]"


# =============================================================================
# RRF
# =============================================================================

def _rrf_merge(
    dense_ids: list[str],
    sparse_ids: list[str],
    dense_weight: float,
    sparse_weight: float,
) -> dict[str, float]:
    """Compute RRF score per id across both ranked lists.

    score(id) = dense_weight * 1/(k+rank_dense) + sparse_weight * 1/(k+rank_sparse)

    The weights default to 0.5 each (balanced); fts_weight=0 → pure dense,
    fts_weight=1 → pure FTS.
    """
    scores: dict[str, float] = {}
    for rank, _id in enumerate(dense_ids):
        scores[_id] = scores.get(_id, 0.0) + dense_weight / (_RRF_CONSTANT + rank + 1)
    for rank, _id in enumerate(sparse_ids):
        scores[_id] = scores.get(_id, 0.0) + sparse_weight / (_RRF_CONSTANT + rank + 1)
    return scores


# =============================================================================
# MAIN
# =============================================================================

def retrieve_chunks(
    db: Client | None,  # noqa: ARG001 — reserved for a future stored-function path
    edital_ids: list[str],
    query: str,
    k: int = DEFAULT_TOP_K,
    fts_weight: float = DEFAULT_FTS_WEIGHT,
    max_per_source: int = DEFAULT_MAX_PER_SOURCE,
    query_vec: list[float] | None = None,
    primary_boost: float = 1.5,
) -> list[dict]:
    """Hybrid retrieval over edital_chunks para um ou mais editais.

    Returns top-k dicts with keys:
        id, edital_id, chunk_index, text, section, source_file, page_range, score

    Args:
        edital_ids: lista de IDs de editais. O PRIMEIRO é tratado como o
            edital "primário" (o que está sendo redigido); os demais são
            "análogos" — outros editais cujos chunks podem aparecer no
            top-K como referência. Chunks são restritos a esses IDs via
            `edital_id = ANY(%s)`.
        query: pergunta em linguagem natural. Embedada pra dense, tokenizada
            em OR-tsquery pra FTS.
        k: número final de chunks no retorno (após dedup).
        fts_weight: peso ∈ [0, 1] na fusão RRF. 0 = puro dense; 1 = puro FTS;
            valores intermediários fundem por RRF.
        max_per_source: nº máximo de chunks do mesmo `source_file` no
            top-K. Default 2 evita o caso de "3 versões do mesmo FAQ
            dominando o top-3". Use 0 pra desativar a dedup.
        query_vec: embedding pré-computado da query. Se fornecido, pula a
            chamada `embed_query` (reuso entre edital RAG + biblioteca no
            mesmo turno). Se None, embeda internamente (callers standalone).
        primary_boost: multiplicador aplicado ao score RRF de chunks vindos
            do `edital_ids[0]` (o primário). Default 1.5: análogos têm sinal
            valioso (paráfrases, exemplos de regras parecidas) mas NÃO devem
            dominar o top-K — o foco do turno é o edital primário. Use 1.0
            pra desativar o boost.

    Tuning do default
    -----------------
    Default `fts_weight=0.3` (não 0.5) porque, no corpus FINEP, o dense
    captura literalidade técnica do regulamento (vocabulário fixo: "valor
    solicitado", "ICT", "subvenção"), enquanto o FTS premia chunks de FAQ
    por densidade de paráfrase. Em paridade (0.5), FAQs ganham o RRF por
    aparecerem em ambos retrievers — empurrando o regulamento fora do
    top-k. 0.3 mantém o FTS como complemento sem deixá-lo dominar.

    Example:
        chunks = retrieve_chunks(
            db, ["601", "602", "603"], "qual o valor máximo?", k=5,
        )
        # chunks pode conter trechos do 601 (primário, boosted) e 602/603
        # (análogos, score original). Use `format_chunks_for_prompt(chunks,
        # edital_ids=["601", "602", "603"])` para prefixar os análogos.
    """
    if not edital_ids:
        return []
    if not query or not query.strip():
        return []
    fts_weight = max(0.0, min(1.0, fts_weight))
    dense_weight = 1.0 - fts_weight

    # 1. Embed the query (sync, blocking ~100ms — acceptable in a turn).
    #    Reusa o vetor pré-computado quando o caller já embedou (mesmo turno).
    if query_vec is None:
        query_vec = embed_query(query)
    vec_literal = _vector_literal(query_vec)
    ts_or_query = _build_or_tsquery(query)

    # 2. Open a short-lived psycopg connection. We don't pool here because the
    #    function is called once per `turn()` — small overhead vs. an extra
    #    background pool to manage. If volume grows, hoist this into a module
    #    global pool (psycopg_pool.ConnectionPool) initialised lazily.
    import psycopg
    dsn = _get_dsn()

    try:
        with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
            # 2a. Dense retrieval (top _CANDIDATE_LIMIT by cosine distance).
            #     `<=>` is the cosine-distance operator in pgvector.
            #     If fts_weight=1.0 we skip dense to save the index lookup.
            #     `edital_id = ANY(%s)` aceita lista — psycopg3 converte
            #     `list[str]` Python para `text[]` Postgres automaticamente.
            dense_rows: list[tuple[Any, ...]] = []
            if dense_weight > 0.0:
                cur.execute(
                    """
                    SELECT id::text, edital_id, chunk_index, text, section, source_file, page_range
                      FROM public.edital_chunks
                     WHERE edital_id = ANY(%s)
                       AND embedding IS NOT NULL
                     ORDER BY embedding <=> %s::vector
                     LIMIT %s
                    """,
                    (edital_ids, vec_literal, _CANDIDATE_LIMIT),
                )
                dense_rows = cur.fetchall()

            # 2b. Sparse FTS retrieval (Portuguese tsvector).
            #     Usamos OR-tsquery construído da query (vide `_build_or_tsquery`).
            #     `plainto_tsquery` faz AND e zera o recall em queries naturais
            #     longas — substituímos por `to_tsquery('portuguese', 'a | b | c')`.
            #     Se a query não tiver nenhum termo válido após filtragem, pula.
            sparse_rows: list[tuple[Any, ...]] = []
            if fts_weight > 0.0 and ts_or_query:
                cur.execute(
                    """
                    SELECT id::text, edital_id, chunk_index, text, section, source_file, page_range
                      FROM public.edital_chunks
                     WHERE edital_id = ANY(%s)
                       AND text_search @@ to_tsquery('portuguese', %s)
                     ORDER BY ts_rank(text_search, to_tsquery('portuguese', %s)) DESC
                     LIMIT %s
                    """,
                    (edital_ids, ts_or_query, ts_or_query, _CANDIDATE_LIMIT),
                )
                sparse_rows = cur.fetchall()
    except Exception as e:
        logger.error("retrieve_chunks: erro de SQL para editais=%s: %s", edital_ids, e)
        raise

    # 3. Build id→row index for assembly after fusion. The same id may appear
    #    in both lists; we keep one canonical row payload.
    by_id: dict[str, dict] = {}
    for row in dense_rows + sparse_rows:
        _id, edital_id_val, chunk_index, text, section, source_file, page_range = row
        if _id not in by_id:
            by_id[_id] = {
                "id": _id,
                "edital_id": edital_id_val,
                "chunk_index": chunk_index,
                "text": text,
                "section": section,
                "source_file": source_file,
                "page_range": page_range,
            }

    dense_ids = [r[0] for r in dense_rows]
    sparse_ids = [r[0] for r in sparse_rows]
    scores = _rrf_merge(dense_ids, sparse_ids, dense_weight, fts_weight)

    if not scores:
        return []

    # 3a. Boost dos chunks do edital primário. Análogos contribuem com sinal
    #     útil (regras parecidas, paráfrases) mas não devem dominar o top-K
    #     — o turno está focado no edital primário. Multiplicar o score RRF
    #     em vez de filtrar mantém os análogos visíveis quando não há
    #     concorrência forte do primário (corpus do primário pobre, p.ex.).
    primary_id = edital_ids[0]
    if primary_boost != 1.0:
        scores = {
            _id: (score * primary_boost if by_id[_id].get("edital_id") == primary_id else score)
            for _id, score in scores.items()
        }

    # 4. Top-k por RRF score, com dedup por source_file pra diversidade.
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    return _dedup_by_source(ranked, by_id, k, max_per_source)


# =============================================================================
# Formatting helper (used by WritingSession but kept here so the
# retrieval-side data model is the single source of truth)
# =============================================================================

def format_chunks_for_prompt(
    chunks: list[dict],
    edital_ids: list[str] | None = None,
) -> str:
    """Render a list of retrieved chunks as a prompt-friendly block.

    Layout (one trecho per block, blank line between):

        TRECHOS RELEVANTES DO EDITAL (top-N mais relevantes para a sua pergunta):

        [Trecho 1 — <section>] <source_file>, p. <page_range>
        <text>

        [Trecho 2 — Análogo <edital_id> — <section>] ...
        ...

    Args:
        chunks: lista de dicts retornados por `retrieve_chunks`.
        edital_ids: se fornecido, o PRIMEIRO é o edital primário (vínculo do
            turno). Chunks vindos de outros editais recebem o prefixo
            "Análogo <edital_id> — " no label para o LLM distinguir entre
            o texto vinculante (primário) e referências de outros editais.
    """
    if not chunks:
        return ""
    header = (
        f"TRECHOS RELEVANTES DO EDITAL "
        f"(top-{len(chunks)} mais relevantes para a sua pergunta):"
    )
    blocks: list[str] = [header]
    primary = edital_ids[0] if edital_ids else None
    for i, c in enumerate(chunks, start=1):
        section = c.get("section") or "sem seção"
        source = c.get("source_file") or "fonte desconhecida"
        page_range = c.get("page_range")
        chunk_edital = c.get("edital_id")
        # Marca explicitamente os análogos: o LLM precisa saber que esse
        # trecho NÃO é do edital da sessão (não-vinculante, só referência).
        is_analogue = primary is not None and chunk_edital and chunk_edital != primary
        section_label = (
            f"Análogo {chunk_edital} — {section}" if is_analogue else section
        )
        meta = f"[Trecho {i} — {section_label}] {source}"
        if page_range:
            meta += f", p. {page_range}"
        blocks.append(f"{meta}\n{c.get('text', '').strip()}")
    return "\n\n".join(blocks)


# =============================================================================
# LIBRARY ITEMS — multi-criteria retrieval (Fase 2 #16)
# =============================================================================
# Generative Agents-style scoring (RADAR §4.3):
#   final = α·recency + β·importance(decay) + γ·relevance
#
# Defaults (do RADAR.md): α=0.4, β=0.3, γ=0.3
# Half-lives:
#   recency       — 90 dias (items mais antigos perdem peso aos poucos)
#   importance    — 30 dias desde last_referenced_at (memória se "esfria")
# importance_score normalizado: score/10 ∈ [0.1, 1.0]
# cosine_sim normalizado: 1 - cosine_distance ∈ [-1, 1] (na prática quase
#   sempre ∈ [0, 1] para vetores de domínio similar)

_LIBRARY_RETRIEVAL_SQL = """
WITH candidates AS (
    SELECT
        id, title, type, summary, key_facts, themes, tags,
        importance_score, last_referenced_at, created_at,
        EXTRACT(EPOCH FROM (now() - created_at)) / 86400.0 AS days_since_created,
        EXTRACT(EPOCH FROM (now() - last_referenced_at)) / 86400.0 AS days_since_referenced,
        1 - (embedding <=> %(query_embedding)s::vector) AS cosine_sim
    FROM content_items
    WHERE workspace_id = %(workspace_id)s
      AND archived_at IS NULL
      AND embedding IS NOT NULL
    ORDER BY embedding <=> %(query_embedding)s::vector
    LIMIT 50
)
SELECT
    id, title, type, summary, key_facts, themes, tags,
    importance_score, last_referenced_at, created_at,
    cosine_sim AS relevance_score,
    EXP(-days_since_created / 90.0) AS recency_score,
    (importance_score / 10.0) * EXP(-days_since_referenced / 30.0) AS importance_decay,
    (
        %(alpha)s * EXP(-days_since_created / 90.0) +
        %(beta)s  * (importance_score / 10.0) * EXP(-days_since_referenced / 30.0) +
        %(gamma)s * cosine_sim
    ) AS final_score
FROM candidates
ORDER BY final_score DESC
LIMIT %(k)s;
"""


def retrieve_library_items(
    workspace_id: str,
    query: str,
    k: int = 10,
    alpha: float = 0.4,
    beta: float = 0.3,
    gamma: float = 0.3,
    query_vec: list[float] | None = None,
) -> list[dict]:
    """Retrieval multi-critério em content_items (ADR §4.3 / RADAR §4.3).

    Scoring final = α·recency + β·importance(decay) + γ·relevance.
    Requer que o item tenha embedding (gerado por embed_content_task após
    enrich_content_task). Items sem embedding são silenciosamente excluídos
    do candidato — esperado durante backfill de embeddings.

    Returns: lista de dicts com chaves do schema + scores de cada componente
    (relevance_score, recency_score, importance_decay, final_score).

    Falha graciosa: se embed_query falhar ou DB indisponível, retorna [].
    """
    try:
        import psycopg
        query_embedding = query_vec if query_vec is not None else embed_query(query)
        embedding_str = "[" + ",".join(str(x) for x in query_embedding) + "]"

        with psycopg.connect(_get_dsn()) as conn, conn.cursor() as cur:
            cur.execute(_LIBRARY_RETRIEVAL_SQL, {
                "query_embedding": embedding_str,
                "workspace_id": workspace_id,
                "alpha": alpha,
                "beta": beta,
                "gamma": gamma,
                "k": k,
            })
            cols = [desc[0] for desc in cur.description]
            return [dict(zip(cols, row, strict=True)) for row in cur.fetchall()]
    except Exception as e:
        logger.warning("retrieve_library_items falhou para workspace=%s: %s", workspace_id, e)
        return []


__all__ = ["retrieve_chunks", "format_chunks_for_prompt", "retrieve_library_items"]
