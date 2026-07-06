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
import logging
import os
import time
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

# Piso no score AGREGADO (MaxSim, PR6) p/ um edital entrar no resultado. Editais que
# casam só por afinidade fraca ficam abaixo e somem. RECALIBRADO p/ a escala MaxSim
# (Σ dos máximos por nó-empresa, cosseno cru ~1-4) — o 0.30 antigo era do marginsum
# (Σ de score-threshold). Calibrado no golden matching (2026-07-04): platô recall@8
# 0.8333 preservado em [1.33, 1.39], ruído 3.0 (< 3.125 do marginsum); recall despenca
# em 1.40. 1.35 fica central, com margem do precipício.
MIN_AGGREGATE_SCORE = 1.35

# Piso AGREGADO p/ ENTIDADES (find_matching_entities). Mais baixo que o de editais
# de propósito: um Programa/Investidor casa pelo CAMINHO DIRETO (1 aresta à própria
# descrição — programas não têm nós de conteúdo no arquivo), então o piso de editais
# os mataria. RECALIBRADO p/ MaxSim (era 0.05 no marginsum): 0.60 ≈ um casamento
# direto razoável (cosseno ≥ 0.60 num nó-empresa) — em MaxSim a afinidade de 1 aresta
# É o próprio cosseno. PROVISÓRIO — sem golden de entidade ainda (ver
# [[project_hypergraph_sprint3]]).
MIN_AGGREGATE_ENTITY = 0.60

# Tipo que conta como AFINIDADE (sinal de match cross-domínio): Conceito (v2 —
# funde Tema/Tecnologia/Aplicação, D7). Mecanismo/Requisito saíram do grafo (viraram
# propriedade/constraint) — são ELEGIBILIDADE (filtro duro), não afinidade. Empírico:
# sem isso, uma agtech florestal "casa" com 100% dos editais via subvenção↔subvenção.
AFFINITY_TYPES = frozenset({"Conceito"})

# Marca de ex-Entidade reclassificada no PR2 (core/kg/migrate_v2). Em v1 Entidade
# NUNCA esteve em AFFINITY_TYPES; excluí-la aqui preserva o comportamento (a promoção
# nó-a-nó desses descritores é decisão do PR3/higiene, não desta consolidação).
_ENTIDADE_V1_ORIGEM = "entidade_v1"


def _is_affinity(node: dict, types: frozenset = AFFINITY_TYPES) -> bool:
    """True se o nó conta como afinidade: tipo ∈ `types` (vazio = qualquer) e NÃO é
    ex-Entidade v1 (inerte no match até a higiene do PR3)."""
    if node.get("origem") == _ENTIDADE_V1_ORIGEM:
        return False
    return not types or node.get("type") in types


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
    dst_kind: str = ""  # kind (slug) da entidade destino — "" se não é entidade-oferta


# kind-slug da entidade-OFERTA de um nó v2, ou None se não é entidade. Ator(ict/
# investidor) + Oportunidade(programa) são entidades do match de entidade; o edital
# (Oportunidade kind=edital) NÃO é (é o resultado do match direto de editais).
ENTITY_KINDS = frozenset({"ict", "investidor", "programa"})
_ENTITY_NODE_TYPES = frozenset({"Ator", "Oportunidade"})


def _maxsim(edges: list[SyntheticEdge]) -> float:
    """Agregação MaxSim (família ColBERT, KG v2 PR6) — chave de RANKING.

    Para cada nó DA EMPRESA (`src`), conta só a MELHOR aresta (max cosseno) até a
    oferta; `score = Σ dos máximos por nó-empresa`. Substitui o marginsum (Σ de
    TODAS as arestas acima do threshold), que inflava ofertas com muitos Conceitos
    redundantes — cada Conceito quase-duplicado da oferta somava outro termo. Com
    MaxSim um Conceito da empresa contribui UMA vez (seu melhor casamento), então a
    densidade de nós da oferta não infla o score. Escala nova (cosseno cru, não
    marginal) → pisos recalibrados (MIN_AGGREGATE_*)."""
    best_by_src: dict[str, float] = {}
    for e in edges:
        if e.score > best_by_src.get(e.src, 0.0):
            best_by_src[e.src] = e.score
    return sum(best_by_src.values())


