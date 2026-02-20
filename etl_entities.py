import json
import logging
import hashlib
import sys
import asyncio
import httpx
from pathlib import Path
from datetime import datetime
from typing import Optional, List, Dict
import pandas as pd

# =============================================================================
# CONFIGURAÇÃO AVANÇADA
# =============================================================================

SILVER_PATH = Path("silver_data")
ENTITIES_PATH = Path("graph_data/entities")
RELATIONSHIPS_PATH = Path("graph_data/relationships")
CHECKPOINT_PATH = Path("graph_data/checkpoint.json")

OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "llama3.2"

# Paralelismo: Ajuste CONCURRENT_REQUESTS conforme sua GPU/VRAM
CONCURRENT_REQUESTS = 5 
BATCH_SIZE = 50  # Quantidade de editais carregados por vez na memória
TIMEOUT = 120

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger(__name__)

# =============================================================================
# RESILIÊNCIA E CONTROLE DE ESTADO
# =============================================================================

def get_text_hash(text: str) -> str:
    """Gera hash para detectar se o conteúdo textual mudou."""
    return hashlib.md5(str(text).encode('utf-8')).hexdigest()

def save_checkpoint(processed_ids: set):
    """Salva progresso para permitir retomada após falhas."""
    with open(CHECKPOINT_PATH, "w") as f:
        json.dump(list(processed_ids), f)

def load_checkpoint() -> set:
    if CHECKPOINT_PATH.exists():
        with open(CHECKPOINT_PATH, "r") as f:
            return set(json.load(f))
    return set()

# =============================================================================
# MOTOR DE EXTRAÇÃO ASSÍNCRONO
# =============================================================================

async def extract_with_ollama(client: httpx.AsyncClient, row: pd.Series, semaphore: asyncio.Semaphore) -> Optional[dict]:
    """Extração individual com controle de concorrência."""
    prompt = EXTRACTION_PROMPT.format(
        title=row.get("title", "")[:200],
        source=row.get("source", ""),
        description=str(row.get("description", ""))[:2500] # Aumentado para captar mais contexto
    )

    async with semaphore:
        try:
            response = await client.post(
                OLLAMA_URL,
                json={
                    "model": OLLAMA_MODEL,
                    "prompt": prompt,
                    "stream": False,
                    "options": {"temperature": 0.1}
                },
                timeout=TIMEOUT
            )
            
            if response.status_code == 200:
                raw_text = response.json().get("response", "")
                # Parse robusto de JSON
                start = raw_text.find('{')
                end = raw_text.rfind('}') + 1
                if start >= 0 and end > start:
                    return json.loads(raw_text[start:end])
            return None
        except Exception as e:
            logger.error(f"Erro no edital {row['id']}: {e}")
            return None

# =============================================================================
# PIPELINE PRINCIPAL
# =============================================================================

async def process_all_editais(df: pd.DataFrame):
    semaphore = asyncio.Semaphore(CONCURRENT_REQUESTS)
    processed_ids = load_checkpoint()
    
    # Filtro Incremental (além do que já existe nos arquivos Parquet)
    df_to_process = df[~df["id"].isin(processed_ids)]
    total = len(df_to_process)
    
    logger.info(f"Iniciando processamento de {total} novos editais...")
    
    new_entities = []
    new_rels = []
    
    # httpx.AsyncClient gerencia o pool de conexões automaticamente
    async with httpx.AsyncClient() as client:
        # Processamento em chunks para não sobrecarregar o loop de eventos
        for i in range(0, total, BATCH_SIZE):
            chunk = df_to_process.iloc[i:i+BATCH_SIZE]
            tasks = [extract_with_ollama(client, row, semaphore) for _, row in chunk.iterrows()]
            
            results = await asyncio.gather(*tasks)
            
            # Processa resultados do chunk
            for (idx, row), entities in zip(chunk.iterrows(), results):
                if entities:
                    edital_id = row["id"]
                    for e_type, values in entities.items():
                        if not isinstance(values, list): continue
                        for val in values:
                            if not val: continue
                            
                            val_norm = val.strip().title()
                            ent_id = hashlib.sha256(f"{e_type}|{val_norm.lower()}".encode()).hexdigest()[:12]
                            
                            new_entities.append({
                                "entity_id": ent_id,
                                "entity_type": e_type.upper(),
                                "entity_value": val_norm
                            })
                            new_rels.append({
                                "source_id": edital_id,
                                "target_id": ent_id,
                                "relationship_type": f"HAS_{e_type.upper()}"
                            })
                    
                    processed_ids.add(edital_id)
            
            # Checkpoint a cada batch
            save_checkpoint(processed_ids)
            logger.info(f"Progresso: {min(i + BATCH_SIZE, total)}/{total} editais analisados.")

    return pd.DataFrame(new_entities), pd.DataFrame(new_rels)

def main():
    # Carregamento e validação inicial (Bronze -> Silver -> Filter)
    # ... (mesma lógica de carregamento de Parquet do seu código original) ...
    
    # Execução do loop assíncrono
    try:
        df_new_entities, df_new_rels = asyncio.run(process_all_editais(df))
        
        # Merge e persistência (mesma lógica do seu código de merge_results)
        # ...
        logger.info("Pipeline finalizado com sucesso.")
    except KeyboardInterrupt:
        logger.info("Interrompido pelo usuário. Checkpoint salvo.")

if __name__ == "__main__":
    main()