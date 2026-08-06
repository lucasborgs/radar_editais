"""core/kg/spike/serialize.py — textualização estrutura-consciente.

SPEC §10. A resposta ao texto 1: o subgrafo sobrevive à viagem ao token space
como JSON estruturado que PRESERVA adjacência, tipo, direção e peso — não
bullets planos. Funções PURAS sobre arestas/nós (testáveis sem DB).

Contrato (Bloco 2 — contexto completo e rastreável para o LLM):
- nós: `id`, `kind` (entidade) ou `family` (qualidade), `native_id` quando
  disponível, `name` (entidade) ou `value` (qualidade), `description` quando
  útil;
- arestas: `source_id`/`target_id` (extremidades), `type`, `weight`,
  `properties` e `source` — a classificação de origem do Bloco 1
  (`factual_catalogada` | `deterministica_derivada` | `similaridade_derivada`),
  sem camada de provenance nova;
- `center` com a mesma estrutura completa; `communities` preservadas.

Formato de saída:
{
  "center": {"id", "kind", "native_id", "name"},
  "nodes": [{"id", "kind", "native_id", "name"}, {"id", "family", "value"}],
  "edges": [{"source_id", "target_id", "type", "weight", "source", "properties"}],
  "communities": [names],
  "paths_to_profile": [[["empresa", "atua_em", "setor:agro"], ...], ...],
}
"""
from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

from radar.core.kg.spike import traverse


def _node_payload(node: dict[str, Any]) -> dict[str, Any]:
    """Payload de um nó do spike: identidade completa + rótulo legível.

    Entidade → `kind`/`native_id`/`name`/`description` (quando houver);
    qualidade → `family`/`value`. `description` só entra se já existir e for
    útil ao contexto (não é inventado).
    """
    if "family" in node or "value" in node:
        return {
            "id": node["id"],
            "family": node.get("family") or "",
            "value": node.get("value") or "",
        }
    payload: dict[str, Any] = {
        "id": node["id"],
        "kind": node.get("kind") or "",
    }
    if node.get("native_id"):
        payload["native_id"] = node["native_id"]
    payload["name"] = node.get("name") or ""
    desc = (node.get("description") or "").strip()
    if desc:
        payload["description"] = desc
    return payload


