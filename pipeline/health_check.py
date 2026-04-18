"""
Health Check — Self-improving loop para o Knowledge Base FINEP.

5 checks por edital:
  1. consistency  — prazo/status no grafo == nos fatos?
  2. coverage     — quais section_types do Template A faltam?
  3. staleness    — fatos com > 30 dias sem re-verificação
  4. provisional  — fatos de writing_feedback que podem ser confirmados (com LLM)
  5. rerratificacao — novos PDFs na página de detalhe não processados

Uso:
    python pipeline/health_check.py                        # todos editais ABERTA
    python pipeline/health_check.py --edital 782           # edital específico
    python pipeline/health_check.py --check consistency    # check específico
    python pipeline/health_check.py --dry-run              # mostra sem corrigir
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import getpass
import re
from datetime import datetime, timedelta
from pathlib import Path

from config import KNOWLEDGE_GRAPH_DIR, FINEP_PDFS_DIR, FINEP_FACTS_DIR, KG_CARDS_DIR

# =============================================================================
# CONFIGURAÇÃO
# =============================================================================

FACTS_DIR = FINEP_FACTS_DIR
INDEX_FILE = KNOWLEDGE_GRAPH_DIR / "index.json"
LOG_FILE = KNOWLEDGE_GRAPH_DIR / "health_check_log.jsonl"

STALENESS_DAYS = 30

# Seções esperadas em um edital completo (Template A)
EXPECTED_SECTIONS = {
    "OBJETIVO", "RECURSOS", "ELEGIBILIDADE", "CRONOGRAMA",
    "AVALIACAO", "DISPOSICOES_GERAIS",
}

ALL_CHECKS = ["consistency", "coverage", "staleness", "provisional", "rerratificacao"]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


# =============================================================================
# CARREGAMENTO DE DADOS
# =============================================================================

def load_index() -> dict:
    if not INDEX_FILE.exists():
        return {}
    return json.loads(INDEX_FILE.read_text(encoding="utf-8"))


def load_facts(edital_id: str) -> dict:
    facts_file = FACTS_DIR / f"{edital_id}.json"
    if not facts_file.exists():
        return {"edital_id": edital_id, "facts": []}
    return json.loads(facts_file.read_text(encoding="utf-8"))


def save_facts(edital_id: str, data: dict) -> None:
    FACTS_DIR.mkdir(parents=True, exist_ok=True)
    path = FACTS_DIR / f"{edital_id}.json"
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def append_log(entry: dict) -> None:
    KNOWLEDGE_GRAPH_DIR.mkdir(parents=True, exist_ok=True)
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


# =============================================================================
# CHECK 1: CONSISTENCY (sem LLM)
# =============================================================================

def check_consistency(edital_id: str, index_entry: dict, facts: list[dict]) -> dict:
    """Verifica inconsistências reais entre metadados do scraper e fatos dos PDFs.

    O prazo de envio (deadline) vem diretamente do HTML estruturado do FINEP e é
    autoritativo — não comparar contra texto livre dos PDFs, que contém muitos
    outros tipos de prazo (execução, esclarecimentos, cadastro, etc.).

    Verifica apenas: se o edital tem fatos mas nenhum menciona o número do edital
    ou a chamada, sugerindo que o PDF processado pode ser de outro edital.
    """
    issues = []

    # Sem fatos → nada a verificar
    if not facts:
        return {"check": "consistency", "status": "OK", "issues": []}

    # Verifica se pelo menos um fato menciona a chamada/edital (sanidade básica)
    edital_id_str = str(edital_id)
    chamada_refs = [
        f for f in facts
        if edital_id_str in f.get("text", "") or "chamada" in f.get("text", "").lower()
        or "finep" in f.get("text", "").lower()
    ]
    if not chamada_refs and len(facts) > 5:
        issues.append(
            f"Nenhum fato menciona FINEP ou 'chamada' — verificar se PDF é do edital correto"
        )

    status = "OK" if not issues else "INCONSISTENCY"
    return {"check": "consistency", "status": status, "issues": issues}


# =============================================================================
# CHECK 2: COVERAGE (sem LLM)
# =============================================================================

def check_coverage(edital_id: str, facts: list[dict]) -> dict:
    """Verifica quais section_types do Template A faltam."""
    covered = {f.get("section_type") for f in facts if f.get("status") != "superseded"}
    missing = EXPECTED_SECTIONS - covered

    status = "OK" if not missing else "GAP"
    return {
        "check": "coverage",
        "status": status,
        "covered": sorted(covered),
        "missing": sorted(missing),
    }


# =============================================================================
# CHECK 3: STALENESS (sem LLM)
# =============================================================================

def check_staleness(edital_id: str, facts: list[dict]) -> dict:
    """Identifica fatos com extracted_at > STALENESS_DAYS dias."""
    today = datetime.now().date()
    stale = []

    for fact in facts:
        if fact.get("status") == "superseded":
            continue
        extracted_at = fact.get("extracted_at", "")
        if not extracted_at:
            continue
        try:
            fact_date = datetime.strptime(extracted_at, "%Y-%m-%d").date()
            age_days = (today - fact_date).days
            if age_days > STALENESS_DAYS:
                stale.append({
                    "text": fact["text"][:80],
                    "section_type": fact.get("section_type", ""),
                    "age_days": age_days,
                })
        except ValueError:
            pass

    status = "OK" if not stale else "STALE"
    return {"check": "staleness", "status": status, "stale_count": len(stale), "stale": stale[:10]}


# =============================================================================
# CHECK 4: PROVISIONAL (com LLM — opcional)
# =============================================================================

def check_provisional(
    edital_id: str, facts_data: dict, client=None, model: str = "",
    dry_run: bool = False,
) -> dict:
    """Identifica fatos provisórios de writing_feedback que precisam validação."""
    facts = facts_data.get("facts", [])
    provisional = [f for f in facts if f.get("status") == "provisional"]

    if not provisional:
        return {"check": "provisional", "status": "OK", "count": 0}

    if dry_run or not client:
        return {
            "check": "provisional",
            "status": "PENDING",
            "count": len(provisional),
            "facts": [f["text"][:80] for f in provisional],
        }

    # Com LLM: tenta confirmar cada fato contra o texto do PDF
    pdf_dir = FINEP_PDFS_DIR / edital_id
    if not pdf_dir.exists():
        return {"check": "provisional", "status": "NO_PDF", "count": len(provisional)}

    # Carrega texto do PDF principal
    from pipeline.etl_finep_facts import pick_main_pdf, extract_pdf_text
    main_pdf = pick_main_pdf(pdf_dir)
    if not main_pdf:
        return {"check": "provisional", "status": "NO_PDF", "count": len(provisional)}

    pdf_text = extract_pdf_text(main_pdf)
    if not pdf_text:
        return {"check": "provisional", "status": "NO_TEXT", "count": len(provisional)}

    confirmed = 0
    rejected = 0

    for fact in provisional:
        prompt = f"""Fato a verificar: "{fact['text']}"

