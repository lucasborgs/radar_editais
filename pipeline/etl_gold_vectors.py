"""
ETL Enriched → Gold Vectors (Radar de Mecanismos de Fomento)

Transforma dados de silver_data_enriched/ em chunks vetorizados para busca
semântica. Gera embeddings usando o `search_document` (template semântico
padronizado), mas armazena o `chunk_text` completo no ChromaDB para o LLM.

Separação embedding ↔ documento (Harmonização Semântica Pré-Embedding):
  - embedding: gerado a partir do `search_document` da linha pai
  - document: `chunk_text` do chunk (texto original completo para o LLM)

Mantém Parquet como backup/auditoria em gold_vectors/.
"""

import hashlib
import json
import logging
from pathlib import Path
from datetime import datetime
from typing import Optional
import gc

import numpy as np
import pandas as pd
import chromadb

# Imports para chunking
from langchain_text_splitters import RecursiveCharacterTextSplitter

# Sentence Transformers para BERTimbau
from sentence_transformers import SentenceTransformer

# =============================================================================
# CONFIGURAÇÃO
# =============================================================================

from config import SILVER_ENRICHED_DIR, GOLD_VECTORS_DIR as GOLD_PATH, CHROMA_DB_DIR as CHROMA_PATH, MODELS_DIR, SECTION_INDEX_DIR
ENRICHED_FILE = SILVER_ENRICHED_DIR / "editais_enriched.parquet"

# Modelo de Embedding para Português
# Usa modelo fine-tunado local se existir (colocado em models/fomento_bert/ após treino no Colab),
# caso contrário usa o modelo base do HuggingFace.
_LOCAL_MODEL = MODELS_DIR / "albertina_tripletloss"
EMBEDDING_MODEL = str(_LOCAL_MODEL) if _LOCAL_MODEL.exists() \
    else "PORTULAN/albertina-900m-portuguese-ptbr-encoder-pa-msmarco-sts"
EMBEDDING_DIM = 1536

# Chunking
CHUNK_SIZE = 500          # Caracteres por chunk
CHUNK_OVERLAP = 100       # 20% de overlap
MIN_CHUNK_SIZE = 50       # Chunks menores são descartados

# Batch Processing
EMBEDDING_BATCH_SIZE = 10
SOURCE_BATCH_SIZE = 5000
CHROMA_UPSERT_BATCH = 500  # ChromaDB recomenda batches de ~500

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
    description = str(row.get("description", "") or "").strip()
    title = str(row.get("title", "") or "").strip()
    if len(description) < MIN_CHUNK_SIZE:
        if description:
            text = f"{title}. {description}"
        else:
            text = title
        return text.strip()
    return description


def generate_chunk_id(parent_id: str, chunk_index: int) -> str:
    content = f"{parent_id}|{chunk_index}"
    return hashlib.sha256(content.encode('utf-8')).hexdigest()[:16]


# =============================================================================
# CHUNKING
# =============================================================================

def create_text_splitter() -> RecursiveCharacterTextSplitter:
    return RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
        length_function=len,
        separators=[
            "\n\n", "\n", ". ", "; ", ", ", " ", ""
        ],
        keep_separator=True,
    )


def chunk_text(
    text: str,
    parent_id: str,
    splitter: RecursiveCharacterTextSplitter
) -> list[dict]:
    text = text.strip()
    if len(text) < MIN_CHUNK_SIZE:
        return []
    if len(text) <= CHUNK_SIZE:
        return [{
            "chunk_index": 0,
            "chunk_text": text,
            "chunk_id": generate_chunk_id(parent_id, 0),
        }]
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


