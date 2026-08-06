"""core/kg/spike/tools.py — tools de explore do spike (flag KG_SPIKE_ENABLED=1).

Esconde atrás de `KG_SPIKE_ENABLED`: flag off = comportamento do sistema
intocado. `graph_explore` devolve o subgrafo estrutura-consciente; `graph_reason`
devolve caminhos de dedução (incluindo do perfil efêmero até a entidade);
`graph_community` devolve os membros e qualidades compartilhadas de um cluster.
"""
from __future__ import annotations

import logging
import os
import unicodedata
from typing import Any

from langchain_core.tools import tool as langchain_tool

from radar.core.kg.spike import graph_store, serialize
from radar.core.kg.spike.traverse import find_paths

logger = logging.getLogger(__name__)

# Arestas com peso abaixo deste corte não expandem a vizinhança (hub
# `setor:multissetorial` recebe weight=0.1 no ingest — SPEC §7 opção 4).
MIN_WEIGHT = 0.5


def enabled() -> bool:
    return os.environ.get("KG_SPIKE_ENABLED", "0").lower() in {"1", "true", "yes"}


def build_spike_tools(profile: dict[str, Any] | None = None) -> list[Any]:
    """Tools de explore do spike, embrulhadas como BaseTool.

    Só devolve tools quando `KG_SPIKE_ENABLED` é verdadeiro — flag off = lista
    vazia (comportamento do ExploreAgent intocado).

    `profile`: perfil estruturado da empresa capturado por closure (Design B —
    o nó `empresa:efemera` é montado em memória por `graph_reason`). Sem ele, a
    dedução `paths_to_profile` não tem âncora. Mesmo padrão do `build_match_tools`.
    """
    if not enabled():
        return []

    def _reason(entity_ref: str, max_depth: int = 4) -> str:
        return graph_reason(entity_ref, profile=profile, max_depth=max_depth)

    _reason.__name__ = "graph_reason"
    _reason.__doc__ = graph_reason.__doc__

    return [
        langchain_tool(graph_explore),
        langchain_tool(_reason),
        langchain_tool(graph_community),
    ]


def _load() -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], dict[str, list[str]]]:
    """(edges, nodes, quality, communities) — carga única por chamada (barata)."""
    return (
        graph_store.load_edges(),
        graph_store.load_nodes(),
        graph_store.load_quality_nodes(),
        graph_store.load_communities(),
    )


def _resolve(entity_ref: str, nodes: list[dict[str, Any]], quality: list[dict[str, Any]]) -> str | None:
    """Resolve id exato ou sufixo (native) → node_id do spike."""
    ref = (entity_ref or "").strip()
    if not ref:
        return None
    for n in nodes:
        if n["id"] == ref or n["native_id"] == ref or n["native_id"].endswith(f":{ref}"):
            return n["id"]
    for q in quality:
        if q["id"] == ref or q["value"].lower() == ref.lower():
            return q["id"]
    return None


def graph_explore(entity_ref: str, depth: int = 1, max_nodes: int = 60) -> str:
    """Vizinhança estrutural de uma entidade do grafo (BFS até `depth` saltos).

    Retorna um JSON estrutura-consciente: nós (substâncias + qualidades),
    arestas tipadas com peso, e comunidades. Preserva a topologia — o grafo
    sobrevive à viagem ao token space sem aplainar em texto.
    """
    if not enabled():
        return "KG spike desabilitado (KG_SPIKE_ENABLED=1 para ativar)."
    edges, nodes, quality, communities = _load()
    seed = _resolve(entity_ref, nodes, quality)
    if seed is None:
        return f"Nenhuma entidade '{entity_ref}' no grafo do spike."
    sub = serialize.serialize_subgraph(
        seed, edges, nodes, quality, depth=int(depth),
        communities=communities, max_nodes=int(max_nodes), min_weight=MIN_WEIGHT,
    )
    return serialize.dump(sub)


