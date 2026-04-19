"""
Configuração centralizada de paths e variáveis de ambiente do projeto.
"""
import os
from pathlib import Path

# Supabase (definir no .env)
SUPABASE_URL         = os.getenv("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "")
SUPABASE_JWT_SECRET  = os.getenv("SUPABASE_JWT_SECRET", "")
SUPABASE_ANON_KEY    = os.getenv("SUPABASE_ANON_KEY", "")

ROOT = Path(__file__).parent

# Dados Bronze (raw)
BRONZE_DIR = ROOT / "bronze_data"

# Dados Silver (normalizados)
SILVER_DIR = ROOT / "silver_data"

# Perfis de empresa
PROFILES_DIR = ROOT / "profiles"

# Section index para WritingSession
SECTION_INDEX_DIR = ROOT / "silver_data" / "section_index"

# FINEP PDFs organizados por chamada_id
FINEP_PDFS_DIR = BRONZE_DIR / "finep_pdfs"

# FINEP fatos atômicos extraídos por LLM
FINEP_FACTS_DIR = SILVER_DIR / "finep" / "facts"

# Knowledge Graph
KNOWLEDGE_GRAPH_DIR = ROOT / "knowledge_graph"

# Cards ricos por edital (Karpathy-style)
KG_CARDS_DIR = KNOWLEDGE_GRAPH_DIR / "cards"
