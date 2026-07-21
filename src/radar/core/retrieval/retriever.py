"""
Hybrid retrieval over edital_chunks.

Two retrievers, fused via Reciprocal Rank Fusion (RRF, k=60):
  • Dense: cosine similarity over the `embedding` column (HNSW index)
  • Sparse: BM25 Okapi em Python (`rank_bm25`) sobre os chunks dos editais
    (default), ou tsvector FTS over `text_search` (GIN index, Portuguese) via
    `sparse="fts"` (legado). BM25 adiciona saturação k1 e normalização por
    comprimento b que o `ts_rank` não tem.

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

from radar.core.retrieval.embedder import embed_query
from radar.core.retrieval.hyde import generate_hyde_doc
from supabase import Client

logger = logging.getLogger(__name__)

DEFAULT_TOP_K = 5
DEFAULT_FTS_WEIGHT = 0.5    # ver "Tuning do default" no docstring de `retrieve_chunks`
# Coluna de embedding usada no braço dense — FONTE ÚNICA compartilhada com o
# ingest (radar.core.tasks.chunk_edital_task importa esta constante), para que gravação
# e leitura nunca divirjam (a divergência foi a raiz do landmine de 2026-06-26).
# Default "embedding" (1536d, OpenAI text-embedding-3-*). O braço gemma (768d) foi
# removido. Trocável por env para futuros bake-offs.
RETRIEVAL_EMBEDDING_COLUMN = os.environ.get("RETRIEVAL_EMBEDDING_COLUMN", "embedding")
DEFAULT_MAX_PER_SOURCE = 2  # diversidade: nº máx de chunks do mesmo PDF no top-K
DEFAULT_RERANK_CANDIDATES = 20  # tamanho do pool reordenado pelo reranker (Front 4)
DEFAULT_METADATA_BOOST = 1.2    # boost de chunks cujas flags de metadata casam com a query
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


def _bm25_tokenize(text: str) -> list[str]:
    """Tokeniza com a mesma lógica do braço FTS (`_WORD_RE` + stopwords PT-BR).

    Usar o mesmo tokenizador do FTS mantém os dois braços sparse comparáveis no
    bake-off: a diferença medida é puramente o ranker (BM25 Okapi vs ts_rank),
    não o pré-processamento do texto.
    """
    return [
        w for w in _WORD_RE.findall(text.lower())
        if len(w) >= 3 and w not in _PT_STOPWORDS
    ]


def _bm25_retrieve(
    all_chunks: list[tuple[str, str]],
    query: str,
    limit: int,
) -> list[str]:
    """Top-`limit` chunk ids por BM25 Okapi sobre o corpus em memória.

    `all_chunks` é uma lista de (id, text) — tipicamente todos os chunks dos
    editais em jogo, corpus pequeno (~100-400 por edital). Tokeniza corpus e
    query com `_bm25_tokenize`, builda `BM25Okapi` e ordena por score. Chunks
    sem token útil (corpo vazio após stopwords) recebem score 0 e só entram se
    houver score positivo — descartamos os zerados para não poluir o RRF.

    Retorna lista de ids ordenada por score decrescente (até `limit`). Vazia se
    o corpus estiver vazio ou a query não tiver token válido.
    """
    if not all_chunks:
        return []
    query_tokens = _bm25_tokenize(query)
    if not query_tokens:
        return []
    from rank_bm25 import BM25Okapi

    ids = [cid for cid, _ in all_chunks]
    corpus_tokens = [_bm25_tokenize(text or "") for _, text in all_chunks]
    bm25 = BM25Okapi(corpus_tokens)
    scores = bm25.get_scores(query_tokens)
    order = sorted(range(len(ids)), key=lambda i: scores[i], reverse=True)
    return [ids[i] for i in order[:limit] if scores[i] > 0.0]


# Detecção de intenção da query → flags de metadata do chunker.
# Espelha as flags gravadas em `edital_chunks.metadata` (core/retrieval/chunker.py
# `_detect_metadata`), mas com vocabulário de PERGUNTA, não de conteúdo: quem
# pergunta "qual o prazo?" não escreve uma data — o chunk que responde tem uma.
# `contem_tabela` fica de fora deliberadamente: não há query-pattern natural
# ("tem tabela?" não é pergunta de usuário).
_QUERY_FLAG_PATTERNS: dict[str, re.Pattern] = {
    "contem_data": re.compile(
        r"\bprazo|\bdata\b|\bquando\b|\bcronograma|\bvigên|\bvigenc|\bencerr|\bdeadline",
        re.IGNORECASE,
    ),
    "contem_valor_financeiro": re.compile(
        r"\bvalor|\bquanto\b|\brecursos?\b|\borçament|\borcament|\bfinanc"
        r"|\bmilhõ|\bmilho[e]?s|\bbilhõ|\bcontrapartida|\bverba|\bcusto",
        re.IGNORECASE,
    ),
    "contem_elegibilidade": re.compile(
        r"\belegív|\belegiv|\bproponente|\bexecutora|\bcoexecutora|\bhabilita"
        r"|\bquem\s+pode\b|\bICT\b|\bCNPJ\b",
        re.IGNORECASE,
    ),
    "contem_criterios": re.compile(
        r"\bcrit[ée]rio|\bpontua|\bpeso\b|\bnota\b|\bavalia|\bclassifica|\bjulgament",
        re.IGNORECASE,
    ),
}


def _detect_query_flags(query: str) -> frozenset[str]:
    """Flags de metadata cuja intenção aparece na query. Vazio = sem sinal."""
    if not query:
        return frozenset()
    return frozenset(
        flag for flag, pat in _QUERY_FLAG_PATTERNS.items() if pat.search(query)
    )


def _apply_metadata_boost(
    scores: dict[str, float],
    by_id: dict[str, dict],
    query_flags: frozenset[str],
    boost: float,
) -> dict[str, float]:
    """Multiplica o score de chunks cuja metadata casa com a intenção da query.

    Boost suave (não filtro): a detecção de intent é regex e erra — um WHERE
    duro mataria recall nesses erros; multiplicar só reordena. Aplica uma vez
    por chunk (any-match), não cumulativo por flag. Chunks sem metadata (rows
    indexadas antes das flags) ficam neutros — nunca penaliza.
    """
    if boost == 1.0 or not query_flags:
        return scores
    out: dict[str, float] = {}
    for _id, score in scores.items():
        meta = by_id[_id].get("metadata") or {}
        matched = any(meta.get(f) is True for f in query_flags)
        out[_id] = score * boost if matched else score
    return out


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
    rerank: bool = True,
    k_candidates: int = DEFAULT_RERANK_CANDIDATES,
    metadata_boost: float = DEFAULT_METADATA_BOOST,
    sparse: str = "bm25",
    hyde: bool = True,
    expand_sections: bool = False,
    expansion_limit: int = 24,
) -> list[dict]:
    """Hybrid retrieval over edital_chunks para um ou mais editais.

    Returns top-k dicts with keys:
        id, edital_id, chunk_index, text, section, source_file, page_range,
        metadata, score

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
        rerank: se True (default), reordena um pool de `k_candidates` chunks
            (top-RRF) por relevância à query usando `radar.core.reranker` antes do
            corte top-k. O dedup `max_per_source` é preservado.
            Degrada graciosamente: se o backend de rerank falhar/estiver
            ausente, mantém a ordenação RRF pura.
        k_candidates: tamanho do pool levado ao reranker (over-fetch). Só tem
            efeito quando `rerank=True`.
        metadata_boost: multiplicador aplicado ao score RRF de chunks cujas
            flags de metadata (contem_data, contem_valor_financeiro, etc. —
            gravadas pelo chunker) casam com a intenção detectada na query
            ("qual o prazo?" → contem_data). Boost suave, não filtro: a
            detecção é regex e erra; multiplicar só reordena, nunca exclui.
            Aplicado SÓ no estágio RRF (molda o pool que vai ao reranker e a
            ordenação de fallback) — não é reaplicado pós-rerank, porque o
            cross-encoder já vê o texto do chunk e julgar relevância à query é
            exatamente o trabalho dele; reaplicar contaria o sinal duas vezes.
            Use 1.0 pra desativar.
        sparse: ranker do braço sparse. "bm25" (default) usa BM25 Okapi em
            Python (`rank_bm25`) sobre todos os chunks dos editais em memória —
            saturação de frequência (k1) e normalização por comprimento (b) que
            o `ts_rank` do Postgres não tem. "fts" mantém o caminho legado
            (tsvector + `ts_rank` no banco). A fusão RRF e `fts_weight` são
            idênticos nos dois casos; só muda quem produz a lista sparse.
        hyde: se True (default), gera um pseudo-doc hipotético via LLM (HyDE)
            e embeda esse trecho no lugar da query crua. O pseudo-doc usa
            vocabulário formal de edital, aproximando a query do corpus no
            espaço de embedding. Fallback silencioso: se o LLM falhar/timeout,
            usa a query original. Setar False para desativar (query crua).

    Tuning do default
    -----------------
    Default `fts_weight=0.5`, calibrado para dar o mesmo peso às listas dense e
    sparse no RRF. No corpus FINEP, o dense
    captura literalidade técnica do regulamento (vocabulário fixo: "valor
    solicitado", "ICT", "subvenção"), enquanto o FTS premia chunks de FAQ
    por densidade de paráfrase. Em paridade (0.5), FAQs ganham o RRF por
    aparecerem em ambos retrievers — empurrando o regulamento fora do
    top-k; os boosts de metadados e o reranker preservam o sinal regulatório sem
    alterar o equilíbrio base entre os braços.

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
    if sparse not in ("bm25", "fts"):
        raise ValueError(f"sparse deve ser 'bm25' ou 'fts', recebido: {sparse!r}")
    fts_weight = max(0.0, min(1.0, fts_weight))
    dense_weight = 1.0 - fts_weight

    # HyDE afeta exclusivamente o braço dense. BM25/FTS, metadata, rerank e
    # observabilidade sempre recebem a pergunta original do usuário.
    raw_query, dense_query = _prepare_retrieval_queries(
        query, hyde=hyde, has_query_vec=query_vec is not None,
    )

    # 2. Embed the query (sync, blocking ~100ms — acceptable in a turn).
    #    Reusa o vetor pré-computado quando o caller já embedou (mesmo turno).
    if query_vec is None:
        query_vec = embed_query(dense_query)
    vec_literal = _vector_literal(query_vec)
    ts_or_query = _build_or_tsquery(raw_query)

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
                # RETRIEVAL_EMBEDDING_COLUMN é valor de código (env interna), não input
                # de usuário — f-string é segura aqui; não interpolar dados externos.
                emb_col = RETRIEVAL_EMBEDDING_COLUMN
                cur.execute(
                    f"""
                    SELECT id::text, edital_id, chunk_index, text, section, source_file, page_range, metadata
                      FROM public.edital_chunks
                     WHERE edital_id = ANY(%s)
                       AND {emb_col} IS NOT NULL
                       AND COALESCE(metadata->>'authority_state', 'vigente') <> 'superseded'
                     ORDER BY {emb_col} <=> %s::vector
                     LIMIT %s
                    """,
                    (edital_ids, vec_literal, _CANDIDATE_LIMIT),
                )
                dense_rows = cur.fetchall()

            # 2b. Sparse retrieval.
            #     sparse="fts": tsvector FTS no banco (legado). OR-tsquery
            #       construído da query (`_build_or_tsquery`) porque
            #       `plainto_tsquery` faz AND e zera recall em queries longas.
            #     sparse="bm25" (default): busca TODOS os chunks dos editais
            #       (corpus pequeno por edital) para rankear por BM25 Okapi em
            #       Python fora da conexão — o ts_rank não tem saturação k1 nem
            #       normalização por comprimento b. O GIN em text_search fica
            #       ocioso aqui (sem migration; ainda serve o caminho FTS).
            sparse_rows: list[tuple[Any, ...]] = []
            bm25_rows: list[tuple[Any, ...]] = []
            if fts_weight > 0.0 and sparse == "fts" and ts_or_query:
                cur.execute(
                    """
                    SELECT id::text, edital_id, chunk_index, text, section, source_file, page_range, metadata
                      FROM public.edital_chunks
                     WHERE edital_id = ANY(%s)
                       AND COALESCE(metadata->>'authority_state', 'vigente') <> 'superseded'
                       AND text_search @@ to_tsquery('portuguese', %s)
                     ORDER BY ts_rank(text_search, to_tsquery('portuguese', %s)) DESC
                     LIMIT %s
                    """,
                    (edital_ids, ts_or_query, ts_or_query, _CANDIDATE_LIMIT),
                )
                sparse_rows = cur.fetchall()
            elif fts_weight > 0.0 and sparse == "bm25":
                cur.execute(
                    """
                    SELECT id::text, edital_id, chunk_index, text, section, source_file, page_range, metadata
                      FROM public.edital_chunks
                     WHERE edital_id = ANY(%s)
                       AND COALESCE(metadata->>'authority_state', 'vigente') <> 'superseded'
                    """,
                    (edital_ids,),
                )
                bm25_rows = cur.fetchall()
    except Exception as e:
        logger.error("retrieve_chunks: erro de SQL para editais=%s: %s", edital_ids, e)
        raise

    # 3. Build id→row index for assembly after fusion. The same id may appear
    #    in both lists; we keep one canonical row payload. bm25_rows carrega o
    #    corpus inteiro dos editais; só os ids que entram no top sparse (via
    #    _bm25_retrieve) ou no dense recebem score no RRF — o resto fica inerte.
    by_id: dict[str, dict] = {}
    for row in dense_rows + sparse_rows + bm25_rows:
        _id, edital_id_val, chunk_index, text, section, source_file, page_range, metadata = row
        if _id not in by_id:
            by_id[_id] = {
                "id": _id,
                "edital_id": edital_id_val,
                "chunk_index": chunk_index,
                "text": text,
                "section": section,
                "source_file": source_file,
                "page_range": page_range,
                "metadata": metadata,
            }

    dense_ids = [r[0] for r in dense_rows]
    if sparse == "bm25":
        sparse_ids = _bm25_retrieve(
            [(r[0], r[3]) for r in bm25_rows], raw_query, _CANDIDATE_LIMIT
        )
    else:
        sparse_ids = [r[0] for r in sparse_rows]
    scores = _rrf_merge(dense_ids, sparse_ids, dense_weight, fts_weight)

    if not scores:
        return []

    # 3. Boost por flags de metadata: query pedindo prazo/valor/elegibilidade/
    #     critérios sobe chunks que comprovadamente contêm esse tipo de conteúdo
    #     (flags do chunker). Ver docstring de `metadata_boost`.
    scores = _apply_metadata_boost(
        scores, by_id, _detect_query_flags(raw_query), metadata_boost
    )

    # 4. Ordenação por RRF score.
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)

    # 4a. Rerank (Front 4): reordena o pool top-RRF por relevância à query.
    #     Over-fetch modesto (k_candidates) → rerank. Degradação graciosa:
    #     rerank_scores devolve None se o backend estiver ausente/falhar e
    #     mantemos o RRF.
    if rerank and len(ranked) > 1:
        ranked = _apply_rerank(ranked, by_id, raw_query, k_candidates)

    # 5. Corte top-k com dedup por source_file pra diversidade.
    selected = _dedup_by_source(ranked, by_id, k, max_per_source)
    if expand_sections:
        return _expand_section_families(selected, by_id, expansion_limit)
    return selected