def graph_reason(entity_ref: str, profile: dict[str, Any] | None = None, max_depth: int = 4) -> str:
    """Dedução de caminho no grafo: caminhos que conectam a entidade ao perfil
    (Design B — nó de empresa efêmero) e caminhos internos de interesse.

    O perfil efêmero ancora `:empresa → atua_em → setor → tem_setor → entidade`
    (SPEC §11 `paths_to_profile`). Quando embrulhada pelo ExploreAgent, o
    `profile` é injetado por closure — o LLM NÃO precisa (nem deve) preenchê-lo.
    """
    if not enabled():
        return "KG spike desabilitado (KG_SPIKE_ENABLED=1 para ativar)."
    edges, nodes, quality, communities = _load()
    seed = _resolve(entity_ref, nodes, quality)
    if seed is None:
        return f"Nenhuma entidade '{entity_ref}' no grafo do spike."

    # Perfil efêmero (Design B): cria arestas atua_em/... em memória, sem persistir.
    profile_edges: list[dict[str, Any]] = []
    profile_start: str | None = None
    if profile:
        profile_edges, profile_start = _profile_edges(profile, quality)
        all_edges = [*edges, *profile_edges]
    else:
        all_edges = edges

    out: dict[str, Any] = {}
    if profile_start:
        paths = find_paths(all_edges, profile_start, seed, max_depth=int(max_depth), limit=5, min_weight=MIN_WEIGHT)
        out["paths_to_profile"] = serialize.enrich_paths(paths, all_edges, nodes, quality)
        if paths:
            out["deduction"] = serialize.paths_to_prose(paths)

    # Caminhos internos de alto valor: entidade → ICTs alcançáveis em
    # `max_depth`. Seleção: todas as ICTs, sem duplicatas, mais curtos primeiro,
    # empate estável por ID do destino — sem score/ranking/heurística.
    ict_ids = [n["id"] for n in nodes if n["kind"] == "ict"]
    internal = _select_internal_paths(
        all_edges, seed, ict_ids, max_depth=int(max_depth),
        min_weight=MIN_WEIGHT, limit=3,
    )
    out["internal_paths"] = serialize.enrich_paths(internal, all_edges, nodes, quality)
    return serialize.dump(out)


def _resolve_community(community_ref: str, communities: dict[str, list[str]]) -> str | None:
    """Resolve `com_11`, `comunidade:11`, `community 11` ou `11` → community_id."""
    ref = (community_ref or "").strip().lower()
    if not ref:
        return None
    candidates = [ref]
    for sep in ("comunidade:", "community:", "com:"):
        if ref.startswith(sep):
            candidates.append(ref[len(sep):])
            break
    for cand in candidates:
        if cand in communities:
            return cand
        for cid in communities:
            c = cid.lower()
            if c == cand or c.endswith(f":{cand}") or c.endswith(f"_{cand}"):
                return cid
    return None


def graph_community(community_ref: str) -> str:
    """Membros e cola de uma COMUNIDADE (cluster Louvain) do grafo.

    Use quando o usuário citar uma comunidade (ex.: "com_11", "comunidade 11")
    ou pedir o "cluster"/o que agrupa um conjunto de entidades. Devolve JSON:
    id, nº de membros, membros agrupados por kind e as QUALIDADES COMPARTILHADAS
    (setores/tecnologias/estágios que vários membros têm em comum) — a "cola"
    do cluster.
    """
    if not enabled():
        return "KG spike desabilitado (KG_SPIKE_ENABLED=1 para ativar)."
    communities = graph_store.load_communities()
    cid = _resolve_community(community_ref, communities)
    if cid is None:
        available = sorted(communities)[:10]
        return (
            f"Comunidade '{community_ref}' não encontrada. "
            f"Comunidades disponíveis (amostra): {', '.join(available)}."
        )

    members = communities[cid]
    nodes = graph_store.load_nodes()
    quality = graph_store.load_quality_nodes()
    edges = graph_store.load_edges()
    by_id = serialize.build_node_map(nodes, quality)

    # Arestas INTERNAS da comunidade (ambas as pontas no cluster) — payload com
    # `source_id`/`target_id`/`type`/`weight`/`source`/`properties`.
    member_set = set(members)
    internal = [
        serialize.edge_payload(e)
        for e in edges
        if e["source_id"] in member_set and e["target_id"] in member_set
    ]

    # Qualidades compartilhadas: nós de qualidade dentro da comunidade que
    # conectam ≥2 substâncias do cluster via aresta interna.
    shared: dict[str, int] = {}
    for e in edges:
        if e["source_id"] not in member_set or e["target_id"] not in member_set:
            continue
        if e["type"] in {"tem_setor", "tem_tecnologia", "busca_estagio", "tem_uf", "usa_mecanismo", "tem_trl_faixa"}:
            shared[e["target_id"]] = shared.get(e["target_id"], 0) + 1

    # Membros agrupados por kind (entidade) / family (qualidade) — objetos
    # resolvidos, não nomes crus.
    grouped: dict[str, list[dict[str, Any]]] = {}
    for m in serialize.community_members(members, by_id):
        key = m.get("family") or m.get("kind")
        if not key:
            continue
        grouped.setdefault(key, []).append(m)
    grouped = {k: sorted(v, key=lambda x: x["id"]) for k, v in sorted(grouped.items())}

    return serialize.dump({
        "community_id": cid,
        "n_members": len(members),
        "members_by_kind": grouped,
        "n_internal_edges": len(internal),
        "edge_types": sorted({e["type"] for e in internal}),
        "shared_qualities": sorted(
            serialize.shared_quality_payloads(shared, by_id),
            key=lambda x: -x["member_count"],
        )[:12],
    })


