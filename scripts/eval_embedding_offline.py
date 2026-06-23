#!/usr/bin/env python3
"""
Bake-off de embeddings + contextualização — eval OFFLINE do braço dense (numpy).

Filtro barato dos tiers 1-2: compara braços
de retrieval por cosseno puro sobre o corpus golden, SEM re-indexar a coluna
`edital_chunks.embedding` (vector(1536)) e sem tocar prod.

Dois eixos testáveis (independentes):
  • EMBEDDING (tier 1): troca o modelo de embedding. Candidatos de dim ≠ 1536
    (Qwen3-0.6B/BGE-M3 = 1024) são avaliáveis sem migração.  → --candidate
  • CONTEXTUALIZAÇÃO (tier 2): troca o LLM que gera o contexto-no-chunk
    (core/contextual_retrieval.py) ANTES do embed. Embedding fica fixo no
    baseline OpenAI. É o único ponto onde um LLM barato move o retrieval.  → --contextualizer

O que mede e o que NÃO mede
---------------------------
Isola o braço DENSE puro: top-k por cosseno query×chunk, SEM FTS, SEM RRF, SEM
rerank, SEM metadata-boost. É uma TRIAGEM CONSERVADORA do dense — decide se um
braço merece o investimento da coluna-sombra/re-index, mas pode dar
FALSO-NEGATIVO: em prod o rerank (pool top-20) + a fusão (fts_weight) podem
socorrer um candidato levemente abaixo. Logo "perdeu aqui" ≠ "perde end-to-end".
Por isso mede recall@20 (=POOL_K, pool do reranker em prod) além de @5. Critério
de promoção (WIN/CLOSE/REJECT). Quem passa a triagem
ainda precisa do `core.eval rag` real (FTS+RRF+rerank) antes de promover.

Métricas (reusa core.rag_eval, mesmas do gate `rag`):
  • recall@{1,3,5} + MRR   — contra `expected` (source_file + section) do golden
  • gold_recall@{3,5}      — token-recall sobre `gold_text` (chunking-invariante)

Uso:
    # baseline (texto cru + embed OpenAI) sozinho
    python scripts/eval_embedding_offline.py

    # tier 1: trocar o modelo de embedding (formato "modelo|dims|base_url")
    python scripts/eval_embedding_offline.py \
        --candidate "bge-m3|1024|http://localhost:11434/v1"

    # tier 2: trocar o contextualizador (formato "modelo|base_url|KEY_ENV_NAME")
    #   embed fica no baseline OpenAI; compara cru vs gpt-4o-mini vs gemini
    python scripts/eval_embedding_offline.py \
        --contextualizer "gpt-4o-mini" \
        --contextualizer "gemini-2.5-flash|https://generativelanguage.googleapis.com/v1beta/openai/|GEMINI_API_KEY"

Pré-req: DATABASE_URL (corpus) + OPENAI_API_KEY (baseline) + keys/endpoints dos
braços. Saída: tabela no stdout + JSON em eval_results/<ts>_embedding_offline.json.
"""
from __future__ import annotations

import argparse
import importlib
import json
import os
import time
from datetime import datetime, timezone

from dotenv import load_dotenv

load_dotenv()

from config import ROOT  # noqa: E402

GOLDEN_DIR = ROOT / "eval_data" / "golden"
RESULTS_DIR = ROOT / "eval_results"

BASELINE = "text-embedding-3-large"
BASELINE_DIMS = 1536


# ---------------------------------------------------------------------------
# Specs: parsing + (re)carga dos módulos por env (modelo lido no import)
# ---------------------------------------------------------------------------

def _parse_embed_spec(spec: str) -> dict:
    """'model|dims|base_url' → {model, dims, base_url}. Campos 2 e 3 opcionais."""
    parts = [p.strip() for p in spec.split("|")]
    return {
        "model": parts[0],
        "dims": int(parts[1]) if len(parts) > 1 and parts[1] else None,
        "base_url": parts[2] if len(parts) > 2 and parts[2] else None,
    }


