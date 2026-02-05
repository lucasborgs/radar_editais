"""
ETL Silver → Gold Vectors (Radar de Editais)

Script para transformar dados da Camada Silver em chunks vetorizados
para busca semântica. Gera embeddings usando sentence-transformers
e salva em Parquet particionado por fonte.

Autor: Pipeline Radar Editais
"""

import hashlib
import logging
from pathlib import Path
from datetime import datetime
from typing import Optional
import gc

import numpy as np
import pandas as pd
import requests

# Imports para chunking
from langchain_text_splitters import RecursiveCharacterTextSplitter

# =============================================================================
# CONFIGURAÇÃO
# =============================================================================

SILVER_PATH = Path("silver_data")
GOLD_PATH = Path("gold_vectors")

# Ollama Configuration
OLLAMA_URL = "http://localhost:11434/api/embeddings"
EMBEDDING_MODEL = "nomic-embed-text"  # 768 dimensões, via Ollama
EMBEDDING_DIM = 768

# Chunking
CHUNK_SIZE = 500          # Caracteres por chunk
CHUNK_OVERLAP = 100       # 20% de overlap
MIN_CHUNK_SIZE = 50       # Chunks menores são descartados

# Batch Processing
EMBEDDING_BATCH_SIZE = 10  # Textos por batch (Ollama é síncrono)
SOURCE_BATCH_SIZE = 5000   # Registros por batch para fontes grandes (PNCP)
LOG_INTERVAL = 500         # Log a cada N chunks processados

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger(__name__)


# =============================================================================
# FUNÇÕES UTILITÁRIAS
# =============================================================================

def get_text_for_embedding(row: pd.Series) -> str:
    """
    Retorna o texto a ser usado para embedding.
    Usa description se disponível, senão usa title como fallback.

    Importante para:
    - SERRAPILHEIRA: 30 registros com description vazia
    - ARAPYAU: 95% das descriptions vazias

    Args:
        row: Linha do DataFrame Silver

    Returns:
        Texto para embedding
    """
    description = str(row.get("description", "") or "").strip()
    title = str(row.get("title", "") or "").strip()

    # Se description está vazia ou muito curta, usa title
    if len(description) < MIN_CHUNK_SIZE:
        # Combina title + description (se houver algo)
        if description:
            text = f"{title}. {description}"
        else:
            text = title
        return text.strip()

    return description


def generate_chunk_id(parent_id: str, chunk_index: int) -> str:
    """
    Gera ID único para o chunk baseado em hash SHA256.

    Args:
        parent_id: ID do registro original
        chunk_index: Índice do chunk dentro do documento

    Returns:
        Hash de 16 caracteres
    """
    content = f"{parent_id}|{chunk_index}"
    return hashlib.sha256(content.encode('utf-8')).hexdigest()[:16]


# =============================================================================
# CHUNKING
# =============================================================================

def create_text_splitter() -> RecursiveCharacterTextSplitter:
    """
    Cria splitter otimizado para textos em português.
    Separadores ordenados por preferência semântica.

    Returns:
        Instância configurada do RecursiveCharacterTextSplitter
    """
    return RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
        length_function=len,
        separators=[
            "\n\n",    # Parágrafos
            "\n",      # Linhas
            ". ",      # Sentenças
            "; ",      # Cláusulas
            ", ",      # Itens
            " ",       # Palavras
            ""         # Caracteres (último recurso)
        ],
        keep_separator=True,
    )


def chunk_text(
    text: str,
    parent_id: str,
    splitter: RecursiveCharacterTextSplitter
) -> list[dict]:
    """
    Divide texto em chunks ou mantém inteiro se pequeno.

    Otimização: 94% dos textos têm < 500 chars (não precisam de chunking).

    Args:
        text: Texto a ser dividido
        parent_id: ID do registro original
        splitter: Instância do text splitter

    Returns:
        Lista de dicionários com chunk_index, chunk_text e chunk_id
    """
    text = text.strip()

    # Se texto vazio ou muito curto, retorna lista vazia
    if len(text) < MIN_CHUNK_SIZE:
        return []

    # Otimização: se texto cabe em um chunk, não dividir
    if len(text) <= CHUNK_SIZE:
        return [{
            "chunk_index": 0,
            "chunk_text": text,
            "chunk_id": generate_chunk_id(parent_id, 0),
        }]

    # Texto longo: aplicar splitting
    chunks = splitter.split_text(text)

    return [
        {
            "chunk_index": i,
            "chunk_text": chunk.strip(),
            "chunk_id": generate_chunk_id(parent_id, i),
        }
        for i, chunk in enumerate(chunks)
        if len(chunk.strip()) >= MIN_CHUNK_SIZE
    ]


