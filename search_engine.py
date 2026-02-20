"""
Motor de Busca Híbrido (Radar de Editais)

Implementa busca híbrida (semântica via ChromaDB + lexical via BM25)
sobre os dados vetorizados do Radar de Editais.
"""

import logging
import re
from pathlib import Path
from datetime import datetime
from typing import Optional

import numpy as np
import pandas as pd
import chromadb
from rank_bm25 import BM25Okapi
from sentence_transformers import SentenceTransformer

# NLTK para stemming português
try:
    from nltk.stem import RSLPStemmer
    STEMMER = RSLPStemmer()
    HAS_STEMMER = True
except ImportError:
    STEMMER = None
    HAS_STEMMER = False

# Stopwords português
PORTUGUESE_STOPWORDS = {
    "a", "ao", "aos", "as", "à", "às", "com", "como", "da", "das", "de", "do",
    "dos", "e", "em", "é", "foi", "foram", "há", "isso", "isto", "já", "na",
    "nas", "não", "no", "nos", "o", "os", "ou", "para", "pela", "pelas", "pelo",
    "pelos", "por", "qual", "quando", "que", "quem", "se", "sem", "seu", "seus",
    "sua", "suas", "são", "só", "também", "tem", "tendo", "ter", "um", "uma",
    "uns", "umas", "você", "vocês", "será", "sendo", "pode", "podem", "deve",
    "devem", "sobre", "entre", "após", "até", "desde", "durante", "mediante",
    "perante", "sob", "este", "esta", "estes", "estas", "esse", "essa", "esses",
    "essas", "aquele", "aquela", "aqueles", "aquelas", "mais", "menos", "muito",
    "muita", "muitos", "muitas", "outro", "outra", "outros", "outras", "mesmo",
    "mesma", "mesmos", "mesmas", "tal", "tais", "todo", "toda", "todos", "todas"
}

# =============================================================================
# CONFIGURAÇÃO
# =============================================================================

SILVER_PATH = Path("silver_data")
CHROMA_PATH = Path("chroma_db")

# Modelo de Embedding (mesmo usado no ETL)
EMBEDDING_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
EMBEDDING_DIM = 384

# Search Configuration
DEFAULT_TOP_K = 10
DEFAULT_SEMANTIC_WEIGHT = 0.7
DEFAULT_LEXICAL_WEIGHT = 0.3
RRF_K = 60

# Source Boosting
SOURCE_BOOST = {
    "FAPESP": 1.3,
    "CNPQ": 1.3,
    "FINEP": 1.2,
    "BNDES": 1.1,
}
DEFAULT_SOURCE_BOOST = 1.0

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger(__name__)


# =============================================================================
# UTILIDADES
# =============================================================================

def tokenize(text: str, use_stemming: bool = True) -> list[str]:
    if not text:
        return []
    text = text.lower()
    text = re.sub(r'[^\w\s]', ' ', text)
    tokens = [t for t in text.split() if len(t) > 1]
    tokens = [t for t in tokens if t not in PORTUGUESE_STOPWORDS]
    if use_stemming and HAS_STEMMER and STEMMER:
        tokens = [STEMMER.stem(t) for t in tokens]
    return tokens


# =============================================================================
# MOTOR DE BUSCA HÍBRIDO
# =============================================================================