Texto do edital (trecho relevante):
{pdf_text[:8000]}

O fato acima está correto segundo o texto do edital?
Responda APENAS: CONFIRMED ou REJECTED"""

        try:
            response = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,
                max_tokens=50,
            )
            answer = response.choices[0].message.content.strip().upper()

            if "CONFIRMED" in answer:
                fact["status"] = "confirmed"
                confirmed += 1
            elif "REJECTED" in answer:
                fact["status"] = "rejected"
                rejected += 1
        except Exception as e:
            logger.warning("Erro LLM ao validar fato: %s", e)

    # Salva fatos atualizados
    save_facts(edital_id, facts_data)

    return {
        "check": "provisional",
        "status": "VALIDATED",
        "confirmed": confirmed,
        "rejected": rejected,
        "remaining": len(provisional) - confirmed - rejected,
    }


# =============================================================================
# CHECK 5: RERRATIFICAÇÃO (sem LLM)
# =============================================================================

def check_rerratificacao(edital_id: str, facts: list[dict]) -> dict:
    """Verifica se há PDFs em disco não processados nos fatos."""
    pdf_dir = FINEP_PDFS_DIR / edital_id
    if not pdf_dir.exists():
        return {"check": "rerratificacao", "status": "NO_PDF_DIR"}

    pdfs_on_disk = {p.name for p in pdf_dir.glob("*.pdf")}
    pdfs_in_facts = {f.get("source_pdf", "") for f in facts if f.get("source_pdf")}

    new_pdfs = pdfs_on_disk - pdfs_in_facts
    # Filter out empty strings
    new_pdfs.discard("")

    status = "OK" if not new_pdfs else "NEW_PDFS"
    return {
        "check": "rerratificacao",
        "status": status,
        "new_pdfs": sorted(new_pdfs),
        "processed_pdfs": sorted(pdfs_in_facts),
    }


# =============================================================================
# ORQUESTRADOR
# =============================================================================

def run_health_check(
    edital_ids: list[str] | None = None,
    checks: list[str] | None = None,
    backend: str | None = None,
    dry_run: bool = False,
) -> list[dict]:
    """Executa health checks para editais FINEP.

    Args:
        edital_ids: IDs específicos. None = todos os ABERTA no index.
        checks: Checks específicos. None = todos.
        backend: LLM backend para checks que precisam (ex: "gemini").
        dry_run: Se True, mostra problemas sem corrigir.

    Returns:
        Lista de resultados por edital.
    """
    print("=" * 60)
    print("HEALTH CHECK — Knowledge Base FINEP")
    print("=" * 60)

    checks = checks or ALL_CHECKS

    # Determina editais a verificar
    if edital_ids:
        target_ids = edital_ids
    else:
        index = load_index()
        target_ids = [
            e["id"] for e in index.get("editais", [])
            if e.get("status") in ("ABERTA", "Desconhecido")
        ]

    if not target_ids:
        print("Nenhum edital para verificar.")
        return []

    print(f"Editais: {len(target_ids)} | Checks: {', '.join(checks)}")
    if dry_run:
        print("MODO DRY-RUN — apenas diagnóstico, sem correções\n")

    # Prepara LLM client se necessário
    client, model = None, ""
    if "provisional" in checks and backend and not dry_run:
        from openai import OpenAI
        if backend == "gemini":
            api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or \
                      getpass.getpass("Gemini API Key: ")
            client = OpenAI(
                api_key=api_key,
                base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
            )
            model = "gemini-2.5-flash"

    # Carrega index para check_consistency
    index = load_index()
    index_by_id = {e["id"]: e for e in index.get("editais", [])}

    all_results = []

    for edital_id in target_ids:
        logger.info("Verificando edital %s...", edital_id)
        facts_data = load_facts(edital_id)
        facts = facts_data.get("facts", [])
        index_entry = index_by_id.get(edital_id, {})

        edital_result = {
            "edital_id": edital_id,
            "checked_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "n_facts": len(facts),
            "results": {},
            "actions_taken": [],
        }

        if "consistency" in checks:
            r = check_consistency(edital_id, index_entry, facts)
            edital_result["results"]["consistency"] = r
            if r["status"] != "OK":
                for issue in r.get("issues", []):
                    edital_result["actions_taken"].append(f"Inconsistência: {issue}")

        if "coverage" in checks:
            r = check_coverage(edital_id, facts)
            edital_result["results"]["coverage"] = r
            if r["status"] == "GAP":
                edital_result["actions_taken"].append(
                    f"Gaps de cobertura: {', '.join(r['missing'])}"
                )

        if "staleness" in checks:
            r = check_staleness(edital_id, facts)
            edital_result["results"]["staleness"] = r
            if r["status"] == "STALE":
                edital_result["actions_taken"].append(
                    f"{r['stale_count']} fatos com mais de {STALENESS_DAYS} dias"
                )

        if "provisional" in checks:
            r = check_provisional(edital_id, facts_data, client, model, dry_run)
            edital_result["results"]["provisional"] = r
            if r.get("confirmed") or r.get("rejected"):
                edital_result["actions_taken"].append(
                    f"Validação: {r.get('confirmed',0)} confirmados, {r.get('rejected',0)} rejeitados"
                )

        if "rerratificacao" in checks:
            r = check_rerratificacao(edital_id, facts)
            edital_result["results"]["rerratificacao"] = r
            if r["status"] == "NEW_PDFS":
                edital_result["actions_taken"].append(
                    f"PDFs não processados: {', '.join(r['new_pdfs'])}"
                )

        all_results.append(edital_result)

        # Log
        append_log(edital_result)

        # Imprime resumo
        statuses = {k: v["status"] for k, v in edital_result["results"].items()}
        issues = [k for k, v in statuses.items() if v not in ("OK", "NO_PDF_DIR", "NO_PDF")]
        status_str = "OK" if not issues else f"ISSUES: {', '.join(issues)}"
        print(f"  [{edital_id}] {status_str} ({len(facts)} fatos)")
        for action in edital_result["actions_taken"]:
            print(f"    → {action}")

    # Resumo final
    print(f"\n{'=' * 60}")
    total_ok = sum(1 for r in all_results if not r["actions_taken"])
    total_issues = len(all_results) - total_ok
    print(f"RESUMO: {len(all_results)} editais verificados | {total_ok} OK | {total_issues} com issues")
    print(f"Log: {LOG_FILE}")
    print(f"{'=' * 60}")

    return all_results


# =============================================================================
# CLI
# =============================================================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Health Check — Knowledge Base FINEP")
    parser.add_argument("--edital", nargs="+", help="IDs de editais específicos")
    parser.add_argument("--check", nargs="+", choices=ALL_CHECKS, help="Checks específicos")
    parser.add_argument("--backend", choices=["gemini", "openai"], help="LLM para check provisional")
    parser.add_argument("--dry-run", action="store_true", help="Apenas diagnóstico")
    args = parser.parse_args()

    run_health_check(
        edital_ids=args.edital,
        checks=args.check,
        backend=args.backend,
        dry_run=args.dry_run,
    )
