#!/usr/bin/env python3
"""
Bake-off FAPESP (estágio 2): baseline (split_into_units→structurer-LLM, chunks de
prod) vs numbering-determinístico (blocks_from_numbered_text, zero-LLM).

Mesma metodologia do bench_parsing.py: dense-only, token-recall sobre gold_text
(chunking-invariante). Mede onde a patologia de unit gigante (24k) vive.

Uso: python scripts/bench_fapesp.py
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv  # noqa: E402

load_dotenv()

import psycopg  # noqa: E402

from core.retrieval.chunker import chunk_from_blocks  # noqa: E402
from pipeline.adapters.base import blocks_from_numbered_text, get_adapter  # noqa: E402
from scripts.bench_parsing import _eval_arm  # noqa: E402  (reusa retrieval+métrica)

EDITAIS = ["18067", "18203"]
GOLDEN = ROOT / "eval_data" / "golden" / "fapesp.json"


def _baseline_chunks(native: str) -> list[dict]:
    with psycopg.connect(os.environ["DATABASE_URL"]) as c, c.cursor() as cur:
        cur.execute(
            "SELECT text, section, source_file FROM public.edital_chunks "
            "WHERE edital_id = %s ORDER BY chunk_index",
            (f"fapesp:{native}",),
        )
        return [{"text": t, "section": s, "source_file": sf} for t, s, sf in cur.fetchall()]


def _numbering_chunks(native: str) -> list[dict]:
    docs = get_adapter("fapesp").to_documents(native)
    text = "\n\n".join(u for d in docs for u in d["units"])
    return chunk_from_blocks(blocks_from_numbered_text(text))


def main() -> int:
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    queries = [q for q in golden["queries"] if q["edital_id"].split(":")[-1] in EDITAIS]
    print(f"bake-off FAPESP: {len(queries)} queries em {EDITAIS}")

    base = {e: _baseline_chunks(e) for e in EDITAIS}
    num = {e: _numbering_chunks(e) for e in EDITAIS}

    m_base = _eval_arm("BASELINE (prod: split_into_units→LLM)", base, queries)
    m_num = _eval_arm("NUMBERING (determinístico, zero-LLM)", num, queries)

    print("\n=== DELTA (numbering - baseline) ===")
    for m in m_base:
        print(f"  {m:<32} {round(m_num[m] - m_base[m], 4):+}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
