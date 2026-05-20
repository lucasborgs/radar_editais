#!/usr/bin/env python3
"""
Gate de validação do caminho silver (WIKI.md §11/§12).

Roda o pipeline `adapter → structurer → chunk_from_blocks` num edital e reporta:
  - nº de chunks, % com `section` populado (`section_path` do silver)
  - cobertura de texto (anti-perda)
  - % com `page_range` preenchido

Critérios PASS: section ≥80%, cobertura ≥95%.

Uso:
    python scripts/probe_silver_chunks.py            # edital 589
    python scripts/probe_silver_chunks.py --edital 768 --force
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from config import FINEP_PDFS_DIR  # noqa: E402
from core.chunker import chunk_from_blocks  # noqa: E402
from core.structurer import build_or_load_structured_doc  # noqa: E402
from pipeline.adapters.base import get_adapter  # noqa: E402

_NORM = re.compile(r"\s+")


def _norm(s: str) -> str:
    return _NORM.sub("", s).lower()


def _coverage(source_text: str, chunks: list[dict]) -> float:
    """Fração aproximada do texto-fonte presente nos chunks (anti-perda).

    Heurística: dos tokens únicos do source, quantos aparecem no corpus de
    chunks. Não é exato (overlap/limpeza), mas pega perda grosseira.
    """
    src_tokens = set(_NORM.sub(" ", source_text).lower().split())
    if not src_tokens:
        return 1.0
    chunk_blob = _NORM.sub(" ", " ".join(c["text"] for c in chunks)).lower()
    chunk_tokens = set(chunk_blob.split())
    return len(src_tokens & chunk_tokens) / len(src_tokens)


def _report(name: str, chunks: list[dict], source_text: str) -> None:
    n = len(chunks)
    with_sec = sum(1 for c in chunks if c.get("section"))
    with_pr = sum(1 for c in chunks if c.get("page_range"))
    cov = _coverage(source_text, chunks)
    print(f"\n── {name} ──")
    print(f"  chunks            : {n}")
    print(f"  com section       : {with_sec}/{n}"
          f" ({100 * with_sec / n:.0f}%)" if n else "  com section       : 0")
    print(f"  com page_range    : {with_pr}/{n}")
    print(f"  cobertura texto   : {cov:.1%}")
    secs = [c["section"] for c in chunks if c.get("section")][:5]
    for s in secs:
        print(f"     section: {s[:80]}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--edital", default="589")
    ap.add_argument("--force", action="store_true",
                    help="ignora cache silver e re-roda o structurer")
    args = ap.parse_args()

    pdf_dir = FINEP_PDFS_DIR / args.edital
    if not pdf_dir.exists():
        print(f"ERRO: {pdf_dir} não existe", file=sys.stderr)
        return 2

    # L1: Source Adapter (§12) → Documento Canônico.
    adapter = get_adapter("finep")
    documents = adapter.to_documents(args.edital)
    print(f"Edital {args.edital} — {len(documents)} documentos: "
          f"{[d['doc_name'] for d in documents]}")

    source_text = "\n".join("\n".join(d["units"]) for d in documents)

    # SILVER (único caminho — Fase 5 matou o regex fallback)
    print("\n[silver] rodando structurer (LLM por página)…")
    blocks = build_or_load_structured_doc(
        "finep", args.edital, documents, force=args.force
    )
    print(f"[silver] {len(blocks)} blocos")
    silver_chunks = chunk_from_blocks(blocks)
    _report("SILVER", silver_chunks, source_text)

    # GATE
    n = len(silver_chunks) or 1
    sec_pct = sum(1 for c in silver_chunks if c.get("section")) / n
    cov = _coverage(source_text, silver_chunks)
    print("\n── GATE ──")
    ok_sec = sec_pct >= 0.8
    ok_cov = cov >= 0.95
    print(f"  section ≥80%      : {'PASS' if ok_sec else 'FAIL'} ({sec_pct:.0%})")
    print(f"  cobertura ≥95%    : {'PASS' if ok_cov else 'FAIL'} ({cov:.0%})")
    return 0 if (ok_sec and ok_cov) else 1


if __name__ == "__main__":
    sys.exit(main())