class HybridSearchEngine:
    """
    Motor de busca híbrido que combina:
    - Busca semântica (ChromaDB com cosine similarity)
    - Busca lexical (BM25)
    - Fusão via Reciprocal Rank Fusion (RRF)
    """

    def __init__(
        self,
        silver_path: str = "silver_data",
        chroma_path: str = "chroma_db",
    ):
        self.silver_path = Path(silver_path)
        self.chroma_path = Path(chroma_path)

        logger.info("Inicializando HybridSearchEngine...")

        # Carrega Silver (metadados)
        logger.info("Carregando dados Silver...")
        self.df_silver = pd.read_parquet(self.silver_path)
        self.silver_ids = set(self.df_silver["id"].values)
        logger.info(f"  → {len(self.df_silver)} registros carregados")

        # Prepara índice BM25
        logger.info("Construindo índice BM25...")
        self._build_bm25_index()
        logger.info(f"  → Índice BM25 pronto")

        # Carrega ChromaDB
        logger.info("Conectando ao ChromaDB...")
        self._load_chroma()

        # Cache de queries e fontes
        self._query_cache = {}
        self._source_cache = {}
        self._embedding_model = None

        logger.info("Motor de busca inicializado!")

    def _build_bm25_index(self):
        """Constrói índice BM25 sobre title + description."""
        corpus = []
        for _, row in self.df_silver.iterrows():
            text = f"{row.get('title', '')} {row.get('description', '')}"
            tokens = tokenize(text)
            corpus.append(tokens)
        self.bm25 = BM25Okapi(corpus)
        self.bm25_doc_ids = self.df_silver["id"].tolist()

    def _load_chroma(self):
        """Conecta ao ChromaDB e carrega a collection."""
        self.collection = None
        try:
            client = chromadb.PersistentClient(path=str(self.chroma_path))
            self.collection = client.get_collection("editais")
            logger.info(f"  → ChromaDB: {self.collection.count()} chunks indexados")
        except Exception as e:
            logger.warning(f"ChromaDB não disponível: {e}")
            logger.warning("Busca semântica ficará desabilitada.")

    def _get_embedding_model(self) -> SentenceTransformer:
        if self._embedding_model is None:
            logger.info(f"Carregando modelo {EMBEDDING_MODEL}...")
            self._embedding_model = SentenceTransformer(EMBEDDING_MODEL)
        return self._embedding_model

    def _embed_query(self, query: str) -> list[float]:
        """Gera embedding para a query."""
        if query in self._query_cache:
            return self._query_cache[query]
        try:
            model = self._get_embedding_model()
            embedding = model.encode(
                [query],
                show_progress_bar=False,
                normalize_embeddings=True
            )[0]
            result = embedding.tolist()
            self._query_cache[query] = result
            return result
        except Exception as e:
            logger.warning(f"Erro ao gerar embedding: {e}")
            return [0.0] * EMBEDDING_DIM

    def _get_doc_source(self, doc_id: str) -> str:
        if doc_id in self._source_cache:
            return self._source_cache[doc_id]
        row = self.df_silver[self.df_silver["id"] == doc_id]
        if not row.empty:
            source = row.iloc[0].get("source", "")
            self._source_cache[doc_id] = source
            return source
        return ""

    def _build_chroma_where(self, filters: dict) -> Optional[dict]:
        """Converte filtros do usuário para ChromaDB where clause."""
        if not filters:
            return None

        conditions = []

        if "source" in filters and filters["source"]:
            sources = filters["source"]
            if isinstance(sources, str):
                sources = [sources]
            if len(sources) == 1:
                conditions.append({"source": {"$eq": sources[0]}})
            else:
                conditions.append({"source": {"$in": sources}})

        if "status" in filters and filters["status"]:
            statuses = filters["status"]
            if isinstance(statuses, str):
                statuses = [statuses]
            if len(statuses) == 1:
                conditions.append({"status": {"$eq": statuses[0]}})
            else:
                conditions.append({"status": {"$in": statuses}})

        if not conditions:
            return None
        if len(conditions) == 1:
            return conditions[0]
        return {"$and": conditions}

    def _apply_filters(self, filters: dict) -> set[str]:
        """Aplica filtros nos metadados Silver (para BM25 e enriquecimento)."""
        if not filters:
            return self.silver_ids.copy()

        mask = pd.Series([True] * len(self.df_silver))

        if "source" in filters and filters["source"]:
            sources = filters["source"]
            if isinstance(sources, str):
                sources = [sources]
            mask &= self.df_silver["source"].isin(sources)

        if "status" in filters and filters["status"]:
            statuses = filters["status"]
            if isinstance(statuses, str):
                statuses = [statuses]
            mask &= self.df_silver["status"].isin(statuses)

        if "deadline_after" in filters and filters["deadline_after"]:
            deadline = pd.to_datetime(filters["deadline_after"])
            mask &= (self.df_silver["deadline_date"] >= deadline) | self.df_silver["deadline_date"].isna()

        if "deadline_before" in filters and filters["deadline_before"]:
            deadline = pd.to_datetime(filters["deadline_before"])
            mask &= (self.df_silver["deadline_date"] <= deadline) | self.df_silver["deadline_date"].isna()

        if "location" in filters and filters["location"]:
            location = filters["location"].upper()
            mask &= self.df_silver["location"].str.contains(location, na=False, case=False)

        if "min_value" in filters and filters["min_value"]:
            mask &= (self.df_silver["value_brl"] >= filters["min_value"]) | self.df_silver["value_brl"].isna()

        if "max_value" in filters and filters["max_value"]:
            mask &= (self.df_silver["value_brl"] <= filters["max_value"]) | self.df_silver["value_brl"].isna()

        filtered_ids = set(self.df_silver.loc[mask, "id"].values)
        return filtered_ids

    def _semantic_search(
        self,
        query_embedding: list[float],
        candidate_ids: set,
        top_k: int,
        filters: dict = None,
    ) -> list[tuple]:
        """Busca semântica via ChromaDB."""
        if self.collection is None:
            logger.warning("ChromaDB não disponível!")
            return []

        # Constrói where clause para ChromaDB (source/status filtering)
        where = self._build_chroma_where(filters)

        # Query ChromaDB
        try:
            result = self.collection.query(
                query_embeddings=[query_embedding],
                n_results=min(top_k * 4, self.collection.count()),
                where=where,
                include=["documents", "metadatas", "distances"],
            )
        except Exception as e:
            logger.warning(f"Erro na busca ChromaDB: {e}")
            return []

        if not result["ids"] or not result["ids"][0]:
            return []

        # Processa resultados: agrupa por parent_id, mantém melhor score
        results = {}
        ids = result["ids"][0]
        distances = result["distances"][0]
        documents = result["documents"][0]
        metadatas = result["metadatas"][0]

        for i, chunk_id in enumerate(ids):
            parent_id = metadatas[i]["parent_id"]

            # ChromaDB com cosine retorna distância (0=idêntico, 2=oposto)
            # Converte para similaridade: sim = 1 - dist/2
            score = 1.0 - distances[i] / 2.0

            if parent_id in candidate_ids:
                if parent_id not in results or score > results[parent_id][0]:
                    results[parent_id] = (score, documents[i])

        sorted_results = sorted(results.items(), key=lambda x: x[1][0], reverse=True)[:top_k * 2]
        return [(pid, score, text) for pid, (score, text) in sorted_results]

    def _lexical_search(
        self,
        query: str,
        candidate_ids: set,
        top_k: int
    ) -> list[tuple]:
        """Busca lexical via BM25."""
        query_tokens = tokenize(query)
        if not query_tokens:
            return []
        scores = self.bm25.get_scores(query_tokens)
        results = []
        for idx, score in enumerate(scores):
            doc_id = self.bm25_doc_ids[idx]
            if doc_id in candidate_ids and score > 0:
                results.append((doc_id, score))
        results.sort(key=lambda x: x[1], reverse=True)
        return results[:top_k * 2]

    def _reciprocal_rank_fusion(
        self,
        semantic_results: list,
        lexical_results: list,
        semantic_weight: float,
        lexical_weight: float,
        k: int = RRF_K
    ) -> list[tuple]:
        """Combina rankings via Reciprocal Rank Fusion."""
        rrf_scores = {}
        semantic_scores = {}
        lexical_scores = {}
        chunk_texts = {}

        for rank, (doc_id, score, chunk_text) in enumerate(semantic_results):
            rrf_scores[doc_id] = rrf_scores.get(doc_id, 0) + semantic_weight / (k + rank + 1)
            semantic_scores[doc_id] = score
            chunk_texts[doc_id] = chunk_text

        for rank, (doc_id, score) in enumerate(lexical_results):
            rrf_scores[doc_id] = rrf_scores.get(doc_id, 0) + lexical_weight / (k + rank + 1)
            lexical_scores[doc_id] = score

        combined = []
        for doc_id, rrf_score in rrf_scores.items():
            source = self._get_doc_source(doc_id)
            boost = SOURCE_BOOST.get(source, DEFAULT_SOURCE_BOOST)
            boosted_score = rrf_score * boost

            combined.append((
                doc_id,
                boosted_score,
                semantic_scores.get(doc_id, 0),
                lexical_scores.get(doc_id, 0),
                chunk_texts.get(doc_id, "")
            ))

        combined.sort(key=lambda x: x[1], reverse=True)
        return combined

    def _enrich_results(
        self,
        ranked_results: list,
        top_k: int,
        semantic_weight: float = DEFAULT_SEMANTIC_WEIGHT,
        lexical_weight: float = DEFAULT_LEXICAL_WEIGHT
    ) -> list[dict]:
        """Enriquece resultados com metadados do Silver."""
        results = []

        for doc_id, score, sem_score, lex_score, chunk_text in ranked_results[:top_k]:
            row = self.df_silver[self.df_silver["id"] == doc_id]
            if row.empty:
                continue
            row = row.iloc[0]

            max_possible = semantic_weight / RRF_K + lexical_weight / RRF_K
            normalized_score = min(score / max_possible, 1.0) if max_possible > 0 else 0.0

            result = {
                "id": doc_id,
                "title": row.get("title", ""),
                "description": row.get("description", ""),
                "url": row.get("url", ""),
                "deadline_date": row.get("deadline_date"),
                "source": row.get("source", ""),
                "status": row.get("status", ""),
                "category": row.get("category", ""),
                "location": row.get("location", ""),
                "value_brl": row.get("value_brl"),
                "target_audience": row.get("target_audience", ""),
                "score": round(normalized_score, 4),
                "semantic_score": round(float(sem_score), 4),
                "lexical_score": round(float(lex_score), 4),
                "matching_chunk": chunk_text[:500] if chunk_text else "",
            }
            results.append(result)

        return results

    def search(
        self,
        query: str,
        top_k: int = DEFAULT_TOP_K,
        filters: dict = None,
        semantic_weight: float = DEFAULT_SEMANTIC_WEIGHT,
        lexical_weight: float = DEFAULT_LEXICAL_WEIGHT,
    ) -> list[dict]:
        """
        Executa busca híbrida.

        Args:
            query: Texto de busca em linguagem natural
            top_k: Número de resultados (default 10)
            filters: Filtros opcionais (source, status, deadline_after/before, location, min/max_value)
            semantic_weight: Peso da busca semântica (0-1)
            lexical_weight: Peso da busca lexical (0-1)

        Returns:
            Lista de dicts com resultados rankeados
        """
        logger.info(f"Buscando: '{query}' (top_k={top_k})")

        # 1. Aplica filtros (Silver para BM25, ChromaDB para semântico)
        candidate_ids = self._apply_filters(filters)
        if not candidate_ids:
            logger.info("Nenhum documento passou nos filtros")
            return []

        # 2. Busca semântica via ChromaDB
        semantic_results = []
        if self.collection is not None and semantic_weight > 0:
            query_embedding = self._embed_query(query)
            semantic_results = self._semantic_search(
                query_embedding, candidate_ids, top_k, filters
            )

        # 3. Busca lexical
        lexical_results = []
        if lexical_weight > 0:
            lexical_results = self._lexical_search(query, candidate_ids, top_k)

        # 4. Fusão RRF
        if not semantic_results and not lexical_results:
            logger.info("Nenhum resultado encontrado")
            return []

        ranked_results = self._reciprocal_rank_fusion(
            semantic_results,
            lexical_results,
            semantic_weight,
            lexical_weight
        )

        # 5. Enriquece com metadados
        results = self._enrich_results(ranked_results, top_k, semantic_weight, lexical_weight)

        logger.info(f"Retornando {len(results)} resultados")
        return results

    def search_semantic_only(self, query: str, top_k: int = 10, filters: dict = None) -> list[dict]:
        """Busca apenas semântica (100% peso)."""
        return self.search(query, top_k, filters, semantic_weight=1.0, lexical_weight=0.0)

    def search_lexical_only(self, query: str, top_k: int = 10, filters: dict = None) -> list[dict]:
        """Busca apenas lexical/BM25 (100% peso)."""
        return self.search(query, top_k, filters, semantic_weight=0.0, lexical_weight=1.0)


