#!/usr/bin/env python3
"""
Pipeline completo FINEP — Knowledge Graph + Fatos Atômicos + Cards.

Etapas:
  1. Scraper FINEP (web + PDFs)
  2. Knowledge Graph (metadata → graph.json + index.json)
  3. Fatos atômicos (PDFs → facts/{id}.json via LLM)
  4. Cards ricos (facts + metadata → cards/{id}.json via LLM)
  5. Health check

Uso:
    python scripts/run_finep_pipeline.py                    # pipeline completo
    python scripts/run_finep_pipeline.py --skip-scraper     # pula scraping
    python scripts/run_finep_pipeline.py --skip-facts       # pula extração LLM de fatos
    python scripts/run_finep_pipeline.py --skip-cards       # pula geração de cards
    python scripts/run_finep_pipeline.py --edital 782 790   # editais específicos
"""
import argparse
import sys
from datetime import datetime


def main():
    parser = argparse.ArgumentParser(description="Pipeline FINEP completo")
    parser.add_argument("--skip-scraper", action="store_true", help="Pula etapa de scraping")
    parser.add_argument("--skip-facts", action="store_true", help="Pula extração de fatos (LLM)")
    parser.add_argument("--skip-cards", action="store_true", help="Pula geração de cards (LLM)")
    parser.add_argument("--edital", nargs="+", help="IDs de editais específicos")
    parser.add_argument("--backend", default="gemini", choices=["gemini", "openai"])
    parser.add_argument("--dry-run", action="store_true", help="Dry-run nos fatos e health check")
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
            scraper = FINEPScraper()
            results = scraper.run()
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
        build_graph()  # gera index.json (vigentes) + index_historico.json
    except Exception as e:
        print(f"  ERRO no grafo: {e}")

    # Etapa 3: Fatos atômicos
    if not args.skip_facts:
        print("\n>>> ETAPA 3: Extração de Fatos Atômicos")
        print("-" * 40)
        try:
            from pipeline.etl_finep_facts import main as extract_facts
            extract_facts(
                backend=args.backend,
                edital_ids=args.edital,
                dry_run=args.dry_run,
            )
        except Exception as e:
            print(f"  ERRO na extração de fatos: {e}")
    else:
        print("\n>>> ETAPA 3: Fatos atômicos — PULADO")

    # Etapa 4: Cards ricos
    if not args.skip_cards:
        print("\n>>> ETAPA 4: Geração de Cards")
        print("-" * 40)
        try:
            from pipeline.etl_finep_cards import main as generate_cards
            generate_cards(
                backend=args.backend,
                edital_ids=args.edital,
            )
        except Exception as e:
            print(f"  ERRO na geração de cards: {e}")
    else:
        print("\n>>> ETAPA 4: Cards — PULADO")

    # Etapa 5: Health check
    print("\n>>> ETAPA 5: Health Check")
    print("-" * 40)
    try:
        from pipeline.health_check import run_health_check
        run_health_check(edital_ids=args.edital, dry_run=args.dry_run)
    except Exception as e:
        print(f"  ERRO no health check: {e}")

    elapsed = datetime.now() - start
    print(f"\n{'=' * 70}")
    print(f"PIPELINE FINEP CONCLUÍDO — Duração: {elapsed}")
    print(f"{'=' * 70}")


if __name__ == "__main__":
    sys.exit(main() or 0)