def _entity_kind(node: dict) -> str | None:
    k = node.get("kind")
    if k in ENTITY_KINDS and node.get("type") in _ENTITY_NODE_TYPES:
        return k
    return None


def _node_text(node: dict) -> str:
    """Representação textual canônica de um nó p/ embedding — IDÊNTICA nos dois
    lados (o match é geométrico: exige a mesma função de texto em empresa e eco).

    Prefixo = descritor MAIS específico do v2 (dim → kind → type). Preserva o sinal
    semântico do type v1 e minimiza o drift dos cossenos vs. o baseline: um Conceito
    dim=tema vira `tema: X` (≈ `Tema: X` de antes), um Ator ict vira `ict: X`."""
    name = node.get("name", "")
    prefix = node.get("dim") or node.get("kind") or node.get("type", "")
    desc = node.get("description", "") or ""
    return f"{prefix}: {name}. {desc}".strip()


def load_ecosystem_nodes() -> list[tuple[str, dict]]:
    """Carrega `(file_key, node)` de todos os subgrafos do ecossistema via kg_store.

    postgres-aware: lê o blob do PG em prod (disco do worker não é fonte de verdade durável) e cai pro
    disco em dev — MESMA fonte que `_entity_attribution` usa (`load_all_hypergraphs`),
    para os dois lados do match de entidade não divergirem (disco vazio ↔ PG cheio)."""
    from core.kg import kg_store
    return [
        (fk, n)
        for fk, g in kg_store.load_all_hypergraphs().items()
        for n in g.get("nodes", [])
    ]


