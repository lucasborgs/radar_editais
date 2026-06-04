#!/usr/bin/env python3
"""Bootstrap do golden de extração (Fase 2.3).

Pega N editais de cada fonte (input cru via core.edital_extractor), roda o
extrator contra o schema `EditalExtraction` e grava um **rascunho** de golden
para o humano CORRIGIR. O golden corrigido (não este rascunho) é a verdade que
a suíte `extraction` mede.

Uso:
    python scripts/bootstrap_extraction_golden.py --dry-run      # só junta inputs
    python scripts/bootstrap_extraction_golden.py --n 5          # roda extração (custa API)
    python scripts/bootstrap_extraction_golden.py --source web --n 3
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from core.edital_extractor import GATHERERS, extract_edital, gather_source  # noqa: E402
from domain.edital_extraction import DECISION_FIELDS  # noqa: E402

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("bootstrap_golden")

OUT = ROOT / "eval_data" / "golden" / "extraction_draft.json"


def _abstention_summary(extractions: list[dict]) -> dict:
    counts: dict[str, dict] = {f: {"stated": 0, "inferred": 0, "absent": 0} for f in DECISION_FIELDS}
    for e in extractions:
        for f in DECISION_FIELDS:
            st = (e.get(f) or {}).get("state", "absent")
            counts[f][st] = counts[f].get(st, 0) + 1
    return counts


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--n", type=int, default=5, help="editais por fonte")
    p.add_argument("--source", choices=list(GATHERERS), help="só uma fonte")
    p.add_argument("--dry-run", action="store_true", help="só junta inputs, sem LLM")
    args = p.parse_args()

    # Default FINEP+FAPESP (web adiada — ver BACKLOG). Web é opt-in via --source web.
    sources = [args.source] if args.source else ["finep", "fapesp"]
    items: list[dict] = []
    for s in sources:
        got = gather_source(s, args.n)
        print(f"  [{s}] {len(got)} inputs", file=sys.stderr)
        items.extend(got)

    if args.dry_run:
        print(f"\nDRY-RUN: {len(items)} inputs. Amostra:")
        for it in items:
            print(f"  [{it['source']}] {it['native_id'][:30]:30s} {it['title'][:50]:50s} "
                  f"raw={len(it['raw'])} chars")
        return 0

    golden: list[dict] = []
    for it in items:
        print(f"→ extraindo [{it['source']}] {it['native_id'][:40]}...", file=sys.stderr)
        try:
            extraction = extract_edital(it["source"], it["native_id"], it["raw"]).model_dump()
        except Exception as e:
            logger.error("extração falhou (%s/%s): %s", it["source"], it["native_id"], e)
            continue
        golden.append({
            "source": it["source"], "native_id": it["native_id"], "title": it["title"],
            "input_excerpt": it["raw"][:400], "extraction": extraction,
        })

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(golden, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nRascunho do golden: {OUT}  ({len(golden)} editais)")
    print("\nAbstenção por campo DECISÃO (stated/inferred/absent):")
    for f, c in _abstention_summary([g["extraction"] for g in golden]).items():
        print(f"  {f:20s} {c}")
    print("\n>>> REVISE e corrija eval_data/golden/extraction_draft.json antes de usar como verdade.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
