#!/usr/bin/env python3
"""
Scraping histórico para geração de dados de fine-tuning.

Executa scrapers com modo histórico (include_historical=True), capturando
editais de anos anteriores além da página principal. Os dados históricos
ficam em bronze_data/ com prefixo _historical, separados dos dados de produção.

Após rodar este script:
  1. python3 etl_silver.py          — normaliza bronze → silver
  2. python3 etl_enrichment.py      — enriquece silver → silver_data_enriched
  3. python3 etl_finetune_data.py   — _filter_closed_editais() seleciona automaticamente os encerrados

NOTA: URLs validadas em fev/2026 para FINEP e FAPESP.
      FINEP: acessa chamadaspublicas?situacao=encerrada com paginação (&start=N).
      FAPESP: acessa páginas de arquivo por ano com IDs WordPress específicos.
"""
import sys
import json
from datetime import datetime
from pathlib import Path

from pipeline.extractors import SCRAPER_REGISTRY

# =============================================================================
# CONFIGURAÇÃO
# =============================================================================

# Fontes que suportam modo histórico (EMBRAPII excluída — sem prazo, sempre ativo)
HISTORICAL_SOURCES = ["FAPESP", "FINEP", "BNDES"]

BRONZE_PATH = Path("bronze_data")


# =============================================================================
# ORQUESTRADOR HISTÓRICO
# =============================================================================

def main():
    print("=" * 70)
    print(f"SCRAPING HISTÓRICO — Iniciado em {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Fontes: {', '.join(HISTORICAL_SOURCES)}")
    print("=" * 70)
    print()
    print("FINEP: buscará até 20 páginas de encerradas (~200 chamadas, desde 2001)")
    print("FAPESP: buscará arquivos de 2020-2025 (6 anos)")
    print()

    resultados = {}
    total = 0

    for key in HISTORICAL_SOURCES:
        if key not in SCRAPER_REGISTRY:
            print(f"[{key}] não encontrado no SCRAPER_REGISTRY — pulando")
            continue

        cfg = SCRAPER_REGISTRY[key]
        hist_kwargs = cfg.get("historical_kwargs", {})

        if not hist_kwargs:
            print(f"[{key}] sem historical_kwargs configurado — pulando")
            continue

        scraper = cfg["cls"](**cfg["kwargs"])
        source_name = cfg["source_name"]

        try:
            print(f"\n{'─' * 60}")
            results = scraper.extract(**hist_kwargs)
            count = len(results) if results else 0
            resultados[source_name] = count
            total += count
            print(f"[{source_name}] {count} itens extraídos (incluindo histórico)")

            # Contagem de encerrados vs abertos para estimativa
            if results:
                encerrados = sum(
                    1 for r in results
                    if str(r.get("status", "")).upper() in ("ENCERRADA", "ENCERRADO", "RESULTADO_DIVULGADO")
                )
                print(f"[{source_name}] Estimativa encerrados: {encerrados}/{count}")

        except Exception as e:
            print(f"[{source_name}] ERRO: {e}")
            resultados[source_name] = "ERRO"

    print()
    print("=" * 70)
    print("RESUMO DO SCRAPING HISTÓRICO")
    print("=" * 70)
    for fonte, count in resultados.items():
        status = f"{count} editais" if isinstance(count, int) else count
        print(f"  {fonte}: {status}")
    print(f"  TOTAL: {total} editais extraídos")
    print()
    print("Próximos passos:")
    print("  1. python3 etl_silver.py")
    print("  2. python3 etl_enrichment.py")
    print("  3. python3 etl_finetune_data.py")
    print("=" * 70)

    return 0 if total > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