# =============================================================================
# EMBEDDING (via Ollama)
# =============================================================================

def check_ollama_available() -> bool:
    """
    Verifica se Ollama está disponível e o modelo está carregado.

    Returns:
        True se disponível, False caso contrário
    """
    try:
        response = requests.post(
            OLLAMA_URL,
            json={"model": EMBEDDING_MODEL, "prompt": "test"},
            timeout=30
        )
        return response.status_code == 200
    except Exception as e:
        logger.error(f"Erro ao conectar com Ollama: {e}")
        return False


def embed_single(text: str) -> list[float]:
    """
    Gera embedding para um único texto via Ollama.

    Args:
        text: Texto para embedding

    Returns:
        Lista de floats (embedding)
    """
    try:
        response = requests.post(
            OLLAMA_URL,
            json={"model": EMBEDDING_MODEL, "prompt": text},
            timeout=60
        )
        if response.status_code == 200:
            return response.json().get("embedding", [])
        else:
            logger.warning(f"Erro Ollama: {response.status_code}")
            return [0.0] * EMBEDDING_DIM
    except Exception as e:
        logger.warning(f"Erro ao gerar embedding: {e}")
        return [0.0] * EMBEDDING_DIM


def embed_batch(texts: list[str], show_progress: bool = False) -> np.ndarray:
    """
    Gera embeddings para um batch de textos via Ollama.

    Args:
        texts: Lista de textos para embedding
        show_progress: Se True, mostra progresso

    Returns:
        Array numpy de shape (len(texts), embedding_dim)
    """
    if not texts:
        return np.array([])

    embeddings = []
    for i, text in enumerate(texts):
        emb = embed_single(text)
        embeddings.append(emb)

        if show_progress and (i + 1) % LOG_INTERVAL == 0:
            logger.info(f"      Embeddings: {i+1}/{len(texts)}")

    return np.array(embeddings)


# =============================================================================
# PROCESSAMENTO
# =============================================================================

def process_batch(
    df_batch: pd.DataFrame,
    source_name: str,
    splitter: RecursiveCharacterTextSplitter,
    batch_num: int = 0,
    total_batches: int = 1
) -> pd.DataFrame:
    """
    Processa um batch de registros de uma fonte.

    Args:
        df_batch: DataFrame do batch
        source_name: Nome da fonte
        splitter: Text splitter
        batch_num: Número do batch atual
        total_batches: Total de batches

    Returns:
        DataFrame com schema Gold
    """
    all_chunks = []
    texts_for_embedding = []

    # Fase 1: Chunking
    for _, row in df_batch.iterrows():
        text = get_text_for_embedding(row)
        chunks = chunk_text(text, row["id"], splitter)

        for chunk in chunks:
            chunk.update({
                "parent_id": row["id"],
                "source": source_name,
                "url": row.get("url", ""),
                "deadline_date": row.get("deadline_date"),
                "title": row.get("title", ""),
            })
            all_chunks.append(chunk)
            texts_for_embedding.append(chunk["chunk_text"])

    if not all_chunks:
        return pd.DataFrame()

    # Fase 2: Embedding via Ollama
    embeddings = embed_batch(texts_for_embedding, show_progress=True)

    # Fase 3: Montar DataFrame
    for i, chunk in enumerate(all_chunks):
        chunk["embedding"] = embeddings[i].tolist()

    df_gold = pd.DataFrame(all_chunks)

    # Reordenar colunas conforme schema
    column_order = [
        "chunk_id", "parent_id", "source", "chunk_index",
        "chunk_text", "embedding", "url", "deadline_date", "title"
    ]
    df_gold = df_gold[[col for col in column_order if col in df_gold.columns]]

    return df_gold


