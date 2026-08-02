"""core/kg/phase1/tools.py — integração read-only da Fase 1 com o Explorar.

Três tools (graph_explore, graph_reason, graph_community) sobre o snapshot
CONSISTENTE da geração corrente (`store.load_snapshot`), atrás da flag única:

    KG_PHASE1_EXPLORE_ENABLED=false     # default OFF

- flag off → `build_graph_tools()` devolve [] — o ExploreAgent fica exatamente
  como antes (regressão zero);
- flag on → as tools são ADITIVAS às tools atuais do catálogo;
- falha do Postgres, timeout, ausência de geração ou erro interno NÃO derrubam
  o agente: a tool devolve resultado categórico e sanitizado (`unavailable`/
  `error`) e o agente segue com as tools legadas.

Observabilidade SÓ estrutural (nunca conteúdo): tool, generation_id, duração,
nº de nós/arestas/caminhos, outcome (`hit|not_found|ambiguous|unavailable|
error`), uso de fallback e categoria canônica do erro. Nunca: pergunta do
usuário, `entity_ref`, perfil, nomes/descrições, payload, mensagem bruta de
exceção, DSN ou URL.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
import unicodedata
from dataclasses import dataclass, field
from typing import Any

from langchain_core.tools import BaseTool, tool

from radar.core.kg.phase1 import store, traverse
from radar.core.kg.phase1.resolve import MAX_CANDIDATES, Resolution, resolve_entity

logger = logging.getLogger(__name__)

# Flag única de ativação (default OFF).
ENABLED_FLAG = "KG_PHASE1_EXPLORE_ENABLED"

# Arestas com peso abaixo do corte não expandem a vizinhança — o hub
# `setor:multissetorial` recebe weight=0.1 no build (SPEC §7 opção 4).
MIN_WEIGHT = 0.5

# Limites rígidos (payload final compacto e limitado).
MAX_DEPTH_CAP = 2            # faixa pequena e segura de `depth`
MAX_REASON_DEPTH_CAP = 4     # profundidade máxima de caminho no graph_reason
MAX_NODES = 60               # teto de nós no payload do graph_explore
MAX_EDGES = 80               # teto de arestas no payload do graph_explore
MAX_PATHS = 3                # poucos caminhos (perfil e atores)
MAX_PAYLOAD_BYTES = 12_000   # teto do payload serializado
MAX_NAMES_PER_KIND = 12      # nomes por tipo no graph_community
MAX_SHARED_QUALITIES = 8     # características compartilhadas no graph_community

# Predicados de QUALIDADE (fatos determinísticos do gold) — usados pelo
# graph_community para achar características compartilhadas.
QUALITY_PREDICATES = {
    "tem_setor", "tem_tecnologia", "busca_estagio", "tem_uf",
    "usa_mecanismo", "tem_trl_faixa",
}

# Relações DERIVADAS — heurística, nunca fato documental.
DERIVED_PREDICATES = {"similar_a", "potential_parceria", "potencial_parceria"}

_TRL_FAIXAS = [
    ("faixa_trl:pesquisa", 1, 3),
    ("faixa_trl:prototipo", 4, 6),
    ("faixa_trl:industrial", 7, 9),
]

# tipos_financiamento_interesse do perfil → mecanismo do catálogo (§5.1 gold).
_MECANISMO_MAP = {
    "subvencao": "subvencao",
    "subvencao_nao_reembolsavel": "subvencao",
    "bolsa": "bolsa",
    "premio": "premio",
    "equity": "equity",
    "capital_risco": "equity",
    "pesquisa_colaborativa": "parceria_pd",
    "parceria_pd": "parceria_pd",
}

_UNAVAILABLE_MSG = (
    "Grafo da Fase 1 indisponível no momento. Use as ferramentas do catálogo "
    "(get_edital, search_entities, get_node_neighborhood)."
)
_ERROR_MSG = (
    "Falha ao consultar o grafo da Fase 1. Use as ferramentas do catálogo "
    "(get_edital, search_entities, get_node_neighborhood)."
)


def graph_tools_enabled() -> bool:
    """Única fonte de verdade da flag `KG_PHASE1_EXPLORE_ENABLED`."""
    return os.environ.get(ENABLED_FLAG, "false").lower() in {"1", "true", "yes"}


# ─────────────────────────────────────────────────────────────────────────────
# Serialização compacta (KG-P1B-1 achado 3)
# ─────────────────────────────────────────────────────────────────────────────

def dump(payload: dict[str, Any]) -> str:
    """JSON compacto e estável (chaves ordenadas) para injetar na tool."""
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _utf8(payload: Any) -> int:
    """Bytes UTF-8 do payload serializado — o teto REAL do contrato."""
    return len(dump(payload).encode("utf-8"))


def _clip_utf8(value: str, max_bytes: int) -> str:
    """Corta uma string em BYTES UTF-8, SEM cortar no meio de um caractere
    multibyte (decodifica com `errors="ignore"` → JSON sempre válido)."""
    if max_bytes < 0:
        max_bytes = 0
    raw = value.encode("utf-8")
    if len(raw) <= max_bytes:
        return value
    return raw[:max_bytes].decode("utf-8", errors="ignore")


def _clone(value: Any) -> Any:
    """Cópia profunda simples (dicts/listas de dicts/listas de str)."""
    if isinstance(value, dict):
        return {k: _clone(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_clone(v) for v in value]
    return value


def _shrink_targets(root: Any) -> list[tuple[int, tuple, list | dict | None, Any, str]]:
    """Enumera TODOS os encolhimentos possíveis como
    `(economia_em_bytes, caminho_lexicográfico, container, chave, tipo)`.

    - listas: descarta o ÚLTIMO elemento (a cauda é a menos estrutural);
    - dicts: descarta a ÚLTIMA chave (ordem do build — as primeiras são as
      mais estruturais: center/entity/community_id primeiro);
    - strings: corta pela METADE (bytes UTF-8, sem partir caractere).
    Determinístico: a economia é o critério, e o caminho desempata.
    Para strings, `container` é o pai (dict ou lista) e `chave` é a posição;
    para uma string raiz solta, container=None e chave é a própria string."""
    targets: list[tuple[int, tuple, list | dict | None, Any, str]] = []

    def walk(value: Any, path: tuple, container: list | dict | None, key: Any) -> None:
        if isinstance(value, list):
            if value:
                last = value[-1]
                savings = _utf8(last) + 1  # + vírgula
                targets.append((savings, path + ("pop_list",), value, -1, "list"))
            for i, item in enumerate(value):
                walk(item, path + (i,), value, i)
        elif isinstance(value, dict):
            if len(value) > 1:
                last_key = list(value)[-1]
                last_val = value[last_key]
                if isinstance(last_val, (dict, list, str)):
                    savings = _utf8(last_val) + len(last_key) + 4
                    targets.append((savings, path + ("pop_key",), value, last_key, "dict"))
            for k, item in value.items():
                walk(item, path + (k,), value, k)
        elif isinstance(value, str) and _utf8(value) > 16:
            targets.append((_utf8(value) // 2, path + ("clip",), container, key, "str"))

    walk(root, (), None, None)
    return targets


def _minimal_envelope(max_bytes: int) -> dict[str, Any]:
    """Envelope categórico MÍNIMO e válido — usado quando nem o payload mais
    enxuto cabe no teto. `{"truncated": true}` sempre cabe em MAX_PAYLOAD_BYTES."""
    env: dict[str, Any] = {"truncated": True}
    if _utf8(env) <= max_bytes:
        return env
    return {}


def _trim_payload(payload: dict[str, Any], max_bytes: int) -> dict[str, Any]:
    """Limita QUALQUER payload ao teto de BYTES UTF-8 (`_utf8 <= max_bytes`),
    mantendo JSON válido e preservando a estrutura mais importante primeiro.

    - mede bytes UTF-8 (não caracteres);
    - limita listas, dicts (members_by_kind), nomes, ids/candidatos, centro,
      entidade, comunidades e mensagens/notas;
    - marca o corte com `"truncated": true`;
    - se nem o envelope mínimo couber, devolve envelope categórico mínimo.

    Algoritmo: cópia profunda + encolhimento guloso e determinístico (sempre o
    alvo de MAIOR economia de bytes; o caminho desempata). Cada candidato é
    verificado pela serialização real, então o teto é garantido por construção."""
    if _utf8(payload) <= max_bytes:
        return payload
    work = _clone(payload)
    guard = 0
    while _utf8(work) > max_bytes and guard < 10_000:
        guard += 1
        targets = _shrink_targets(work)
        if not targets:
            break
        targets.sort(key=lambda t: (-t[0], t[1]))
        _, _, container, where, kind = targets[0]
        if kind == "list":
            container.pop()
        elif kind == "dict":
            container.pop(where)
        else:
            old = container[where] if container is not None else where
            half = max(0, _utf8(old) // 2)
            clipped = _clip_utf8(old, half)
            if container is None:
                work = clipped
            else:
                container[where] = clipped
    if _utf8(work) > max_bytes:
        return _minimal_envelope(max_bytes)
    if not isinstance(work, dict):
        return {"truncated": True}
    work["truncated"] = True
    return work


def _props_of(edge: dict[str, Any]) -> dict[str, Any]:
    """`properties` (jsonb) defensivamente — psycopg devolve str se o loader
    jsonb não foi registrado."""
    raw = edge.get("properties") or {}
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (ValueError, TypeError):
            raw = {}
    return raw if isinstance(raw, dict) else {}


def _node_index(snapshot: Any) -> dict[str, dict[str, str]]:
    """{node_id: {id, kind, name}} mesclando substâncias e nós de qualidade."""
    idx: dict[str, dict[str, str]] = {}
    for n in snapshot.nodes:
        idx[n["id"]] = {"id": n["id"], "kind": n["kind"], "name": n["name"]}
    for q in snapshot.quality_nodes:
        idx[q["id"]] = {"id": q["id"], "kind": q["family"], "name": q["value"]}
    return idx


def _node_entry(idx: dict[str, dict[str, str]], node_id: str) -> dict[str, str]:
    return idx.get(node_id, {"id": node_id, "kind": "", "name": node_id})


def _edge_entry(edge: dict[str, Any]) -> dict[str, Any]:
    """Aresta serializada preservando DIREÇÃO real, predicado, peso, origin e
    caráter derivado."""
    props = _props_of(edge)
    return {
        "source": edge["source_id"],
        "target": edge["target_id"],
        "predicate": edge["type"],
        "weight": float(edge.get("weight") or 1.0),
        "origin": edge.get("origin", ""),
        "derived": bool(props.get("derived")),
    }


def _edge_index(edges: list[dict[str, Any]]) -> dict[tuple[str, str, str], dict[str, Any]]:
    """{(source, type, target): edge} — direção exata; a busca de passo tenta a
    orientação exata e, em seguida, a inversa (traversal não-direcionado)."""
    return {(e["source_id"], e["type"], e["target_id"]): e for e in edges}


def _step_entry(
    from_: str, predicate: str, to: str,
    edge_index: dict[tuple[str, str, str], dict[str, Any]],
) -> dict[str, Any]:
    edge = edge_index.get((from_, predicate, to)) or edge_index.get((to, predicate, from_))
    if edge is None:
        return {"from": from_, "to": to, "predicate": predicate, "origin": "unknown", "derived": False}
    props = _props_of(edge)
    return {
        "from": from_,
        "to": to,
        "predicate": predicate,
        "weight": float(edge.get("weight") or 1.0),
        "origin": edge.get("origin", ""),
        "derived": bool(props.get("derived")),
    }


def _path_entry(
    path: list[tuple[str, str, str]],
    edge_index: dict[tuple[str, str, str], dict[str, Any]],
) -> list[dict[str, Any]]:
    """Um caminho como lista de saltos (cada um com direção, predicado, peso,
    origin e derivada) — nada se perde na textualização."""
    return [_step_entry(s, t, o, edge_index) for s, t, o in path]


def _resolution_payload(res: Resolution) -> dict[str, Any]:
    """Payload categórico e sanitizado para not_found/ambiguous."""
    if res.status == "not_found":
        return {"status": "not_found", "message": "Entidade não encontrada no grafo."}
    return {
        "status": "ambiguous",
        "message": "Referência ambígua — não foi escolhida uma opção.",
        "candidates": res.candidates[:MAX_CANDIDATES],
    }


# ─────────────────────────────────────────────────────────────────────────────
# Perfil efêmero (Design B — em memória, nada é persistido)
# ─────────────────────────────────────────────────────────────────────────────

def _as_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(x) for x in value if x]
    if value:
        return [str(value)]
    return []


def _norm(value: Any) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFKD", str(value or "")).lower()
        if not unicodedata.combining(c)
    ).strip()


def _trl_faixa_for(trl: Any) -> list[str]:
    if not isinstance(trl, int) and not (isinstance(trl, float) and trl == int(trl)):
        return []
    t = int(trl)
    return [fid for fid, lo, hi in _TRL_FAIXAS if lo <= t <= hi]


def _edge(source_id: str, target_id: str, type_: str, *, origin: str,
          weight: float = 1.0, properties: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "source_id": source_id, "target_id": target_id, "type": type_,
        "weight": weight, "properties": properties or {}, "origin": origin,
    }


def _profile_edges(profile: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    """Arestas do nó efêmero `empresa:efemera` (Design B) a partir dos campos
    ESTRUTURAIS do perfil (CompanyProfilePayload): uf, estágio, TRL e tipos de
    financiamento. Determinístico, sem LLM — só âncoras que o perfil declara."""
    node_id = "empresa:efemera"
    edges: list[dict[str, Any]] = []

    uf = _norm(profile.get("uf"))
    if uf:
        edges.append(_edge(node_id, f"uf:{uf}", "atua_em", origin="profile_ephemeral"))

    estagio = _norm(profile.get("estagio"))
    if estagio:
        edges.append(_edge(node_id, f"estagio:{estagio}", "busca_estagio", origin="profile_ephemeral"))

    for fid in _trl_faixa_for(profile.get("trl")):
        edges.append(_edge(node_id, fid, "tem_trl_faixa", origin="profile_ephemeral"))

    for fin in _as_list(profile.get("tipos_financiamento_interesse")):
        mecanismo = _MECANISMO_MAP.get(_norm(fin))
        if mecanismo:
            edges.append(_edge(node_id, f"mecanismo:{mecanismo}", "usa_mecanismo", origin="profile_ephemeral"))

    return edges, node_id


# ─────────────────────────────────────────────────────────────────────────────
# Builders de payload (PURAS sobre o snapshot — testáveis sem DB)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ToolOutcome:
    """Resultado estrutural de uma graph tool (para métricas/log)."""
    outcome: str
    generation_id: int | None
    n_nodes: int = 0
    n_edges: int = 0
    n_paths: int = 0
    payload: dict[str, Any] = field(default_factory=dict)


def explore_payload(
    ref: str,
    snapshot: Any,
    *,
    depth: int = 1,
    max_nodes: int = MAX_NODES,
    max_edges: int = MAX_EDGES,
    max_bytes: int = MAX_PAYLOAD_BYTES,
    min_weight: float = MIN_WEIGHT,
) -> ToolOutcome:
    """Vizinhança ESTRUTURAL de `ref`: centro, nós, arestas (direção/predicado/
    peso/origin/derivada) e comunidades. `depth` limitado a 1-2; `setor:multi-
    setorial` NÃO expande (min_weight)."""
    res = resolve_entity(ref, snapshot)
    if res.status != "hit":
        return ToolOutcome(res.status, snapshot.generation_id, payload=_resolution_payload(res))

    seed = res.node_id
    depth = max(1, min(int(depth), MAX_DEPTH_CAP))
    edges = traverse.bfs_edges(snapshot.edges, seed, depth=depth, min_weight=min_weight)
    edges = edges[:max_edges]

    node_ids: set[str] = {seed}
    for e in edges:
        node_ids.add(e["source_id"])
        node_ids.add(e["target_id"])
    idx = _node_index(snapshot)
    nodes = [_node_entry(idx, nid) for nid in sorted(node_ids) if nid in idx]
    if len(nodes) > max_nodes:
        nodes = nodes[:max_nodes]

    kept = {n["id"] for n in nodes}
    edges = [e for e in edges if e["source_id"] in kept and e["target_id"] in kept]

    communities = sorted(
        cid for cid, members in snapshot.communities.items() if kept & set(members)
    )

    payload = {
        "center": _node_entry(idx, seed),
        "nodes": nodes,
        "edges": [_edge_entry(e) for e in edges],
        "communities": communities,
    }
    payload = _trim_payload(payload, max_bytes)
    return ToolOutcome(
        outcome="hit",
        generation_id=snapshot.generation_id,
        n_nodes=len(payload["nodes"]),
        n_edges=len(payload["edges"]),
        payload=payload,
    )


def reason_payload(
    ref: str,
    snapshot: Any,
    profile: dict[str, Any] | None = None,
    *,
    max_depth: int = 3,
    max_paths: int = MAX_PATHS,
    min_weight: float = MIN_WEIGHT,
    max_bytes: int = MAX_PAYLOAD_BYTES,
) -> ToolOutcome:
    """Caminhos limitados entre o PERFIL (nó efêmero), a entidade e atores
    (ICTs/agências). Cada salto preserva direção, predicado, origin e o caráter
    derivado — similaridade/parceria NUNCA viram fato confirmado."""
    res = resolve_entity(ref, snapshot)
    if res.status != "hit":
        return ToolOutcome(res.status, snapshot.generation_id, payload=_resolution_payload(res))

    seed = res.node_id
    max_depth = max(1, min(int(max_depth), MAX_REASON_DEPTH_CAP))

    edges = list(snapshot.edges)
    profile_node: str | None = None
    if profile:
        profile_edges, profile_node = _profile_edges(profile)
        edges = edges + profile_edges
    edge_index = _edge_index(edges)

    paths_to_profile: list[list[dict[str, Any]]] = []
    if profile_node:
        raw = traverse.find_paths(
            edges, profile_node, seed, max_depth=max_depth, limit=max_paths, min_weight=min_weight,
        )
        paths_to_profile = [_path_entry(p, edge_index) for p in raw[:max_paths]]

    actors = sorted(n["id"] for n in snapshot.nodes if n["kind"] in ("ict", "agencia"))
    internal_raw = traverse.find_paths_to_goals(
        edges, seed, actors, max_depth=max_depth, limit=max_paths, min_weight=min_weight,
    )
    paths_to_actors = [_path_entry(p, edge_index) for p in internal_raw[:max_paths]]

    idx = _node_index(snapshot)
    payload = {
        "entity": _node_entry(idx, seed),
        "profile_anchor": profile_node or "sem_perfil",
        "paths_to_profile": paths_to_profile,
        "paths_to_actors": paths_to_actors,
        "note": (
            "Arestas com origin similar_a (phase1_similarity) ou potencial_parceria "
            "(phase1_tech_bridge) são DERIVADAS (heurística de embeddings/tecnologia) "
            "— nunca fatos confirmados."
        ),
    }
    payload = _trim_payload(payload, max_bytes)
    n_paths = len(payload["paths_to_profile"]) + len(payload["paths_to_actors"])
    return ToolOutcome(
        outcome="hit",
        generation_id=snapshot.generation_id,
        n_paths=n_paths,
        payload=payload,
    )


def _resolve_community(ref: str, communities: dict[str, list[str]]) -> str | None:
    """Resolução ESTRITA de comunidade: id exato (`com_11`) ou variantes de
    rótulo (`11`, `comunidade 11`, `com:11`, `com 11`). Sem sufixo solto —
    só devolve ids EXISTENTES da geração corrente."""
    ref = (ref or "").strip()
    if not ref:
        return None
    lowered = ref.lower()
    variants: set[str] = {lowered}
    core = lowered
    for prefix in ("comunidade:", "community:", "com:", "comunidade ", "community ", "com "):
        if lowered.startswith(prefix):
            core = lowered[len(prefix):].strip()
            variants.add(core)
            break
    if re.fullmatch(r"\d+", core):
        variants.add(f"com_{core}")
    for variant in variants:
        if variant in communities:
            return variant
    return None


def community_payload(
    ref: str,
    snapshot: Any,
    *,
    max_names_per_kind: int = MAX_NAMES_PER_KIND,
    max_shared: int = MAX_SHARED_QUALITIES,
    max_bytes: int = MAX_PAYLOAD_BYTES,
) -> ToolOutcome:
    """Membros (agrupados por `kind`), características compartilhadas e tipos de
    relação emitidos pelos membros de UMA comunidade existente da geração corrente."""
    cid = _resolve_community(ref, snapshot.communities)
    if cid is None:
        sample = sorted(snapshot.communities)[:MAX_CANDIDATES]
        return ToolOutcome(
            outcome="not_found",
            generation_id=snapshot.generation_id,
            payload={
                "status": "not_found",
                "message": "Comunidade não encontrada.",
                "available_sample": sample,
            },
        )

    members = snapshot.communities[cid]
    member_set = set(members)
    idx = _node_index(snapshot)

    grouped: dict[str, dict[str, Any]] = {}
    for member in members:
        entry = _node_entry(idx, member)
        kind = entry["kind"] or "desconhecido"
        bucket = grouped.setdefault(kind, {"count": 0, "names": []})
        bucket["count"] += 1
        if len(bucket["names"]) < max_names_per_kind:
            bucket["names"].append(entry["name"])
    grouped = {k: grouped[k] for k in sorted(grouped)}

    internal = [
        e for e in snapshot.edges
        if e["source_id"] in member_set and e["target_id"] in member_set
    ]
    edge_types = sorted({e["type"] for e in internal})

    shared_counts: dict[str, int] = {}
    for e in internal:
        if e["type"] in QUALITY_PREDICATES and e["target_id"] in idx:
            shared_counts[e["target_id"]] = shared_counts.get(e["target_id"], 0) + 1
    shared = [
        {"quality_id": qid, "name": idx[qid]["name"], "n_members": count}
        for qid, count in sorted(shared_counts.items(), key=lambda kv: (-kv[1], kv[0]))[:max_shared]
    ]

    payload = {
        "community_id": cid,
        "n_members": len(members),
        "members_by_kind": grouped,
        "n_internal_edges": len(internal),
        "edge_types": edge_types,
        "shared_characteristics": shared,
    }
    payload = _trim_payload(payload, max_bytes)
    return ToolOutcome(
        outcome="hit",
        generation_id=snapshot.generation_id,
        n_nodes=len(members),
        n_edges=len(internal),
        payload=payload,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Wrappers de agente + observabilidade estrutural
# ─────────────────────────────────────────────────────────────────────────────

def _category_of(exc: BaseException) -> str:
    """Categoria canônica — o ÚNICO conteúdo do erro registrado (nunca a
    mensagem). DSN/URL/SQL/segredo presentes na mensagem NUNCA vazam."""
    import psycopg

    if isinstance(exc, ImportError):
        return "dependency_error"
    if isinstance(exc, (ValueError, KeyError, TypeError)):
        return "contract_error"
    if isinstance(exc, psycopg.Error):
        return "database_error"
    if type(exc).__module__.startswith(("psycopg", "psycopg_pool")):
        return "database_error"
    return "unexpected_error"


def _observe(
    tool_name: str, *,
    outcome: str,
    generation_id: int | None,
    duration_ms: float,
    n_nodes: int = 0,
    n_edges: int = 0,
    n_paths: int = 0,
    fallback: bool = False,
    category: str = "",
) -> None:
    """Métrica/log ESTRUTURAL — NUNCA conteúdo. Não registra: pergunta do
    usuário, `entity_ref`, perfil, nomes/descrições, payload, mensagem bruta
    de exceção, DSN ou URL."""
    logger.info(
        "kg_phase1_explore tool=%s outcome=%s generation_id=%s duration_ms=%.1f "
        "nodes=%d edges=%d paths=%d fallback=%s category=%s",
        tool_name, outcome, generation_id, duration_ms,
        n_nodes, n_edges, n_paths, str(fallback).lower(), category or "-",
    )


def _run(tool_name: str, fn) -> str:
    """Executa uma graph tool com snapshot; degrada SEMPRE com resultado
    categórico e sanitizado (o agente segue com as tools legadas)."""
    started = time.perf_counter()

    try:
        snapshot = store.load_snapshot()
    except Exception as exc:  # noqa: BLE001
        category = _category_of(exc)
        outcome = "unavailable" if category == "database_error" else "error"
        _observe(
            tool_name, outcome=outcome, generation_id=None,
            duration_ms=_elapsed(started), fallback=True, category=category,
        )
        return _UNAVAILABLE_MSG if outcome == "unavailable" else _ERROR_MSG

    if snapshot is None:
        _observe(
            tool_name, outcome="unavailable", generation_id=None,
            duration_ms=_elapsed(started), fallback=True,
        )
        return _UNAVAILABLE_MSG

    try:
        result = fn(snapshot)
    except Exception as exc:  # noqa: BLE001
        _observe(
            tool_name, outcome="error", generation_id=snapshot.generation_id,
            duration_ms=_elapsed(started), fallback=True, category=_category_of(exc),
        )
        return _ERROR_MSG

    _observe(
        tool_name, outcome=result.outcome, generation_id=result.generation_id,
        duration_ms=_elapsed(started), n_nodes=result.n_nodes, n_edges=result.n_edges,
        n_paths=result.n_paths, fallback=result.outcome != "hit",
    )
    # Garantia FINAL do contrato de bytes (KG-P1B-1 achado 3): mesmo payloads
    # categóricos (not_found/ambiguous/available_sample) nunca excedem o teto.
    return dump(_trim_payload(result.payload, MAX_PAYLOAD_BYTES))


def _elapsed(started: float) -> float:
    return (time.perf_counter() - started) * 1000.0


def build_graph_tools(*, profile: dict[str, Any] | None = None) -> list[BaseTool]:
    """Tools read-only do grafo da Fase 1 (ADITIVAS às do catálogo).

    Flag off → lista vazia (comportamento do ExploreAgent intocado). `profile`
    (dict do CompanyProfilePayload) é capturado por CLOSURE — graph_reason NUNCA
    recebe o perfil como argumento preenchível pela LLM."""
    if not graph_tools_enabled():
        return []

    @tool
    def graph_explore(entity_ref: str, depth: int = 1) -> str:
        """Vizinhança ESTRUTURAL de uma entidade no grafo da Fase 1 (JSON: centro, nós, arestas com direção/predicado/peso/origem/derivada e comunidades relacionadas). Use para RELAÇÕES estruturais: quem opera, credencia, subordina; atores ligados a um nó.

        Args:
            entity_ref: id exato (ex.: "edital:finep:589"), native_id, nome ou valor de qualidade.
            depth: 1 = vizinhos diretos (default); 2 = vizinhos-dos-vizinhos. Máximo 2.
        """
        return _run("graph_explore", lambda snap: explore_payload(entity_ref, snap, depth=depth))

    @tool
    def graph_reason(entity_ref: str, max_depth: int = 3) -> str:
        """Caminhos LIMITADOS no grafo conectando o PERFIL da empresa, a entidade e atores (ICTs/agências) — JSON com saltos preservados (direção, predicado, origem, derivada). Use para ESTRATÉGIA e dedução (ex.: como a empresa se conecta a uma ICT/agência). O perfil é INJETADO automaticamente — não é argumento.

        Args:
            entity_ref: id exato, native_id, nome ou valor de qualidade da entidade-alvo.
            max_depth: profundidade máxima do caminho (máximo 4).
        """
        return _run(
            "graph_reason",
            lambda snap: reason_payload(entity_ref, snap, profile, max_depth=max_depth),
        )

    @tool
    def graph_community(community_ref: str) -> str:
        """Membros (agrupados por tipo) e características COMPARTILHADAS de uma COMUNIDADE (cluster Louvain) do grafo. Use quando o usuário citar uma comunidade ("com_3", "comunidade 3") ou quiser entender o que agrupa um conjunto de entidades.

        Args:
            community_ref: id da comunidade (ex.: "com_3") ou rótulo ("3", "comunidade 3").
        """
        return _run("graph_community", lambda snap: community_payload(community_ref, snap))

    return [graph_explore, graph_reason, graph_community]
