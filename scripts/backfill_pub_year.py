"""
Migração one-shot: adiciona `pub_year` às wiki pages existentes.

Deriva `pub_year` de `pub_date` via `wiki_schema.parse_pub_year` (fallback: unknown_label).
Idempotente — pages que já têm `pub_year` são puladas.

Uso:
    python scripts/backfill_pub_year.py
"""
import json
from pathlib import Path

from config import KG_WIKI_DIR
from core import wiki_schema


def main() -> None:
    updated = skipped = 0
    for f in sorted(KG_WIKI_DIR.glob("*.json")):
        if f.name.startswith("."):
            continue
        page = json.loads(f.read_text(encoding="utf-8"))
        if "pub_year" in page:
            skipped += 1
            continue
        page["pub_year"] = wiki_schema.parse_pub_year(page.get("pub_date"))
        f.write_text(json.dumps(page, indent=2, ensure_ascii=False), encoding="utf-8")
        updated += 1
    print(f"Backfill pub_year: {updated} atualizadas, {skipped} já tinham")


if __name__ == "__main__":
    main()
