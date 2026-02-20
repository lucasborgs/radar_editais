"""
GraphRAG Search Engine

Motor de busca baseado em grafo de conhecimento.
Implementa Local Search, Global Search e DRIFT Search.

Autor: Pipeline Radar Editais
"""

import logging
import pickle
import re
from pathlib import Path
from collections import defaultdict
from typing import Optional

import pandas as pd
import networkx as nx
import numpy as np
import requests

# =============================================================================
# CONFIGURAÇÃO
# =============================================================================

SILVER_PATH = Path("silver_data")
GRAPH_PATH = Path("graph_data")
COMMUNITIES_PATH = Path("graph_data/communities")
ENTITIES_PATH = Path("graph_data/entities")

# Ollama Configuration
OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "llama3.2"

# Search Configuration
DEFAULT_TOP_K = 10
LOCAL_NEIGHBORHOOD_DEPTH = 2  # Profundidade de vizinhança para Local Search
GLOBAL_TOP_COMMUNITIES = 5    # Número de comunidades para Global Search

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger(__name__)

# =============================================================================
# INTENT CLASSIFICATION
# =============================================================================

INTENT_KEYWORDS = {
    "global": [
        "quais", "quantos", "listar", "todos", "resumo geral",
        "tendência", "comparar", "visão geral", "panorama",
        "principais", "mais comuns", "estatísticas"
    ],
    "local": [
        "específico", "detalhe", "prazo", "requisito", "valor",
        "como participar", "critério", "documento", "edital",
        "fapesp", "cnpq", "finep", "bndes", "pncp", "pipe", "sprint"
    ]
}


def classify_query_intent(query: str) -> str:
    """
    Classifica a intenção da query em 'global', 'local' ou 'drift'.

    Args:
        query: Query do usuário

    Returns:
        'global', 'local' ou 'drift'
    """
    query_lower = query.lower()

    # Conta matches por categoria
    global_score = sum(1 for kw in INTENT_KEYWORDS["global"] if kw in query_lower)
    local_score = sum(1 for kw in INTENT_KEYWORDS["local"] if kw in query_lower)

    if global_score > local_score:
        return "global"
    elif local_score > global_score:
        return "local"
    else:
        return "drift"  # Híbrido quando não é claro


# =============================================================================
# GRAPH SEARCH ENGINE
# =============================================================================

