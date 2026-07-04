"""core/kg/migrate_v2.py — migração mecânica dos hipergrados para o schema v2.

Spec docs/specs/kg-redesign.md. Transformação PURA e idempotente em DOIS passos,
composta por `migrate_to_v2`:

  • FORMATO (PR1, `_migrate_format`): IDs estáveis prefixados + arestas ligadas
    por `id` (não mais por name-string lowercased). Estado A (v1) → B (format v2,
    tipos v1).
  • TIPOS (PR2, `consolidate_to_v2_types`): consolida os 12 tipos v1 em 3 entidades
    (Oportunidade/Ator/Conceito), fold de Fonte/Mecanismo/Requisito/Exclusão em
    propriedades, remap dos prefixos de id (ed:/tema:→op:/con:). Estado B → C (v2).

Usada por:
  • scripts/migrate_hypergraphs_v2.py — reescreve os arquivos em disco;
  • core/kg/kg_store — upgrade-on-read: normaliza para v2 em memória a cada leitura,
    para que leitores nunca vejam v1 (blobs/arquivos ainda em A ou B são elevados a C).

Propriedade-chave: o id é `{prefixo}:{slug(name)}`, determinístico a partir de
(tipo-v2, name). Logo o MESMO (tipo, name) recebe o MESMO id em qualquer subgrafo
— a resolução cross-fonte vira lookup por id sem perda. A canonicalização de forma
(PR3) só APERTA isso (IA≡I.A.).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from core.kg.schema import slugify

logger = logging.getLogger(__name__)

FORMAT_VERSION = 2

# Prefixo de id por tipo v1. O PR2 remapeia estes para op/ator/con ao consolidar
# os tipos; aqui a transformação é puramente de formato, então o id descreve o
# tipo v1 do nó.
_TYPE_PREFIX: dict[str, str] = {
    "Edital": "ed",
    "Programa": "prog",
    "Investidor": "inv",
    "ICT": "ict",
    "Fonte": "fonte",
    "Tema": "tema",
    "Tecnologia": "tec",
    "Aplicação": "apl",
    "Mecanismo": "mec",
    "Requisito": "req",
    "Exclusão": "exc",
    "Entidade": "ent",
    # Tipos v2: a extração FRESCA (pós-PR2) emite tipos v2 sem ids/format_version.
    # `_migrate_format` precisa atribuir o prefixo v2 direto — `consolidate_to_v2_types`
    # dá short-circuit em grafos já-tipados-v2 e NÃO remapearia um fallback errado.
    "Oportunidade": "op",
    "Ator": "ator",
    "Conceito": "con",
}
_FALLBACK_PREFIX = "no"  # tipo fora da lista (não deve ocorrer no corpus atual)


@dataclass
class MigrationStats:
    """Contadores de auditoria de uma migração (para o log do script)."""
    nodes: int = 0
    edges_in: int = 0
    edges_out: int = 0
    dropped_members: int = 0
    dropped_edges: int = 0
    # PR2 — consolidação de tipos
    folded_nodes: int = 0        # Fonte/Mecanismo/Requisito/Exclusão foldados/removidos
    reclassified_entidade: int = 0  # Entidade → Ator (casou) ou Conceito(entidade_v1)


def is_v2(graph: dict) -> bool:
    """True se o FORMATO é v2 (ids + members-by-id). Não garante tipos consolidados."""
    return graph.get("format_version") == FORMAT_VERSION


def node_by_id(graph: dict) -> dict[str, dict]:
    """id → nó de um subgrafo v2. Padrão de acesso das arestas (members = ids)."""
    return {n["id"]: n for n in graph.get("nodes", []) if n.get("id")}


def _assign_id(node: dict, seen: set[str]) -> str:
    prefix = _TYPE_PREFIX.get(node.get("type", ""), _FALLBACK_PREFIX)
    base = f"{prefix}:{slugify(node.get('name') or '')}"
    cand = base
    i = 2
    while cand in seen:  # desambigua slugs que colidam no mesmo arquivo
        cand = f"{base}-{i}"
        i += 1
    seen.add(cand)
    return cand


def _migrate_format(graph: dict, stats: MigrationStats | None = None) -> dict:
    """FORMATO v1 → v2 (Estado A → B; idempotente na entrada já-v2).

    - atribui `id` = `{prefixo-do-tipo-v1}:{slug(name)}` (único por arquivo);
    - converte `members` de name-lower → id;
    - DROPA members que não resolvem para nenhum nó (dangling — hoje já são
      no-op em todos os leitores) e arestas que sobrem com <2 membros;
    - cria bloco `proveniencia` vazio (PR4 preenche a URL determinística);
    - marca `format_version: 2`.

    Os TIPOS permanecem v1 aqui — `consolidate_to_v2_types` os consolida depois.
    Retorna um NOVO dict; não muta a entrada.
    """
    if is_v2(graph):
        return graph

    nodes = graph.get("nodes", [])
    edges = graph.get("edges", [])
    st = stats if stats is not None else MigrationStats()

    seen_ids: set[str] = set()
    name_to_id: dict[str, str] = {}  # name_lower → id (0 colisões cross-tipo medidas)
    new_nodes: list[dict] = []
    for n in nodes:
        n2 = dict(n)
        n2["id"] = _assign_id(n2, seen_ids)
        new_nodes.append(n2)
        nm = (n.get("name") or "").strip().lower()
        if nm:
            name_to_id[nm] = n2["id"]  # "último vence" — espelha o _node_index legado
    st.nodes += len(new_nodes)

    new_edges: list[dict] = []
    for e in edges:
        st.edges_in += 1
        mids: list[str] = []
        for m in e.get("members", []):
            mid = name_to_id.get((m or "").strip().lower())
            if mid is None:
                st.dropped_members += 1
                continue
            mids.append(mid)
        if len(mids) < 2:  # aresta degenerada (perdeu membros) — não liga nada
            st.dropped_edges += 1
            continue
        e2 = dict(e)
        e2["members"] = mids
        new_edges.append(e2)
    st.edges_out += len(new_edges)

    return {
        "format_version": FORMAT_VERSION,
        "source_hash": graph.get("source_hash"),
        "proveniencia": graph.get("proveniencia", {}),  # vazio no PR1; PR4 preenche
        "nodes": new_nodes,
        "edges": new_edges,
    }


# ---------------------------------------------------------------------------
# PR2 — consolidação de tipos (12 v1 → Oportunidade/Ator/Conceito)
# ---------------------------------------------------------------------------
# Mapa D5/D6/D7 da spec. Opera sobre um grafo JÁ no formato v2 (Estado B):
# nós com `id` prefixado por tipo v1 e arestas por id. Remapeia o prefixo do id
# para op:/ator:/con:, retipa os nós, e FOLDA os tipos-faceta (Fonte/Mecanismo/
# Requisito/Exclusão) em propriedades/proveniência. Idempotente.

V2_TYPES = frozenset({"Oportunidade", "Ator", "Conceito"})

# tipo v1 → (tipo v2, kind, aperture). Conceitos têm dim (abaixo), não kind.
_CONSOLIDATE_MAP: dict[str, tuple[str, str | None, str | None]] = {
    "Edital":     ("Oportunidade", "edital",    "prazo"),
    "Programa":   ("Oportunidade", "programa",  "recorrente"),
    "ICT":        ("Ator",         "ict",        None),
    "Investidor": ("Ator",         "investidor", None),
    "Tema":       ("Conceito",     None,         None),
    "Tecnologia": ("Conceito",     None,         None),
    "Aplicação":  ("Conceito",     None,         None),
}
_DIM_MAP = {"Tema": "tema", "Tecnologia": "tecnologia", "Aplicação": "aplicacao"}
_V2_ID_PREFIX = {"Oportunidade": "op", "Ator": "ator", "Conceito": "con"}

# Tipos-faceta que SOMEM: viram propriedade do nó edital (Mecanismo/Requisito/
# Exclusão) ou proveniência (Fonte, D4). Foldados file-level (ver _fold_facets).
_FOLDED_TYPES = frozenset({"Fonte", "Mecanismo", "Requisito", "Exclusão"})

# Marca de ex-Entidade reclassificada como Conceito (D5). Excluída da afinidade
# do match (em v1 Entidade NUNCA esteve em AFFINITY_TYPES) — a promoção nó-a-nó
# é decisão do PR3 (higiene). Ver core/services/hypergraph_match.
ENTIDADE_V1_ORIGEM = "entidade_v1"


def is_types_v2(graph: dict) -> bool:
    """True se TODOS os nós já são tipos v2 (Oportunidade/Ator/Conceito).

    Grafo vazio conta como consolidado (no-op)."""
    return all(n.get("type") in V2_TYPES for n in graph.get("nodes", []))


def _reclassify_entidade(node: dict, known_atores: dict[str, str]) -> tuple[str, str | None, str | None, bool]:
    """Entidade v1 → (tipo v2, kind, dim, is_entidade_v1) via heurística D5.

    Casa com um Ator conhecido do MESMO arquivo (name lower → kind) → Ator;
    senão → Conceito(dim=tema) marcado `origem=entidade_v1` (inerte no match)."""
    nm = (node.get("name") or "").strip().lower()
    kind = known_atores.get(nm)
    if kind is not None:
        return "Ator", kind, None, False
    return "Conceito", None, "tema", True


def _fold_facets(
    targets: list[dict], facet_nodes: dict[str, list[dict]], file_key_hint: str,
) -> None:
    """Anexa os tipos-faceta foldados às Oportunidades edital do arquivo (D6).

    Regra file-level (fork 4):
      • target = Oportunidade(kind=edital) do arquivo;
      • Mecanismo → `mecanismo[]` (slugs canônicos de core/skills);
      • Requisito → `requisitos_texto[]`; Exclusão → `exclusoes_texto[]` (SEPARADOS
        — o veredito do PR7 precisa distinguir "exige X" de "não pode ser Y");
      • sem target (catálogos ict/investidores/programas) → NO-OP, loga a perda;
      • múltiplos targets → anexa a todos e loga warning (não deveria ocorrer).
    """
    from core.skills import _normalize_mechanism

    n_mec = len(facet_nodes.get("Mecanismo", []))
    n_req = len(facet_nodes.get("Requisito", []))
    n_exc = len(facet_nodes.get("Exclusão", []))
    if not (n_mec or n_req or n_exc):
        return
    if not targets:
        logger.info(
            "consolidate[%s]: sem nó edital — descartando %d mecanismo/%d requisito/%d exclusão (catálogo, no-op)",
            file_key_hint, n_mec, n_req, n_exc,
        )
        return
    if len(targets) > 1:
        logger.warning("consolidate[%s]: %d nós edital — foldando facetas em todos", file_key_hint, len(targets))

    mecanismo = sorted({
        s for n in facet_nodes.get("Mecanismo", [])
        if (s := _normalize_mechanism(n.get("name", ""))) is not None
    })
    requisitos = [n.get("description") or n.get("name", "") for n in facet_nodes.get("Requisito", [])]
    exclusoes = [n.get("description") or n.get("name", "") for n in facet_nodes.get("Exclusão", [])]
    for t in targets:
        if mecanismo:
            t["mecanismo"] = mecanismo
        if requisitos:
            t["requisitos_texto"] = [r for r in requisitos if r]
        if exclusoes:
            t["exclusoes_texto"] = [e for e in exclusoes if e]


def consolidate_to_v2_types(graph: dict, stats: MigrationStats | None = None) -> dict:
    """TIPOS v1 → v2 (Estado B → C; idempotente se já-C). Espera formato v2.

    - retipa cada nó via `_CONSOLIDATE_MAP` (+ dim para Conceito, kind/aperture
      para Oportunidade/Ator); Entidade via heurística `_reclassify_entidade`;
    - remapeia o id para `{prefixo-v2}:{slug(name)}` (op:/ator:/con:), mantendo o
      determinismo (mesmo (tipo-v2, name) → mesmo id cross-fonte);
    - REMOVE Fonte/Mecanismo/Requisito/Exclusão, foldando seu conteúdo nas
      propriedades do nó edital (`_fold_facets`);
    - reescreve arestas por id (old→new); membros de nós removidos caem; aresta
      com <2 membros cai (as arestas exige/exclui que apontavam a Requisito somem;
      as relacionais que apontam a Ator sobrevivem — D6/D12).

    Retorna um NOVO dict; não muta a entrada.
    """
    if is_types_v2(graph):
        return graph
    st = stats if stats is not None else MigrationStats()

    nodes = graph.get("nodes", [])
    # Atores conhecidos do arquivo (name lower → kind) p/ a heurística de Entidade.
    known_atores: dict[str, str] = {}
    for n in nodes:
        m = _CONSOLIDATE_MAP.get(n.get("type", ""))
        if m and m[0] == "Ator":
            known_atores[(n.get("name") or "").strip().lower()] = m[1]

    id_map: dict[str, str | None] = {}   # old id → new id (None = removido)
    seen_ids: set[str] = set()
    new_nodes: list[dict] = []
    facet_nodes: dict[str, list[dict]] = {}  # tipo v1 faceta → nós (p/ fold)

    for n in nodes:
        old_id = n.get("id")
        v1t = n.get("type", "")

        if v1t in _FOLDED_TYPES:
            facet_nodes.setdefault(v1t, []).append(n)
            if old_id is not None:
                id_map[old_id] = None
            st.folded_nodes += 1
            continue

        if v1t == "Entidade":
            v2t, kind, dim, is_ent = _reclassify_entidade(n, known_atores)
            aperture = None
            st.reclassified_entidade += 1
        else:
            mapped = _CONSOLIDATE_MAP.get(v1t)
            if mapped is None:  # já-v2 ou tipo inesperado — preserva
                new_nodes.append(dict(n))
                if old_id is not None:
                    id_map[old_id] = old_id
                continue
            v2t, kind, aperture = mapped
            dim = _DIM_MAP.get(v1t)
            is_ent = False

        n2 = {k: v for k, v in n.items() if k not in ("type", "id", "kind", "dim", "aperture", "origem")}
        n2["type"] = v2t
        if kind:
            n2["kind"] = kind
        if dim:
            n2["dim"] = dim
        if aperture:
            n2["aperture"] = aperture
        if is_ent:
            n2["origem"] = ENTIDADE_V1_ORIGEM
        # Campos de display vazios (Ator/Conceito não têm prazo/status/valor/fonte)
        # são omitidos — não null-poluir (regra do schema v2).
        for f in ("prazo", "status", "valor", "fonte"):
            if n2.get(f) is None:
                n2.pop(f, None)

        new_id = _dedup_id(f"{_V2_ID_PREFIX[v2t]}:{slugify(n2.get('name') or '')}", seen_ids)
        n2["id"] = new_id
        if old_id is not None:
            id_map[old_id] = new_id
        new_nodes.append(n2)

    # Fold das facetas nas Oportunidades edital do arquivo.
    edital_targets = [n for n in new_nodes if n.get("type") == "Oportunidade" and n.get("kind") == "edital"]
    _fold_facets(edital_targets, facet_nodes, graph.get("source_hash") or "?")

    # Reescreve arestas por id (old→new); dropa membros removidos e arestas <2.
    new_edges: list[dict] = []
    for e in graph.get("edges", []):
        st.edges_in += 1
        mids: list[str] = []
        for m in e.get("members", []):
            nm = id_map.get(m, m)  # não-mapeado (raro) passa cru
            if nm is None:  # membro apontava a nó removido (faceta)
                st.dropped_members += 1
                continue
            mids.append(nm)
        mids = list(dict.fromkeys(mids))  # dedup preservando ordem
        if len(mids) < 2:
            st.dropped_edges += 1
            continue
        new_edges.append({**e, "members": mids})
    st.edges_out += len(new_edges)
    st.nodes += len(new_nodes)

    return {
        "format_version": FORMAT_VERSION,
        "source_hash": graph.get("source_hash"),
        "proveniencia": graph.get("proveniencia", {}),
        "nodes": new_nodes,
        "edges": new_edges,
    }


def _dedup_id(base: str, seen: set[str]) -> str:
    cand, i = base, 2
    while cand in seen:
        cand = f"{base}-{i}"
        i += 1
    seen.add(cand)
    return cand


def migrate_to_v2(graph: dict, stats: MigrationStats | None = None) -> dict:
    """v1 → v2 COMPLETO: formato (ids, members-by-id) + consolidação de tipos
    (Oportunidade/Ator/Conceito). Idempotente. Ponto único chamado por todos os
    leitores (upgrade-on-read) e pelo script de migração. Retorna NOVO dict.
    """
    st = stats if stats is not None else MigrationStats()
    if not is_v2(graph):
        graph = _migrate_format(graph, st)      # Estado A → B
    return consolidate_to_v2_types(graph, st)    # Estado B → C (no-op se já-C)
