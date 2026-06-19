#!/usr/bin/env python3
"""Enriquece o golden independente com labels `expected` (source_file + section).

O golden NotebookLM nasce só com `gold_text` (suficiente pra token-recall, que é
chunking-invariante). Sem `expected`, as métricas de RANKING (hit@k, MRR) ficam
estruturalmente em 0. Aqui, para cada query, achamos no `edital_chunks` o chunk
de MAIOR sobreposição de tokens com o `gold_text` e gravamos seu (source_file,
section) como `expected` — assim hit@k/MRR passam a medir se o retrieval colocou
ESSE chunk no top-k.

Determinístico, sem LLM. Roda: python scripts/label_golden_expected.py
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

import psycopg  # noqa: E402

GOLDEN = ROOT / "eval_data" / "golden" / "finep_independent.json"
_TOK = re.compile(r"\w+", re.UNICODE)


def _toks(t: str) -> set[str]:
    return {x for x in _TOK.findall((t or "").lower()) if len(x) > 2}


def main() -> None:
    data = json.loads(GOLDEN.read_text(encoding="utf-8"))
    dsn = os.environ["DATABASE_URL"]
    labeled = 0
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        # cache de chunks por edital (evita re-query)
        cache: dict[str, list[tuple[str, str, str]]] = {}
        for q in data["queries"]:
            eid = q["edital_id"]
            if eid not in cache:
                cur.execute(
                    "select source_file, section, text from edital_chunks where edital_id=%s",
                    (eid,),
                )
                cache[eid] = cur.fetchall()
            gset = _toks(q["gold_text"])
            best, best_ov = None, 0.0
            for src, sec, txt in cache[eid]:
                ov = len(gset & _toks(txt)) / len(gset) if gset else 0.0
                if ov > best_ov:
                    best, best_ov = (src, sec), ov
            if best:
                q["expected"] = [{"source_file": best[0], "section": best[1]}]
                q["_label_overlap"] = round(best_ov, 3)
                labeled += 1
    GOLDEN.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Rotuladas {labeled}/{len(data['queries'])} queries com expected (source_file+section).")
    low = [(q["_label_overlap"], q["id"]) for q in data["queries"] if q.get("_label_overlap", 1) < 0.6]
    if low:
        print("Queries cujo melhor chunk cobre <60% do gold (label fraco, revisar):")
        for ov, qid in sorted(low):
            print(f"  {ov:.2f}  {qid}")


if __name__ == "__main__":
    main()
