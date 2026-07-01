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

# Piso AGREGADO p/ ENTIDADES (find_matching_entities). Mais baixo que o de editais
# de propósito: um Programa/Investidor casa pelo CAMINHO DIRETO (1 aresta à própria
# descrição — programas não têm nós de conteúdo no arquivo), então o piso de editais
# (0.30 ≈ exige cosseno ≥ 0.85 numa aresta só) os mataria. 0.05 deixa passar o
# direto razoável (cosseno ≥ 0.60) e ainda discrimina. PROVISÓRIO — sem golden de
# entidade ainda (ver [[project-hypergraph-sprint3]]).
MIN_AGGREGATE_ENTITY = 0.05

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
    """Carrega `(file_key, node)` de todos os subgrafos do ecossistema via kg_store.

    postgres-aware: lê o blob do PG em prod (disco do Railway é efêmero) e cai pro
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
    eco = ecosystem if ecosystem is not None else load_ecosystem_nodes()
    if not eco:
        logger.warning("build_synthetic_edges: ecossistema vazio")
        return []
    dst_filter = affinity_types if dst_types is None else dst_types

    comp_emb = np.asarray(embed_texts([_node_text(n) for n in company_nodes]), dtype=np.float32)
    eco_emb = embed_ecosystem(eco)  # completo, cacheado por hash dos textos
    sims = _cosine_matrix(comp_emb, eco_emb)

    def _ok(node: dict, types: frozenset) -> bool:
        return not types or node.get("type") in types

    edges: list[SyntheticEdge] = []
    for i, cn in enumerate(company_nodes):
        if not _ok(cn, affinity_types):
            continue
        for j, (fk, en) in enumerate(eco):
            if not _ok(en, dst_filter):
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

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "edital_id": self.edital_id,
            "name": self.name,
            "score": self.score,
            "affinity": self.affinity,
            "n_paths": self.n_paths,
            "status": self.status,
            "prazo": self.prazo,
            "valor": self.valor,
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

    # Índice de nós do catálogo ICT: name_lower → node
    ict_idx = {
        (n.get("name") or "").strip().lower(): n
        for n in ict_graph.get("nodes", [])
        if n.get("name")
    }

    # Índice de arestas do catálogo ICT: nome ICT(lower) → [(tipo_aresta, [membros])]
    ict_edges_by_entity: dict[str, list[tuple[str, list[str]]]] = defaultdict(list)
    for e in ict_graph.get("edges", []):
        et = e.get("type", "")
        members = [(m or "").strip().lower() for m in e.get("members", [])]
        for m in members:
            if m in ict_idx and ict_idx[m].get("type") == "ICT":
                ict_edges_by_entity[m].append((et, members))

    # Embed da empresa (só nós de afinidade) para comparar com temas do catálogo
    comp_affinity = [n for n in company_nodes if n.get("type") in AFFINITY_TYPES]
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
        # ICTs parceiras deste edital (arestas `parceria_com` no subgrafo)
        ict_partners: set[str] = set()
        for e in graph.get("edges", []):
            if e.get("type") != "parceria_com":
                continue
            members = [(m or "").strip().lower() for m in e.get("members", [])]
            # O nó Edital é um dos members; os outros são ICTs/Investidores
            for m in members:
                node_type = ""
                for n in graph.get("nodes", []):
                    if (n.get("name") or "").strip().lower() == m:
                        node_type = n.get("type", "")
                        break
                if node_type == "ICT":
                    ict_partners.add(m)

        if not ict_partners:
            continue

        # Para cada ICT parceira, buscar temas no catálogo ict.json
        for ict_name in ict_partners:
            related_edges = ict_edges_by_entity.get(ict_name, [])
            for et, e_members in related_edges:
                # Tipos de aresta que conectam ICT a conteúdo temático
                if et not in {"abrange_tema", "viabiliza", "aplica_em"}:
                    continue
                # Os outros membros da aresta (que não são a ICT)
                for m in e_members:
                    if m == ict_name:
                        continue
                    m_node = ict_idx.get(m)
                    if not m_node:
                        continue
                    m_type = m_node.get("type", "")
                    if m_type not in AFFINITY_TYPES:
                        continue
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
            # Recalcula affinity com expansão (marginsum com damping)
            mt.affinity = sum(x.score - threshold for x in merged)
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
) -> list[EditalMatch]:
    """Match por PATH SEARCH: empresa → aresta sintética → Edital.

    Agrupa as arestas sintéticas por edital e rankeia por EVIDÊNCIA AGREGADA, não pela
    melhor aresta: `affinity = Σ(cosseno − threshold)` sobre as arestas (marginsum). Um
    edital tematicamente DENSO (várias afinidades reais) vence o spike único de um nó
    boilerplate genérico — o max-cosseno deixava «defesa»↔«PROPOSTA»=0.71 soterrar o
    match real. Editais abaixo de `min_aggregate` somem (corta ruído sem matar recall).
    Calibrado na suíte `matching`: recall@8 0.80→0.88, ruído 4.2→3.6. Sem LLM.

    Quando `catalog_expansion=True`, após o match geométrico, expande via catálogo
    de ICTs (ADDITIVO): identifica ICTs parceiras dos editais, consulta `ict.json`
    para temas que elas dominam, e adiciona paths com damping. Empresa → tema da
    ICT → edital parceiro da ICT.

    Catálogos (ICT/inv/prog) ficam fora do match direto (não têm '__' no file_key)."""
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

    if catalog_expansion:
        from core.kg import kg_store
        graphs = kg_store.load_all_hypergraphs()
        matches = _expand_match_via_catalog(
            matches, company_nodes, graphs,
            threshold=threshold,
            max_paths=max_paths,
        )

    logger.info("find_matching_editais: %d editais (threshold=%.2f, min_agg=%.2f, catalog_expansion=%s)",
                len(matches), threshold, min_aggregate, catalog_expansion)
    return matches[:top_k]


# =============================================================================
# Match de ENTIDADES (investidor / programa / ICT) — irmão do match de editais
# =============================================================================
# Mesma OFERTA, agrupamento diferente: editais são 1-arquivo-cada (group by
# file_key); entidades coabitam um arquivo de catálogo (group by NÓ). Unifica o
# que o RadarService fundia (HybridMatch + EntityMatcher + ict_match) num motor só.

# Tipos de nó que SÃO uma entidade-oferta (não conteúdo).
ENTITY_TYPES = frozenset({"Investidor", "Programa", "ICT"})

# Arquivos de catálogo no ecossistema (file_key SEM '__' — não são editais).
CATALOG_FILES = frozenset({"investidores", "programas", "ict"})


@dataclass
class EntityMatch:
    """Uma entidade (investidor/programa/ICT) que casa com a empresa por afinidade."""

    file_key: str               # investidores | programas | ict
    kind: str                   # Investidor | Programa | ICT
    name: str
    description: str | None
    score: float                # melhor cosseno — display
    affinity: float             # marginsum — chave de RANKING
    n_paths: int
    paths: list[SyntheticEdge]  # arestas-justificativa, ordenadas por score desc


def _entity_attribution(
    graphs: dict[str, dict],
) -> tuple[dict[str, dict[str, list[tuple[str, str]]]], dict[str, dict[str, tuple[str, str, str | None]]]]:
    """Pré-computa, por arquivo de catálogo:
      • attribution: nome-de-nó-de-conteúdo(lower) → [(kind, nome_canônico)] donos,
        derivado das arestas nativas (uma ICT `viabiliza`/`abrange_tema` um Tema);
      • entity_index: nome-de-entidade(lower) → (kind, nome_canônico, descrição),
        para o caminho direto (Programa casa pela própria descrição).
    """
    attribution: dict[str, dict[str, list[tuple[str, str]]]] = {}
    entity_index: dict[str, dict[str, tuple[str, str, str | None]]] = {}
    for fk in CATALOG_FILES:
        g = graphs.get(fk)
        if not g:
            continue
        type_by: dict[str, str] = {}
        name_by: dict[str, str] = {}
        ent_idx: dict[str, tuple[str, str, str | None]] = {}
        for n in g.get("nodes", []):
            nm = (n.get("name") or "").strip().lower()
            if not nm:
                continue
            t = n.get("type") or ""
            type_by[nm] = t
            name_by[nm] = n.get("name") or nm
            if t in ENTITY_TYPES:
                ent_idx[nm] = (t, n.get("name") or nm, n.get("description"))
        attr: dict[str, list[tuple[str, str]]] = defaultdict(list)
        for e in g.get("edges", []):
            members = [(x or "").strip().lower() for x in e.get("members", [])]
            ents = [(type_by[x], name_by[x]) for x in members if type_by.get(x) in ENTITY_TYPES]
            for a in (x for x in members if type_by.get(x) in AFFINITY_TYPES):
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
    kinds: frozenset = ENTITY_TYPES,
    ecosystem: list[tuple[str, dict]] | None = None,
    graphs: dict[str, dict] | None = None,
) -> list[EntityMatch]:
    """Match empresa → INVESTIDOR/PROGRAMA/ICT. Irmão de `find_matching_editais`.

    Mesmo motor (build_synthetic_edges + marginsum), agrupando por NÓ de catálogo.
    Caminho duplo conforme o catálogo (ver `_entity_attribution`):
      • aresta a um nó de CONTEÚDO (Tema/Tec/Aplic de ict/investidores) → atribui à(s)
        entidade(s) dona(s) via arestas nativas — é como a ICT casa (desc é pobre);
      • aresta a um nó-ENTIDADE direto (descrição rica do Programa/Investidor) → atribui
        a ele — programas não têm temas no arquivo, casam pela descrição.
    Sem LLM no loop. `min_aggregate` usa MIN_AGGREGATE_ENTITY (mais baixo — provisório,
    sem golden de entidade; EntityMatcher legacy nunca foi gate duro)."""
    from core.kg import kg_store

    # eco e atribuição saem do MESMO load_all_hypergraphs (postgres-aware) — nunca
    # misturar disco↔PG (senão em prod o eco vem vazio e a atribuição não, e o match
    # de entidade devolve nada).
    graphs = graphs if graphs is not None else kg_store.load_all_hypergraphs()
    eco = ecosystem if ecosystem is not None else [
        (fk, n) for fk, g in graphs.items() for n in g.get("nodes", [])
    ]
    # dst inclui ENTIDADE além de conteúdo → cobre o caminho direto (programas).
    edges = build_synthetic_edges(
        company_nodes, threshold=threshold,
        dst_types=AFFINITY_TYPES | ENTITY_TYPES, ecosystem=eco,
    )
    attribution, entity_index = _entity_attribution(graphs)

    by_entity: dict[tuple[str, str], list[SyntheticEdge]] = defaultdict(list)
    meta: dict[tuple[str, str], tuple[str, str | None]] = {}
    for e in edges:
        if e.file_key not in CATALOG_FILES:
            continue
        dst_lower = e.dst.strip().lower()
        if e.dst_type in ENTITY_TYPES:
            owners = [(e.dst_type, e.dst)]                              # direto
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
        affinity = sum(x.score - threshold for x in es)  # marginsum
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
