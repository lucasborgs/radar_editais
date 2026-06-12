"""
Health Check — Knowledge Base FINEP.

3 checks por edital:
  1. wiki_page_quality — wiki page existe e tem campos essenciais preenchidos
  2. staleness         — wiki page gerada há mais de 30 dias (pode estar desatualizada)
  3. new_pdfs          — PDFs em disco não refletidos no cache (retificação pendente)

1 check por fonte (roda também no cron run_daily_etl — fonte quebrada não
fica acumulando silêncio):
  4. sources_freshness — bronze mais recente da fonte mais velho que N dias

Uso:
    python pipeline/health_check.py
    python pipeline/health_check.py --edital 782
    python pipeline/health_check.py --check wiki_page_quality new_pdfs
    python pipeline/health_check.py --dry-run
"""
from __future__ import annotations

import argparse
import json
import logging
from datetime import datetime

from config import FINEP_PDFS_DIR, KNOWLEDGE_GRAPH_DIR
from core.kg.edital_id import native_id_of, wiki_page_path

logger = logging.getLogger(__name__)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%H:%M:%S",
)

INDEX_FILE = KNOWLEDGE_GRAPH_DIR / "index.json"
LOG_FILE   = KNOWLEDGE_GRAPH_DIR / "health_check_log.jsonl"

STALENESS_DAYS = 30

# Fonte parada: o cron de ETL roda diário, então bronze sem arquivo novo há
# mais que isto indica scraper quebrado/cron morto. 3 dias tolera um incidente
# de fim de semana sem alertar à toa.
SOURCE_FRESHNESS_MAX_DAYS = 3

REQUIRED_WIKI_PAGE_FIELDS = ["objective", "mechanism", "eligible_entities", "eligible_sectors"]

ALL_CHECKS = ["wiki_page_quality", "staleness", "new_pdfs"]


# =============================================================================
# CHECKS
# =============================================================================

def check_wiki_page_quality(edital_id: str) -> dict:
    """Verifica se a wiki page existe e tem campos essenciais preenchidos."""
    wiki_file = wiki_page_path(edital_id)

    if not wiki_file.exists():
        return {"check": "wiki_page_quality", "status": "MISSING",
                "issues": ["wiki page não encontrada — executar etl_process"]}

    try:
        wiki_page = json.loads(wiki_file.read_text(encoding="utf-8"))
    except Exception as e:
        return {"check": "wiki_page_quality", "status": "ERROR", "issues": [str(e)]}

    issues = []

    if wiki_page.get("source") == "metadata_only":
        issues.append("wiki page gerada apenas de metadados HTML (sem PDFs)")

    null_fields = [f for f in REQUIRED_WIKI_PAGE_FIELDS if not wiki_page.get(f)]
    if null_fields:
        issues.append(f"campos nulos: {', '.join(null_fields)}")

    if wiki_page.get("source") != "metadata_only":
        if not wiki_page.get("key_requirements"):
            issues.append("key_requirements vazio")
        if not wiki_page.get("key_facts"):
            issues.append("key_facts vazio")

    status = "OK" if not issues else (
        "WARN" if wiki_page.get("source") == "metadata_only" else "ISSUE"
    )
    return {"check": "wiki_page_quality", "status": status, "issues": issues}


def check_staleness(edital_id: str) -> dict:
    """Verifica se a wiki page foi gerada há mais de STALENESS_DAYS dias."""
    wiki_file = wiki_page_path(edital_id)
    if not wiki_file.exists():
        return {"check": "staleness", "status": "NO_WIKI_PAGE"}

    try:
        wiki_page = json.loads(wiki_file.read_text(encoding="utf-8"))
        generated_at = wiki_page.get("generated_at", "")
        if not generated_at:
            return {"check": "staleness", "status": "NO_DATE"}

        generated_date = datetime.strptime(generated_at, "%Y-%m-%d").date()
        age_days = (datetime.now().date() - generated_date).days

        status = "OK" if age_days <= STALENESS_DAYS else "STALE"
        return {"check": "staleness", "status": status,
                "age_days": age_days, "generated_at": generated_at}
    except Exception as e:
        return {"check": "staleness", "status": "ERROR", "issues": [str(e)]}