def _select_internal_paths(
    edges: list[dict[str, Any]],
    seed: str,
    ict_ids: list[str],
    *,
    max_depth: int,
    min_weight: float = 0.0,
    limit: int = 3,
) -> list[list[tuple[str, str, str]]]:
    """Seleção dos caminhos internos entidade → ICT.

    Coleta candidatos para TODAS as ICTs alcançáveis em `max_depth`, descarta
    caminhos duplicados e ordena por (nº de saltos, ID do destino) — ordenação
    estável. Sem score, ranking semântico, LLM ou heurística textual. `limit`
    corta o payload (3 por padrão). Relações derivadas (`similar_a`,
    `potencial_parceria`) não são descartadas nem promovidas a fato — a
    classificação `source`/`properties` chega à serialização intacta.
    """
    seen: set[tuple[tuple[str, str, str], ...]] = set()
    candidates: list[tuple[int, str, list[tuple[str, str, str]]]] = []
    for goal in sorted(ict_ids):
        for path in find_paths(edges, seed, goal, max_depth=max_depth, limit=limit, min_weight=min_weight):
            key = tuple(path)
            if key in seen:
                continue
            seen.add(key)
            candidates.append((len(path), goal, list(path)))
    candidates.sort(key=lambda c: (c[0], c[1]))
    return [path for _, _, path in candidates[:limit]]


def _profile_edges(profile: dict[str, Any], quality: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], str]:
    """Arestas do perfil efêmero (Design B): empresa → nós de QUALIDADE que JÁ
    EXISTEM no grafo. Nada é persistido. Retorna (edges, node_id_empresa).

    Valores do perfil só viram `atua_em` quando há um nó de qualidade
    compatível: setor → `setor:<valor>`, tecnologia → `tecnologia:<valor>`;
    tema usa nó de setor existente. Sem nó compatível, a conexão não existe.
    """
    node_id = "empresa:efemera"
    existing = {q["id"] for q in quality}
    edges: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    def _atua(family: str, values: list[str]) -> None:
        for v in values:
            vid = f"{family}:{_normalize(v) or 'sem-valor'}"
            if vid not in existing or (vid, "atua_em") in seen:
                continue
            seen.add((vid, "atua_em"))
            edges.append({"source_id": node_id, "target_id": vid, "type": "atua_em", "weight": 1.0})

    _atua("setor", _as_list(profile.get("setores")) + _as_list(profile.get("tema")))
    _atua("tecnologia", _as_list(profile.get("tecnologias")))
    return edges, node_id


def _normalize(value: str) -> str:
    """Normaliza valor do perfil para casar com o id de nó de qualidade do
    ingest (mesmo critério de `_q`/`_deburr` do ingest): NFC-deburr + lower."""
    return "".join(
        c for c in unicodedata.normalize("NFKD", value or "").lower()
        if not unicodedata.combining(c)
    ).strip()


def _as_list(v: Any) -> list[str]:
    if isinstance(v, list):
        return [str(x) for x in v if x]
    if v:
        return [str(v)]
    return []
