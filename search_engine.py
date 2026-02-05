"""
Motor de Busca Híbrido (Radar de Editais)

Implementa busca híbrida (semântica + lexical) sobre os dados
vetorizados do Radar de Editais.

Autor: Pipeline Radar Editais
"""

import logging
import re
from pathlib import Path
from datetime import datetime
from typing import Optional
from functools import lru_cache

import numpy as np
import pandas as pd
import requests
from rank_bm25 import BM25Okapi

# NLTK para stemming português
try:
    from nltk.stem import RSLPStemmer
    STEMMER = RSLPStemmer()
    HAS_STEMMER = True
except ImportError:
    STEMMER = None
    HAS_STEMMER = False

# Stopwords português (palavras muito comuns que não agregam valor semântico)
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
GOLD_PATH = Path("gold_vectors")

# Ollama Configuration
OLLAMA_URL = "http://localhost:11434/api/embeddings"
EMBEDDING_MODEL = "nomic-embed-text"
EMBEDDING_DIM = 768

# Search Configuration
DEFAULT_TOP_K = 10
DEFAULT_SEMANTIC_WEIGHT = 0.7
DEFAULT_LEXICAL_WEIGHT = 0.3
RRF_K = 60  # Parâmetro para Reciprocal Rank Fusion

# Source Boosting - Prioriza fontes curadas sobre volume (PNCP)
# Valores > 1.0 aumentam relevância, < 1.0 diminuem
SOURCE_BOOST = {
    "FAPESP": 1.5,       # Alta qualidade, editais de fomento
    "CNPQ": 1.5,         # Alta qualidade, editais de pesquisa
    "FINEP": 1.4,        # Alta qualidade, inovação
    "BNDES": 1.3,        # Financiamento desenvolvimento
    "LEMANN": 1.3,       # Fundação educação
    "SERRAPILHEIRA": 1.3, # Ciência
    "ARAPYAU": 1.2,      # Programas sociais
    "ITAU_SOCIAL": 1.2,  # Programas sociais
    "PNCP": 0.7,         # Volume alto, licitações genéricas - penalizado
}
DEFAULT_SOURCE_BOOST = 1.0  # Para fontes não listadas

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
    """
    Tokeniza texto para BM25 com stemming português e remoção de stopwords.

    Args:
        text: Texto para tokenizar
        use_stemming: Se True, aplica stemming português (RSLP)

    Returns:
        Lista de tokens processados
    """
    if not text:
        return []

    # Lowercase e remove pontuação
    text = text.lower()
    text = re.sub(r'[^\w\s]', ' ', text)

    # Split e remove vazios e tokens muito curtos
    tokens = [t for t in text.split() if len(t) > 1]

    # Remove stopwords
    tokens = [t for t in tokens if t not in PORTUGUESE_STOPWORDS]

    # Aplica stemming português se disponível
    if use_stemming and HAS_STEMMER and STEMMER:
        tokens = [STEMMER.stem(t) for t in tokens]

    return tokens


def normalize_vector(v: np.ndarray) -> np.ndarray:
    """Normaliza vetor para norma unitária."""
    norm = np.linalg.norm(v)
    if norm > 0:
        return v / norm
    return v


# =============================================================================
# MOTOR DE BUSCA HÍBRIDO
# =============================================================================

