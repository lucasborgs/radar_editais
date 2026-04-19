#!/usr/bin/env python3
"""
Pipeline completo FINEP.

Etapas:
  1. Scraper FINEP (web + PDFs)
  2. Knowledge Graph (metadata → index.json)
  3. ETL Process (PDFs + metadata → card + section index, uma chamada LLM por edital)
  4. Health check

Uso:
    python scripts/run_finep_pipeline.py
    python scripts/run_finep_pipeline.py --skip-scraper
    python scripts/run_finep_pipeline.py --skip-process
    python scripts/run_finep_pipeline.py --edital 782 790
    python scripts/run_finep_pipeline.py --backend openai
    python scripts/run_finep_pipeline.py --skip-cache
"""
import argparse
import sys
from datetime import datetime


def main():
    parser = argparse.ArgumentParser(description="Pipeline FINEP completo")
    parser.add_argument("--skip-scraper",  action="store_true", help="Pula scraping")
    parser.add_argument("--skip-process",  action="store_true", help="Pula etapa LLM (cards + sections)")
    parser.add_argument("--edital", nargs="+", help="IDs de editais específicos")
    parser.add_argument("--backend", default="gemini", choices=["gemini", "openai"])
    parser.add_argument("--skip-cache", action="store_true", help="Reprocessa mesmo com cache válido")
    parser.add_argument("--delay", type=float, default=1.5, help="Delay entre chamadas LLM (s)")
    args = parser.parse_args()

    start = datetime.now()
    print("=" * 70)
    print(f"PIPELINE FINEP — Início: {start.strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)

    # Etapa 1: Scraper
    if not args.skip_scraper:
        print("\n>>> ETAPA 1: Scraper FINEP")
        print("-" * 40)
        try:
            from pipeline.extractors.finep import FINEPScraper
            results = FINEPScraper().run()
            print(f"  → {len(results)} chamadas extraídas")
        except Exception as e:
            print(f"  ERRO no scraper: {e}")
    else:
        print("\n>>> ETAPA 1: Scraper — PULADO")

    # Etapa 2: Knowledge Graph
    print("\n>>> ETAPA 2: Knowledge Graph")
    print("-" * 40)
    try:
        from pipeline.build_knowledge_graph import main as build_graph
        build_graph()
    except Exception as e:
        print(f"  ERRO no grafo: {e}")

    # Etapa 3: ETL Process (card + section index)
    if not args.skip_process:
        print("\n>>> ETAPA 3: ETL Process (cards + sections)")
        print("-" * 40)
        try:
            from pipeline.etl_process import main as etl_process
            etl_process(
                backend=args.backend,
                edital_ids=args.edital,
                skip_cache=args.skip_cache,
                delay=args.delay,
            )
        except Exception as e:
            print(f"  ERRO no ETL Process: {e}")
    else:
        print("\n>>> ETAPA 3: ETL Process — PULADO")

    # Etapa 4: Health check
    print("\n>>> ETAPA 4: Health Check")
    print("-" * 40)
    try:
        from pipeline.health_check import run_health_check
        run_health_check(edital_ids=args.edital)
    except Exception as e:
        print(f"  ERRO no health check: {e}")

    elapsed = datetime.now() - start
    print(f"\n{'=' * 70}")
    print(f"PIPELINE FINEP CONCLUÍDO — Duração: {elapsed}")
    print(f"{'=' * 70}")


if __name__ == "__main__":
    sys.exit(main() or 0)
