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

# Dados locais (medallion): data/bronze (raw imutável) e data/silver (derivado,
# reconstruível do bronze). Camadas separadas como subpastas de um único dir.
BRONZE_DIR = ROOT / "data" / "bronze"
SILVER_DIR = ROOT / "data" / "silver"

# FINEP PDFs organizados por chamada_id
FINEP_PDFS_DIR = BRONZE_DIR / "finep_pdfs"

# Diretório de artefatos legados ainda usado pelo ledger de Descoberta e por
# ferramentas locais de vocabulário; catálogo e match v3 vivem no Postgres gold.
KNOWLEDGE_GRAPH_DIR = ROOT / "data" / "knowledge_graph"

# Compatibilidade com ferramentas que ainda inspecionam wiki pages históricas.
KG_WIKI_DIR = KNOWLEDGE_GRAPH_DIR / "wiki"

# Vault Obsidian unificado dentro do projeto (raiz do vault).
# As notas vivem em OBSIDIAN_VAULT_DIR/<subfolder>/ (subfolder = "radar-editais").
# Gerado pelo ETL diário ou sob demanda por scripts/export_to_obsidian.py.
OBSIDIAN_VAULT_DIR = ROOT / "data" / "hyper_extract_output_v2" / "vault"
