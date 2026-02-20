"""
ETL Grafo de Conhecimento (Radar de Editais)

Constrói grafo NetworkX a partir dos dados ENRIQUECIDOS (não via LLM).
Usa campos estruturados extraídos pelo etl_enrichment.py.

Nós: EDITAL, THEME, SOURCE, REQUIREMENT
Arestas: HAS_THEME, FROM_SOURCE, REQUIRES, SIMILAR_TO
"""

import logging
import pickle
from pathlib import Path
from datetime import datetime
from collections import defaultdict

import pandas as pd
import networkx as nx

# =============================================================================
# CONFIGURAÇÃO
# =============================================================================

ENRICHED_PATH = Path("silver_data_enriched")
ENRICHED_FILE = ENRICHED_PATH / "editais_enriched.parquet"
GRAPH_PATH = Path("graph_data")

SIMILARITY_THRESHOLD = 0.25

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger(__name__)


# =============================================================================
# CONSTRUÇÃO DO GRAFO
# =============================================================================

def _safe_list(val) -> list:
    if val is None:
        return []
    import numpy as np
    if isinstance(val, np.ndarray):
        return val.tolist()
    if isinstance(val, list):
        return val
    if isinstance(val, str):
        return [val] if val else []
    try:
        if pd.isna(val):
            return []
    except (TypeError, ValueError):
        pass
    return list(val) if hasattr(val, '__iter__') else []


def build_graph(df: pd.DataFrame) -> nx.Graph:
    """Constrói grafo de conhecimento a partir de dados enriquecidos."""
    G = nx.Graph()

    # 1. Nós de SOURCE (fontes)
    sources = df["source"].unique()
    for source in sources:
        G.add_node(f"SRC:{source}", node_type="SOURCE", label=source)

    # 2. Para cada edital
    theme_counter = defaultdict(int)
    requirement_counter = defaultdict(int)

    for _, row in df.iterrows():
        edital_id = row["id"]
        source = row.get("source", "")
        title = str(row.get("title", ""))[:100]
        status = str(row.get("status", ""))
        url = str(row.get("url", ""))
        category = str(row.get("category", ""))

        deadline = row.get("deadline_date")
        deadline_str = ""
        if deadline is not None and not (isinstance(deadline, float) and pd.isna(deadline)):
            deadline_str = str(deadline)[:10] if hasattr(deadline, "isoformat") else str(deadline)

        # Nó EDITAL
        G.add_node(
            edital_id,
            node_type="EDITAL",
            title=title,
            source=source,
            status=status,
            url=url,
            category=category,
            deadline_date=deadline_str,
        )

        # Aresta FROM_SOURCE
        G.add_edge(edital_id, f"SRC:{source}", edge_type="FROM_SOURCE", weight=1.0)

        # Nós e arestas de THEME
        themes = _safe_list(row.get("themes"))
        for theme in themes:
            theme_id = f"THEME:{theme.lower().strip()}"
            if not G.has_node(theme_id):
                G.add_node(theme_id, node_type="THEME", label=theme.strip())
            G.add_edge(edital_id, theme_id, edge_type="HAS_THEME", weight=1.0)
            theme_counter[theme.lower().strip()] += 1

        # Nós e arestas de REQUIREMENT (porte, certificações, CNAEs)
        for porte in _safe_list(row.get("required_porte")):
            req_id = f"REQ:porte:{porte.upper()}"
            if not G.has_node(req_id):
                G.add_node(req_id, node_type="REQUIREMENT", req_type="porte", label=porte.upper())
            G.add_edge(edital_id, req_id, edge_type="REQUIRES", weight=1.0)
            requirement_counter[f"porte:{porte.upper()}"] += 1

        for cert in _safe_list(row.get("required_certifications")):
            req_id = f"REQ:cert:{cert.lower().strip()}"
            if not G.has_node(req_id):
                G.add_node(req_id, node_type="REQUIREMENT", req_type="certificacao", label=cert.strip())
            G.add_edge(edital_id, req_id, edge_type="REQUIRES", weight=1.0)
            requirement_counter[f"cert:{cert.lower().strip()}"] += 1

        for cnae in _safe_list(row.get("required_cnaes")):
            req_id = f"REQ:cnae:{cnae.strip()}"
            if not G.has_node(req_id):
                G.add_node(req_id, node_type="REQUIREMENT", req_type="cnae", label=cnae.strip())
            G.add_edge(edital_id, req_id, edge_type="ACCEPTS_CNAE", weight=1.0)

        for etype in _safe_list(row.get("eligible_entity_types")):
            req_id = f"REQ:entity:{etype.lower().strip()}"
            if not G.has_node(req_id):
                G.add_node(req_id, node_type="REQUIREMENT", req_type="entity_type", label=etype.strip())
            G.add_edge(edital_id, req_id, edge_type="REQUIRES", weight=1.0)

    # 3. Arestas THEME co-occurrence
    logger.info("Calculando co-ocorrência de temas...")
    edital_themes = {}
    for _, row in df.iterrows():
        themes = _safe_list(row.get("themes"))
        if themes:
            edital_themes[row["id"]] = {f"THEME:{t.lower().strip()}" for t in themes}

    theme_cooccurrence = defaultdict(int)
    for eid, themes_set in edital_themes.items():
        themes_list = sorted(themes_set)
        for i in range(len(themes_list)):
            for j in range(i + 1, len(themes_list)):
                theme_cooccurrence[(themes_list[i], themes_list[j])] += 1

    for (t1, t2), count in theme_cooccurrence.items():
        if count >= 2:  # Pelo menos 2 editais em comum
            G.add_edge(t1, t2, edge_type="CO_OCCURS", weight=count / 10.0)

    # 4. Arestas SIMILAR_TO entre editais (por temas compartilhados)
    logger.info("Calculando similaridade entre editais...")
    candidate_pairs = set()
    theme_to_editais = defaultdict(set)
    for eid, themes_set in edital_themes.items():
        for t in themes_set:
            theme_to_editais[t].add(eid)

    for t, editais_set in theme_to_editais.items():
        editais_list = sorted(editais_set)
        for i in range(len(editais_list)):
            for j in range(i + 1, len(editais_list)):
                candidate_pairs.add((editais_list[i], editais_list[j]))

    sim_count = 0
    for ed1, ed2 in candidate_pairs:
        t1 = edital_themes.get(ed1, set())
        t2 = edital_themes.get(ed2, set())
        if t1 and t2:
            jaccard = len(t1 & t2) / len(t1 | t2)
            if jaccard >= SIMILARITY_THRESHOLD:
                G.add_edge(ed1, ed2, edge_type="SIMILAR_TO", weight=jaccard)
                sim_count += 1

    logger.info(f"  → {sim_count} arestas de similaridade")

    return G, theme_counter, requirement_counter