def _cosine_matrix(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Matriz de cossenos (C×E) entre dois conjuntos de embeddings normalizados."""
    a = a / (np.linalg.norm(a, axis=1, keepdims=True) + 1e-9)
    b = b / (np.linalg.norm(b, axis=1, keepdims=True) + 1e-9)
    return a @ b.T


def _texts_hash(texts: list[str]) -> str:
    return hashlib.sha256("\n".join(texts).encode("utf-8")).hexdigest()


def _embed_ecosystem_texts(texts: list[str], h: str) -> np.ndarray:
    """Corpo do cache em disco (.npz por hash) — ver `embed_ecosystem`."""
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
    logger.info("embed_ecosystem: %d nós embedados e cacheados → %s", len(texts), _EMB_CACHE)
    return emb


def embed_ecosystem(eco: list[tuple[str, dict]]) -> np.ndarray:
    """Embeddings dos nós do ecossistema, cacheados em disco por hash dos textos.
    Re-embeda só quando o corpus muda (ETL). Custa LLM só no cache-miss."""
    texts = [_node_text(n) for _, n in eco]
    return _embed_ecosystem_texts(texts, _texts_hash(texts))


# ---------------------------------------------------------------------------
# Caches de request (PR6.3 / F12)
# ---------------------------------------------------------------------------

# Cache in-process dos embeddings dos nós-EMPRESA por hash dos textos — simétrico
# ao .npz do ecossistema, mas em memória (o perfil é pequeno e recorrente entre
# requests do mesmo usuário). Cap FIFO simples contra crescimento sem limite.
_COMPANY_EMB_CACHE: dict[str, np.ndarray] = {}
_COMPANY_EMB_CACHE_MAX = 128


def _embed_company_nodes(company_nodes: list[dict]) -> np.ndarray:
    texts = [_node_text(n) for n in company_nodes]
    h = _texts_hash(texts)
    hit = _COMPANY_EMB_CACHE.get(h)
    if hit is not None:
        return hit
    emb = np.asarray(embed_texts(texts), dtype=np.float32)
    if len(_COMPANY_EMB_CACHE) >= _COMPANY_EMB_CACHE_MAX:
        _COMPANY_EMB_CACHE.pop(next(iter(_COMPANY_EMB_CACHE)))
    _COMPANY_EMB_CACHE[h] = emb
    return emb


# Memo de módulo para o snapshot do ecossistema: (graphs, eco_nodes, eco_emb).
# O kg_store tem TTL (60s) no blob PG, mas cada `load_all_hypergraphs` REMONTA o
# dict (em file-mode re-parseia os JSONs) e cada `embed_ecosystem` recomputa o
# hash e relê o .npz. O memo congela a montagem inteira pelo mesmo TTL e só
# re-embeda quando o hash dos textos muda (ETL). graphs/eco/emb saem do MESMO
# snapshot — o match de entidades exige eco e atribuição consistentes (disco↔PG).
# Escrita lock-free: atribuição de tupla é atômica; corrida no miss só duplica
# trabalho, nunca corrompe.
_ECO_MEMO_TTL = float(os.getenv("KG_STORE_TTL", "60"))
_eco_memo: tuple[float, str, dict[str, dict], list[tuple[str, dict]], np.ndarray] | None = None


def _ecosystem_snapshot() -> tuple[dict[str, dict], list[tuple[str, dict]], np.ndarray]:
    """(graphs, eco_nodes, eco_emb) memoizado no módulo — ver comentário acima."""
    global _eco_memo
    now = time.monotonic()
    memo = _eco_memo
    if memo is not None and (now - memo[0]) < _ECO_MEMO_TTL:
        return memo[2], memo[3], memo[4]
    from core.kg import kg_store
    graphs = kg_store.load_all_hypergraphs()
    eco = [(fk, n) for fk, g in graphs.items() for n in g.get("nodes", [])]
    if not eco:
        # Sem memo de vazio: ambiente ainda populando (backfill) tenta de novo
        # no próximo request em vez de servir vazio por um TTL inteiro.
        return graphs, [], np.empty((0, 0), dtype=np.float32)
    texts = [_node_text(n) for _, n in eco]
    h = _texts_hash(texts)
    if memo is not None and memo[1] == h:
        emb = memo[4]  # corpus idêntico — reusa sem tocar disco/API
    else:
        emb = _embed_ecosystem_texts(texts, h)
    _eco_memo = (now, h, graphs, eco, emb)
    return graphs, eco, emb


def _eco_embeddings_for(eco: list[tuple[str, dict]]) -> np.ndarray:
    """Embeddings para um `eco` explícito: reusa o memo quando é o MESMO objeto
    do snapshot (identidade); senão cai no cache em disco (`embed_ecosystem`)."""
    memo = _eco_memo
    if memo is not None and eco is memo[3]:
        return memo[4]
    return embed_ecosystem(eco)


def build_synthetic_edges(
    company_nodes: list[dict],
    *,
    threshold: float = SYNTHETIC_EDGE_THRESHOLD,
    affinity_types: frozenset = AFFINITY_TYPES,
    dst_types: frozenset | None = None,
    ecosystem: list[tuple[str, dict]] | None = None,
) -> list[SyntheticEdge]:
    """Arestas sintéticas empresa↔ecossistema por cosseno ≥ threshold.

    Filtra a EMPRESA por `affinity_types` (conteúdo) e o ECOSSISTEMA por `dst_types`
    (default = `affinity_types`, simétrico — o caso do match de editais). Tipos
    assimétricos servem ao match de ENTIDADES: empresa=conteúdo, eco=conteúdo+entidade
    (ver find_matching_entities). `frozenset()` em qualquer lado desativa aquele filtro.
    Mecanismo/Requisito ficam de fora do conteúdo (são elegibilidade, ver AFFINITY_TYPES).
    Mesmo espaço de embedding dos dois lados; sem LLM, sem score além do cosseno. Embeda
    o eco COMPLETO (cache) e filtra por máscara, para reusar o cache entre filtros."""
    if not company_nodes:
        return []
    if ecosystem is not None:
        eco = ecosystem
        eco_emb: np.ndarray | None = None  # resolvido abaixo, após o guard de vazio
    else:
        _, eco, eco_emb = _ecosystem_snapshot()
    if not eco:
        logger.warning("build_synthetic_edges: ecossistema vazio")
        return []
    dst_filter = affinity_types if dst_types is None else dst_types

    comp_emb = _embed_company_nodes(company_nodes)  # cache in-process por hash
    if eco_emb is None:
        eco_emb = _eco_embeddings_for(eco)  # memo (identidade) ou .npz por hash
    sims = _cosine_matrix(comp_emb, eco_emb)

    edges: list[SyntheticEdge] = []
    for i, cn in enumerate(company_nodes):
        if not _is_affinity(cn, affinity_types):
            continue
        for j, (fk, en) in enumerate(eco):
            if not _is_affinity(en, dst_filter):
                continue
            s = float(sims[i, j])
            if s >= threshold:
                edges.append(
                    SyntheticEdge(
                        src=cn.get("name", ""), dst=en.get("name", ""), file_key=fk,
                        src_type=cn.get("type", ""), dst_type=en.get("type", ""),
                        dst_kind=_entity_kind(en) or "", score=s,
                    )
                )
    edges.sort(key=lambda e: e.score, reverse=True)
    logger.info(
        "build_synthetic_edges: %d arestas (threshold=%.2f, afinidade=%s, empresa=%d, eco=%d)",
        len(edges), threshold, sorted(affinity_types) or "todos", len(company_nodes), len(eco),
    )
    return edges


# Damping da expansão via catálogo (ICT/investidor/programa). Um edital que
# casa com a empresa por temas diretos (Tema/Tecnologia/Aplicacao) recebe
# score sem desconto. Um edital que só casa via ICT parceira (empresa → tema
# da ICT em ict.json → edital parceiro da ICT) recebe score * damping.
CATALOG_EXPANSION_DAMPING = 0.30


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
    # Elegibilidade dura (PR5/Estágio 0): {status, unsat[], unknown[]} quando um
    # perfil foi passado ao match; None quando não avaliada. `inelegivel` NÃO
    # aparece aqui — é filtrado antes; sobram `elegivel`/`nao_verificada`.
    elegibilidade: dict | None = None

    def to_dict(self) -> dict:
        return {
            # KG v2 resíduos PR-A: discriminador p/ o ranking unificado do radar
            # (entities já têm `kind`); o front funde as duas listas num `map` só.
            "kind": "edital",
            "source": self.source,
            "edital_id": self.edital_id,
            "name": self.name,
            "score": self.score,
            "affinity": self.affinity,
            "n_paths": self.n_paths,
            "status": self.status,
            "prazo": self.prazo,
            "valor": self.valor,
            "elegibilidade": self.elegibilidade,
            "paths": [
                {"src": p.src, "dst": p.dst, "dst_type": p.dst_type, "score": p.score}
                for p in self.paths
            ],
        }


def _expand_match_via_catalog(
    matches: list[EditalMatch],
    company_nodes: list[dict],
    graphs: dict[str, dict],
    *,
    threshold: float = SYNTHETIC_EDGE_THRESHOLD,
    damping: float = CATALOG_EXPANSION_DAMPING,
    max_paths: int = 5,
) -> list[EditalMatch]:
    """Expande matches de editais via catálogo de ICTs (ADDITIVO ao match direto).

    Para cada edital já matchado, identifica suas ICTs parceiras (arestas
    `parceria_com`). Consulta o catálogo `ict.json` para descobrir que temas/
    tecnologias/aplicações essas ICTs dominam. Se a empresa casa com esses temas
    (por cosseno), adiciona paths de expansão com score * damping.

    Fiel ao Hyper-Extract: cada subgrafo é independente; a conexão entre edital
    e ICT vem da aresta `parceria_com` no subgrafo do edital, e a conexão ICT→tema
    vem das arestas nativas em `ict.json`. A "ponte" é a entidade compartilhada
    (o nome da ICT), resolvida pelo índice global de entidades.

    Uso: `matches = _expand_match_via_catalog(matches, company_nodes, graphs)`
    após `find_matching_editais`. Os matches originais mantêm score; a expansão
    adiciona paths com damping."""
    if not company_nodes or not matches:
        return matches

    ict_graph = graphs.get("ict")
    if not ict_graph:
        return matches

    # Índice de nós do catálogo ICT: id → node (v2)
    ict_by_id = {n["id"]: n for n in ict_graph.get("nodes", []) if n.get("id")}

    # Índice de arestas do catálogo ICT: id da ICT → [(tipo_aresta, [ids_membros])]
    ict_edges_by_entity: dict[str, list[tuple[str, list[str]]]] = defaultdict(list)
    for e in ict_graph.get("edges", []):
        et = e.get("type", "")
        members = e.get("members", [])  # ids (v2)
        for m in members:
            nd = ict_by_id.get(m)
            if nd and _entity_kind(nd) == "ict":
                ict_edges_by_entity[m].append((et, members))

    # Embed da empresa (só nós de afinidade) para comparar com temas do catálogo
    comp_affinity = [n for n in company_nodes if _is_affinity(n)]
    if not comp_affinity:
        return matches
    comp_emb = np.asarray(
        [embed_texts([_node_text(n)])[0] for n in comp_affinity], dtype=np.float32
    )
    comp_emb = comp_emb / (np.linalg.norm(comp_emb, axis=1, keepdims=True) + 1e-9)

    new_paths: dict[str, list[SyntheticEdge]] = defaultdict(list)

    for mt in matches:
        graph = graphs.get(mt.file_key)
        if not graph:
            continue
        # ICTs parceiras deste edital (arestas `parceria_com` no subgrafo).
        # A ligação com o catálogo ict.json é por id (`ict:{slug}`), determinístico
        # a partir de (tipo, name) — a MESMA ICT tem o mesmo id em ambos os grafos.
        graph_by_id = {n["id"]: n for n in graph.get("nodes", []) if n.get("id")}
        ict_partners: set[str] = set()
        for e in graph.get("edges", []):
            if e.get("type") != "parceria_com":
                continue
            for m in e.get("members", []):  # ids
                nd = graph_by_id.get(m)
                if nd and _entity_kind(nd) == "ict":
                    ict_partners.add(m)

        if not ict_partners:
            continue

        # Para cada ICT parceira, buscar temas no catálogo ict.json
        for ict_id in ict_partners:
            related_edges = ict_edges_by_entity.get(ict_id, [])
            for et, e_members in related_edges:
                # Tipos de aresta que conectam ICT a conteúdo temático
                if et not in {"abrange_tema", "viabiliza", "aplica_em"}:
                    continue
                # Os outros membros da aresta (que não são a ICT)
                for m in e_members:
                    if m == ict_id:
                        continue
                    m_node = ict_by_id.get(m)
                    if not m_node:
                        continue
                    if not _is_affinity(m_node):
                        continue
                    m_type = m_node.get("type", "")
                    # Embed do tema e cosseno com empresa
                    m_emb = np.asarray(
                        embed_texts([_node_text(m_node)]), dtype=np.float32
                    )
                    m_emb = m_emb / (np.linalg.norm(m_emb) + 1e-9)
                    sims = comp_emb @ m_emb.T  # (C × 1)
                    best = float(sims.max())
                    if best >= threshold:
                        new_paths[mt.file_key].append(
                            SyntheticEdge(
                                src=comp_affinity[int(sims.argmax())].get("name", ""),
                                dst=m_node.get("name", ""),
                                file_key="ict",
                                src_type=comp_affinity[int(sims.argmax())].get("type", ""),
                                dst_type=m_type,
                                score=best * damping,
                            )
                        )

    # Adiciona paths de expansão aos matches existentes
    for mt in matches:
        extra = new_paths.get(mt.file_key, [])
        if extra:
            extra.sort(key=lambda x: x.score, reverse=True)
            # Atualiza paths (preserva os originais + expansão)
            seen_dst: set[str] = set()
            merged = list(mt.paths)
            for p in extra:
                if p.dst not in seen_dst:
                    seen_dst.add(p.dst)
                    merged.append(p)
            merged.sort(key=lambda x: x.score, reverse=True)
            mt.paths = merged[:max_paths]
            # Recalcula affinity com expansão (MaxSim; paths de catálogo já com damping)
            mt.affinity = _maxsim(merged)
            mt.n_paths = len(merged)

    matches.sort(key=lambda m: m.affinity, reverse=True)
    logger.info(
        "expand_match_via_catalog: %d editais expandidos (damping=%.2f)",
        sum(1 for m in matches if new_paths.get(m.file_key)),
        damping,
    )
    return matches


def find_matching_editais(
    company_nodes: list[dict],
    *,
    threshold: float = SYNTHETIC_EDGE_THRESHOLD,
    min_aggregate: float = MIN_AGGREGATE_SCORE,
    top_k: int = 10,
    max_paths: int = 5,
    ecosystem: list[tuple[str, dict]] | None = None,
    catalog_expansion: bool = False,
    profile: object | None = None,
) -> list[EditalMatch]:
    """Match por PATH SEARCH: empresa → aresta sintética → Edital.

    Agrupa as arestas sintéticas por edital e rankeia por EVIDÊNCIA AGREGADA, não pela
    melhor aresta: `affinity = MaxSim` (PR6) — para cada nó DA EMPRESA, o melhor
    casamento com a oferta; Σ dos máximos. Um edital tematicamente DENSO (várias
    afinidades reais) vence o spike único de um nó genérico, SEM inflar por Conceitos
    redundantes da oferta (o que o marginsum — Σ de todas as arestas — fazia). Editais
    abaixo de `min_aggregate` somem (corta ruído sem matar recall). Sem LLM.

    Quando `catalog_expansion=True`, após o match geométrico, expande via catálogo
    de ICTs (ADDITIVO): identifica ICTs parceiras dos editais, consulta `ict.json`
    para temas que elas dominam, e adiciona paths com damping. Empresa → tema da
    ICT → edital parceiro da ICT.

    `profile` (PR5/Estágio 0 — FILTRO DURO de elegibilidade): quando passado (dict
    OU CompanyProfile), avalia as `constraints[]` de cada edital contra os campos
    estruturados do perfil ANTES do rank. `unsat` (incompatibilidade comprovada)
    elimina o edital; `unknown` (campo faltando no perfil) NÃO elimina — anexa a
    flag `elegibilidade` ao match para o card mostrar "não verificada". Sem
    `profile`, comportamento idêntico ao anterior (nada é filtrado).

    Catálogos (ICT/inv/prog) ficam fora do match direto (não têm '__' no file_key)."""
    # eco do snapshot memoizado (PR6.3): build_synthetic_edges reconhece o objeto
    # por identidade e reusa os embeddings sem re-hash/reload.
    eco = ecosystem if ecosystem is not None else _ecosystem_snapshot()[1]
    edges = build_synthetic_edges(company_nodes, threshold=threshold, ecosystem=eco)
    edital_node = {
        fk: n for fk, n in eco
        if n.get("type") == "Oportunidade" and n.get("kind") == "edital"
    }

    by_file: dict[str, list[SyntheticEdge]] = defaultdict(list)
    for e in edges:
        if "__" in e.file_key and e.file_key in edital_node:
            by_file[e.file_key].append(e)

    matches: list[EditalMatch] = []
    n_eliminados = 0
    for fk, es in by_file.items():
        es.sort(key=lambda x: x.score, reverse=True)
        affinity = _maxsim(es)  # MaxSim (PR6): Σ do melhor casamento por nó-empresa
        if affinity < min_aggregate:
            continue
        node = edital_node[fk]
        # Estágio 0 (PR5): filtro duro de elegibilidade. `unsat` elimina;
        # `unknown` não elimina (anexa flag). Sem perfil → elegibilidade=None.
        elegibilidade = None
        if profile is not None:
            from core.services import eligibility
            elegibilidade = eligibility.evaluate_opportunity(node.get("constraints"), profile)
            if elegibilidade["status"] == eligibility.INELEGIVEL:
                n_eliminados += 1
                continue
        source, _, native = fk.partition("__")
        matches.append(
            EditalMatch(
                file_key=fk, source=source, edital_id=native,
                name=node.get("name", ""), score=es[0].score, affinity=affinity,
                n_paths=len(es), prazo=node.get("prazo"), status=node.get("status"),
                valor=node.get("valor"), paths=es[:max_paths],
                elegibilidade=elegibilidade,
            )
        )
    matches.sort(key=lambda m: m.affinity, reverse=True)

    if catalog_expansion:
        from core.kg import kg_store
        graphs = kg_store.load_all_hypergraphs()
        matches = _expand_match_via_catalog(
            matches, company_nodes, graphs,
            threshold=threshold,
            max_paths=max_paths,
        )

    logger.info(
        "find_matching_editais: %d editais (threshold=%.2f, min_agg=%.2f, "
        "catalog_expansion=%s, elegibilidade_eliminou=%d)",
        len(matches), threshold, min_aggregate, catalog_expansion, n_eliminados,
    )
    return matches[:top_k]


# =============================================================================
# Match de ENTIDADES (investidor / programa / ICT) — irmão do match de editais
# =============================================================================
# Mesma OFERTA, agrupamento diferente: editais são 1-arquivo-cada (group by
# file_key); entidades coabitam um arquivo de catálogo (group by NÓ). Unifica o
# que o RadarService fundia (HybridMatch + EntityMatcher + ict_match) num motor só.

# Arquivos de catálogo no ecossistema (file_key SEM '__' — não são editais).
CATALOG_FILES = frozenset({"investidores", "programas", "ict"})


@dataclass
class EntityMatch:
    """Uma entidade (investidor/programa/ICT) que casa com a empresa por afinidade."""

    file_key: str               # investidores | programas | ict
    kind: str                   # slug v2: investidor | programa | ict
    name: str
    description: str | None
    score: float                # melhor cosseno — display
    affinity: float             # marginsum — chave de RANKING
    n_paths: int
    paths: list[SyntheticEdge]  # arestas-justificativa, ordenadas por score desc

    def to_dict(self) -> dict:
        return {
            "file_key": self.file_key,
            "kind": self.kind,
            "name": self.name,
            "description": self.description,
            "score": self.score,
            "affinity": self.affinity,
            "n_paths": self.n_paths,
            "paths": [
                {"src": p.src, "dst": p.dst, "dst_type": p.dst_type, "score": p.score}
                for p in self.paths
            ],
        }


def _entity_attribution(
    graphs: dict[str, dict],
) -> tuple[dict[str, dict[str, list[tuple[str, str]]]], dict[str, dict[str, tuple[str, str, str | None]]]]:
    """Pré-computa, por arquivo de catálogo:
      • attribution: nome-de-nó-de-conteúdo(lower) → [(kind_slug, nome_canônico)] donos,
        derivado das arestas nativas (uma ICT `viabiliza`/`abrange_tema` um Conceito);
      • entity_index: nome-de-entidade(lower) → (kind_slug, nome_canônico, descrição),
        para o caminho direto (Programa casa pela própria descrição).

    v2: as arestas referenciam members por `id` (não por name), então resolvemos
    cada member pelo índice id→nó do arquivo. Conceitos ex-Entidade (entidade_v1)
    não atribuem (não são afinidade)."""
    attribution: dict[str, dict[str, list[tuple[str, str]]]] = {}
    entity_index: dict[str, dict[str, tuple[str, str, str | None]]] = {}
    for fk in CATALOG_FILES:
        g = graphs.get(fk)
        if not g:
            continue
        by_id = {n["id"]: n for n in g.get("nodes", []) if n.get("id")}
        ent_idx: dict[str, tuple[str, str, str | None]] = {}
        for n in g.get("nodes", []):
            if (ek := _entity_kind(n)) is not None:
                nm = (n.get("name") or "").strip().lower()
                if nm:
                    ent_idx[nm] = (ek, n.get("name") or nm, n.get("description"))
        attr: dict[str, list[tuple[str, str]]] = defaultdict(list)
        for e in g.get("edges", []):
            ents: list[tuple[str, str]] = []
            aff_names: list[str] = []
            for m in e.get("members", []):  # ids (v2)
                nd = by_id.get(m)
                if nd is None:
                    continue
                if (ek := _entity_kind(nd)) is not None:
                    ents.append((ek, nd.get("name") or m))
                elif _is_affinity(nd):
                    aff_names.append((nd.get("name") or "").strip().lower())
            for a in aff_names:
                for ent in ents:
                    if ent not in attr[a]:
                        attr[a].append(ent)
        attribution[fk] = attr
        entity_index[fk] = ent_idx
    return attribution, entity_index


def find_matching_entities(
    company_nodes: list[dict],
    *,
    threshold: float = SYNTHETIC_EDGE_THRESHOLD,
    min_aggregate: float = MIN_AGGREGATE_ENTITY,
    top_k: int = 10,
    max_paths: int = 5,
    kinds: frozenset = ENTITY_KINDS,
    ecosystem: list[tuple[str, dict]] | None = None,
    graphs: dict[str, dict] | None = None,
) -> list[EntityMatch]:
    """Match empresa → INVESTIDOR/PROGRAMA/ICT. Irmão de `find_matching_editais`.

    Mesmo motor (build_synthetic_edges + marginsum), agrupando por NÓ de catálogo.
    Caminho duplo conforme o catálogo (ver `_entity_attribution`):
      • aresta a um nó de CONTEÚDO (Conceito de ict/investidores) → atribui à(s)
        entidade(s) dona(s) via arestas nativas — é como a ICT casa (desc é pobre);
      • aresta a um nó-ENTIDADE direto (Ator investidor/ict ou Oportunidade programa,
        descrição rica) → atribui a ele — programas não têm temas no arquivo, casam
        pela descrição.
    `kinds` filtra por slug v2 ({"ict","investidor","programa"}). Sem LLM no loop.
    `min_aggregate` usa MIN_AGGREGATE_ENTITY (mais baixo — provisório, sem golden de
    entidade; EntityMatcher legacy nunca foi gate duro)."""
    # eco e atribuição saem do MESMO snapshot (postgres-aware) — nunca misturar
    # disco↔PG (senão em prod o eco vem vazio e a atribuição não, e o match de
    # entidade devolve nada). O snapshot memoizado (PR6.3) garante isso de graça.
    if graphs is None and ecosystem is None:
        graphs, eco, _ = _ecosystem_snapshot()
    else:
        if graphs is None:
            from core.kg import kg_store
            graphs = kg_store.load_all_hypergraphs()
        eco = ecosystem if ecosystem is not None else [
            (fk, n) for fk, g in graphs.items() for n in g.get("nodes", [])
        ]
    # dst inclui os tipos-ENTIDADE (Ator/Oportunidade) além de conteúdo (Conceito)
    # → cobre o caminho direto (programas/investidores). Editais são descartados
    # abaixo por file_key (têm '__'), mesmo entrando aqui como Oportunidade.
    edges = build_synthetic_edges(
        company_nodes, threshold=threshold,
        dst_types=AFFINITY_TYPES | _ENTITY_NODE_TYPES, ecosystem=eco,
    )
    attribution, entity_index = _entity_attribution(graphs)

    by_entity: dict[tuple[str, str], list[SyntheticEdge]] = defaultdict(list)
    meta: dict[tuple[str, str], tuple[str, str | None]] = {}
    for e in edges:
        if e.file_key not in CATALOG_FILES:
            continue
        dst_lower = e.dst.strip().lower()
        if e.dst_kind:  # entidade direta (Ator investidor/ict ou Oportunidade programa)
            owners = [(e.dst_kind, e.dst)]
        else:
            owners = attribution.get(e.file_key, {}).get(dst_lower, [])  # via aresta
        for kind, name in owners:
            if kind not in kinds:
                continue
            key = (e.file_key, name)
            by_entity[key].append(e)
            if key not in meta:
                ei = entity_index.get(e.file_key, {}).get(name.strip().lower())
                meta[key] = (kind, ei[2] if ei else None)

    matches: list[EntityMatch] = []
    for (fk, name), es in by_entity.items():
        es.sort(key=lambda x: x.score, reverse=True)
        affinity = _maxsim(es)  # MaxSim (PR6): Σ do melhor casamento por nó-empresa
        if affinity < min_aggregate:
            continue
        kind, desc = meta[(fk, name)]
        matches.append(
            EntityMatch(
                file_key=fk, kind=kind, name=name, description=desc,
                score=es[0].score, affinity=affinity, n_paths=len(es), paths=es[:max_paths],
            )
        )
    matches.sort(key=lambda m: m.affinity, reverse=True)
    logger.info("find_matching_entities: %d entidades (threshold=%.2f, min_agg=%.2f)",
                len(matches), threshold, min_aggregate)
    return matches[:top_k]