def _resolve_node_ids(node_ids: Iterable[str], nodes: list[dict[str, Any]], quality: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """{node_id: payload completo} mesclando substâncias e nós de qualidade."""
    by_id: dict[str, dict[str, Any]] = {}
    for n in nodes:
        by_id[n["id"]] = _node_payload(n)
    for q in quality:
        by_id[q["id"]] = _node_payload(q)
    return by_id


def _edge_payload(e: dict[str, Any]) -> dict[str, Any]:
    """Payload de uma aresta — extremidades, tipo, peso e a classificação de
    origem do Bloco 1 (`source`), além das `properties` preservadas."""
    return {
        "source_id": e["source_id"],
        "target_id": e["target_id"],
        "type": e["type"],
        "weight": float(e.get("weight") or 1.0),
        "source": e.get("source") or "",
        "properties": e.get("properties") or {},
    }


def edge_payload(e: dict[str, Any]) -> dict[str, Any]:
    """Formato público de aresta (usado por `graph_community`)."""
    return _edge_payload(e)


def build_node_map(nodes: list[dict[str, Any]], quality: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """{node_id: payload completo} de todas as substâncias e qualidades — base
    de resolução de IDs em nomes/significado para `graph_reason`/`community`."""
    return _resolve_node_ids([], nodes, quality)


def _node_ref(nid: str, by_id: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Resumo de um nó para um passo de caminho: `id` + rótulo/tipo legível."""
    n = by_id.get(nid)
    if n is None:
        return {"id": nid}
    if "family" in n:
        return {"id": n["id"], "family": n["family"], "value": n["value"]}
    return {"id": n["id"], "kind": n["kind"], "name": n["name"] or n["id"]}


def enrich_paths(
    paths: list[list[tuple[str, str, str]]],
    edges: list[dict[str, Any]],
    nodes: list[dict[str, Any]],
    quality: list[dict[str, Any]],
) -> list[list[dict[str, Any]]]:
    """Caminhos de dedução com nós resolvidos — cada salto é um objeto com a
    origem/destino (id + nome + tipo), predicado, peso e a classificação de
    origem do Bloco 1 (`source`) + `properties`.

    Os passos de `find_paths` usam a direção PERCORRIDA; o lookup aceita as
    duas orientações da aresta. `source`/`properties` vêm direto da aresta já
    carregada pelo Bloco 1.
    """
    bucket: dict[tuple[str, str, str], dict[str, Any]] = {}
    for e in edges:
        bucket[(e["source_id"], e["target_id"], e["type"])] = e
        if e["source_id"] != e["target_id"]:
            bucket[(e["target_id"], e["source_id"], e["type"])] = e
    by_id = build_node_map(nodes, quality)
    out: list[list[dict[str, Any]]] = []
    for path in paths:
        steps: list[dict[str, Any]] = []
        for sid, pred, tid in path:
            e = bucket.get((sid, tid, pred)) or {}
            steps.append({
                "source_node": _node_ref(sid, by_id),
                "predicate": pred,
                "target_node": _node_ref(tid, by_id),
                "weight": float(e.get("weight") or 1.0),
                "source": e.get("source") or "",
                "properties": e.get("properties") or {},
            })
        out.append(steps)
    return out


def community_members(
    member_ids: Iterable[str], by_id: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    """Membros de uma comunidade resolvidos: entidade → id/native_id/kind/name;
    qualidade → id/family/value. Campos inexistentes não entram no objeto."""
    out: list[dict[str, Any]] = []
    for mid in member_ids:
        n = by_id.get(mid)
        if n is None:
            out.append({"id": mid})
            continue
        if "family" in n:
            out.append({"id": n["id"], "family": n["family"], "value": n["value"]})
            continue
        m: dict[str, Any] = {"id": n["id"], "kind": n["kind"]}
        if n.get("native_id"):
            m["native_id"] = n["native_id"]
        if n.get("name"):
            m["name"] = n["name"]
        out.append(m)
    return out


def shared_quality_payloads(
    counts: dict[str, int], by_id: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    """`shared_qualities` resolvidas: `{id, family, value, member_count}`."""
    out: list[dict[str, Any]] = []
    for qid, count in counts.items():
        n = by_id.get(qid) or {}
        out.append({
            "id": qid,
            "family": n.get("family", "") if "family" in n else "",
            "value": n.get("value", "") if "value" in n else "",
            "member_count": count,
        })
    return out


def serialize_subgraph(
    seed: str,
    edges: list[dict[str, Any]],
    nodes: list[dict[str, Any]],
    quality: list[dict[str, Any]],
    *,
    depth: int = 1,
    communities: dict[str, list[str]] | None = None,
    profile_paths: list[list[tuple[str, str, str]]] | None = None,
    max_nodes: int = 60,
    min_weight: float = 0.0,
) -> dict[str, Any]:
    """Subgrafo da vizinhança de `seed` (BFS até `depth`) em JSON estruturado.

    `profile_paths`: caminhos do perfil efêmero até o seed (SPEC §10,
    `paths_to_profile`); são pré-computados por `tools.graph_reason`/perfil.
    `min_weight`: arestas abaixo do peso não expandem a vizinhança (opção 4).
    `max_nodes`: teto de nós — quando atinge o teto, o subgrafo é recortado de
    forma CONSISTENTE (nenhuma aresta aponta para nó ausente de `nodes`).
    """
    reached = traverse.bfs_edges(edges, seed, depth=depth, min_weight=min_weight)
    resolved = _resolve_node_ids([seed], nodes, quality)
    if not reached:
        return {"center": resolved.get(seed, {"id": seed}), "nodes": [], "edges": [],
                "communities": [], "paths_to_profile": profile_paths or []}

    # Ordem de descoberta (BFS): seed primeiro, depois extremidades na ordem
    # em que as arestas alcançadas aparecem — recorte determinístico e próximo
    # ao centro quando `max_nodes` limita.
    ordered: list[str] = []
    seen: set[str] = set()

    def _add(nid: str) -> None:
        if nid not in seen:
            seen.add(nid)
            ordered.append(nid)

    _add(seed)
    for e in reached:
        _add(e["source_id"])
        _add(e["target_id"])

    payload_ids = ordered if len(ordered) <= max_nodes else ordered[:max_nodes]
    retained = set(payload_ids)
    node_payload = [resolved[nid] for nid in payload_ids if nid in resolved]

    # Arestas SÓ dentro do conjunto retido — nenhuma aresta aponta para nó
    # ausente de `nodes` (consistência do subgrafo sob `max_nodes`).
    edge_payload = [_edge_payload(e) for e in reached
                    if e["source_id"] in retained and e["target_id"] in retained]

    # Comunidades que contêm qualquer nó RETIDO do subgrafo
    com_payload: list[str] = []
    if communities:
        for cid, members in communities.items():
            if retained & set(members):
                com_payload.append(cid)
        com_payload.sort()

    center = resolved.get(seed, {"id": seed})
    return {
        "center": center,
        "nodes": node_payload,
        "edges": edge_payload,
        "communities": com_payload,
        "paths_to_profile": profile_paths or [],
    }


def paths_to_prose(paths: list[list[tuple[str, str, str]]]) -> str:
    """Caminhos de dedução em prosa (para o contexto do LLM) — o "textualização
    feita direito": cada salto preservado, não um saco de sentenças."""
    if not paths:
        return ""
    lines = ["Caminhos de dedução no grafo:"]
    for i, path in enumerate(paths, 1):
        steps = " → ".join(f"{s} -[{t}]-> {o}" for s, t, o in path)
        lines.append(f"  {i}. {steps}")
    return "\n".join(lines)


def dump(subgraph: dict[str, Any]) -> str:
    """JSON compacto e estável (keys ordenadas) para injetar no prompt/tool."""
    return json.dumps(subgraph, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