def process_source(
    df_source: pd.DataFrame,
    source_name: str,
    splitter: RecursiveCharacterTextSplitter
) -> pd.DataFrame:
    """
    Processa todos os registros de uma fonte.
    Para fontes grandes (>SOURCE_BATCH_SIZE), processa em batches.

    Args:
        df_source: DataFrame da fonte (camada Silver)
        source_name: Nome da fonte
        splitter: Text splitter

    Returns:
        DataFrame com schema Gold
    """
    n_records = len(df_source)
    logger.info(f"Processando {source_name}: {n_records} registros")

    # Se fonte pequena, processa de uma vez
    if n_records <= SOURCE_BATCH_SIZE:
        df_gold = process_batch(df_source, source_name, splitter)
        if df_gold.empty:
            logger.warning(f"  ⚠ Nenhum chunk gerado para {source_name}")
        else:
            explosion_rate = len(df_gold) / n_records
            logger.info(f"  → {len(df_gold)} chunks (explosão: {explosion_rate:.2f}x)")
        return df_gold

    # Fonte grande: processar em batches
    n_batches = (n_records + SOURCE_BATCH_SIZE - 1) // SOURCE_BATCH_SIZE
    logger.info(f"  → Fonte grande, processando em {n_batches} batches de {SOURCE_BATCH_SIZE}")

    all_dfs = []
    total_chunks = 0

    for i in range(n_batches):
        start_idx = i * SOURCE_BATCH_SIZE
        end_idx = min((i + 1) * SOURCE_BATCH_SIZE, n_records)
        df_batch = df_source.iloc[start_idx:end_idx]

        logger.info(f"  → Batch {i+1}/{n_batches}: registros {start_idx}-{end_idx}")

        df_gold = process_batch(df_batch, source_name, splitter, i, n_batches)

        if not df_gold.empty:
            all_dfs.append(df_gold)
            total_chunks += len(df_gold)
            logger.info(f"    ✓ {len(df_gold)} chunks gerados")

        # Liberar memória
        del df_batch, df_gold
        gc.collect()

    if not all_dfs:
        logger.warning(f"  ⚠ Nenhum chunk gerado para {source_name}")
        return pd.DataFrame()

    df_final = pd.concat(all_dfs, ignore_index=True)
    explosion_rate = len(df_final) / n_records
    logger.info(f"  ✓ Total {source_name}: {len(df_final)} chunks (explosão: {explosion_rate:.2f}x)")

    return df_final


def validate_output(df: pd.DataFrame) -> bool:
    """
    Valida integridade do DataFrame Gold.

    Args:
        df: DataFrame Gold a ser validado

    Returns:
        True se válido, False caso contrário
    """
    errors = []

    # 1. Colunas obrigatórias
    required_cols = ["chunk_id", "parent_id", "source", "chunk_text", "embedding"]
    missing = set(required_cols) - set(df.columns)
    if missing:
        errors.append(f"Colunas faltando: {missing}")

    # 2. Valores nulos em campos críticos
    for col in ["chunk_id", "parent_id", "chunk_text"]:
        if col in df.columns and df[col].isna().any():
            null_count = df[col].isna().sum()
            errors.append(f"Valores nulos em {col}: {null_count}")

    # 3. Dimensão dos embeddings
    if "embedding" in df.columns and len(df) > 0:
        first_embedding = df["embedding"].iloc[0]
        if len(first_embedding) != EMBEDDING_DIM:
            errors.append(f"Dimensão embedding incorreta: {len(first_embedding)} (esperado: {EMBEDDING_DIM})")

    # 4. Unicidade de chunk_id
    if "chunk_id" in df.columns:
        duplicates = df["chunk_id"].duplicated().sum()
        if duplicates > 0:
            errors.append(f"chunk_id duplicados: {duplicates}")

    if errors:
        for error in errors:
            logger.error(f"Validação: {error}")
        return False

    logger.info("Validação: ✓ OK")
    return True


