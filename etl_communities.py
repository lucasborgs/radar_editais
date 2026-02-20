"""
ETL Communities - Clustering e Sumarização de Comunidades

Aplica o algoritmo Leiden para detectar comunidades no grafo
e gera resumos usando LLM para cada comunidade.

Cache de sumários: reutiliza resumos se a composição da comunidade
não mudou significativamente (>10% dos IDs).
"""

import json
import hashlib
import logging
import pickle
from pathlib import Path
from datetime import datetime
from collections import defaultdict

import pandas as pd
import networkx as nx
import requests

# Leiden Algorithm
try:
    import igraph as ig
    import leidenalg
    HAS_LEIDEN = True
except ImportError:
    HAS_LEIDEN = False

# =============================================================================
# CONFIGURAÇÃO
# =============================================================================

GRAPH_PATH = Path("graph_data")
COMMUNITIES_PATH = Path("graph_data/communities")
CACHE_FILE = COMMUNITIES_PATH / "summary_cache.json"

# Ollama Configuration
OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "llama3.2"

# Leiden Parameters
RESOLUTION = 1.0

# Cache: threshold de mudança para invalidar sumário (Jaccard < 0.9 = >10% mudança)
CACHE_JACCARD_THRESHOLD = 0.9

# Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger(__name__)

# =============================================================================
# PROMPT DE SUMARIZAÇÃO
# =============================================================================

SUMMARY_PROMPT = """Analise a seguinte comunidade de editais de fomento e crie um resumo executivo.

COMUNIDADE: {community_id}
NÚMERO DE EDITAIS: {n_editais}
FONTES: {sources}

TÓPICOS PRINCIPAIS:
{topics}

PÚBLICO-ALVO:
{audiences}

DOMÍNIOS/SETORES:
{domains}

EDITAIS DA COMUNIDADE:
{editais}

Crie um resumo de 2-3 parágrafos que:
1. Descreva o tema central que une os editais desta comunidade
2. Identifique o perfil ideal de candidato
3. Destaque as principais oportunidades e prazos

RESUMO:"""

# =============================================================================
# CACHE DE SUMÁRIOS
# =============================================================================

def _load_summary_cache() -> dict:
    """Carrega cache de sumários de comunidades."""
    if CACHE_FILE.exists():
        try:
            return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save_summary_cache(cache: dict) -> None:
    """Salva cache de sumários."""
    COMMUNITIES_PATH.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(
        json.dumps(cache, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )


def _hash_edital_ids(edital_ids: list[str]) -> str:
    """Gera hash da lista ordenada de IDs."""
    content = "|".join(sorted(edital_ids))
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]


def _jaccard_similarity(set_a: set, set_b: set) -> float:
    """Calcula similaridade de Jaccard entre dois conjuntos."""
    if not set_a and not set_b:
        return 1.0
    if not set_a or not set_b:
        return 0.0
    intersection = len(set_a & set_b)
    union = len(set_a | set_b)
    return intersection / union if union > 0 else 0.0


def _get_cached_summary(
    cache: dict,
    edital_ids: list[str],
) -> str | None:
    """
    Busca sumário no cache. Retorna None se não encontrado ou se
    a comunidade mudou significativamente.
    """
    current_ids_set = set(edital_ids)
    current_hash = _hash_edital_ids(edital_ids)

    # Busca por hash exato (match perfeito)
    if current_hash in cache:
        return cache[current_hash]["summary"]

    # Busca por similaridade: se alguma entrada tem Jaccard >= threshold, reutiliza
    for cached_hash, cached_data in cache.items():
        cached_ids_set = set(cached_data.get("edital_ids", []))
        similarity = _jaccard_similarity(current_ids_set, cached_ids_set)
        if similarity >= CACHE_JACCARD_THRESHOLD:
            logger.info(f"    Cache hit (Jaccard={similarity:.2f})")
            return cached_data["summary"]

    return None


# =============================================================================
# LEIDEN CLUSTERING
# =============================================================================

def networkx_to_igraph(G: nx.Graph) -> 'ig.Graph':
    node_list = list(G.nodes())
    node_to_idx = {n: i for i, n in enumerate(node_list)}
    edges = [(node_to_idx[u], node_to_idx[v]) for u, v in G.edges()]
    weights = [G[u][v].get("weight", 1.0) for u, v in G.edges()]
    ig_graph = ig.Graph(n=len(node_list), edges=edges, directed=False)
    ig_graph.vs["name"] = node_list
    ig_graph.es["weight"] = weights
    return ig_graph


