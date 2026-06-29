"""Match cross-domínio via hipergrado (Sprint 1).

Não há serviço de match com LLM no loop — o match é GEOMÉTRICO. Os nós da empresa
(lado DEMANDA) e do ecossistema (lado OFERTA) vivem no MESMO espaço de embedding
porque saem do mesmo extractor/embedder (ver core/retrieval/hyper_extractor). Aqui:

- `build_synthetic_edges`  liga os dois lados por cosseno > threshold (F1)
- `find_matching_editais`  path search empresa → aresta sintética → edital (F2)

O grafo da empresa é privado/efêmero (computado on-demand); o do ecossistema é o KG
global durável. A "sobreposição" é o conjunto de arestas sintéticas, não uma fusão.
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass

import numpy as np

from core.retrieval.embedder import embed_texts
from core.retrieval.hyper_extractor import HYPERGRAPHS_DIR

logger = logging.getLogger(__name__)

# Embeddings do ecossistema mudam só quando o ETL re-extrai → cacheável por hash
# dos textos. Evita re-embeddar 4.6k nós a cada match/iteração de threshold.
_EMB_CACHE = HYPERGRAPHS_DIR.parent / "graph" / "ecosystem_embeddings.npz"

# Threshold do cosseno p/ materializar uma aresta sintética. PROVISÓRIO: 0.60 vem
# do teste iFlorestal (matches de conteúdo vivem em ~0.55-0.73; 0.80 era calibrado
# p/ o ruído estrutural removido). Calibrar formalmente com 10-20 pares anotados.
SYNTHETIC_EDGE_THRESHOLD = 0.60

# Tipos que contam como AFINIDADE (sinal de match cross-domínio). Mecanismo e
# Requisito ficam de FORA de propósito: são estruturais (todo edital tem
# "subvenção"/"TRL"), casam com cosseno altíssimo e afogam o sinal de conteúdo —
# eles são ELEGIBILIDADE (filtro duro), não afinidade. Empírico: sem isso, uma
# agtech florestal "casa" com 100% dos editais via subvenção↔subvenção.
AFFINITY_TYPES = frozenset({"Tema", "Tecnologia", "Aplicação"})


@dataclass
class SyntheticEdge:
    """Aresta empresa↔ecossistema por similaridade. `file_key` é a proveniência
    (subgrafo de origem do nó ecossistema) — o elo do path search até o edital."""

    src: str        # nome do nó empresa
    dst: str        # nome do nó ecossistema
    file_key: str   # subgrafo de origem (ex.: finep__589, ict)
    src_type: str
    dst_type: str
    score: float


def _node_text(node: dict) -> str:
    """Representação textual canônica de um nó p/ embedding — IDÊNTICA nos dois
    lados (o match é geométrico: exige a mesma função de texto em empresa e eco)."""
    name = node.get("name", "")
    typ = node.get("type", "")
    desc = node.get("description", "") or ""
    return f"{typ}: {name}. {desc}".strip()


def load_ecosystem_nodes() -> list[tuple[str, dict]]:
    """Carrega `(file_key, node)` de todos os subgrafos do ecossistema.

    F1: lê do disco (HYPERGRAPHS_DIR). TODO: migrar p/ kg_store (PG) ao ir pra prod
    — disco do Railway é efêmero (mesmo débito do cache de extração)."""
    out: list[tuple[str, dict]] = []
    for p in sorted(HYPERGRAPHS_DIR.glob("*.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 — JSON corrompido não derruba o match
            logger.warning("load_ecosystem_nodes: JSON inválido %s", p)
            continue
        for n in data.get("nodes", []):
            out.append((p.stem, n))
    return out


def _cosine_matrix(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Matriz de cossenos (C×E) entre dois conjuntos de embeddings normalizados."""
    a = a / (np.linalg.norm(a, axis=1, keepdims=True) + 1e-9)
    b = b / (np.linalg.norm(b, axis=1, keepdims=True) + 1e-9)
    return a @ b.T


def embed_ecosystem(eco: list[tuple[str, dict]]) -> np.ndarray:
    """Embeddings dos nós do ecossistema, cacheados em disco por hash dos textos.
    Re-embeda só quando o corpus muda (ETL). Custa LLM só no cache-miss."""
    texts = [_node_text(n) for _, n in eco]
    h = hashlib.sha256("\n".join(texts).encode("utf-8")).hexdigest()
    if _EMB_CACHE.exists():
        try:
            z = np.load(_EMB_CACHE)
            if str(z["texts_hash"]) == h:
                return z["emb"]
        except Exception:  # noqa: BLE001 — cache corrompido → re-embeda
            pass
    emb = np.asarray(embed_texts(texts), dtype=np.float32)
    _EMB_CACHE.parent.mkdir(parents=True, exist_ok=True)
    np.savez(_EMB_CACHE, emb=emb, texts_hash=np.array(h))
    logger.info("embed_ecosystem: %d nós embedados e cacheados → %s", len(eco), _EMB_CACHE)
    return emb


def build_synthetic_edges(
    company_nodes: list[dict],
    *,
    threshold: float = SYNTHETIC_EDGE_THRESHOLD,
    affinity_types: frozenset = AFFINITY_TYPES,
    ecosystem: list[tuple[str, dict]] | None = None,
) -> list[SyntheticEdge]:
    """Arestas sintéticas empresa↔ecossistema por cosseno ≥ threshold.

    Só liga nós cujo tipo está em `affinity_types` (conteúdo) — Mecanismo/Requisito
    são elegibilidade, não afinidade (ver AFFINITY_TYPES). `affinity_types=frozenset()`
    desativa o filtro. Mesmo espaço de embedding dos dois lados (mesmo embedder); sem
    LLM, sem score além do cosseno. Embeda o eco COMPLETO (cache) e filtra por máscara,
    para reusar o cache independentemente do filtro de tipo."""
    if not company_nodes:
        return []
    eco = ecosystem if ecosystem is not None else load_ecosystem_nodes()
    if not eco:
        logger.warning("build_synthetic_edges: ecossistema vazio")
        return []

    comp_emb = np.asarray(embed_texts([_node_text(n) for n in company_nodes]), dtype=np.float32)
    eco_emb = embed_ecosystem(eco)  # completo, cacheado por hash dos textos
    sims = _cosine_matrix(comp_emb, eco_emb)

    def _ok(node: dict) -> bool:
        return not affinity_types or node.get("type") in affinity_types

    edges: list[SyntheticEdge] = []
    for i, cn in enumerate(company_nodes):
        if not _ok(cn):
            continue
        for j, (fk, en) in enumerate(eco):
            if not _ok(en):
                continue
            s = float(sims[i, j])
            if s >= threshold:
                edges.append(
                    SyntheticEdge(
                        src=cn.get("name", ""), dst=en.get("name", ""), file_key=fk,
                        src_type=cn.get("type", ""), dst_type=en.get("type", ""), score=s,
                    )
                )
    edges.sort(key=lambda e: e.score, reverse=True)
    logger.info(
        "build_synthetic_edges: %d arestas (threshold=%.2f, afinidade=%s, empresa=%d, eco=%d)",
        len(edges), threshold, sorted(affinity_types) or "todos", len(company_nodes), len(eco),
    )
    return edges