class GraphSearchEngine:
    """
    Motor de busca baseado em grafo de conhecimento.
    """

    def __init__(self):
        """Inicializa o motor de busca."""
        logger.info("Inicializando GraphSearchEngine...")

        self.graph = None
        self.df_silver = None
        self.df_entities = None
        self.df_communities = None
        self.node_to_community = {}

        self._load_data()

        logger.info("GraphSearchEngine inicializado!")

    def _load_data(self):
        """Carrega todos os dados necessários."""
        # Carregar grafo
        pickle_file = GRAPH_PATH / "knowledge_graph.pkl"
        if pickle_file.exists():
            with open(pickle_file, 'rb') as f:
                self.graph = pickle.load(f)
            logger.info(f"  → Grafo: {self.graph.number_of_nodes()} nós")
        else:
            logger.warning("Grafo não encontrado. Execute etl_graph.py")

        # Carregar Silver
        if SILVER_PATH.exists():
            self.df_silver = pd.read_parquet(SILVER_PATH)
            logger.info(f"  → Silver: {len(self.df_silver)} editais")

        # Carregar entidades
        entities_file = ENTITIES_PATH / "entities.parquet"
        if entities_file.exists():
            self.df_entities = pd.read_parquet(entities_file)
            logger.info(f"  → Entidades: {len(self.df_entities)}")

        # Carregar comunidades
        summaries_file = COMMUNITIES_PATH / "community_summaries.parquet"
        if summaries_file.exists():
            self.df_communities = pd.read_parquet(summaries_file)
            logger.info(f"  → Comunidades: {len(self.df_communities)}")

        # Carregar mapeamento nó -> comunidade
        mapping_file = COMMUNITIES_PATH / "node_community_mapping.parquet"
        if mapping_file.exists():
            df_mapping = pd.read_parquet(mapping_file)
            self.node_to_community = dict(zip(df_mapping["node_id"], df_mapping["community_id"]))

    def _find_matching_entities(self, query: str) -> list:
        """
        Encontra entidades que casam com a query.

        Args:
            query: Query do usuário

        Returns:
            Lista de entity_ids
        """
        if self.df_entities is None:
            return []

        query_lower = query.lower()
        query_words = set(re.findall(r'\w+', query_lower))

        matches = []
        for _, row in self.df_entities.iterrows():
            entity_value = row["entity_value"].lower()
            entity_words = set(re.findall(r'\w+', entity_value))

            # Match se houver interseção de palavras
            if query_words & entity_words:
                matches.append({
                    "entity_id": row["entity_id"],
                    "entity_type": row["entity_type"],
                    "entity_value": row["entity_value"],
                    "match_score": len(query_words & entity_words) / len(query_words)
                })

        # Ordena por score
        matches.sort(key=lambda x: x["match_score"], reverse=True)
        return matches[:10]  # Top 10 entidades

    def _get_edital_details(self, edital_id: str) -> dict:
        """Retorna detalhes de um edital."""
        if self.df_silver is None:
            return {}

        row = self.df_silver[self.df_silver["id"] == edital_id]
        if row.empty:
            return {}

        row = row.iloc[0]
        return {
            "id": edital_id,
            "title": row.get("title", ""),
            "description": str(row.get("description", ""))[:500],
            "source": row.get("source", ""),
            "url": row.get("url", ""),
            "deadline_date": str(row.get("deadline_date", "")),
            "status": row.get("status", ""),
            "value_brl": row.get("value_brl"),
        }

    # =========================================================================
    # LOCAL SEARCH
    # =========================================================================

    def local_search(self, query: str, top_k: int = DEFAULT_TOP_K) -> list:
        """
        Busca local: encontra entidades relevantes e expande vizinhança.

        Args:
            query: Query do usuário
            top_k: Número de resultados

        Returns:
            Lista de editais ordenados por relevância
        """
        if self.graph is None:
            return []

        logger.info(f"Local Search: '{query}'")

        # 1. Encontra entidades matching
        matching_entities = self._find_matching_entities(query)
        logger.info(f"  → {len(matching_entities)} entidades encontradas")

        if not matching_entities:
            return []

        # 2. Expande vizinhança para cada entidade
        edital_scores = defaultdict(float)

        for entity in matching_entities:
            entity_id = entity["entity_id"]

            if not self.graph.has_node(entity_id):
                continue

            # Busca vizinhos (editais conectados a esta entidade)
            for neighbor in self.graph.neighbors(entity_id):
                node_data = self.graph.nodes.get(neighbor, {})

                if node_data.get("node_type") == "EDITAL":
                    # Pontuação baseada no match score da entidade
                    edital_scores[neighbor] += entity["match_score"]

            # Expande para vizinhos de segundo grau (editais similares)
            if LOCAL_NEIGHBORHOOD_DEPTH >= 2:
                for neighbor in self.graph.neighbors(entity_id):
                    if self.graph.nodes.get(neighbor, {}).get("node_type") == "EDITAL":
                        for second_neighbor in self.graph.neighbors(neighbor):
                            if self.graph.nodes.get(second_neighbor, {}).get("node_type") == "EDITAL":
                                # Peso menor para vizinhos de segundo grau
                                edge_data = self.graph.get_edge_data(neighbor, second_neighbor, {})
                                if edge_data.get("edge_type") == "SIMILAR_TO":
                                    edital_scores[second_neighbor] += entity["match_score"] * 0.5

        # 3. Ordena e retorna top_k
        sorted_editais = sorted(edital_scores.items(), key=lambda x: x[1], reverse=True)

        results = []
        for edital_id, score in sorted_editais[:top_k]:
            details = self._get_edital_details(edital_id)
            if details:
                details["score"] = round(min(score, 1.0), 4)
                details["search_type"] = "local"
                results.append(details)

        logger.info(f"  → {len(results)} resultados")
        return results

    # =========================================================================
    # GLOBAL SEARCH
    # =========================================================================

    def global_search(self, query: str, top_k: int = DEFAULT_TOP_K) -> dict:
        """
        Busca global: usa resumos de comunidades para resposta agregada.

        Args:
            query: Query do usuário
            top_k: Número de editais por comunidade

        Returns:
            Dicionário com comunidades relevantes e seus editais
        """
        if self.df_communities is None:
            return {"communities": [], "editais": []}

        logger.info(f"Global Search: '{query}'")

        query_lower = query.lower()
        query_words = set(re.findall(r'\w+', query_lower))

        # 1. Pontua comunidades por relevância
        community_scores = []

        for _, row in self.df_communities.iterrows():
            score = 0

            # Match em tópicos
            topics = row.get("top_topics", [])
            if isinstance(topics, list):
                for topic in topics:
                    topic_words = set(re.findall(r'\w+', topic.lower()))
                    if query_words & topic_words:
                        score += 2

            # Match em resumo
            summary = str(row.get("summary", "")).lower()
            for word in query_words:
                if word in summary:
                    score += 0.5

            if score > 0:
                community_scores.append({
                    "community_id": row["community_id"],
                    "n_editais": row["n_editais"],
                    "sources": row.get("sources", {}),
                    "top_topics": topics,
                    "summary": row.get("summary", ""),
                    "edital_ids": row.get("edital_ids", []),
                    "score": score
                })

        # 2. Ordena comunidades
        community_scores.sort(key=lambda x: x["score"], reverse=True)
        top_communities = community_scores[:GLOBAL_TOP_COMMUNITIES]

        logger.info(f"  → {len(top_communities)} comunidades relevantes")

        # 3. Coleta editais das top comunidades
        all_editais = []
        for comm in top_communities:
            edital_ids = comm.get("edital_ids", [])
            for eid in edital_ids[:top_k]:
                details = self._get_edital_details(eid)
                if details:
                    details["community_id"] = comm["community_id"]
                    details["score"] = round(comm["score"] / 10, 4)
                    details["search_type"] = "global"
                    all_editais.append(details)

        # Deduplica
        seen = set()
        unique_editais = []
        for ed in all_editais:
            if ed["id"] not in seen:
                seen.add(ed["id"])
                unique_editais.append(ed)

        logger.info(f"  → {len(unique_editais)} editais")

        return {
            "communities": top_communities,
            "editais": unique_editais[:top_k]
        }

    # =========================================================================
    # DRIFT SEARCH (Híbrido)
    # =========================================================================

    def drift_search(self, query: str, top_k: int = DEFAULT_TOP_K) -> list:
        """
        DRIFT Search: combina Local e Global.

        Usa contexto de entidade (Local) + agregação de comunidade (Global).

        Args:
            query: Query do usuário
            top_k: Número de resultados

        Returns:
            Lista de editais
        """
        logger.info(f"DRIFT Search: '{query}'")

        # 1. Busca local
        local_results = self.local_search(query, top_k=top_k)

        # 2. Busca global
        global_results = self.global_search(query, top_k=top_k)

        # 3. Combina resultados (RRF-style)
        edital_scores = {}

        # Local: peso maior
        for i, result in enumerate(local_results):
            eid = result["id"]
            edital_scores[eid] = edital_scores.get(eid, 0) + 1.0 / (60 + i + 1)
            edital_scores[eid] *= 1.5  # Boost local

        # Global: peso base
        for i, result in enumerate(global_results.get("editais", [])):
            eid = result["id"]
            edital_scores[eid] = edital_scores.get(eid, 0) + 1.0 / (60 + i + 1)

        # 4. Ordena e retorna
        sorted_ids = sorted(edital_scores.items(), key=lambda x: x[1], reverse=True)

        results = []
        for eid, score in sorted_ids[:top_k]:
            details = self._get_edital_details(eid)
            if details:
                details["score"] = round(score, 4)
                details["search_type"] = "drift"
                results.append(details)

        logger.info(f"  → {len(results)} resultados combinados")
        return results

    # =========================================================================
    # INTERFACE PRINCIPAL
    # =========================================================================

    def search(
        self,
        query: str,
        search_type: str = "auto",
        top_k: int = DEFAULT_TOP_K
    ) -> dict:
        """
        Interface principal de busca.

        Args:
            query: Query do usuário
            search_type: 'local', 'global', 'drift' ou 'auto'
            top_k: Número de resultados

        Returns:
            Dicionário com resultados e metadados
        """
        # Auto-detect intent
        if search_type == "auto":
            search_type = classify_query_intent(query)
            logger.info(f"Intent detectado: {search_type}")

        # Executa busca apropriada
        if search_type == "local":
            results = self.local_search(query, top_k)
            return {
                "query": query,
                "search_type": "local",
                "results": results,
                "communities": None
            }

        elif search_type == "global":
            global_result = self.global_search(query, top_k)
            return {
                "query": query,
                "search_type": "global",
                "results": global_result["editais"],
                "communities": global_result["communities"]
            }

        else:  # drift
            results = self.drift_search(query, top_k)
            return {
                "query": query,
                "search_type": "drift",
                "results": results,
                "communities": None
            }

    def get_community_summary(self, community_id: int) -> Optional[str]:
        """Retorna o resumo de uma comunidade."""
        if self.df_communities is None:
            return None

        row = self.df_communities[self.df_communities["community_id"] == community_id]
        if row.empty:
            return None

        return row.iloc[0].get("summary", "")


