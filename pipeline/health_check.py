"""
Health Check — Knowledge Base FINEP.

4 checks por edital:
  1. card_quality    — card existe e tem campos essenciais preenchidos
  2. section_coverage — section index existe e cobre seções esperadas
  3. staleness        — card gerado há mais de 30 dias (pode estar desatualizado)
  4. new_pdfs         — PDFs em disco não refletidos no cache (retificação pendente)

Uso:
    python pipeline/health_check.py
    python pipeline/health_check.py --edital 782
    python pipeline/health_check.py --check card_quality section_coverage
    python pipeline/health_check.py --dry-run
"""
from __future__ import annotations

import json
import logging
import argparse
from datetime import datetime, timedelta
from pathlib import Path

from config import KNOWLEDGE_GRAPH_DIR, KG_CARDS_DIR, SECTION_INDEX_DIR, FINEP_PDFS_DIR

logger = logging.getLogger(__name__)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%H:%M:%S",
)

INDEX_FILE  = KNOWLEDGE_GRAPH_DIR / "index.json"
CACHE_FILE  = KG_CARDS_DIR / ".etl_process_cache.json"
LOG_FILE    = KNOWLEDGE_GRAPH_DIR / "health_check_log.jsonl"

STALENESS_DAYS = 30

# Seções esperadas no section index de um edital completo
EXPECTED_SECTION_KEYWORDS = [
    "objeto", "objetivo",
    "elegib", "participan",
    "recurso", "valor", "financ",
    "cronograma", "prazo",
]

# Campos do card que devem estar preenchidos num card de qualidade
REQUIRED_CARD_FIELDS = ["objective", "mechanism", "eligible_entities", "eligible_sectors"]

ALL_CHECKS = ["card_quality", "section_coverage", "staleness", "new_pdfs"]


# =============================================================================
# CHECKS
# =============================================================================

def check_card_quality(edital_id: str) -> dict:
    """Verifica se o card existe e tem campos essenciais não-nulos."""
    card_file = KG_CARDS_DIR / f"{edital_id}.json"

    if not card_file.exists():
        return {"check": "card_quality", "status": "MISSING", "issues": ["card.json não encontrado"]}

    try:
        card = json.loads(card_file.read_text(encoding="utf-8"))
    except Exception as e:
        return {"check": "card_quality", "status": "ERROR", "issues": [str(e)]}

    issues = []

    # Card mínimo (sem PDFs) — avisa mas não é crítico
    if card.get("source") == "metadata_only":
        issues.append("card gerado apenas de metadados HTML (sem PDFs)")

    # Campos essenciais nulos
    null_fields = [f for f in REQUIRED_CARD_FIELDS if not card.get(f)]
    if null_fields:
        issues.append(f"campos nulos: {', '.join(null_fields)}")

    # key_requirements e key_facts vazios num card com PDFs
    if card.get("source") != "metadata_only":
        if not card.get("key_requirements"):
            issues.append("key_requirements vazio")
        if not card.get("key_facts"):
            issues.append("key_facts vazio")

    status = "OK" if not issues else ("WARN" if card.get("source") == "metadata_only" else "ISSUE")
    return {"check": "card_quality", "status": status, "issues": issues}


def check_section_coverage(edital_id: str) -> dict:
    """Verifica se o section index existe e cobre seções essenciais."""
    index_file = SECTION_INDEX_DIR / f"{edital_id}.json"

    if not index_file.exists():
        return {"check": "section_coverage", "status": "MISSING", "missing": [], "found": []}

    try:
        payload = json.loads(index_file.read_text(encoding="utf-8"))
    except Exception as e:
        return {"check": "section_coverage", "status": "ERROR", "issues": [str(e)]}

    sections = payload.get("sections", [])
    titles_lower = [s["title"].lower() for s in sections]

    missing = [
        kw for kw in EXPECTED_SECTION_KEYWORDS
        if not any(kw in t for t in titles_lower)
    ]

    # Deduplicar: se "objeto" e "objetivo" ambos faltam, contar só uma vez
    missing_dedup = list({kw.split("|")[0] for kw in missing})

    status = "OK" if len(missing_dedup) <= 2 else "GAP"
    return {
        "check": "section_coverage",
        "status": status,
        "section_count": len(sections),
        "found": [s["title"] for s in sections],
        "missing_keywords": missing_dedup,
    }


def check_staleness(edital_id: str) -> dict:
    """Verifica se o card foi gerado há mais de STALENESS_DAYS dias."""
    card_file = KG_CARDS_DIR / f"{edital_id}.json"
    if not card_file.exists():
        return {"check": "staleness", "status": "NO_CARD"}

    try:
        card = json.loads(card_file.read_text(encoding="utf-8"))
        generated_at = card.get("generated_at", "")
        if not generated_at:
            return {"check": "staleness", "status": "NO_DATE"}

        generated_date = datetime.strptime(generated_at, "%Y-%m-%d").date()
        age_days = (datetime.now().date() - generated_date).days

        status = "OK" if age_days <= STALENESS_DAYS else "STALE"
        return {"check": "staleness", "status": status, "age_days": age_days, "generated_at": generated_at}
    except Exception as e:
        return {"check": "staleness", "status": "ERROR", "issues": [str(e)]}