def detect_communities_leiden(G: nx.Graph, resolution: float = RESOLUTION) -> dict:
    if not HAS_LEIDEN:
        logger.error("leidenalg não instalado. Execute: pip install leidenalg python-igraph")
        return {}
    ig_graph = networkx_to_igraph(G)
    partition = leidenalg.find_partition(
        ig_graph,
        leidenalg.RBConfigurationVertexPartition,
        weights="weight",
        resolution_parameter=resolution,
        seed=42
    )
    node_to_community = {}
    for community_id, community in enumerate(partition):
        for node_idx in community:
            node_name = ig_graph.vs[node_idx]["name"]
            node_to_community[node_name] = community_id
    return node_to_community


def detect_communities_fallback(G: nx.Graph) -> dict:
    from networkx.algorithms.community import louvain_communities
    communities = louvain_communities(G, seed=42, weight="weight")
    node_to_community = {}
    for community_id, community in enumerate(communities):
        for node in community:
            node_to_community[node] = community_id
    return node_to_community


# =============================================================================
# ANÁLISE DE COMUNIDADES
# =============================================================================

def analyze_community(
    G: nx.Graph,
    community_nodes: list,
    community_id: int
) -> dict:
    editais = []
    entities_by_type = defaultdict(list)

    for node in community_nodes:
        node_data = G.nodes[node]
        node_type = node_data.get("node_type", "")
        if node_type == "EDITAL":
            editais.append({
                "id": node,
                "title": node_data.get("title", ""),
                "source": node_data.get("source", ""),
                "deadline_date": node_data.get("deadline_date", ""),
                "status": node_data.get("status", "")
            })
        else:
            entities_by_type[node_type].append(node_data.get("value", ""))

    sources = defaultdict(int)
    for ed in editais:
        sources[ed["source"]] += 1

    return {
        "community_id": community_id,
        "n_nodes": len(community_nodes),
        "n_editais": len(editais),
        "editais": editais,
        "sources": dict(sources),
        "topics": entities_by_type.get("TOPICS", []),
        "audiences": entities_by_type.get("AUDIENCES", []),
        "domains": entities_by_type.get("DOMAINS", []),
        "requirements": entities_by_type.get("REQUIREMENTS", []),
        "funding_types": entities_by_type.get("FUNDING_TYPES", []),
    }


# =============================================================================
# SUMARIZAÇÃO
# =============================================================================

def call_ollama(prompt: str, timeout: int = 180) -> str:
    try:
        response = requests.post(
            OLLAMA_URL,
            json={
                "model": OLLAMA_MODEL,
                "prompt": prompt,
                "stream": False,
                "options": {
                    "temperature": 0.3,
                    "num_predict": 500,
                }
            },
            timeout=timeout
        )
        if response.status_code == 200:
            return response.json().get("response", "")
        return ""
    except Exception as e:
        logger.warning(f"Erro ao chamar Ollama: {e}")
        return ""


def generate_community_summary(analysis: dict) -> str:
    editais_text = ""
    for ed in analysis["editais"][:10]:
        editais_text += f"- [{ed['source']}] {ed['title']}"
        if ed["deadline_date"]:
            editais_text += f" (Prazo: {ed['deadline_date']})"
        editais_text += "\n"

    topics_text = ", ".join(analysis["topics"][:10]) if analysis["topics"] else "Não identificados"
    audiences_text = ", ".join(analysis["audiences"][:5]) if analysis["audiences"] else "Não identificado"
    domains_text = ", ".join(analysis["domains"][:5]) if analysis["domains"] else "Não identificados"
    sources_text = ", ".join([f"{k} ({v})" for k, v in analysis["sources"].items()])

    prompt = SUMMARY_PROMPT.format(
        community_id=analysis["community_id"],
        n_editais=analysis["n_editais"],
        sources=sources_text,
        topics=topics_text,
        audiences=audiences_text,
        domains=domains_text,
        editais=editais_text
    )

    return call_ollama(prompt)


# =============================================================================
# ORQUESTRADOR
# =============================================================================

