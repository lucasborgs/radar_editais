#!/usr/bin/env python3
"""
Script principal para executar todos os scrapers de editais.
Executa cada extrator e consolida resultados.
"""
import sys
from datetime import datetime

from extractors import (
    FINEPScraper,
    FAPESPScraper,
    BNDESScraper,
    PNCPScraper,
    SerrapilheiraScraper,
    ItauSocialScraper,
    CNPqScraper,
    ArapyauScraper,
    LemannScraper,
)


def main():
    """Executa todos os scrapers registrados."""
    print("=" * 70)
    print(f"RADAR DE EDITAIS - Execução iniciada em {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)

    # Lista de scrapers para executar
    # Nota: PNCP usa 7 dias por padrão para execuções rápidas (ajuste conforme necessário)
    scrapers = [
        # Fontes originais
        FINEPScraper(),
        FAPESPScraper(),
        BNDESScraper(),
        PNCPScraper(dias_retroativos=7),
        # Novas fontes
        SerrapilheiraScraper(),
        ItauSocialScraper(),
        CNPqScraper(),
        ArapyauScraper(),
        LemannScraper(),
    ]

    resultados = {}
    total = 0

    for scraper in scrapers:
        try:
            results = scraper.run()
            count = len(results) if results else 0
            resultados[scraper.source_name] = count
            total += count
        except Exception as e:
            print(f"Erro em {scraper.source_name}: {e}")
            resultados[scraper.source_name] = "ERRO"

    # Resumo final
    print("\n" + "=" * 70)
    print("RESUMO DA EXECUÇÃO")
    print("=" * 70)

    for fonte, count in resultados.items():
        status = f"{count} oportunidades" if isinstance(count, int) else count
        print(f"  {fonte}: {status}")

    print("-" * 70)
    print(f"  TOTAL: {total} oportunidades encontradas")
    print("=" * 70)

    return 0 if total > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
