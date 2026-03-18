#!/usr/bin/env python3
"""
Script principal para executar todos os scrapers de mecanismos de fomento.
Executa cada extrator, detecta mudanças via hash de conteúdo,
e dispara ETL incremental apenas para fontes que mudaram.

Fontes ativas: FAPESP, FINEP, BNDES (editais) + EMBRAPII (fluxo contínuo).
Controladas por SCRAPER_REGISTRY em extractors/__init__.py.

Ordem do pipeline ETL:
  1. Silver   — normalização e schema unificado
  2. Enrichment — extração LLM (TRL, temas) + geração do search_document
  3. Gold     — embedding do search_document + armazenamento no ChromaDB
"""
import sys
import json
import hashlib
from datetime import datetime
from pathlib import Path

from pipeline.extractors import SCRAPER_REGISTRY
from config import BRONZE_DIR as BRONZE_PATH

# =============================================================================
# CONFIGURAÇÃO
# =============================================================================

HASH_FILE = BRONZE_PATH / ".content_hashes.json"

# Todos os scrapers do registry são ativos
ACTIVE_KEYS = list(SCRAPER_REGISTRY.keys())

# Deriva mapeamentos automaticamente do registry
SOURCE_TO_BRONZE_DIR = {v["source_name"]: v["bronze_dir"] for v in SCRAPER_REGISTRY.values()}
SOURCE_TO_SILVER_NAME = {v["source_name"]: v["silver_name"] for v in SCRAPER_REGISTRY.values()}


# =============================================================================
# HASHING INCREMENTAL
# =============================================================================

def _compute_source_hash(source_dir: Path) -> str:
    """Calcula hash MD5 do conteúdo de todos os JSONs de um source."""
    if not source_dir.exists():
        return ""

    hasher = hashlib.md5()
    for f in sorted(source_dir.glob("*.json")):
        try:
            hasher.update(f.read_bytes())
        except Exception:
            hasher.update(f.name.encode())

    return hasher.hexdigest()


def _load_hashes() -> dict:
    if HASH_FILE.exists():
        try:
            return json.loads(HASH_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save_hashes(hashes: dict) -> None:
    BRONZE_PATH.mkdir(parents=True, exist_ok=True)
    HASH_FILE.write_text(json.dumps(hashes, indent=2), encoding="utf-8")


# =============================================================================
# ORQUESTRADOR
# =============================================================================

def main():
    """Executa todos os scrapers ativos e dispara ETL incremental."""
    print("=" * 70)
    print(f"RADAR DE MECANISMOS DE FOMENTO — Execução iniciada em {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Fontes ativas: {len(ACTIVE_KEYS)} | {', '.join(ACTIVE_KEYS)}")
    print("=" * 70)

    previous_hashes = _load_hashes()
    current_hashes = {}
    resultados = {}
    total = 0
    changed_sources = []

    # Instancia e executa scrapers ativos
    for key in ACTIVE_KEYS:
        cfg = SCRAPER_REGISTRY[key]
        scraper = cfg["cls"](**cfg["kwargs"])
        source_name = cfg["source_name"]

        try:
            results = scraper.run()
            count = len(results) if results else 0
            resultados[source_name] = count
            total += count
        except Exception as e:
            print(f"  [{source_name}] ERRO: {e}")
            resultados[source_name] = "ERRO"
            continue

        bronze_dir = cfg["bronze_dir"]
        source_path = BRONZE_PATH / bronze_dir
        new_hash = _compute_source_hash(source_path)
        current_hashes[source_name] = new_hash

        old_hash = previous_hashes.get(source_name, "")
        if new_hash != old_hash:
            changed_sources.append(source_name)
            print(f"  [{source_name}] Conteúdo ALTERADO → será reprocessado")
        else:
            print(f"  [{source_name}] Sem mudanças → ETL será pulado")

    _save_hashes(current_hashes)

    # Resumo
    print("\n" + "=" * 70)
    print("RESUMO DA EXTRAÇÃO")
    print("=" * 70)
    for fonte, count in resultados.items():
        status = f"{count} mecanismos" if isinstance(count, int) else count
        print(f"  {fonte}: {status}")
    print("-" * 70)
    print(f"  TOTAL: {total} mecanismos | FONTES ALTERADAS: {len(changed_sources)}")

    # ETL incremental para fontes que mudaram
    if changed_sources:
        silver_sources = [SOURCE_TO_SILVER_NAME.get(s, s) for s in changed_sources]

        print("\n" + "=" * 70)
        print(f"EXECUTANDO ETL PARA: {', '.join(silver_sources)}")
        print("=" * 70)

        # Ordem obrigatória: enrichment gera o search_document que gold precisa
        try:
            from pipeline.etl_silver import main as silver_main
            silver_main(sources_filter=silver_sources)
        except Exception as e:
            print(f"Erro no ETL Silver: {e}")

        try:
            from pipeline.etl_enrichment import main as enrichment_main
            enrichment_main(sources_filter=silver_sources)
        except Exception as e:
            print(f"Erro no ETL Enriquecimento: {e}")

        try:
            from pipeline.etl_gold_vectors import main as gold_main
            gold_main(sources_filter=silver_sources)
        except Exception as e:
            print(f"Erro no ETL Gold: {e}")

    else:
        print("\nNenhuma fonte alterada. ETL Silver/Gold pulado.")

    print("=" * 70)
    return 0 if total > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
