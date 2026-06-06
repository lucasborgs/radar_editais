#!/usr/bin/env python3
"""
Bake-off de parsing (estágio 2, end-to-end CHUNKING-INVARIANTE).

Compara estratégias de parsing→chunk no MESMO retrieval dense-only (isola a
variável "chunking"; mesma query embedding p/ todos os braços). Métrica:
token-recall sobre `gold_text` do golden (core.rag_eval) — independe de `section`.

Braços:
  • baseline : chunks de PRODUÇÃO (edital_chunks) — pdfplumber+structurer-LLM atual
  • docling  : Docling → blocos (section_path por numeração) → chunk_from_blocks

Uso: python scripts/bench_parsing.py
"""
from __future__ import annotations

import json
import math
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv  # noqa: E402

load_dotenv()

import psycopg  # noqa: E402

from config import FINEP_PDFS_DIR  # noqa: E402
from core.chunker import chunk_from_blocks  # noqa: E402
from core.embedder import embed_query, embed_texts  # noqa: E402
from core.rag_eval import gold_best_chunk_recall_at_k, gold_recall_at_k  # noqa: E402
from pipeline.adapters.finep import _SKIP_KEYWORDS, _filter_to_latest_versions  # noqa: E402

EDITAIS = ["768", "762", "743"]   # nativos (dir); golden usa finep:<id>
GOLDEN = ROOT / "eval_data" / "golden" / "finep.json"


def _select_pdfs(native_id: str) -> list[Path]:
    d = FINEP_PDFS_DIR / native_id
    cands = [p for p in sorted(d.glob("*.pdf"))
             if not any(kw in p.stem.lower() for kw in _SKIP_KEYWORDS)
             and p.read_bytes()[:4] == b"%PDF"]
    return _filter_to_latest_versions(cands)


def _docling_chunks(native_id: str) -> list[dict]:
    from pipeline.parsers.docling_blocks import blocks_from_docling
    blocks = []
    for pdf in _select_pdfs(native_id):
        blocks.extend(blocks_from_docling(pdf))
    for i, b in enumerate(blocks):
        b["idx"] = i
    return chunk_from_blocks(blocks)


def _baseline_chunks(native_id: str) -> list[dict]:
    with psycopg.connect(os.environ["DATABASE_URL"]) as c, c.cursor() as cur:
        cur.execute(
            "SELECT text, section, source_file FROM public.edital_chunks "
            "WHERE edital_id = %s ORDER BY chunk_index",
            (f"finep:{native_id}",),
        )
        return [{"text": t, "section": s, "source_file": sf} for t, s, sf in cur.fetchall()]


def _cos(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def _retrieve(qvec: list[float], chunks: list[dict], embs: list[list[float]], k: int) -> list[dict]:
    scored = sorted(zip((_cos(qvec, e) for e in embs), chunks, strict=False),
                    key=lambda x: x[0], reverse=True)
    return [c for _, c in scored[:k]]


def _eval_arm(name: str, chunks_by_edital: dict, queries: list[dict]) -> dict:
    # embeda chunks de cada edital uma vez. `embed_text` (se presente) permite
    # embeddar um texto != do usado no score (ex.: Contextual Retrieval embeda
    # contexto+corpo, mas pontua só o corpo).
    embs_by_edital = {eid: embed_texts([c.get("embed_text", c["text"]) for c in ch]) if ch else []
                      for eid, ch in chunks_by_edital.items()}
    acc = {"gold_recall_at_3": [], "gold_recall_at_5": [],
           "gold_best_chunk_recall_at_3": [], "gold_best_chunk_recall_at_5": []}
    n_chunks = sum(len(c) for c in chunks_by_edital.values())
    for q in queries:
        eid = q["edital_id"].split(":")[-1]
        chunks, embs = chunks_by_edital.get(eid, []), embs_by_edital.get(eid, [])
        if not chunks:
            continue
        qv = embed_query(q["query"])
        gold = q.get("gold_text", "")
        for k in (3, 5):
            top = _retrieve(qv, chunks, embs, k)
            acc[f"gold_recall_at_{k}"].append(gold_recall_at_k(top, gold, k) or 0.0)
            acc[f"gold_best_chunk_recall_at_{k}"].append(gold_best_chunk_recall_at_k(top, gold, k) or 0.0)
    mean = {m: round(sum(v) / len(v), 4) if v else 0.0 for m, v in acc.items()}
    sizes = [len(c["text"]) for ch in chunks_by_edital.values() for c in ch]
    print(f"\n[{name}] {n_chunks} chunks | tamanho médio {int(sum(sizes)/len(sizes)) if sizes else 0}")
    for m, v in mean.items():
        print(f"  {m:<32} {v}")
    return mean


def main() -> int:
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    queries = [q for q in golden["queries"] if q["edital_id"].split(":")[-1] in EDITAIS]
    print(f"bake-off: {len(queries)} queries em editais {EDITAIS}")

    base = {e: _baseline_chunks(e) for e in EDITAIS}
    print("docling: convertendo PDFs (1ª vez carrega modelos)…", flush=True)
    doc = {e: _docling_chunks(e) for e in EDITAIS}

    m_base = _eval_arm("BASELINE (prod chunks)", base, queries)
    m_doc = _eval_arm("DOCLING (numbering section_path)", doc, queries)

    print("\n=== DELTA (docling - baseline) ===")
    for m in m_base:
        print(f"  {m:<32} {round(m_doc[m] - m_base[m], 4):+}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