def _parse_ctx_spec(spec: str) -> dict:
    """'model|base_url|KEY_ENV_NAME' → {model, base_url, key}. Campos 2 e 3 opcionais.

    `KEY_ENV_NAME` é o NOME da env que guarda a key (não a key em si) — evita
    segredo no histórico do shell. Default cai em OPENAI_API_KEY.
    """
    parts = [p.strip() for p in spec.split("|")]
    key_env = parts[2] if len(parts) > 2 and parts[2] else "OPENAI_API_KEY"
    return {
        "model": parts[0],
        "base_url": parts[1] if len(parts) > 1 and parts[1] else None,
        "key": os.environ.get(key_env),
    }


def _load_embedder(embed: dict):
    """Seta o env do modelo de embedding e recarrega o embedder."""
    os.environ["EMBEDDING_MODEL"] = embed["model"]
    if embed["dims"]:
        os.environ["EMBEDDING_DIMENSIONS"] = str(embed["dims"])
    if embed["base_url"]:
        os.environ["EMBEDDING_BASE_URL"] = embed["base_url"]
    else:
        os.environ.pop("EMBEDDING_BASE_URL", None)
    from core.retrieval import embedder
    importlib.reload(embedder)
    return embedder.embed_texts, embedder.embed_query


# ---------------------------------------------------------------------------
# Backend sentence-transformers (in-process) — para modelos HF sem API
# OpenAI-compat. NÃO toca core/retrieval/embedder.py (produção); vive só aqui.
#
# Por que in-process e não TEI/shim: modelos E5/EmbeddingGemma são ASSIMÉTRICOS
# — query e passagem exigem prefixos diferentes. O harness chama embed_texts
# (passagens) e embed_query (query) separadamente, mas um endpoint OpenAI-compat
# recebe ambos sem saber o papel → não dá pra aplicar o prefixo certo, o que
# mutila o modelo e o reprova injustamente. Aqui injetamos o prefixo por papel.
#
# O prefixo é propriedade do modelo (não escolha de runtime) → registry abaixo.
# query_prefix/passage_prefix são prepended a cada texto antes do encode.
# trust_remote_code só p/ modelos que exigem (ex.: Jina v2).
# ---------------------------------------------------------------------------

# Fonte de verdade em core/retrieval/embedder.py — importamos daqui para evitar
# divergência de prefixos entre o eval offline e o caminho de prod/bake-off.
from core.retrieval.embedder import _ST_REGISTRY  # noqa: E402