# =============================================================================
# CLI INTERATIVO
# =============================================================================

def main():
    """CLI interativo para testar GraphRAG Search."""
    print("=" * 60)
    print("GRAPHRAG SEARCH ENGINE - Radar de Editais")
    print("=" * 60)

    engine = GraphSearchEngine()

    print("\nComandos:")
    print("  /local <query>    - Busca local (entidades + vizinhança)")
    print("  /global <query>   - Busca global (comunidades)")
    print("  /drift <query>    - Busca híbrida")
    print("  /auto <query>     - Auto-detect intent")
    print("  /sair             - Sair")
    print()

    while True:
        try:
            user_input = input("\n🔍 Query: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nSaindo...")
            break

        if not user_input:
            continue

        if user_input.lower() in ["/sair", "/exit", "q"]:
            print("Saindo...")
            break

        # Parse comando
        if user_input.startswith("/local "):
            query = user_input[7:]
            search_type = "local"
        elif user_input.startswith("/global "):
            query = user_input[8:]
            search_type = "global"
        elif user_input.startswith("/drift "):
            query = user_input[7:]
            search_type = "drift"
        elif user_input.startswith("/auto "):
            query = user_input[6:]
            search_type = "auto"
        else:
            query = user_input
            search_type = "auto"

        # Executa busca
        result = engine.search(query, search_type=search_type)

        # Mostra resultados
        print(f"\n{'='*60}")
        print(f"Tipo de busca: {result['search_type'].upper()}")
        print(f"Resultados: {len(result['results'])}")

        if result.get("communities"):
            print(f"\nComunidades relevantes:")
            for comm in result["communities"][:3]:
                topics = ", ".join(comm.get("top_topics", [])[:3])
                print(f"  - Comunidade {comm['community_id']}: {comm['n_editais']} editais ({topics})")

        print(f"\nTop Editais:")
        for i, ed in enumerate(result["results"][:5], 1):
            print(f"\n{i}. [{ed['score']:.3f}] {ed['title'][:70]}")
            print(f"   Fonte: {ed['source']} | Status: {ed['status']}")
            if ed.get("deadline_date"):
                print(f"   Prazo: {ed['deadline_date']}")


if __name__ == "__main__":
    main()