def chunk_from_section_index(parent_id: str) -> list[dict]:
    """
    Gera chunks a partir do section_index do documento quando disponível.
    Cada seção estrutural vira um chunk — semanticamente mais coerente
    do que cortes arbitrários de 500 chars.

    Returns:
        Lista de chunks no mesmo formato de chunk_text(), ou [] se não houver index.
    """
    index_path = SECTION_INDEX_DIR / f"{parent_id}.json"
    if not index_path.exists():
        return []

    try:
        payload = json.loads(index_path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning(f"Erro ao ler section index {index_path}: {e}")
        return []

    sections = payload.get("sections", [])
    if not sections:
        return []

    chunks = []
    for i, section in enumerate(sections):
        title   = section.get("title", "")
        content = section.get("content", "").strip()
        if not content or len(content) < MIN_CHUNK_SIZE:
            continue
        # Prefixar o título para enriquecer o contexto semântico do chunk
        chunk_text_val = f"{title}\n{content}" if title else content
        chunks.append({
            "chunk_index": i,
            "chunk_text":  chunk_text_val,
            "chunk_id":    generate_chunk_id(parent_id, i),
        })

    return chunks


# =============================================================================
# EMBEDDING
# =============================================================================

_embedding_model = None


def get_embedding_model() -> SentenceTransformer:
    global _embedding_model
    if _embedding_model is None:
        logger.info(f"Carregando modelo {EMBEDDING_MODEL}...")
        _embedding_model = SentenceTransformer(EMBEDDING_MODEL)
        logger.info("Modelo carregado!")
    return _embedding_model


def check_model_available() -> bool:
    try:
        model = get_embedding_model()
        _ = model.encode(["teste"], show_progress_bar=False)
        return True
    except Exception as e:
        logger.error(f"Erro ao carregar modelo: {e}")
        return False


def embed_batch(texts: list[str], show_progress: bool = False) -> np.ndarray:
    if not texts:
        return np.array([])
    model = get_embedding_model()
    embeddings = model.encode(
        texts,
        show_progress_bar=show_progress,
        batch_size=32,
        normalize_embeddings=True
    )
    return embeddings


# =============================================================================
# CHROMADB
# =============================================================================

def get_chroma_collection(reset: bool = False) -> chromadb.Collection:
    """Obtém ou cria a collection ChromaDB para editais."""
    client = chromadb.PersistentClient(path=str(CHROMA_PATH))

    if reset:
        try:
            client.delete_collection("editais")
            logger.info("Collection 'editais' resetada")
        except Exception:
            pass

    collection = client.get_or_create_collection(
        name="editais",
        metadata={"hnsw:space": "cosine"}
    )
    return collection


def upsert_to_chroma(
    collection: chromadb.Collection,
    chunk_ids: list[str],
    embeddings: list[list[float]],
    documents: list[str],
    metadatas: list[dict],
) -> None:
    """Faz upsert em batches no ChromaDB, deduplicando IDs."""
    # Deduplica por chunk_id (mantém primeira ocorrência)
    seen = set()
    dedup_ids, dedup_embs, dedup_docs, dedup_metas = [], [], [], []
    for i, cid in enumerate(chunk_ids):
        if cid not in seen:
            seen.add(cid)
            dedup_ids.append(cid)
            dedup_embs.append(embeddings[i])
            dedup_docs.append(documents[i])
            dedup_metas.append(metadatas[i])

    total = len(dedup_ids)
    for start in range(0, total, CHROMA_UPSERT_BATCH):
        end = min(start + CHROMA_UPSERT_BATCH, total)
        collection.upsert(
            ids=dedup_ids[start:end],
            embeddings=dedup_embs[start:end],
            documents=dedup_docs[start:end],
            metadatas=dedup_metas[start:end],
        )
    logger.info(f"    → {total} chunks upserted no ChromaDB")


# =============================================================================
# PROCESSAMENTO
# =============================================================================

def process_batch(
    df_batch: pd.DataFrame,
    source_name: str,
    splitter: RecursiveCharacterTextSplitter,
) -> tuple[pd.DataFrame, list[str], list[list[float]], list[str], list[dict]]:
    """
    Processa um batch de registros. Retorna DataFrame + dados para ChromaDB.
    """
    all_chunks = []
    texts_for_embedding = []   # search_document (template) para embedding
    texts_for_chroma_docs = [] # chunk_text completo para o LLM

    for _, row in df_batch.iterrows():
        # Prioridade: section index (chunks por seção estrutural)
        # Fallback: RecursiveCharacterTextSplitter genérico
        chunks = chunk_from_section_index(row["id"])
        if not chunks:
            text = get_text_for_embedding(row)
            chunks = chunk_text(text, row["id"], splitter)

        # Template semântico desta linha (gerado pelo etl_enrichment.py)
        search_doc = str(row.get("search_document", "") or "").strip()

        # Prepara metadados para injeção no ChromaDB
        status = str(row.get("status", "")) or ""
        deadline = row.get("deadline_date")

        for chunk in chunks:
            chunk.update({
                "parent_id": row["id"],
                "source": source_name,
                "url": str(row.get("url", "") or ""),
                "deadline_date": deadline,
                "title": str(row.get("title", "") or ""),
                "status": status,
                "tipo_fluxo": str(row.get("tipo_fluxo", "") or ""),
                "tipo_instrumento": str(row.get("tipo_instrumento", "") or ""),
            })
            all_chunks.append(chunk)
            # Embedding: usa o search_document do pai (quando disponível)
            texts_for_embedding.append(search_doc or chunk["chunk_text"])
            # ChromaDB document: texto original completo para o LLM
            texts_for_chroma_docs.append(chunk["chunk_text"])

    if not all_chunks:
        return pd.DataFrame(), [], [], [], []

    # Embedding: gerado a partir do search_document (template semântico)
    embeddings = embed_batch(texts_for_embedding, show_progress=True)

    # Preparar dados para ChromaDB
    chroma_ids = []
    chroma_embeddings = []
    chroma_documents = []   # texto completo para o LLM
    chroma_metadatas = []

    for i, chunk in enumerate(all_chunks):
        chunk["embedding"] = embeddings[i].tolist()

        chroma_ids.append(chunk["chunk_id"])
        chroma_embeddings.append(embeddings[i].tolist())
        # O documento armazenado é o chunk_text original (não o template)
        # para que o LLM de geração tenha acesso ao texto completo.
        chroma_documents.append(texts_for_chroma_docs[i])

        # ChromaDB metadata: apenas tipos primitivos (str, int, float, bool)
        deadline_val = chunk.get("deadline_date")
        deadline_meta = ""
        if deadline_val is not None and pd.notna(deadline_val):
            if hasattr(deadline_val, "isoformat"):
                deadline_meta = deadline_val.isoformat()
            else:
                deadline_meta = str(deadline_val)

        chroma_metadatas.append({
            "parent_id": chunk["parent_id"],
            "source": chunk["source"],
            "url": chunk["url"],
            "title": chunk["title"],
            "status": chunk.get("status", ""),
            "deadline_date": deadline_meta,
            "chunk_index": chunk["chunk_index"],
            "tipo_fluxo": chunk.get("tipo_fluxo", ""),
            "tipo_instrumento": chunk.get("tipo_instrumento", ""),
        })

    # DataFrame para Parquet backup
    df_gold = pd.DataFrame(all_chunks)
    column_order = [
        "chunk_id", "parent_id", "source", "chunk_index",
        "chunk_text", "embedding", "url", "deadline_date", "title",
        "tipo_fluxo", "tipo_instrumento",
    ]
    df_gold = df_gold[[col for col in column_order if col in df_gold.columns]]

    return df_gold, chroma_ids, chroma_embeddings, chroma_documents, chroma_metadatas


def process_source(
    df_source: pd.DataFrame,
    source_name: str,
    splitter: RecursiveCharacterTextSplitter,
    collection: chromadb.Collection,
) -> pd.DataFrame:
    """Processa todos os registros de uma fonte."""
    n_records = len(df_source)
    logger.info(f"Processando {source_name}: {n_records} registros")

    if n_records <= SOURCE_BATCH_SIZE:
        df_gold, c_ids, c_embs, c_docs, c_metas = process_batch(df_source, source_name, splitter)
        if df_gold.empty:
            logger.warning(f"  Nenhum chunk gerado para {source_name}")
        else:
            upsert_to_chroma(collection, c_ids, c_embs, c_docs, c_metas)
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

        df_gold, c_ids, c_embs, c_docs, c_metas = process_batch(df_batch, source_name, splitter)

        if not df_gold.empty:
            upsert_to_chroma(collection, c_ids, c_embs, c_docs, c_metas)
            all_dfs.append(df_gold)
            total_chunks += len(df_gold)
            logger.info(f"    {len(df_gold)} chunks gerados e indexados")

        del df_batch, df_gold
        gc.collect()

    if not all_dfs:
        logger.warning(f"  Nenhum chunk gerado para {source_name}")
        return pd.DataFrame()

    df_final = pd.concat(all_dfs, ignore_index=True)
    explosion_rate = len(df_final) / n_records
    logger.info(f"  Total {source_name}: {len(df_final)} chunks (explosão: {explosion_rate:.2f}x)")

    return df_final


def save_source_parquet(df: pd.DataFrame, source_name: str) -> None:
    if df.empty:
        return
    if "deadline_date" in df.columns:
        df["deadline_date"] = pd.to_datetime(df["deadline_date"], errors="coerce")
    source_path = GOLD_PATH / f"source={source_name}"
    source_path.mkdir(parents=True, exist_ok=True)
    output_file = source_path / "data.parquet"
    df.to_parquet(output_file, index=False, engine="pyarrow")
    logger.info(f"    → Backup Parquet salvo em {output_file}")


# =============================================================================
# ORQUESTRADOR
# =============================================================================

def main(sources_filter: Optional[list[str]] = None):
    """
    Pipeline ETL Silver → Gold Vectors.

    Args:
        sources_filter: Se fornecido, processa apenas as fontes listadas.
    """
    logger.info("=" * 60)
    logger.info("INICIANDO ETL SILVER → GOLD VECTORS")
    logger.info("=" * 60)

    start_time = datetime.now()

    # 1. Validar paths
    if not ENRICHED_FILE.exists():
        logger.error(f"Enriched parquet não encontrado: {ENRICHED_FILE}")
        logger.error("Execute etl_enrichment.py primeiro.")
        return

    GOLD_PATH.mkdir(parents=True, exist_ok=True)

    # 2. Carregar enriched e listar fontes
    logger.info(f"Lendo {ENRICHED_FILE}...")
    df_all = pd.read_parquet(ENRICHED_FILE)
    total_enriched = len(df_all)

    if sources_filter:
        df_all = df_all[df_all["source"].isin(sources_filter)]
        logger.info(f"Filtro ativo: {sources_filter}")

    sources = sorted(df_all["source"].unique())
    logger.info(f"Total de registros enriquecidos: {total_enriched}")
    logger.info(f"Fontes a processar: {sources}")

    # 3. Verificar modelo de embedding
    logger.info(f"Verificando modelo de embedding ({EMBEDDING_MODEL})...")
    if not check_model_available():
        logger.error("Modelo de embedding não disponível.")
        return
    logger.info("Modelo de embedding OK!")

    splitter = create_text_splitter()

    # 4. Inicializar ChromaDB
    logger.info("Inicializando ChromaDB...")
    collection = get_chroma_collection(reset=(sources_filter is None))
    logger.info(f"  → Collection 'editais' pronta (count={collection.count()})")

    # 5. Processar cada fonte
    logger.info("-" * 60)
    stats = {}

    for source in sources:
        logger.info(f"\n{'='*40}")
        df_source = df_all[df_all["source"] == source].copy()

        df_gold = process_source(df_source, source, splitter, collection)

        if not df_gold.empty:
            save_source_parquet(df_gold, source)
            stats[source] = {
                "enriched": len(df_source),
                "gold": len(df_gold),
                "rate": len(df_gold) / len(df_source) if len(df_source) > 0 else 0
            }

        del df_source, df_gold
        gc.collect()

    # 6. Estatísticas finais
    logger.info("\n" + "=" * 60)
    logger.info("ESTATÍSTICAS FINAIS:")

    total_gold = sum(s["gold"] for s in stats.values())
    logger.info(f"  Registros Enriched: {total_enriched}")
    logger.info(f"  Chunks Gold: {total_gold}")
    if total_enriched > 0:
        logger.info(f"  Taxa de explosão global: {total_gold/total_enriched:.2f}x")
    logger.info(f"  ChromaDB total: {collection.count()} chunks indexados")
    logger.info("")
    logger.info("  Por fonte:")
    for source, s in sorted(stats.items()):
        logger.info(f"    {source}: {s['enriched']} → {s['gold']} chunks ({s['rate']:.2f}x)")

    elapsed = datetime.now() - start_time

    logger.info("=" * 60)
    logger.info("ETL ENRICHED → GOLD VECTORS CONCLUÍDO COM SUCESSO")
    logger.info(f"Fonte: {ENRICHED_FILE}")
    logger.info(f"Total de chunks: {total_gold}")
    logger.info(f"Dimensão embeddings: {EMBEDDING_DIM}")
    logger.info(f"Tempo de execução: {elapsed}")
    logger.info(f"Saída: {GOLD_PATH}/ (Parquet) + {CHROMA_PATH}/ (ChromaDB)")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