def _load_st_embedder(model_name: str):
    """Carrega um SentenceTransformer e devolve (embed_texts, embed_query)
    com os prefixos por papel do registry. Modelo desconhecido → sem prefixo."""
    from sentence_transformers import SentenceTransformer

    cfg = _ST_REGISTRY.get(model_name, {})
    qp = cfg.get("query_prefix", "")
    pp = cfg.get("passage_prefix", "")
    trust = cfg.get("trust_remote_code", False)
    # ST_DEVICE permite forçar cpu/mps; default deixa o sentence-transformers decidir.
    device = os.environ.get("ST_DEVICE") or None
    model = SentenceTransformer(model_name, trust_remote_code=trust, device=device)

    def embed_texts(texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        vecs = model.encode([pp + t for t in texts], batch_size=32,
                            show_progress_bar=False, normalize_embeddings=False)
        return vecs.tolist()

    def embed_query(text: str) -> list[float]:
        vec = model.encode(qp + text, normalize_embeddings=False)
        return vec.tolist()

    return embed_texts, embed_query


def _load_contextualizer(ctx: dict | None):
    """Seta o env do contextualizador e recarrega contextual_retrieval.

    Retorna a função contextualize_chunks ligada a este modelo, ou None quando
    `ctx` é None (braço de texto cru, sem contexto).
    """
    if ctx is None:
        os.environ["CONTEXTUAL_RETRIEVAL"] = "false"
        return None
    os.environ["CONTEXTUAL_RETRIEVAL"] = "true"
    os.environ["CONTEXTUAL_RETRIEVAL_MODEL"] = ctx["model"]
    if ctx["base_url"]:
        os.environ["CONTEXTUAL_RETRIEVAL_BASE_URL"] = ctx["base_url"]
    else:
        os.environ.pop("CONTEXTUAL_RETRIEVAL_BASE_URL", None)
    if ctx["key"]:
        os.environ["CONTEXTUAL_RETRIEVAL_API_KEY"] = ctx["key"]
    from core import contextual_retrieval
    importlib.reload(contextual_retrieval)
    return contextual_retrieval.contextualize_chunks


# ---------------------------------------------------------------------------
# Corpus + golden
# ---------------------------------------------------------------------------

def _load_golden(source: str) -> dict:
    path = GOLDEN_DIR / f"{source}.json"
    if not path.exists():
        raise SystemExit(f"golden ausente: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _fetch_corpus(edital_ids: list[str]) -> dict[str, list[dict]]:
    """Chunks (texto puro, sem embedding) por edital, do edital_chunks."""
    import psycopg
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise SystemExit("DATABASE_URL não configurada — corpus vive em edital_chunks.")
    by_edital: dict[str, list[dict]] = {eid: [] for eid in edital_ids}
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT edital_id, chunk_index, text, section, source_file
              FROM public.edital_chunks
             WHERE edital_id = ANY(%s)
             ORDER BY edital_id, chunk_index
            """,
            (edital_ids,),
        )
        for edital_id, chunk_index, text, section, source_file in cur.fetchall():
            by_edital[edital_id].append({
                "chunk_index": chunk_index,
                "text": text,
                "section": section,
                "source_file": source_file,
            })
    return by_edital


# ---------------------------------------------------------------------------
# Cosseno (numpy) — braço dense puro
# ---------------------------------------------------------------------------

def _rank_by_cosine(query_vec, chunk_vecs, chunks: list[dict]) -> list[dict]:
    """Top chunks por similaridade de cosseno query×chunk (ordem decrescente)."""
    import numpy as np
    q = np.asarray(query_vec, dtype=np.float32)
    M = np.asarray(chunk_vecs, dtype=np.float32)  # noqa: N806  (matriz, convenção numpy)
    qn = q / (np.linalg.norm(q) + 1e-9)
    Mn = M / (np.linalg.norm(M, axis=1, keepdims=True) + 1e-9)  # noqa: N806
    sims = Mn @ qn
    order = np.argsort(-sims)
    return [{**chunks[i], "score": float(sims[i])} for i in order]


# ---------------------------------------------------------------------------
# Braço sparse (BM25/FTS) + fusão RRF — opcional via --sparse
#
# O modo default do script isola o dense puro. Com --sparse, ativamos um braço
# sparse e fundimos por RRF, permitindo comparar BM25 vs FTS isoladamente ANTES
# de tocar o retriever de prod. Pesos 0.5/0.5 (não o fts_weight de prod) de
# propósito: queremos a contribuição máxima do sparse para discriminar os
# rankers, não a mistura calibrada de produção.
# ---------------------------------------------------------------------------

_RRF_CONSTANT = 60  # mesmo k clássico do retriever de prod


def _cosine_order(query_vec, chunk_vecs) -> list[int]:
    """Índices dos chunks em ordem decrescente de cosseno (sem montar dicts)."""
    import numpy as np
    q = np.asarray(query_vec, dtype=np.float32)
    M = np.asarray(chunk_vecs, dtype=np.float32)  # noqa: N806  (matriz, convenção numpy)
    qn = q / (np.linalg.norm(q) + 1e-9)
    Mn = M / (np.linalg.norm(M, axis=1, keepdims=True) + 1e-9)  # noqa: N806
    return [int(i) for i in np.argsort(-(Mn @ qn))]


def _bm25_order(query: str, chunks: list[dict], limit: int) -> list[int]:
    """Índices dos chunks por BM25 Okapi (reusa o ranker do retriever de prod)."""
    from core.retrieval.retriever import _bm25_retrieve
    pairs = [(str(i), c["text"]) for i, c in enumerate(chunks)]
    return [int(i) for i in _bm25_retrieve(pairs, query, limit)]


def _fts_order(query: str, edital_id: str, chunks: list[dict], limit: int) -> list[int]:
    """Índices dos chunks por FTS/ts_rank no banco, mapeados via chunk_index.

    Espelha o braço FTS de prod (to_tsquery OR + ts_rank). Reconsulta o banco
    porque o tsvector vive lá; mapeia o resultado para os índices da lista
    `chunks` (ordenada por chunk_index em `_fetch_corpus`).
    """
    import psycopg

    from core.retrieval.retriever import _build_or_tsquery
    ts_or = _build_or_tsquery(query)
    if not ts_or:
        return []
    by_chunk_index = {c["chunk_index"]: i for i, c in enumerate(chunks)}
    with psycopg.connect(os.environ["DATABASE_URL"], autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT chunk_index
              FROM public.edital_chunks
             WHERE edital_id = %s
               AND text_search @@ to_tsquery('portuguese', %s)
             ORDER BY ts_rank(text_search, to_tsquery('portuguese', %s)) DESC
             LIMIT %s
            """,
            (edital_id, ts_or, ts_or, limit),
        )
        out: list[int] = []
        for (chunk_index,) in cur.fetchall():
            idx = by_chunk_index.get(chunk_index)
            if idx is not None:
                out.append(idx)
        return out


def _rrf_fuse(dense_idx: list[int], sparse_idx: list[int], chunks: list[dict]) -> list[dict]:
    """Funde dois rankings de índices via RRF (0.5/0.5) → lista de dicts."""
    scores: dict[int, float] = {}
    for rank, i in enumerate(dense_idx):
        scores[i] = scores.get(i, 0.0) + 0.5 / (_RRF_CONSTANT + rank + 1)
    for rank, i in enumerate(sparse_idx):
        scores[i] = scores.get(i, 0.0) + 0.5 / (_RRF_CONSTANT + rank + 1)
    order = sorted(scores, key=lambda i: scores[i], reverse=True)
    return [{**chunks[i], "score": scores[i]} for i in order]


# ---------------------------------------------------------------------------
# Avaliação de um braço (embedding model + contextualizador opcional)
# ---------------------------------------------------------------------------

# Profundidades de recall medidas. POOL_K=20 = DEFAULT_RERANK_CANDIDATES de prod
# (retriever.py): recall@20 mede se o chunk certo ENTRA no pool que o reranker
# reordena → discrimina FALSO-NEGATIVO (perde no top-5 mas o rerank salvaria) de
# REJECT real (não entra nem no pool). Ver critério em llm-embedding-bakeoff.md §1.
POOL_K = 20
_KS = (1, 3, 5, POOL_K)
_GOLD_KS = (3, 5, POOL_K)


def _eval_arm(arm: dict, golden: dict, corpus: dict[str, list[dict]]) -> dict:
    from core.rag_eval import (
        aggregate_runs,
        evaluate_query,
        gold_best_chunk_recall_at_k,
        gold_recall_at_k,
    )

    contextualize = _load_contextualizer(arm.get("ctx"))
    if arm.get("st"):
        embed_texts, embed_query = _load_st_embedder(arm["st"])
    else:
        embed_texts, embed_query = _load_embedder(arm["embed"])

    # Embeda o corpus uma vez por edital. Se há contextualizador, prepende o
    # contexto-no-chunk antes (por edital, igual ao chunk_edital_task de prod).
    t0 = time.perf_counter()
    corpus_vecs: dict[str, list] = {}
    for eid, chunks in corpus.items():
        if not chunks:
            corpus_vecs[eid] = []
            continue
        texts = contextualize(chunks) if contextualize else [c["text"] for c in chunks]
        corpus_vecs[eid] = embed_texts(texts)
    embed_corpus_s = time.perf_counter() - t0

    sparse = arm.get("sparse")
    per_query: list[dict] = []
    gold_recall = {k: [] for k in _GOLD_KS}
    gold_best = {k: [] for k in _GOLD_KS}
    max_k = max(_KS)
    for q in golden.get("queries", []):
        eid = str(q["edital_id"])
        chunks = corpus.get(eid, [])
        if not chunks:
            continue
        qv = embed_query(q["query"])
        if sparse:
            dense_idx = _cosine_order(qv, corpus_vecs[eid])
            if sparse == "fts":
                sparse_idx = _fts_order(q["query"], eid, chunks, POOL_K)
            else:
                sparse_idx = _bm25_order(q["query"], chunks, POOL_K)
            ranked = _rrf_fuse(dense_idx, sparse_idx, chunks)
        else:
            ranked = _rank_by_cosine(qv, corpus_vecs[eid], chunks)
        per_query.append(evaluate_query(ranked[:max_k], q.get("expected", []), ks=_KS))
        gold = q.get("gold_text", "")
        if gold:
            for k in _GOLD_KS:
                r = gold_recall_at_k(ranked, gold, k)
                b = gold_best_chunk_recall_at_k(ranked, gold, k)
                if r is not None:
                    gold_recall[k].append(r)
                if b is not None:
                    gold_best[k].append(b)

    agg = aggregate_runs(per_query, ks=_KS)
    for k in _GOLD_KS:
        if gold_recall[k]:
            agg[f"gold_recall_at_{k}"] = round(sum(gold_recall[k]) / len(gold_recall[k]), 4)
        if gold_best[k]:
            agg[f"gold_best_chunk_recall_at_{k}"] = round(sum(gold_best[k]) / len(gold_best[k]), 4)
    agg["embed_corpus_s"] = round(embed_corpus_s, 2)
    return agg


# ---------------------------------------------------------------------------
# Relatório
# ---------------------------------------------------------------------------

_METRICS = [
    "recall_at_1", "recall_at_3", "recall_at_5", f"recall_at_{POOL_K}", "mrr",
    "gold_recall_at_3", "gold_recall_at_5", f"gold_recall_at_{POOL_K}",
    "gold_best_chunk_recall_at_3", "gold_best_chunk_recall_at_5",
    f"gold_best_chunk_recall_at_{POOL_K}",
]


def _print_table(results: list[tuple[str, dict]], sparse: str | None = None) -> None:
    baseline_agg = results[0][1]
    header = "metric".ljust(28) + "".join(name[:18].rjust(22) for name, _ in results)
    print("\n" + header)
    print("-" * len(header))
    for metric in _METRICS:
        if not any(metric in agg for _, agg in results):
            continue
        row = metric.ljust(28)
        for i, (_, agg) in enumerate(results):
            val = agg.get(metric)
            if val is None:
                cell = "—"
            elif i == 0:
                cell = f"{val:.3f}"
            else:
                cell = f"{val:.3f} ({val - baseline_agg.get(metric, 0.0):+.3f})"
            row += cell.rjust(22)
        print(row)
    print("-" * len(header))
    mode = (f"DENSE+{sparse} via RRF (0.5/0.5), sem rerank" if sparse
            else "braço DENSE puro, sem FTS/RRF/rerank")
    print("n_queries=" + str(baseline_agg.get("n_queries", 0)) + f"  ({mode}, offline)")


def main() -> int:
    parser = argparse.ArgumentParser(prog="eval_embedding_offline", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", default=os.getenv("EVAL_RAG_SOURCE", "finep"),
                        help="golden a usar (default: finep)")
    parser.add_argument("--candidate", action="append", default=[],
                        metavar="model|dims|base_url",
                        help="tier 1 — modelo de embedding alternativo via OpenAI-compat (repetível)")
    parser.add_argument("--st-candidate", action="append", default=[],
                        metavar="hf_model_name",
                        help="tier 1 — modelo HF via sentence-transformers in-process; "
                             "prefixos query/passagem vêm do _ST_REGISTRY (repetível)")
    parser.add_argument("--contextualizer", action="append", default=[],
                        metavar="model|base_url|KEY_ENV",
                        help="tier 2 — LLM de contextualização (repetível); embed fica no baseline")
    parser.add_argument("--no-baseline", action="store_true",
                        help="não inclui o braço baseline (texto cru + embed OpenAI)")
    parser.add_argument("--editais", default=None,
                        help="restringe a estes edital_ids (CSV, ex.: 'finep:768,finep:743') — "
                             "corta o nº de chunks a contextualizar/embedar")
    parser.add_argument("--sparse", choices=["bm25", "fts"], default=None,
                        help="ativa o braço sparse e funde com o dense via RRF (0.5/0.5): "
                             "'bm25' (rank_bm25 em Python) ou 'fts' (ts_rank no banco). "
                             "Default: só dense. Use para comparar BM25 vs FTS offline.")
    args = parser.parse_args()

    golden = _load_golden(args.source)
    if args.editais:
        keep = {e.strip() for e in args.editais.split(",")}
        golden["queries"] = [q for q in golden.get("queries", []) if str(q["edital_id"]) in keep]
    edital_ids = sorted({str(q["edital_id"]) for q in golden.get("queries", [])})
    print(f"golden={args.source}  queries={len(golden.get('queries', []))}  editais={edital_ids}")
    print("buscando corpus em edital_chunks...")
    corpus = _fetch_corpus(edital_ids)
    n_chunks = sum(len(v) for v in corpus.values())
    print(f"corpus: {n_chunks} chunks")
    if n_chunks == 0:
        raise SystemExit("corpus vazio — os editais do golden não estão indexados em edital_chunks.")

    # Monta os braços. embed = qual modelo de embedding; ctx = contextualizador (ou None).
    base_embed = {"model": BASELINE, "dims": BASELINE_DIMS, "base_url": None}
    arms: list[dict] = []
    if not args.no_baseline:
        arms.append({"label": "baseline (cru)", "embed": base_embed, "ctx": None})
    for s in args.candidate:
        arms.append({"label": _parse_embed_spec(s)["model"], "embed": _parse_embed_spec(s), "ctx": None})
    for m in args.st_candidate:
        arms.append({"label": f"st:{m.split('/')[-1]}", "embed": base_embed, "ctx": None, "st": m})
    for s in args.contextualizer:
        ctx = _parse_ctx_spec(s)
        arms.append({"label": f"ctx:{ctx['model']}", "embed": base_embed, "ctx": ctx})
    if not arms:
        raise SystemExit("nada a avaliar — passe --candidate/--contextualizer ou rode com baseline.")
    if args.sparse:
        for arm in arms:
            arm["sparse"] = args.sparse
            arm["label"] = f"{arm['label']} +{args.sparse}"

    results: list[tuple[str, dict]] = []
    for arm in arms:
        print(f"\n→ avaliando {arm['label']} ...")
        agg = _eval_arm(arm, golden, corpus)
        results.append((arm["label"], agg))
        print(f"   recall@5={agg.get('recall_at_5')}  recall@{POOL_K}={agg.get(f'recall_at_{POOL_K}')}  "
              f"mrr={agg.get('mrr'):.3f}  gold_recall@5={agg.get('gold_recall_at_5')}  "
              f"embed={agg.get('embed_corpus_s')}s")

    _print_table(results, sparse=args.sparse)

    RESULTS_DIR.mkdir(exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = RESULTS_DIR / f"{ts}_embedding_offline.json"
    out_path.write_text(json.dumps({
        "kind": f"embedding_offline_dense+{args.sparse}" if args.sparse else "embedding_offline_dense",
        "sparse": args.sparse,
        "source": args.source,
        "edital_ids": edital_ids,
        "n_chunks": n_chunks,
        "ks": list(_KS),
        "generated_at": ts,
        "results": [{"arm": name, "aggregate": agg} for name, agg in results],
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n→ salvo em {out_path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