def check_new_pdfs(edital_id: str) -> dict:
    """
    Verifica se há PDFs em disco não refletidos no cache do etl_process.
    Indica retificação ou aditivo publicado que ainda não foi processado.
    """
    pdf_dir = FINEP_PDFS_DIR / edital_id
    if not pdf_dir.exists():
        return {"check": "new_pdfs", "status": "NO_PDF_DIR"}

    pdfs_on_disk = sorted(p.name for p in pdf_dir.glob("*.pdf"))

    # Verifica se o cache existe e se o edital está nele
    if not CACHE_FILE.exists():
        return {"check": "new_pdfs", "status": "NO_CACHE", "pdfs_on_disk": pdfs_on_disk}

    try:
        cache = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"check": "new_pdfs", "status": "CACHE_ERROR"}

    if edital_id not in cache:
        return {
            "check": "new_pdfs",
            "status": "NOT_PROCESSED",
            "pdfs_on_disk": pdfs_on_disk,
        }

    # Se o cache existe mas o card tem source=metadata_only, pode ter PDFs novos
    card_file = KG_CARDS_DIR / f"{edital_id}.json"
    if card_file.exists():
        card = json.loads(card_file.read_text(encoding="utf-8"))
        if card.get("source") == "metadata_only" and pdfs_on_disk:
            return {
                "check": "new_pdfs",
                "status": "PDFS_NOT_USED",
                "pdfs_on_disk": pdfs_on_disk,
                "note": "card gerado sem PDFs mas há PDFs em disco — re-executar etl_process",
            }

    return {"check": "new_pdfs", "status": "OK", "pdfs_on_disk": pdfs_on_disk}


# =============================================================================
# ORQUESTRADOR
# =============================================================================

def run_health_check(
    edital_ids: list[str] | None = None,
    checks: list[str] | None = None,
    dry_run: bool = False,
) -> list[dict]:
    print("=" * 60)
    print("HEALTH CHECK — Knowledge Base FINEP")
    print("=" * 60)

    checks = checks or ALL_CHECKS

    if edital_ids:
        target_ids = edital_ids
    else:
        if not INDEX_FILE.exists():
            print("ERRO: index.json não encontrado.")
            return []
        index = json.loads(INDEX_FILE.read_text(encoding="utf-8"))
        target_ids = [
            e["id"] for e in index.get("editais", [])
            if e.get("status") in ("ABERTA", "Desconhecido")
        ]

    if not target_ids:
        print("Nenhum edital para verificar.")
        return []

    print(f"Editais: {len(target_ids)} | Checks: {', '.join(checks)}")
    if dry_run:
        print("MODO DRY-RUN\n")

    CHECK_FN = {
        "card_quality":     check_card_quality,
        "section_coverage": check_section_coverage,
        "staleness":        check_staleness,
        "new_pdfs":         check_new_pdfs,
    }

    all_results = []

    for edital_id in target_ids:
        edital_result = {
            "edital_id":   edital_id,
            "checked_at":  datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "results":     {},
            "issues":      [],
        }

        for check in checks:
            if check in CHECK_FN:
                r = CHECK_FN[check](edital_id)
                edital_result["results"][check] = r
                if r["status"] not in ("OK", "NO_PDF_DIR"):
                    edital_result["issues"].append(f"{check}: {r['status']}")

        all_results.append(edital_result)

        # Log em disco
        KNOWLEDGE_GRAPH_DIR.mkdir(parents=True, exist_ok=True)
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(edital_result, ensure_ascii=False) + "\n")

        # Saída no terminal
        status_str = "OK" if not edital_result["issues"] else f"{'  |  '.join(edital_result['issues'])}"
        print(f"  [{edital_id}] {status_str}")
        for check, r in edital_result["results"].items():
            if r.get("status") not in ("OK", "NO_PDF_DIR"):
                detail = r.get("issues") or r.get("missing_keywords") or r.get("note") or r.get("pdfs_on_disk") or ""
                if detail:
                    print(f"    → {check}: {detail}")

    print(f"\n{'=' * 60}")
    ok_count = sum(1 for r in all_results if not r["issues"])
    print(f"RESUMO: {len(all_results)} editais | {ok_count} OK | {len(all_results) - ok_count} com issues")
    print(f"Log: {LOG_FILE}")
    print(f"{'=' * 60}")

    return all_results


# =============================================================================
# CLI
# =============================================================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Health Check — Knowledge Base FINEP")
    parser.add_argument("--edital", nargs="+", help="IDs de editais específicos")
    parser.add_argument("--check", nargs="+", choices=ALL_CHECKS, dest="checks")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    run_health_check(
        edital_ids=args.edital,
        checks=args.checks,
        dry_run=args.dry_run,
    )
