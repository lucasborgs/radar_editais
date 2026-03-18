"""
Configuração centralizada de paths do projeto Radar de Editais.
"""
from pathlib import Path

ROOT = Path(__file__).parent

# Dados Bronze (raw)
BRONZE_DIR = ROOT / "bronze_data"

# Dados Silver (normalizados)
SILVER_DIR = ROOT / "silver_data"
SILVER_ENRICHED_DIR = ROOT / "silver_data_enriched"

# Dados Gold (vetores / ChromaDB)
GOLD_VECTORS_DIR = ROOT / "gold_vectors"
CHROMA_DB_DIR = ROOT / "chroma_db"

# Perfis de empresa
PROFILES_DIR = ROOT / "profiles"

# Section index para feature Writing
SECTION_INDEX_DIR = ROOT / "silver_data" / "section_index"

# Fine-tuning
FINETUNE_DATA_DIR = ROOT / "finetune_data"
MODELS_DIR = ROOT / "models"

# Cache de enriquecimento LLM
ENRICHMENT_CACHE = ROOT / ".enrichment_cache.json"
