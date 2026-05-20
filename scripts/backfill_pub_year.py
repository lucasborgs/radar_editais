"""
Migração one-shot: normaliza `pub_date` (ISO→dd/mm/yyyy, contrato §4.1) e
(re)deriva `pub_year` nas wiki pages existentes.

Idempotente — `pub_date` já em dd/mm/yyyy passa direto; `pub_year` é sempre
recomputado de `pub_date` (cards antigos da migração Liferay tinham ISO →
pub_year "desconhecido" incorreto; recomputar conserta).

Uso:
    python scripts/backfill_pub_year.py
"""
import json

from config import KG_WIKI_DIR
from core import wiki_schema


def main() -> None:
    updated = unchanged = 0
    for f in sorted(KG_WIKI_DIR.glob("*.json")):
        if f.name.startswith("."):
            continue
        page = json.loads(f.read_text(encoding="utf-8"))
        before = (page.get("pub_date"), page.get("pub_year"))
        page["pub_date"] = wiki_schema.iso_to_br_date(page.get("pub_date"))
        page["pub_year"] = wiki_schema.parse_pub_year(page["pub_date"])
        if (page["pub_date"], page["pub_year"]) != before:
            f.write_text(json.dumps(page, indent=2, ensure_ascii=False), encoding="utf-8")
            updated += 1
        else:
            unchanged += 1
    print(f"Backfill pub_date/pub_year: {updated} atualizadas, {unchanged} sem mudança")


if __name__ == "__main__":
    main()