def check_new_pdfs(edital_id: str) -> dict:
    """
    Verifica se há PDFs em disco não refletidos no cache do etl_process.
    Indica retificação ou aditivo publicado que ainda não foi processado.
    """
    # PDFs ficam em FINEP_PDFS_DIR/{native_id}/ — parametrizar por fonte é Épico C
    pdf_dir = FINEP_PDFS_DIR / native_id_of(edital_id)
    if not pdf_dir.exists():
        return {"check": "new_pdfs", "status": "NO_PDF_DIR"}

    pdfs_on_disk = sorted(p.name for p in pdf_dir.glob("*.pdf"))

    try:
        from core.kg import kg_store
        cache = kg_store.load("etl_process_cache", default={})
    except Exception:
        return {"check": "new_pdfs", "status": "CACHE_ERROR"}
    if not cache:
        return {"check": "new_pdfs", "status": "NO_CACHE", "pdfs_on_disk": pdfs_on_disk}

    if edital_id not in cache:
        return {"check": "new_pdfs", "status": "NOT_PROCESSED", "pdfs_on_disk": pdfs_on_disk}
    # cache é keyed pelo edital_id prefixado pós-migration; nada a fazer aqui

    wiki_file = wiki_page_path(edital_id)
    if wiki_file.exists():
        wiki_page = json.loads(wiki_file.read_text(encoding="utf-8"))
        if wiki_page.get("source") == "metadata_only" and pdfs_on_disk:
            return {
                "check": "new_pdfs",
                "status": "PDFS_NOT_USED",
                "pdfs_on_disk": pdfs_on_disk,
                "note": "wiki page gerada sem PDFs mas há PDFs em disco — re-executar etl_process",
            }

    return {"check": "new_pdfs", "status": "OK", "pdfs_on_disk": pdfs_on_disk}


# =============================================================================
# CHECK POR FONTE
# =============================================================================

def check_sources_freshness(
    max_age_days: float = SOURCE_FRESHNESS_MAX_DAYS,
    registry: dict | None = None,
) -> list[dict]:
    """Fonte parada: arquivo mais recente do bronze da fonte com idade > N dias.

    Sinal = mtime do arquivo mais novo em BRONZE_DIR/<bronze_dir> de cada fonte
    do SCRAPER_REGISTRY. Bronze é gitignored, então mtime reflete escrita real
    do scraper (não checkout/deploy). Statuses:
      • OK        — bronze fresco
      • STALE     — fonte produzia e parou (scraper quebrado / cron morto) → alertar
      • NO_BRONZE — fonte nunca produziu nesta máquina (ex.: web inerte) — estado
                    de configuração, não regressão; NÃO alerta.

    Puro leitura de filesystem — sem rede, sem DB. `registry` injetável p/ teste.
    """
    import time as _time

    from config import BRONZE_DIR
    if registry is None:
        from pipeline.extractors import SCRAPER_REGISTRY
        registry = SCRAPER_REGISTRY

    now = _time.time()
    results: list[dict] = []
    for source_key, cfg in registry.items():
        bronze = BRONZE_DIR / cfg["bronze_dir"]
        files = [p for p in bronze.glob("*") if p.is_file()] if bronze.exists() else []
        if not files:
            results.append({"check": "sources_freshness", "source": source_key,
                            "status": "NO_BRONZE", "age_days": None})
            continue
        age_days = (now - max(p.stat().st_mtime for p in files)) / 86400.0
        results.append({
            "check": "sources_freshness",
            "source": source_key,
            "status": "OK" if age_days <= max_age_days else "STALE",
            "age_days": round(age_days, 1),
        })
    return results


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

    # Check por fonte (barato, sempre roda): fonte parada aparece antes do
    # detalhe por edital.
    print("\nFONTES:")
    for r in check_sources_freshness():
        age = f"{r['age_days']}d" if r["age_days"] is not None else "—"
        print(f"  [{r['source']}] {r['status']} (bronze mais recente: {age})")
    print()

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

    CHECK_FN = {  # noqa: N806
        "wiki_page_quality": check_wiki_page_quality,
        "staleness":         check_staleness,
        "new_pdfs":          check_new_pdfs,
    }

    all_results = []

    for edital_id in target_ids:
        edital_result = {
            "edital_id":  edital_id,
            "checked_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "results":    {},
            "issues":     [],
        }

        for check in checks:
            if check in CHECK_FN:
                r = CHECK_FN[check](edital_id)
                edital_result["results"][check] = r
                if r["status"] not in ("OK", "NO_PDF_DIR"):
                    edital_result["issues"].append(f"{check}: {r['status']}")

        all_results.append(edital_result)

        KNOWLEDGE_GRAPH_DIR.mkdir(parents=True, exist_ok=True)
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(edital_result, ensure_ascii=False) + "\n")

        status_str = "OK" if not edital_result["issues"] else "  |  ".join(edital_result["issues"])
        print(f"  [{edital_id}] {status_str}")
        for check, r in edital_result["results"].items():
            if r.get("status") not in ("OK", "NO_PDF_DIR"):
                detail = (r.get("issues") or r.get("note") or
                          r.get("pdfs_on_disk") or "")
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
