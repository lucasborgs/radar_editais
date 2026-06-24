"""
One-shot: prefixa IDs existentes com `{source}:` e move wiki pages para
subfolder por fonte, conforme Épico B do plano multi-fonte (Fase 1).

Pré-Fase 1 estado:
  - knowledge_graph/wiki/{id}.json                (flat, fonte única)
  - knowledge_graph/index.json                    entries com id="782"
  - knowledge_graph/index_historico.json          idem
  - DB: writing_sessions/edital_chunks/... com edital_id="782"

Pós-migração:
  - knowledge_graph/wiki/finep/{id}.json          (subfolder por fonte)
  - index*.json com id="finep:782" + campo source="finep"
  - DB: edital_id="finep:782" (via supabase/migrations/012_*.sql)

Este script cobre o filesystem; o SQL roda separado via `supabase db push`.

Idempotente: re-rodar não cria `finep:finep:782` (filtra por presença de `:`).

Uso:
    python scripts/migrate_existing_ids.py --dry-run     # mostra o que faria
    python scripts/migrate_existing_ids.py               # aplica
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from config import KG_WIKI_DIR, KNOWLEDGE_GRAPH_DIR  # noqa: E402

DEFAULT_SOURCE = "finep"


def _prefix(native_id: str, source: str) -> str:
    """Aplica prefixo se ainda não estiver presente."""
    return native_id if ":" in native_id else f"{source}:{native_id}"


def migrate_wiki_files(source: str, dry_run: bool) -> dict:
    """Move knowledge_graph/wiki/*.json (flat) → knowledge_graph/wiki/{source}/.

    Preserva dotfiles (cache do etl_process). Idempotente: arquivo já no
    subfolder de destino é skipado.
    """
    target_dir = KG_WIKI_DIR / source
    moved = skipped = 0

    if not KG_WIKI_DIR.exists():
        return {"moved": 0, "skipped": 0, "note": "KG_WIKI_DIR ausente"}

    candidates = [p for p in KG_WIKI_DIR.glob("*.json") if not p.name.startswith(".")]

    if not dry_run:
        target_dir.mkdir(parents=True, exist_ok=True)

    for src in candidates:
        # Skip já-migrados (raro, mas idempotência)
        if src.parent.name == source:
            skipped += 1
            continue
        dst = target_dir / src.name
        if dst.exists():
            print(f"  skip (destino já existe): {dst.relative_to(ROOT)}")
            skipped += 1
            continue
        if dry_run:
            print(f"  would move: {src.relative_to(ROOT)} → {dst.relative_to(ROOT)}")
        else:
            src.rename(dst)
            print(f"  moved: {src.relative_to(ROOT)} → {dst.relative_to(ROOT)}")
        moved += 1

    return {"moved": moved, "skipped": skipped}


def migrate_index(file_name: str, source: str, dry_run: bool) -> dict:
    """Prefixa entries[].id com `{source}:` e adiciona campo source."""
    path = KNOWLEDGE_GRAPH_DIR / file_name
    if not path.exists():
        return {"updated": 0, "skipped": 0, "note": f"{file_name} ausente"}

    payload = json.loads(path.read_text(encoding="utf-8"))
    entries = payload.get("editais", [])
    updated = skipped = 0

    for entry in entries:
        eid = entry.get("id")
        if not isinstance(eid, (str, int)):
            skipped += 1
            continue
        eid_str = str(eid)
        if ":" in eid_str:
            skipped += 1
            continue
        entry["id"] = _prefix(eid_str, source)
        entry.setdefault("source", source)
        updated += 1

    if dry_run:
        print(f"  would update {updated}/{len(entries)} entries in {file_name}")
    else:
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"  updated {updated}/{len(entries)} entries in {file_name}")

    return {"updated": updated, "skipped": skipped}


def migrate_etl_cache(source: str, dry_run: bool) -> dict:
    """Prefixa keys do cache do etl_process (mesma lógica, dotfile separado)."""
    cache_path = KG_WIKI_DIR / ".etl_process_cache.json"
    if not cache_path.exists():
        return {"updated": 0, "note": "cache ausente"}
    cache = json.loads(cache_path.read_text(encoding="utf-8"))
    new_cache = {}
    updated = skipped = 0
    for k, v in cache.items():
        if ":" in k:
            new_cache[k] = v
            skipped += 1
        else:
            new_cache[_prefix(k, source)] = v
            updated += 1
    if dry_run:
        print(f"  would re-key {updated}/{len(cache)} entries in .etl_process_cache.json")
    else:
        cache_path.write_text(json.dumps(new_cache, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"  re-keyed {updated}/{len(cache)} entries in .etl_process_cache.json")
    return {"updated": updated, "skipped": skipped}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default=DEFAULT_SOURCE,
                        help=f"prefixo de fonte a aplicar (default: {DEFAULT_SOURCE})")
    parser.add_argument("--dry-run", action="store_true",
                        help="mostra o que faria sem aplicar")
    args = parser.parse_args()

    mode = "DRY RUN" if args.dry_run else "APPLY"
    print(f"=== migrate_existing_ids — source={args.source} — {mode} ===")

    print("\n[1/4] Wiki pages (flat → subfolder por fonte):")
    r1 = migrate_wiki_files(args.source, args.dry_run)
    print(f"  → {r1}")

    print("\n[2/4] index.json:")
    r2 = migrate_index("index.json", args.source, args.dry_run)
    print(f"  → {r2}")

    print("\n[3/4] index_historico.json:")
    r3 = migrate_index("index_historico.json", args.source, args.dry_run)
    print(f"  → {r3}")

    print("\n[4/4] .etl_process_cache.json:")
    r4 = migrate_etl_cache(args.source, args.dry_run)
    print(f"  → {r4}")

    print("\n=== Próximo passo: rodar SQL migration 012_prefix_source_edital_id.sql ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
