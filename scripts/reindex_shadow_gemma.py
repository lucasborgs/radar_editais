#!/usr/bin/env python3
"""
Popula embedding_gemma (vector(768)) em edital_chunks para os editais do golden.

Usa tardellirs/embeddinggemma-pt-br via sentence-transformers in-process.
Não toca a coluna embedding (prod). Roda apenas sobre os editais passados
via --editais (default: todos os editais do golden finep).

Uso:
    ST_DEVICE=cpu python scripts/reindex_shadow_gemma.py
    ST_DEVICE=cpu python scripts/reindex_shadow_gemma.py --editais finep:768,finep:743
    ST_DEVICE=cpu python scripts/reindex_shadow_gemma.py --source fapesp
"""
from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

MODEL_NAME = "tardellirs/embeddinggemma-pt-br"
PASSAGE_PREFIX = "title: none | text: "
BATCH_SIZE = 32
TARGET_COLUMN = "embedding_gemma"


def _load_edital_ids_from_golden(source: str, restrict: set[str] | None) -> list[str]:
    root = Path(__file__).resolve().parent.parent
    golden_path = root / "eval_data" / "golden" / f"{source}.json"
    if not golden_path.exists():
        raise SystemExit(f"golden ausente: {golden_path}")
    golden = json.loads(golden_path.read_text(encoding="utf-8"))
    ids = sorted({str(q["edital_id"]) for q in golden.get("queries", [])})
    if restrict:
        ids = [i for i in ids if i in restrict]
    return ids


def _load_all_edital_ids(dsn: str, only_null: bool = True) -> list[str]:
    """Todos os edital_ids em edital_chunks (ou só os com embedding_gemma NULL)."""
    import psycopg
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        if only_null:
            cur.execute(
                "SELECT DISTINCT edital_id FROM public.edital_chunks "
                "WHERE embedding_gemma IS NULL ORDER BY edital_id"
            )
        else:
            cur.execute(
                "SELECT DISTINCT edital_id FROM public.edital_chunks ORDER BY edital_id"
            )
        return [row[0] for row in cur.fetchall()]


def _fetch_chunks(dsn: str, edital_ids: list[str]) -> list[dict]:
    import psycopg
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT id::text, text
              FROM public.edital_chunks
             WHERE edital_id = ANY(%s)
             ORDER BY edital_id, chunk_index
            """,
            (edital_ids,),
        )
        return [{"id": row[0], "text": row[1]} for row in cur.fetchall()]


def _write_embeddings(dsn: str, rows: list[tuple[str, list[float]]]) -> None:
    """Batch UPDATE: id → embedding_gemma."""
    import psycopg
    from psycopg import sql

    col = sql.Identifier(TARGET_COLUMN)
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        for chunk_id, vec in rows:
            vec_literal = "[" + ",".join(f"{x:.7f}" for x in vec) + "]"
            cur.execute(
                sql.SQL(
                    "UPDATE public.edital_chunks SET {} = %s::vector WHERE id::text = %s"
                ).format(col),
                (vec_literal, chunk_id),
            )
        conn.commit()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", default=os.getenv("EVAL_RAG_SOURCE", "finep"))
    parser.add_argument("--editais", default=None,
                        help="CSV de edital_ids a processar (ex.: finep:768,finep:743)")
    parser.add_argument("--all", action="store_true",
                        help="processa todos os edital_ids em edital_chunks com embedding_gemma NULL")
    args = parser.parse_args()

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise SystemExit("DATABASE_URL não configurada.")

    if args.all:
        edital_ids = _load_all_edital_ids(dsn, only_null=True)
    else:
        restrict = {e.strip() for e in args.editais.split(",")} if args.editais else None
        edital_ids = _load_edital_ids_from_golden(args.source, restrict)
    print(f"editais: {edital_ids}")

    print("buscando chunks...")
    chunks = _fetch_chunks(dsn, edital_ids)
    print(f"{len(chunks)} chunks encontrados")
    if not chunks:
        raise SystemExit("nenhum chunk — editais indexados em edital_chunks?")

    print(f"carregando {MODEL_NAME} (ST_DEVICE={os.environ.get('ST_DEVICE', 'auto')})...")
    from sentence_transformers import SentenceTransformer
    device = os.environ.get("ST_DEVICE") or None
    model = SentenceTransformer(MODEL_NAME, device=device)

    print("embedando...")
    t0 = time.perf_counter()
    texts = [PASSAGE_PREFIX + c["text"] for c in chunks]
    all_vecs: list[list[float]] = []
    for start in range(0, len(texts), BATCH_SIZE):
        batch = texts[start:start + BATCH_SIZE]
        vecs = model.encode(batch, show_progress_bar=False, normalize_embeddings=False)
        all_vecs.extend(vecs.tolist())
        done = min(start + BATCH_SIZE, len(texts))
        print(f"  {done}/{len(texts)}", end="\r")
    elapsed = time.perf_counter() - t0
    print(f"\n{len(all_vecs)} vetores em {elapsed:.1f}s ({elapsed/len(all_vecs)*1000:.1f}ms/chunk)")

    print(f"escrevendo em {TARGET_COLUMN}...")
    rows = [(c["id"], v) for c, v in zip(chunks, all_vecs, strict=True)]
    _write_embeddings(dsn, rows)
    print("pronto.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