# =============================================================================
# ORQUESTRADOR
# =============================================================================

def save_source_parquet(df: pd.DataFrame, source_name: str) -> None:
    """
    Salva DataFrame de uma fonte específica em Parquet.

    Args:
        df: DataFrame a ser salvo
        source_name: Nome da fonte
    """
    if df.empty:
        return

    # Converter deadline_date para datetime
    if "deadline_date" in df.columns:
        df["deadline_date"] = pd.to_datetime(df["deadline_date"], errors="coerce")

    # Criar diretório da fonte
    source_path = GOLD_PATH / f"source={source_name}"
    source_path.mkdir(parents=True, exist_ok=True)

    # Salvar parquet
    output_file = source_path / "data.parquet"
    df.to_parquet(output_file, index=False, engine="pyarrow")
    logger.info(f"    → Salvo em {output_file}")


def main():
    """
    Pipeline ETL Silver → Gold Vectors.
    Processa e salva cada fonte separadamente para economizar memória.
    """
    logger.info("=" * 60)
    logger.info("INICIANDO ETL SILVER → GOLD VECTORS")
    logger.info("=" * 60)

    start_time = datetime.now()

    # 1. Validar paths
    if not SILVER_PATH.exists():
        logger.error(f"Camada Silver não encontrada: {SILVER_PATH}")
        return

    # 2. Criar diretório de saída
    GOLD_PATH.mkdir(parents=True, exist_ok=True)

    # 3. Listar fontes disponíveis
    logger.info("Identificando fontes...")
    df_meta = pd.read_parquet(SILVER_PATH, columns=["source"])
    sources = sorted(df_meta["source"].unique())
    source_counts = df_meta["source"].value_counts().to_dict()
    total_silver = len(df_meta)
    del df_meta

    logger.info(f"Total de registros Silver: {total_silver}")
    logger.info(f"Fontes encontradas: {len(sources)}")

    # 4. Verificar Ollama e criar splitter
    logger.info(f"Verificando conexão com Ollama ({EMBEDDING_MODEL})...")
    if not check_ollama_available():
        logger.error("Ollama não está disponível. Execute: ollama serve")
        return
    logger.info("Ollama OK!")

    splitter = create_text_splitter()

    # 5. Processar cada fonte separadamente
    logger.info("-" * 60)
    stats = {}

    for source in sources:
        # Carregar apenas dados desta fonte
        logger.info(f"\n{'='*40}")
        df_source = pd.read_parquet(SILVER_PATH, filters=[("source", "==", source)])

        # Processar
        df_gold = process_source(df_source, source, splitter)

        # Salvar imediatamente
        if not df_gold.empty:
            save_source_parquet(df_gold, source)
            stats[source] = {
                "silver": len(df_source),
                "gold": len(df_gold),
                "rate": len(df_gold) / len(df_source) if len(df_source) > 0 else 0
            }

        # Liberar memória
        del df_source, df_gold
        import gc
        gc.collect()

    # 6. Estatísticas finais
    logger.info("\n" + "=" * 60)
    logger.info("ESTATÍSTICAS FINAIS:")

    total_gold = sum(s["gold"] for s in stats.values())
    logger.info(f"  Registros Silver: {total_silver}")
    logger.info(f"  Chunks Gold: {total_gold}")
    logger.info(f"  Taxa de explosão global: {total_gold/total_silver:.2f}x")
    logger.info("")
    logger.info("  Por fonte:")
    for source, s in sorted(stats.items()):
        logger.info(f"    {source}: {s['silver']} → {s['gold']} chunks ({s['rate']:.2f}x)")

    # 7. Tempo de execução
    elapsed = datetime.now() - start_time

    logger.info("=" * 60)
    logger.info("ETL GOLD VECTORS CONCLUÍDO COM SUCESSO")
    logger.info(f"Total de chunks: {total_gold}")
    logger.info(f"Dimensão embeddings: {EMBEDDING_DIM}")
    logger.info(f"Tempo de execução: {elapsed}")
    logger.info(f"Saída: {GOLD_PATH}/")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