def compute_stats(G: nx.Graph) -> dict:
    """Estatísticas do grafo."""
    node_types = defaultdict(int)
    for _, d in G.nodes(data=True):
        node_types[d.get("node_type", "UNKNOWN")] += 1

    edge_types = defaultdict(int)
    for _, _, d in G.edges(data=True):
        edge_types[d.get("edge_type", "UNKNOWN")] += 1

    return {
        "total_nodes": G.number_of_nodes(),
        "total_edges": G.number_of_edges(),
        "node_types": dict(node_types),
        "edge_types": dict(edge_types),
        "density": nx.density(G),
        "connected_components": nx.number_connected_components(G),
    }


# =============================================================================
# ORQUESTRADOR
# =============================================================================

def main():
    """Pipeline de construção do grafo de conhecimento."""
    logger.info("=" * 60)
    logger.info("INICIANDO ETL GRAFO DE CONHECIMENTO")
    logger.info("=" * 60)

    start_time = datetime.now()

    if not ENRICHED_FILE.exists():
        logger.error(f"Dados enriquecidos não encontrados: {ENRICHED_FILE}")
        logger.error("Execute primeiro: python etl_enrichment.py")
        return

    # Carregar dados
    df = pd.read_parquet(ENRICHED_FILE)
    logger.info(f"Carregados {len(df)} editais enriquecidos")

    # Construir grafo
    G, theme_counter, req_counter = build_graph(df)

    # Estatísticas
    stats = compute_stats(G)
    logger.info("-" * 60)
    logger.info("ESTATÍSTICAS DO GRAFO:")
    for ntype, count in stats["node_types"].items():
        logger.info(f"  Nós {ntype}: {count}")
    for etype, count in stats["edge_types"].items():
        logger.info(f"  Arestas {etype}: {count}")
    logger.info(f"  Densidade: {stats['density']:.4f}")
    logger.info(f"  Componentes: {stats['connected_components']}")

    logger.info("\nTop temas:")
    for theme, count in sorted(theme_counter.items(), key=lambda x: -x[1])[:15]:
        logger.info(f"  {theme}: {count} editais")

    # Salvar
    GRAPH_PATH.mkdir(parents=True, exist_ok=True)

    graphml_file = GRAPH_PATH / "knowledge_graph.graphml"
    nx.write_graphml(G, graphml_file)
    logger.info(f"Grafo salvo: {graphml_file}")

    pkl_file = GRAPH_PATH / "knowledge_graph.pkl"
    with open(pkl_file, "wb") as f:
        pickle.dump(G, f)
    logger.info(f"Grafo (pickle): {pkl_file}")

    elapsed = datetime.now() - start_time
    logger.info(f"Tempo: {elapsed}")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
