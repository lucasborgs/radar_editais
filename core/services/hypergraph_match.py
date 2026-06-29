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
from collections import defaultdict
from dataclasses import dataclass

import numpy as np

from core.retrieval.embedder import embed_texts
from core.retrieval.hyper_extractor import HYPERGRAPHS_DIR

logger = logging.getLogger(__name__)

# Embeddings do ecossistema mudam só quando o ETL re-extrai → cacheável por hash
# dos textos. Evita re-embeddar 4.6k nós a cada match/iteração de threshold.
_EMB_CACHE = HYPERGRAPHS_DIR.parent / "graph" / "ecosystem_embeddings.npz"

# Threshold do cosseno p/ materializar uma aresta sintética. Calibrado contra o
# golden de afinidade (eval_data/golden/matching.json, suíte `matching`): recall@8 é
# plano de 0.45 a 0.60 (os positivos vivem em ~0.65-0.74), então o threshold é só um
# corte de cauda. 0.55 deixa mais arestas p/ o marginsum agregar (ver abaixo).
SYNTHETIC_EDGE_THRESHOLD = 0.55

# Piso no score AGREGADO (marginsum) p/ um edital entrar no resultado. Editais que
# casam só por uma aresta fraca de boilerplate ficam abaixo e somem (corta ruído sem
# matar recall: golden recall@8=0.88 estável até 0.30, despenca em 0.40).
MIN_AGGREGATE_SCORE = 0.30

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


@dataclass
class EditalMatch:
    """Um edital que casa com a empresa, com as arestas-justificativa e as
    propriedades de display (do nó Edital)."""

    file_key: str          # finep__589
    source: str            # finep
    edital_id: str         # 589 (nativo)
    name: str              # título da chamada
    score: float           # melhor afinidade (max cosseno entre as arestas) — display
    affinity: float        # score agregado (marginsum) — a chave de RANKING
    n_paths: int           # nº de arestas de conteúdo que conectam
    prazo: str | None
    status: str | None
    valor: str | None
    paths: list[SyntheticEdge]  # arestas-justificativa, ordenadas por score desc


def find_matching_editais(
    company_nodes: list[dict],
    *,
    threshold: float = SYNTHETIC_EDGE_THRESHOLD,
    min_aggregate: float = MIN_AGGREGATE_SCORE,
    top_k: int = 10,
    max_paths: int = 5,
    ecosystem: list[tuple[str, dict]] | None = None,
) -> list[EditalMatch]:
    """Match por PATH SEARCH: empresa → aresta sintética → Edital.

    Agrupa as arestas sintéticas por edital e rankeia por EVIDÊNCIA AGREGADA, não pela
    melhor aresta: `affinity = Σ(cosseno − threshold)` sobre as arestas (marginsum). Um
    edital tematicamente DENSO (várias afinidades reais) vence o spike único de um nó
    boilerplate genérico — o max-cosseno deixava «defesa»↔«PROPOSTA»=0.71 soterrar o
    match real. Editais abaixo de `min_aggregate` somem (corta ruído sem matar recall).
    Calibrado na suíte `matching`: recall@8 0.80→0.88, ruído 4.2→3.6. Sem LLM.

    Catálogos (ICT/inv/prog) ficam fora (não têm '__' no file_key) — são oferta-
    entidade, não chamada."""
    eco = ecosystem if ecosystem is not None else load_ecosystem_nodes()
    edges = build_synthetic_edges(company_nodes, threshold=threshold, ecosystem=eco)
    edital_node = {fk: n for fk, n in eco if n.get("type") == "Edital"}

    by_file: dict[str, list[SyntheticEdge]] = defaultdict(list)
    for e in edges:
        if "__" in e.file_key and e.file_key in edital_node:
            by_file[e.file_key].append(e)

    matches: list[EditalMatch] = []
    for fk, es in by_file.items():
        es.sort(key=lambda x: x.score, reverse=True)
        affinity = sum(e.score - threshold for e in es)  # marginsum
        if affinity < min_aggregate:
            continue
        node = edital_node[fk]
        source, _, native = fk.partition("__")
        matches.append(
            EditalMatch(
                file_key=fk, source=source, edital_id=native,
                name=node.get("name", ""), score=es[0].score, affinity=affinity,
                n_paths=len(es), prazo=node.get("prazo"), status=node.get("status"),
                valor=node.get("valor"), paths=es[:max_paths],
            )
        )
    matches.sort(key=lambda m: m.affinity, reverse=True)
    logger.info("find_matching_editais: %d editais (threshold=%.2f, min_agg=%.2f)",
                len(matches), threshold, min_aggregate)
    return matches[:top_k]