class HybridSearchEngine:
    """
    Motor de busca híbrido que combina:
    - Busca semântica (embeddings + cosine similarity)
    - Busca lexical (BM25)
    - Fusão via Reciprocal Rank Fusion (RRF)
    """

    def __init__(
        self,
        silver_path: str = "silver_data",
        gold_path: str = "gold_vectors",
        load_gold: bool = True
    ):
        """
        Inicializa o motor carregando os índices.

        Args:
            silver_path: Caminho para dados Silver
            gold_path: Caminho para dados Gold (vetores)
            load_gold: Se True, carrega embeddings na inicialização
        """
        self.silver_path = Path(silver_path)
        self.gold_path = Path(gold_path)

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

        # Carrega Gold (embeddings)
        self.df_gold = None
        self.embeddings = None
        self.embedding_ids = None

        if load_gold:
            self._load_gold_vectors()

        # Cache de embeddings de queries
        self._query_cache = {}

        # Cache de fonte por documento (para source boosting)
        self._source_cache = {}

        logger.info("Motor de busca inicializado!")

    def _build_bm25_index(self):
        """Constrói índice BM25 sobre title + description."""
        # Combina title e description para cada documento
        corpus = []
        for _, row in self.df_silver.iterrows():
            text = f"{row.get('title', '')} {row.get('description', '')}"
            tokens = tokenize(text)
            corpus.append(tokens)

        self.bm25 = BM25Okapi(corpus)
        self.bm25_doc_ids = self.df_silver["id"].tolist()

    def _load_gold_vectors(self):
        """Carrega embeddings do Gold."""
        logger.info("Carregando vetores Gold...")

        # Verifica quais fontes estão disponíveis
        available_sources = [
            d.name for d in self.gold_path.iterdir()
            if d.is_dir() and d.name.startswith("source=")
        ]

        if not available_sources:
            logger.warning("Nenhum dado Gold encontrado!")
            return

        logger.info(f"  → Fontes disponíveis: {len(available_sources)}")

        # Carrega todos os parquets
        dfs = []
        for source_dir in available_sources:
            source_path = self.gold_path / source_dir
            try:
                df = pd.read_parquet(source_path)
                dfs.append(df)
            except Exception as e:
                logger.warning(f"Erro ao carregar {source_dir}: {e}")

        if not dfs:
            logger.warning("Nenhum dado Gold carregado!")
            return

        self.df_gold = pd.concat(dfs, ignore_index=True)
        logger.info(f"  → {len(self.df_gold)} chunks carregados")

        # Extrai embeddings como numpy array
        logger.info("  → Convertendo embeddings para numpy...")
        self.embeddings = np.array(self.df_gold["embedding"].tolist())

        # Normaliza para usar dot product como cosine similarity
        logger.info("  → Normalizando vetores...")
        norms = np.linalg.norm(self.embeddings, axis=1, keepdims=True)
        norms[norms == 0] = 1  # Evita divisão por zero
        self.embeddings = self.embeddings / norms

        # Mapeia índices para IDs
        self.embedding_parent_ids = self.df_gold["parent_id"].tolist()
        self.embedding_chunk_texts = self.df_gold["chunk_text"].tolist()

        logger.info(f"  → Embeddings prontos: {self.embeddings.shape}")

    def _embed_query(self, query: str) -> np.ndarray:
        """
        Gera embedding para a query via Ollama.

        Args:
            query: Texto da query

        Returns:
            Vetor numpy normalizado
        """
        # Verifica cache
        if query in self._query_cache:
            return self._query_cache[query]

        try:
            response = requests.post(
                OLLAMA_URL,
                json={"model": EMBEDDING_MODEL, "prompt": query},
                timeout=30
            )
            if response.status_code == 200:
                embedding = np.array(response.json().get("embedding", []))
                # Normaliza
                embedding = normalize_vector(embedding)
                # Cache
                self._query_cache[query] = embedding
                return embedding
            else:
                logger.warning(f"Erro Ollama: {response.status_code}")
                return np.zeros(EMBEDDING_DIM)
        except Exception as e:
            logger.warning(f"Erro ao gerar embedding: {e}")
            return np.zeros(EMBEDDING_DIM)

    def _get_doc_source(self, doc_id: str) -> str:
        """
        Retorna a fonte de um documento pelo ID.

        Args:
            doc_id: ID do documento

        Returns:
            Nome da fonte ou string vazia se não encontrado
        """
        if doc_id in self._source_cache:
            return self._source_cache[doc_id]

        row = self.df_silver[self.df_silver["id"] == doc_id]
        if not row.empty:
            source = row.iloc[0].get("source", "")
            self._source_cache[doc_id] = source
            return source
        return ""

    def _apply_filters(self, filters: dict) -> set[str]:
        """
        Aplica filtros nos metadados Silver.

        Args:
            filters: Dicionário de filtros

        Returns:
            Set de IDs que passam nos filtros
        """
        if not filters:
            return self.silver_ids.copy()

        mask = pd.Series([True] * len(self.df_silver))

        # Filtro por fonte
        if "source" in filters and filters["source"]:
            sources = filters["source"]
            if isinstance(sources, str):
                sources = [sources]
            mask &= self.df_silver["source"].isin(sources)

        # Filtro por status
        if "status" in filters and filters["status"]:
            statuses = filters["status"]
            if isinstance(statuses, str):
                statuses = [statuses]
            mask &= self.df_silver["status"].isin(statuses)

        # Filtro por deadline (após)
        if "deadline_after" in filters and filters["deadline_after"]:
            deadline = pd.to_datetime(filters["deadline_after"])
            mask &= (self.df_silver["deadline_date"] >= deadline) | self.df_silver["deadline_date"].isna()

        # Filtro por deadline (antes)
        if "deadline_before" in filters and filters["deadline_before"]:
            deadline = pd.to_datetime(filters["deadline_before"])
            mask &= (self.df_silver["deadline_date"] <= deadline) | self.df_silver["deadline_date"].isna()

        # Filtro por localização
        if "location" in filters and filters["location"]:
            location = filters["location"].upper()
            mask &= self.df_silver["location"].str.contains(location, na=False, case=False)

        # Filtro por valor mínimo
        if "min_value" in filters and filters["min_value"]:
            mask &= (self.df_silver["value_brl"] >= filters["min_value"]) | self.df_silver["value_brl"].isna()

        # Filtro por valor máximo
        if "max_value" in filters and filters["max_value"]:
            mask &= (self.df_silver["value_brl"] <= filters["max_value"]) | self.df_silver["value_brl"].isna()

        filtered_ids = set(self.df_silver.loc[mask, "id"].values)
        logger.debug(f"Filtros aplicados: {len(filtered_ids)}/{len(self.silver_ids)} IDs")

        return filtered_ids

    def _semantic_search(
        self,
        query_embedding: np.ndarray,
        candidate_ids: set,
        top_k: int
    ) -> list[tuple]:
        """
        Busca semântica via cosine similarity.

        Args:
            query_embedding: Vetor da query (normalizado)
            candidate_ids: IDs válidos (após filtros)
            top_k: Número de resultados

        Returns:
            Lista de (parent_id, score, chunk_text)
        """
        if self.embeddings is None:
            logger.warning("Embeddings não carregados!")
            return []

        # Calcula similaridade (dot product = cosine para vetores normalizados)
        scores = self.embeddings @ query_embedding

        # Filtra por candidate_ids e agrupa por parent_id
        results = {}
        for idx, score in enumerate(scores):
            parent_id = self.embedding_parent_ids[idx]
            if parent_id in candidate_ids:
                # Mantém o melhor score por documento
                if parent_id not in results or score > results[parent_id][0]:
                    results[parent_id] = (score, self.embedding_chunk_texts[idx])

        # Ordena e retorna top-k
        sorted_results = sorted(results.items(), key=lambda x: x[1][0], reverse=True)[:top_k * 2]

        return [(pid, score, text) for pid, (score, text) in sorted_results]

    def _lexical_search(
        self,
        query: str,
        candidate_ids: set,
        top_k: int
    ) -> list[tuple]:
        """
        Busca lexical via BM25.

        Args:
            query: Texto da query
            candidate_ids: IDs válidos (após filtros)
            top_k: Número de resultados

        Returns:
            Lista de (id, score)
        """
        query_tokens = tokenize(query)
        if not query_tokens:
            return []

        # Calcula scores BM25
        scores = self.bm25.get_scores(query_tokens)

        # Filtra por candidate_ids
        results = []
        for idx, score in enumerate(scores):
            doc_id = self.bm25_doc_ids[idx]
            if doc_id in candidate_ids and score > 0:
                results.append((doc_id, score))

        # Ordena e retorna top-k
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
        """
        Combina rankings via Reciprocal Rank Fusion.

        RRF Score = Σ weight_i / (k + rank_i)

        Args:
            semantic_results: Resultados da busca semântica
            lexical_results: Resultados da busca lexical
            semantic_weight: Peso da busca semântica
            lexical_weight: Peso da busca lexical
            k: Parâmetro RRF (default 60)

        Returns:
            Lista de (id, combined_score, semantic_score, lexical_score, chunk_text)
        """
        rrf_scores = {}
        semantic_scores = {}
        lexical_scores = {}
        chunk_texts = {}

        # Processa resultados semânticos
        for rank, (doc_id, score, chunk_text) in enumerate(semantic_results):
            rrf_scores[doc_id] = rrf_scores.get(doc_id, 0) + semantic_weight / (k + rank + 1)
            semantic_scores[doc_id] = score
            chunk_texts[doc_id] = chunk_text

        # Processa resultados lexicais
        for rank, (doc_id, score) in enumerate(lexical_results):
            rrf_scores[doc_id] = rrf_scores.get(doc_id, 0) + lexical_weight / (k + rank + 1)
            lexical_scores[doc_id] = score

        # Combina e aplica source boosting
        combined = []
        for doc_id, rrf_score in rrf_scores.items():
            # Busca a fonte do documento para aplicar boost
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
        """
        Enriquece resultados com metadados do Silver.

        Args:
            ranked_results: Lista de (id, score, sem_score, lex_score, chunk_text)
            top_k: Número máximo de resultados
            semantic_weight: Peso semântico usado na busca
            lexical_weight: Peso lexical usado na busca

        Returns:
            Lista de dicionários com resultados completos
        """
        results = []

        for doc_id, score, sem_score, lex_score, chunk_text in ranked_results[:top_k]:
            # Busca metadados no Silver
            row = self.df_silver[self.df_silver["id"] == doc_id]

            if row.empty:
                continue

            row = row.iloc[0]

            # Normaliza score para 0-1 usando os pesos reais
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
            filters: Filtros opcionais:
                - source: list[str] - Ex: ["FAPESP", "CNPQ"]
                - status: str - "ABERTA" | "ENCERRADA"
                - deadline_after: str - "YYYY-MM-DD"
                - deadline_before: str - "YYYY-MM-DD"
                - location: str - UF (ex: "SP")
                - min_value: float - Valor mínimo em R$
                - max_value: float - Valor máximo em R$
            semantic_weight: Peso da busca semântica (0-1)
            lexical_weight: Peso da busca lexical (0-1)

        Returns:
            Lista de dicts com resultados rankeados
        """
        logger.info(f"Buscando: '{query}' (top_k={top_k})")

        # 1. Aplica filtros
        candidate_ids = self._apply_filters(filters)
        if not candidate_ids:
            logger.info("Nenhum documento passou nos filtros")
            return []

        logger.debug(f"Candidatos após filtros: {len(candidate_ids)}")

        # 2. Busca semântica
        semantic_results = []
        if self.embeddings is not None and semantic_weight > 0:
            query_embedding = self._embed_query(query)
            semantic_results = self._semantic_search(query_embedding, candidate_ids, top_k)
            logger.debug(f"Resultados semânticos: {len(semantic_results)}")

        # 3. Busca lexical
        lexical_results = []
        if lexical_weight > 0:
            lexical_results = self._lexical_search(query, candidate_ids, top_k)
            logger.debug(f"Resultados lexicais: {len(lexical_results)}")

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

    # Inicializa engine
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

        # Comandos especiais
        if query.lower() in ["/sair", "/exit", "/quit", "q"]:
            print("Saindo...")
            break

        if query.lower() == "/filtros":
            print("\nFiltros ativos:", current_filters if current_filters else "Nenhum")
            print("\nFiltros disponíveis:")
            print("  source: PNCP, FAPESP, CNPQ, FINEP, BNDES, LEMANN, SERRAPILHEIRA, ITAU_SOCIAL, ARAPYAU")
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

        # Executa busca
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