# =============================================================================
# CLI INTERATIVO
# =============================================================================

def print_result(result: dict, index: int):
    """Imprime um resultado formatado."""
    print(f"\n{index}. [{result['score']:.3f}] {result['title'][:80]}")
    print(f"   Fonte: {result['source']} | Status: {result['status']}")
    if result['deadline_date']:
        deadline = result['deadline_date']
        if hasattr(deadline, 'strftime'):
            deadline = deadline.strftime('%Y-%m-%d')
        print(f"   Prazo: {deadline}")
    if result['value_brl']:
        print(f"   Valor: R$ {result['value_brl']:,.2f}")
    print(f"   URL: {result['url'][:80]}..." if len(result['url']) > 80 else f"   URL: {result['url']}")
    if result['matching_chunk']:
        chunk = result['matching_chunk'][:200].replace('\n', ' ')
        print(f"   Trecho: {chunk}...")


def main():
    """CLI interativo para testar o motor de busca."""
    print("=" * 60)
    print("MOTOR DE BUSCA HÍBRIDO - Radar de Editais")
    print("=" * 60)

    engine = HybridSearchEngine()

    print("\nComandos:")
    print("  /filtros          - Ver filtros disponíveis")
    print("  /fonte FAPESP     - Filtrar por fonte")
    print("  /status ABERTA    - Filtrar por status")
    print("  /limpar           - Limpar filtros")
    print("  /sair             - Sair")
    print()

    current_filters = {}

    while True:
        try:
            query = input("\n🔍 Busca: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nSaindo...")
            break

        if not query:
            continue

        if query.lower() in ["/sair", "/exit", "/quit", "q"]:
            print("Saindo...")
            break

        if query.lower() == "/filtros":
            print("\nFiltros ativos:", current_filters if current_filters else "Nenhum")
            print("\nFiltros disponíveis:")
            print("  source: FAPESP, CNPQ, FINEP, BNDES")
            print("  status: ABERTA, ENCERRADA, FLUXO_CONTINUO, VERIFICAR")
            continue

        if query.lower().startswith("/fonte "):
            fonte = query[7:].strip().upper()
            current_filters["source"] = [fonte]
            print(f"Filtro de fonte definido: {fonte}")
            continue

        if query.lower().startswith("/status "):
            status = query[8:].strip().upper()
            current_filters["status"] = status
            print(f"Filtro de status definido: {status}")
            continue

        if query.lower() == "/limpar":
            current_filters = {}
            print("Filtros limpos")
            continue

        results = engine.search(
            query,
            top_k=5,
            filters=current_filters if current_filters else None
        )

        if not results:
            print("\nNenhum resultado encontrado.")
            continue

        print(f"\n{'='*60}")
        print(f"Encontrados {len(results)} resultados para: '{query}'")
        if current_filters:
            print(f"Filtros: {current_filters}")
        print("=" * 60)

        for i, result in enumerate(results, 1):
            print_result(result, i)


if __name__ == "__main__":
    main()
