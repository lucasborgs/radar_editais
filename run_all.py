#!/usr/bin/env python3
"""
Script principal para executar todos os scrapers de editais.
Executa cada extrator, detecta mudanças via hash de conteúdo,
e dispara ETL Silver/Gold apenas para fontes que mudaram.
"""
import sys
import json
import hashlib
from datetime import datetime
from pathlib import Path

from extractors import (
    FINEPScraper,
    FAPESPScraper,
    BNDESScraper,
    CNPqScraper,
)

# =============================================================================
# CONFIGURAÇÃO
# =============================================================================

BRONZE_PATH = Path("bronze_data")
HASH_FILE = BRONZE_PATH / ".content_hashes.json"

# Mapeamento scraper source_name → pasta bronze → nome normalizado Silver
SOURCE_TO_BRONZE_DIR = {
    "FINEP": "finep_raw",
    "FAPESP": "fapesp_raw",
    "BNDES": "bndes_raw",
    "CNPq": "cnpq_raw",
}

# Mapeia source_name do scraper para nome normalizado no Silver/Gold
SOURCE_TO_SILVER_NAME = {
    "FINEP": "FINEP",
    "FAPESP": "FAPESP",
    "BNDES": "BNDES",
    "CNPq": "CNPQ",
}


# =============================================================================
# HASHING INCREMENTAL
# =============================================================================

def _compute_source_hash(source_dir: Path) -> str:
    """Calcula hash MD5 do conteúdo de todos os JSONs de um source."""
    if not source_dir.exists():
        return ""

    hasher = hashlib.md5()
    json_files = sorted(source_dir.glob("*.json"))

    for f in json_files:
        try:
            content = f.read_bytes()
            hasher.update(content)
        except Exception:
            hasher.update(f.name.encode())

    return hasher.hexdigest()


def _load_hashes() -> dict:
    """Carrega hashes salvos da execução anterior."""
    if HASH_FILE.exists():
        try:
            return json.loads(HASH_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save_hashes(hashes: dict) -> None:
    """Persiste hashes da execução atual."""
    BRONZE_PATH.mkdir(parents=True, exist_ok=True)
    HASH_FILE.write_text(
        json.dumps(hashes, indent=2),
        encoding="utf-8"
    )


# =============================================================================
# ORQUESTRADOR
# =============================================================================

def main():
    """Executa todos os scrapers e dispara ETL incremental."""
    print("=" * 70)
    print(f"RADAR DE EDITAIS - Execução iniciada em {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)

    # 1. Carregar hashes anteriores
    previous_hashes = _load_hashes()
    current_hashes = {}

    # 2. Executar scrapers
    scrapers = [
        FINEPScraper(),
        FAPESPScraper(),
        BNDESScraper(),
        CNPqScraper(),
    ]

    resultados = {}
    total = 0
    changed_sources = []

    for scraper in scrapers:
        source_name = scraper.source_name
        try:
            results = scraper.run()
            count = len(results) if results else 0
            resultados[source_name] = count
            total += count
        except Exception as e:
            print(f"Erro em {source_name}: {e}")
            resultados[source_name] = "ERRO"
            continue

        # Calcula hash pós-scraping
        bronze_dir = SOURCE_TO_BRONZE_DIR.get(source_name, "")
        if bronze_dir:
            source_path = BRONZE_PATH / bronze_dir
            new_hash = _compute_source_hash(source_path)
            current_hashes[source_name] = new_hash

            old_hash = previous_hashes.get(source_name, "")
            if new_hash != old_hash:
                changed_sources.append(source_name)
                print(f"  [{source_name}] Conteúdo ALTERADO → será reprocessado")
            else:
                print(f"  [{source_name}] Sem mudanças → ETL será pulado")

    # 3. Salvar hashes atuais
    _save_hashes(current_hashes)

    # 4. Resumo scraping
    print("\n" + "=" * 70)
    print("RESUMO DA EXTRAÇÃO")
    print("=" * 70)

    for fonte, count in resultados.items():
        status = f"{count} oportunidades" if isinstance(count, int) else count
        print(f"  {fonte}: {status}")

    print("-" * 70)
    print(f"  TOTAL: {total} oportunidades encontradas")
    print(f"  FONTES ALTERADAS: {len(changed_sources)} de {len(scrapers)}")

    # 5. Disparar ETL apenas para fontes que mudaram
    if changed_sources:
        silver_sources = [SOURCE_TO_SILVER_NAME.get(s, s) for s in changed_sources]

        print("\n" + "=" * 70)
        print(f"EXECUTANDO ETL PARA: {', '.join(silver_sources)}")
        print("=" * 70)

        # ETL Silver
        try:
            from etl_silver import main as silver_main
            silver_main(sources_filter=silver_sources)
        except Exception as e:
            print(f"Erro no ETL Silver: {e}")

        # ETL Gold Vectors
        try:
            from etl_gold_vectors import main as gold_main
            gold_main(sources_filter=silver_sources)
        except Exception as e:
            print(f"Erro no ETL Gold: {e}")

        # ETL Enriquecimento (extração de dados estruturados via LLM)
        try:
            from etl_enrichment import main as enrichment_main
            enrichment_main(sources_filter=silver_sources)
        except Exception as e:
            print(f"Erro no ETL Enriquecimento: {e}")

        # ETL Grafo de Conhecimento
        try:
            from etl_graph import main as graph_main
            graph_main()
        except Exception as e:
            print(f"Erro no ETL Grafo: {e}")
    else:
        print("\nNenhuma fonte alterada. ETL Silver/Gold pulado.")

    print("=" * 70)
    return 0 if total > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
