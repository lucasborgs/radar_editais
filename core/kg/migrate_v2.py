"""core/kg/migrate_v2.py — migração mecânica dos hipergrados para o formato v2.

PR1 da spec docs/specs/kg-redesign.md: IDs estáveis prefixados por tipo +
arestas ligadas por `id` (não mais por name-string lowercased). Transformação
PURA e idempotente, usada por:
  • scripts/migrate_hypergraphs_v2.py — reescreve os arquivos em disco;
  • core/kg/kg_store — upgrade-on-read: normaliza v1 → v2 em memória a cada
    leitura, para que leitores nunca vejam v1 (mesmo blobs/extrações frescas
    ainda no formato antigo até o PR2 tocar o extractor).

ESCOPO PR1 = só FORMATO. Os tipos de nó permanecem v1 (Edital/Tema/Mecanismo/
Requisito/...); a consolidação em Oportunidade/Ator/Conceito é o PR2. Por isso
o prefixo do id deriva do TIPO v1 — o PR2 remapeia para op/ator/con.

Propriedade-chave: o id é `{prefixo}:{slug(name)}`, determinístico a partir de
(tipo, name). Logo o MESMO (tipo, name) recebe o MESMO id em qualquer subgrafo
— a resolução cross-fonte (hoje por casamento de (type, name)) vira lookup por
id sem perda. A canonicalização de forma (PR3) só APERTA isso (IA≡I.A.).
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


def is_v2(graph: dict) -> bool:
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


def migrate_to_v2(graph: dict, stats: MigrationStats | None = None) -> dict:
    """v1 → v2 (idempotente; devolve a própria entrada se já for v2).

    - atribui `id` = `{prefixo-do-tipo}:{slug(name)}` (único por arquivo);
    - converte `members` de name-lower → id;
    - DROPA members que não resolvem para nenhum nó (dangling — hoje já são
      no-op em todos os leitores) e arestas que sobrem com <2 membros;
    - cria bloco `proveniencia` vazio (PR4 preenche a URL determinística);
    - marca `format_version: 2`.

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