def _prepare_retrieval_queries(
    query: str, *, hyde: bool, has_query_vec: bool,
) -> tuple[str, str]:
    """Retorna ``(raw_query, dense_query)`` sem contaminar o braço lexical."""
    raw_query = query
    dense_query = raw_query
    if hyde and not has_query_vec:
        hyde_query = generate_hyde_doc(raw_query)
        if hyde_query:
            dense_query = hyde_query
    return raw_query, dense_query


_SECTION_NUMBER_RE = re.compile(r"(?<!\d)(\d+)(?:\.\d+)*")


def _section_family(section: str | None) -> str | None:
    # O caminho completo pode conter números antes da seção normativa, como
    # "3ª RERRATIFICAÇÃO" ou o número do próprio edital. A seção efetiva é o
    # último segmento numerado do path (ex.: ... > 4.3.2 → família 4).
    matches = list(_SECTION_NUMBER_RE.finditer(section or ""))
    return matches[-1].group(1) if matches else None


def _expand_section_families(
    selected: list[dict], by_id: dict[str, dict], limit: int,
) -> list[dict]:
    """Expande a família estrutural do melhor hit antes dos hits secundários.

    Uma query enumerativa costuma trazer hits esparsos de outras seções. Se
    todas as famílias forem expandidas na ordem global do documento, esses
    falsos positivos consomem o limite antes do fim da seção relevante. O
    primeiro hit é a âncora; seus irmãos vêm em ordem documental e os demais
    hits selecionados ficam como cauda de apoio.
    """
    if not selected or limit <= len(selected):
        return selected[:max(0, limit)]
    anchor = selected[0]
    anchor_family = _section_family(anchor.get("section"))
    anchor_key = (anchor.get("source_file"), anchor_family)
    if not anchor_family:
        return selected[:limit]

    out: list[dict] = []
    seen: set[str] = set()
    siblings = sorted(
        by_id.values(),
        key=lambda c: (str(c.get("source_file") or ""), int(c.get("chunk_index") or 0)),
    )
    for chunk in siblings:
        key = (chunk.get("source_file"), _section_family(chunk.get("section")))
        chunk_id = str(chunk.get("id"))
        if key != anchor_key or chunk_id in seen:
            continue
        item = dict(chunk)
        item["score"] = anchor.get("score", 0.0) if chunk_id == str(anchor.get("id")) else 0.0
        if chunk_id != str(anchor.get("id")):
            item["structural_expansion"] = True
        out.append(item)
        seen.add(chunk_id)
        if len(out) >= limit:
            return out
    for chunk in selected:
        chunk_id = str(chunk.get("id"))
        if chunk_id in seen:
            continue
        out.append(chunk)
        seen.add(chunk_id)
        if len(out) >= limit:
            break
    return out


def _apply_rerank(
    ranked: list[tuple[str, float]],
    by_id: dict[str, dict],
    query: str,
    k_candidates: int,
) -> list[tuple[str, float]]:
    """Reordena o pool top-`k_candidates` por relevância à query (Front 4).

    Mantém a cauda (além de k_candidates) na ordem RRF original — só o pool
    relevante é reordenado. Se o reranker indisponível (None), devolve `ranked`
    intacto.
    """
    from radar.core.reranker import rerank_scores

    pool = ranked[:k_candidates]
    tail = ranked[k_candidates:]
    texts = [(by_id[cid].get("text") or "") for cid, _ in pool]

    rr = rerank_scores(query, texts)
    if rr is None:
        return ranked

    rescored: list[tuple[str, float]] = [
        (cid, rscore) for (cid, _rrf), rscore in zip(pool, rr, strict=False)
    ]
    rescored.sort(key=lambda kv: kv[1], reverse=True)
    # A cauda (não reordenada) vai depois — só vira top-k se o pool não encher.
    return rescored + tail


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