def main():
    """Pipeline de detecção de comunidades e sumarização com cache."""
    logger.info("=" * 60)
    logger.info("INICIANDO ETL COMMUNITIES (GraphRAG)")
    logger.info("=" * 60)

    start_time = datetime.now()

    # 1. Carregar grafo
    pickle_file = GRAPH_PATH / "knowledge_graph.pkl"
    if not pickle_file.exists():
        logger.error(f"Grafo não encontrado: {pickle_file}")
        logger.error("Execute etl_graph.py primeiro")
        return

    logger.info("Carregando grafo...")
    with open(pickle_file, 'rb') as f:
        G = pickle.load(f)
    logger.info(f"  → {G.number_of_nodes()} nós, {G.number_of_edges()} arestas")

    # 2. Remover nós isolados
    isolated = list(nx.isolates(G))
    if isolated:
        logger.info(f"Removendo {len(isolated)} nós isolados...")
        G.remove_nodes_from(isolated)
        logger.info(f"  → Grafo reduzido: {G.number_of_nodes()} nós, {G.number_of_edges()} arestas")

    # 3. Detectar comunidades
    logger.info("Detectando comunidades com Leiden...")
    if HAS_LEIDEN:
        node_to_community = detect_communities_leiden(G)
    else:
        logger.warning("Leiden não disponível, usando Louvain (fallback)")
        node_to_community = detect_communities_fallback(G)

    n_communities = len(set(node_to_community.values()))
    logger.info(f"  → {n_communities} comunidades detectadas")

    # 4. Analisar comunidades
    logger.info("Analisando comunidades...")
    community_nodes = defaultdict(list)
    for node, comm_id in node_to_community.items():
        community_nodes[comm_id].append(node)

    analyses = []
    for comm_id, nodes in community_nodes.items():
        analysis = analyze_community(G, nodes, comm_id)
        analyses.append(analysis)

    analyses.sort(key=lambda x: x["n_editais"], reverse=True)

    # Log resumo
    logger.info("-" * 60)
    logger.info("TOP 10 COMUNIDADES:")
    for i, a in enumerate(analyses[:10]):
        sources_str = ", ".join([f"{k}:{v}" for k, v in a["sources"].items()])
        topics_str = ", ".join(a["topics"][:3]) if a["topics"] else "N/A"
        logger.info(f"  {i+1}. Comunidade {a['community_id']}: {a['n_editais']} editais")
        logger.info(f"      Fontes: {sources_str}")
        logger.info(f"      Tópicos: {topics_str}")

    # 5. Gerar resumos com cache
    logger.info("-" * 60)
    logger.info("Gerando resumos das comunidades (top 20, com cache)...")

    summary_cache = _load_summary_cache()
    new_cache = {}
    cache_hits = 0
    cache_misses = 0

    summaries = []
    for analysis in analyses[:20]:
        if analysis["n_editais"] < 2:
            continue

        edital_ids = [ed["id"] for ed in analysis["editais"]]

        # Tenta buscar no cache
        cached_summary = _get_cached_summary(summary_cache, edital_ids)

        if cached_summary:
            summary = cached_summary
            cache_hits += 1
            logger.info(f"  Comunidade {analysis['community_id']}: cache hit")
        else:
            logger.info(f"  Sumarizando comunidade {analysis['community_id']}...")
            summary = generate_community_summary(analysis)
            cache_misses += 1

        # Atualiza cache
        id_hash = _hash_edital_ids(edital_ids)
        new_cache[id_hash] = {
            "summary": summary,
            "edital_ids": edital_ids,
            "generated_at": datetime.now().isoformat(),
        }

        summaries.append({
            "community_id": analysis["community_id"],
            "n_editais": analysis["n_editais"],
            "n_nodes": analysis["n_nodes"],
            "sources": analysis["sources"],
            "top_topics": analysis["topics"][:5],
            "top_audiences": analysis["audiences"][:3],
            "top_domains": analysis["domains"][:3],
            "summary": summary,
            "edital_ids": edital_ids
        })

    # 6. Salvar cache
    _save_summary_cache(new_cache)
    logger.info(f"  Cache: {cache_hits} hits, {cache_misses} misses")

    # 7. Salvar resultados
    COMMUNITIES_PATH.mkdir(parents=True, exist_ok=True)

    df_mapping = pd.DataFrame([
        {"node_id": node, "community_id": comm}
        for node, comm in node_to_community.items()
    ])
    mapping_file = COMMUNITIES_PATH / "node_community_mapping.parquet"
    df_mapping.to_parquet(mapping_file, index=False)
    logger.info(f"Mapeamento salvo em {mapping_file}")

    df_summaries = pd.DataFrame(summaries)
    summaries_file = COMMUNITIES_PATH / "community_summaries.parquet"
    df_summaries.to_parquet(summaries_file, index=False)
    logger.info(f"Resumos salvos em {summaries_file}")

    analyses_file = COMMUNITIES_PATH / "community_analyses.pkl"
    with open(analyses_file, 'wb') as f:
        pickle.dump(analyses, f)
    logger.info(f"Análises salvas em {analyses_file}")

    # 8. Finalização
    elapsed = datetime.now() - start_time

    logger.info("=" * 60)
    logger.info("ESTATÍSTICAS FINAIS:")
    logger.info(f"  Comunidades totais: {n_communities}")
    logger.info(f"  Comunidades com resumo: {len(summaries)}")
    logger.info(f"  Cache hits: {cache_hits}, Cache misses: {cache_misses}")
    logger.info(f"  Maior comunidade: {analyses[0]['n_editais']} editais")
    logger.info(f"  Média editais/comunidade: {sum(a['n_editais'] for a in analyses)/len(analyses):.1f}")

    logger.info("=" * 60)
    logger.info("ETL COMMUNITIES CONCLUÍDO")
    logger.info(f"Tempo de execução: {elapsed}")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
