"""core/kg/phase1/tools.py — exploração profile-first read-only no Explorar.

Uma tool (`graph_strategy`) sobre o snapshot CONSISTENTE da geração corrente
(`store.load_snapshot`), atrás da flag única:

    KG_PHASE1_EXPLORE_ENABLED=false     # default OFF

- flag off → `build_graph_tools()` devolve [] — o ExploreAgent fica exatamente
  como antes (regressão zero);
- flag on → somente `graph_strategy` é injetada; não há busca ou Match paralelo;
- falha do Postgres, timeout, ausência de geração ou erro interno NÃO derrubam
  o agente: a tool devolve resultado categórico e sanitizado, sem fallback
  silencioso para o fluxo anterior.

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
import threading
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

# Acumulador em processo (KG-P1B-2): contagens/duração POR tool da rodada —
# SÓ estrutura (ints/floats), nunca conteúdo. Alimenta os diagnósticos da
# suíte `explore` (graph_fallback_rate, graph_latency_ms). `reset_run_stats`
# é chamado no início de cada run do harness de eval.
_LOCK = threading.Lock()
_RUN_STATS: dict[str, dict[str, float]] = {}


def reset_run_stats() -> None:
    """Zera o acumulador estrutural da rodada (harness de eval, KG-P1B-2)."""
    with _LOCK:
        _RUN_STATS.clear()


def run_stats() -> dict[str, dict[str, float]]:
    """Snapshot sanitizado (só tool → calls/fallbacks/duration_ms) da rodada."""
    with _LOCK:
        return {name: dict(stats) for name, stats in _RUN_STATS.items()}


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
MAX_STRATEGY_RESULTS = 5
STRATEGY_KINDS = ("edital", "programa", "agencia", "ict", "investidor")

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

_UNAVAILABLE_MSG = "Grafo da Fase 1 indisponível no momento; o recorte não foi consultado."
_ERROR_MSG = "Falha ao consultar o grafo da Fase 1; nenhum resultado estratégico foi fabricado."


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
        return {
            "traversal_from": from_, "traversal_to": to,
            "source": from_, "target": to, "predicate": predicate,
            "origin": "unknown", "derived": False,
        }
    props = _props_of(edge)
    return {
        "traversal_from": from_,
        "traversal_to": to,
        "source": edge["source_id"],
        "target": edge["target_id"],
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


def _quality_index(snapshot: Any) -> dict[tuple[str, str], list[dict[str, Any]]]:
    """Índice exato de qualidade; não faz busca parcial nem aproximação."""
    out: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for node in snapshot.quality_nodes:
        out.setdefault((node["family"], _norm(node["value"])), []).append(node)
    return out


def _profile_text(profile: dict[str, Any]) -> dict[str, str]:
    return {
        field: str(profile.get(field) or "")
        for field in ("one_liner", "solution_summary", "descricao_atividades", "portfolio_projetos")
        if profile.get(field)
    }


def _phrase_in_text(phrase: str, text: str) -> bool:
    normalized_phrase = _norm(phrase)
    normalized_text = _norm(text)
    if not normalized_phrase or not normalized_text:
        return False
    return re.search(
        rf"(?<!\w){re.escape(normalized_phrase)}(?!\w)", normalized_text,
    ) is not None


def _sector_aliases() -> dict[str, str]:
    """Aliases da taxonomia vigente, sem vocabulário específico da empresa."""
    from radar.core.kg import schema
    from radar.core.kg.gold import normalize_setores

    tax = schema.setores_taxonomia()
    aliases: dict[str, str] = {}
    for label in tax.get("labels") or []:
        canonical = normalize_setores([label])
        if canonical:
            aliases[_norm(label)] = canonical[0]
    for raw, mapped in (tax.get("alias_map") or {}).items():
        values = mapped if isinstance(mapped, list) else [mapped]
        for value in values:
            canonical = normalize_setores([value])
            if canonical:
                aliases[_norm(raw)] = canonical[0]
    for raw, mapped in (tax.get("tese_theme_map") or {}).items():
        values = mapped if isinstance(mapped, list) else [mapped]
        for value in values:
            canonical = normalize_setores([value])
            if canonical:
                aliases[_norm(raw)] = canonical[0]
    return aliases


def _technology_aliases(node: dict[str, Any]) -> set[str]:
    """Valor do nó + aliases do normalizador gold para esse valor."""
    from radar.core.kg import schema
    from radar.core.kg.gold import normalize_tag

    value = str(node.get("value") or "")
    canonical = _norm(normalize_tag(value) or value)
    aliases = {_norm(value), canonical}
    synonyms = schema.tag_normalization().get("synonyms") or {}
    for alias, mapped in synonyms.items():
        if _norm(normalize_tag(str(mapped)) or str(mapped)) == canonical:
            aliases.add(_norm(alias))
    return {item for item in aliases if item}


def _profile_strategy_anchors(
    profile: dict[str, Any] | None, snapshot: Any,
) -> tuple[list[dict[str, Any]], str, dict[str, Any], list[dict[str, Any]]]:
    """Converte o contrato canônico em âncoras virtuais conservadoras.

    Campos estruturados são ``declared``. Setores e tecnologias projetados de
    texto usam somente a taxonomia/aliases existentes e valores efetivamente
    presentes no snapshot. Nenhuma lista específica de empresa é criada.
    """
    virtual = "perfil:virtual"
    edges: list[dict[str, Any]] = []
    profile_view: dict[str, Any] = {"declared": [], "projected": [], "unresolved": []}
    unresolved: list[dict[str, Any]] = []
    if not profile:
        return edges, virtual, profile_view, [{"field": "perfil", "reason": "perfil ausente"}]

    index = _quality_index(snapshot)

    def add_exact(field: str, value: Any, family: str) -> None:
        if value in (None, "", []):
            return
        values = value if isinstance(value, list) else [value]
        for item in values:
            text = str(item).strip()
            if not text:
                continue
            matches = index.get((family, _norm(text)), [])
            if len(matches) != 1:
                unresolved.append({
                    "field": field, "value": text,
                    "reason": "no_exact_quality_node" if not matches else "ambiguous_quality_node",
                })
                continue
            node = matches[0]
            profile_view["declared"].append({"field": field, "value": text, "node_id": node["id"]})
            edges.append(_edge(
                virtual, node["id"], "ancora_perfil", origin="profile_ephemeral",
            ))

    add_exact("uf", profile.get("uf"), "uf")
    add_exact("estagio", profile.get("estagio"), "estagio")

    trl = profile.get("trl")
    if trl is not None:
        faixa_ids = _trl_faixa_for(trl)
        if not faixa_ids:
            unresolved.append({"field": "trl", "value": trl, "reason": "fora_da_taxonomia_trl"})
        else:
            for faixa_id in faixa_ids:
                add_exact("trl", faixa_id, "faixa_trl")

    for item in _as_list(profile.get("tipos_financiamento_interesse")):
        mecanismo = _MECANISMO_MAP.get(_norm(item))
        if mecanismo is None:
            unresolved.append({"field": "tipos_financiamento_interesse", "value": item,
                               "reason": "tipo_de_financiamento_nao_mapeado"})
        else:
            add_exact("tipos_financiamento_interesse", mecanismo, "mecanismo")

    text_fields = _profile_text(profile)
    sector_aliases = _sector_aliases()
    for text_field, text in text_fields.items():
        matched_fields: set[tuple[str, str]] = set()
        for alias, canonical in sector_aliases.items():
            if not _phrase_in_text(alias, text):
                continue
            matches = index.get(("setor", _norm(canonical)), [])
            if len(matches) == 1:
                node = matches[0]
                profile_view["projected"].append({
                    "source_field": text_field, "dimension": "setor",
                    "value": node["value"], "node_id": node["id"], "matched_alias": alias,
                })
                edges.append(_edge(virtual, node["id"], "ancora_textual",
                                   origin="profile_ephemeral"))
                matched_fields.add((text_field, "setor"))

        for node in snapshot.quality_nodes:
            if node.get("family") != "tecnologia":
                continue
            aliases = sorted(_technology_aliases(node), key=lambda value: (len(value), value), reverse=True)
            match = next((alias for alias in aliases if _phrase_in_text(alias, text)), None)
            if match is None:
                continue
            profile_view["projected"].append({
                "source_field": text_field, "dimension": "tecnologia",
                "value": node["value"], "node_id": node["id"], "matched_alias": match,
            })
            edges.append(_edge(virtual, node["id"], "ancora_textual",
                               origin="profile_ephemeral"))
            matched_fields.add((text_field, "tecnologia"))

        for dimension in ("setor", "tecnologia"):
            if (text_field, dimension) not in matched_fields:
                unresolved.append({"field": text_field, "dimension": dimension,
                                   "reason": "no_existing_quality_value_or_alias_in_text"})

    # Deduplica projeções e âncoras por qualidade, preservando a origem dos
    # sinais no payload separado.
    profile_view["projected"] = list({
        (item["dimension"], item["node_id"]): item for item in profile_view["projected"]
    }.values())
    profile_view["unresolved"].extend(unresolved)

    # Evita repetir a mesma âncora quando dois campos chegam ao mesmo nó.
    unique: dict[tuple[str, str], dict[str, Any]] = {}
    for edge in edges:
        unique[(edge["target_id"], edge["type"])] = edge
    return list(unique.values()), virtual, profile_view, unresolved


def _fact_from_step(step: dict[str, Any], *, via_entity_id: str | None = None) -> dict[str, Any]:
    return {
        "source": step["source"], "target": step["target"],
        "predicate": step["predicate"], "weight": step.get("weight", 1.0),
        "origin": step.get("origin", ""), "derived": step.get("derived", False),
        "via_entity_id": via_entity_id,
        "classification": (
            "catalog_structural_fact" if step.get("origin") == "phase1_structural"
            else "cataloged_attribute"
        ),
        "confirmed": True,
    }


def _edge_entry_for_fact(edge: dict[str, Any]) -> dict[str, Any]:
    props = _props_of(edge)
    return {
        "source": edge["source_id"], "target": edge["target_id"],
        "predicate": edge["type"], "weight": float(edge.get("weight") or 1.0),
        "origin": edge.get("origin", ""), "derived": bool(props.get("derived")),
    }


def _strategy_evidence(
    path: list[dict[str, Any]],
    shared: list[dict[str, Any]],
    snapshot: Any,
) -> dict[str, Any]:
    """Separa a rota derivada das evidências que a sustentam."""
    supporting: list[dict[str, Any]] = []
    derived: list[dict[str, Any]] = []
    seen_facts: set[tuple[str, str, str, str]] = set()

    def add_fact(step: dict[str, Any], *, via_entity_id: str | None = None) -> None:
        key = (step["source"], step["target"], step["predicate"], step.get("origin", ""))
        if key not in seen_facts:
            seen_facts.add(key)
            supporting.append(_fact_from_step(step, via_entity_id=via_entity_id))

    entity_ids = {node["id"] for node in snapshot.nodes}
    for step in path:
        origin = step.get("origin")
        if origin in {"phase1_deterministic", "phase1_structural"}:
            via = step["source"] if step["source"] in entity_ids else (
                step["target"] if step["target"] in entity_ids else None
            )
            add_fact(step, via_entity_id=via)
        elif origin in {"phase1_similarity", "phase1_tech_bridge"}:
            derived.append({**step, "classification": "derived_graph_step", "confirmed": False})
        elif origin == "profile_ephemeral":
            derived.append({**step, "classification": "profile_affinity_anchor", "confirmed": False})
        else:
            derived.append({**step, "classification": "insufficient_information", "confirmed": False})

    # The selected shortest route can use only one of several shared qualities.
    # Explain every advertised signal using its real catalog edge.
    for item in shared:
        for edge in snapshot.edges:
            endpoints = {edge["source_id"], edge["target_id"]}
            if item["node_id"] not in endpoints or item["via_entity_id"] not in endpoints:
                continue
            if edge["origin"] not in {"phase1_deterministic", "phase1_structural"}:
                continue
            add_fact(_edge_entry_for_fact(edge), via_entity_id=item["via_entity_id"])
    return {
        "route_relation": {"classification": "derived_profile_route", "confirmed": False},
        "supporting_facts": supporting,
        "derived_steps": derived,
    }


def _strategy_requested_kinds(requested_types: list[str] | None) -> tuple[str, ...]:
    if not requested_types:
        return STRATEGY_KINDS
    selected = tuple(dict.fromkeys(requested_types))
    invalid = sorted(set(selected) - set(STRATEGY_KINDS))
    if invalid:
        raise ValueError("requested_types contém kind não suportado")
    return tuple(kind for kind in STRATEGY_KINDS if kind in selected)


def strategy_payload(
    profile: dict[str, Any] | None, snapshot: Any, *, requested_types: list[str] | None = None,
    max_bytes: int = MAX_PAYLOAD_BYTES,
) -> ToolOutcome:
    """Consulta única, determinística e profile-first sobre o snapshot."""
    profile_edges, virtual, profile_view, unresolved = _profile_strategy_anchors(profile, snapshot)
    recognized = profile_view["declared"] + profile_view["projected"]
    edges = sorted(
        list(snapshot.edges) + profile_edges,
        key=lambda item: (item["source_id"], item["target_id"], item["type"],
                          item.get("origin", ""), float(item.get("weight") or 1.0)),
    )
    edge_index = _edge_index(edges)
    kinds = _strategy_requested_kinds(requested_types)
    payload: dict[str, Any] = {
        "status": "ok" if recognized else "insufficient_profile_anchors",
        "generation_id": snapshot.generation_id,
        "requested_types": list(kinds),
        "profile": profile_view,
        "results_by_type": {},
        "coverage": {kind: {"queried": True, "status": "queried", "total_reachable": 0,
                            "returned": 0, "truncated": False} for kind in STRATEGY_KINDS},
        "truncated": False,
        "limitations": [],
    }
    if not recognized:
        payload["limitations"].append("Sem âncoras exatas suficientes; o grafo não foi usado para aproximar o perfil.")

    # Um índice direto de qualidade preserva todos os sinais compartilhados,
    # mesmo quando a rota explicativa escolhida usa somente um deles.
    shared_by_entity: dict[str, list[dict[str, Any]]] = {}
    for item in recognized:
        anchor_id = item["node_id"]
        for edge in snapshot.edges:
            other = None
            if edge["source_id"] == anchor_id:
                other = edge["target_id"]
            elif edge["target_id"] == anchor_id:
                other = edge["source_id"]
            if other is None or not any(n["id"] == other for n in snapshot.nodes):
                continue
            shared_by_entity.setdefault(other, []).append({
                **item, "via_entity_id": other,
            })
    for entity_id in shared_by_entity:
        unique_shared = {
            (item["node_id"], item["via_entity_id"]): item
            for item in shared_by_entity[entity_id]
        }
        shared_by_entity[entity_id] = [
            unique_shared[key] for key in sorted(unique_shared)
        ]

    for kind in kinds:
        goals = sorted(n["id"] for n in snapshot.nodes if n["kind"] == kind)
        raw_paths = traverse.find_paths_to_goals(
            edges, virtual, goals, max_depth=4,
            limit=max(len(goals), MAX_STRATEGY_RESULTS), min_weight=MIN_WEIGHT,
        )
        candidates: list[tuple[int, int, str, list[dict[str, Any]], list[dict[str, Any]]]] = []
        for raw_path in raw_paths:
            path = _path_entry(raw_path, edge_index)
            target = raw_path[-1][2] if raw_path else ""
            path_entity_ids = {
                node_id for step in raw_path for node_id in (step[0], step[2])
                if node_id in {node["id"] for node in snapshot.nodes}
            }
            shared_map = {
                (item["node_id"], item["via_entity_id"]): item
                for entity_id in path_entity_ids
                for item in shared_by_entity.get(entity_id, [])
            }
            shared = [shared_map[key] for key in sorted(shared_map)]
            candidates.append((len(path), -len(shared), target, path, shared))
        candidates.sort(key=lambda item: (item[1], item[0], item[2]))
        total = len(candidates)
        selected = candidates[:MAX_STRATEGY_RESULTS]
        results = []
        for _, _, target, path, shared in selected:
            node = next(n for n in snapshot.nodes if n["id"] == target)
            results.append({
                "id": node["id"], "name": node["name"], "kind": node["kind"],
                "path": path,
                "shared_characteristics": [{
                    "field": item.get("field", item.get("source_field")),
                    "value": item.get("value"), "node_id": item["node_id"],
                    "via_entity_id": item["via_entity_id"],
                } for item in shared],
                "evidence": _strategy_evidence(path, shared, snapshot),
            })
        payload["results_by_type"][kind] = results
        coverage = payload["coverage"][kind]
        coverage["total_reachable"] = total
        coverage["returned"] = len(results)
        coverage["truncated"] = total > len(results)
        if coverage["truncated"]:
            payload["truncated"] = True
    for kind in STRATEGY_KINDS:
        if kind not in kinds:
            payload["coverage"][kind] = {"queried": False, "status": "not_queried",
                                          "total_reachable": None, "returned": 0, "truncated": False}
    if recognized and not any(payload["results_by_type"].get(kind) for kind in kinds):
        payload["status"] = "empty"
    payload["limitations"].append("Ausência significa ausência no recorte atualmente representado pelo grafo, não inexistência no mercado.")
    result = _trim_payload(payload, max_bytes)
    result_groups = result.get("results_by_type", {})
    return ToolOutcome("hit", snapshot.generation_id,
                       n_nodes=sum(len(v) for v in result_groups.values()),
                       n_paths=sum(len(v) for v in result_groups.values()),
                       payload=result)


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
    with _LOCK:
        stats = _RUN_STATS.setdefault(
            tool_name, {"calls": 0.0, "fallbacks": 0.0, "duration_ms": 0.0},
        )
        stats["calls"] += 1.0
        stats["duration_ms"] += float(duration_ms)
        if fallback:
            stats["fallbacks"] += 1.0
    logger.info(
        "kg_phase1_explore tool=%s outcome=%s generation_id=%s duration_ms=%.1f "
        "nodes=%d edges=%d paths=%d fallback=%s category=%s",
        tool_name, outcome, generation_id, duration_ms,
        n_nodes, n_edges, n_paths, str(fallback).lower(), category or "-",
    )


def _run(tool_name: str, fn, *, unavailable_msg: str = _UNAVAILABLE_MSG,
         error_msg: str = _ERROR_MSG) -> str:
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
        return dump({"status": outcome, "message": unavailable_msg if outcome == "unavailable" else error_msg,
                     "results_by_type": {}, "coverage": {}, "truncated": False})

    if snapshot is None:
        _observe(
            tool_name, outcome="unavailable", generation_id=None,
            duration_ms=_elapsed(started), fallback=True,
        )
        return dump({"status": "unavailable", "message": unavailable_msg,
                     "results_by_type": {}, "coverage": {}, "truncated": False})

    try:
        result = fn(snapshot)
    except Exception as exc:  # noqa: BLE001
        _observe(
            tool_name, outcome="error", generation_id=snapshot.generation_id,
            duration_ms=_elapsed(started), fallback=True, category=_category_of(exc),
        )
        return dump({"status": "error", "message": error_msg,
                     "results_by_type": {}, "coverage": {}, "truncated": False})

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
    """Consulta profile-first read-only do grafo da Fase 1.

    Flag off → lista vazia (comportamento do ExploreAgent intocado). Quando
    ligada, somente ``graph_strategy`` é exposta; o perfil é capturado por
    CLOSURE e nunca é argumento preenchível pela LLM."""
    if not graph_tools_enabled():
        return []

    unavailable = "Grafo da Fase 1 indisponível; não foi possível consultar o recorte do perfil."
    error = "Falha ao consultar o grafo da Fase 1; nenhum resultado estratégico foi fabricado."

    @tool
    def graph_strategy(requested_types: list[str] | None = None) -> str:
        """Consulta o grafo exclusivamente a partir do perfil autenticado injetado.

        Retorna, em uma única consulta, oportunidades/editais, programas,
        agências, ICTs e investidores, com âncoras reconhecidas, atributos não
        resolvidos, caminhos, características compartilhadas, classificação da
        relação e cobertura. ``requested_types`` é opcional e aceita apenas a
        lista fechada ``edital``, ``programa``, ``agencia``, ``ict``,
        ``investidor``; vazio consulta todos os tipos. Kind inválido é rejeitado.
        Nunca informe um
        perfil, entity ID ou node ID nesta ferramenta.
        """
        try:
            _strategy_requested_kinds(requested_types)
        except ValueError:
            return dump({
                "status": "invalid_request",
                "message": "requested_types contém kind não suportado; use os kinds canônicos.",
                "coverage": {kind: {"queried": False, "status": "not_queried"}
                             for kind in STRATEGY_KINDS},
                "truncated": False,
            })
        return _run(
            "graph_strategy",
            lambda snap: strategy_payload(profile, snap, requested_types=requested_types),
            unavailable_msg=unavailable, error_msg=error,
        )

    @tool
    def graph_explore(ref: str, depth: int = 1) -> str:
        """Explora a vizinhança estrutural de uma entidade do snapshot atual."""
        return _run(
            "graph_explore",
            lambda snap: explore_payload(ref, snap, depth=depth),
            unavailable_msg=unavailable, error_msg=error,
        )

    @tool
    def graph_reason(ref: str, max_depth: int = 3) -> str:
        """Explica caminhos entre o perfil injetado e uma entidade atual."""
        return _run(
            "graph_reason",
            lambda snap: reason_payload(ref, snap, profile=profile, max_depth=max_depth),
            unavailable_msg=unavailable, error_msg=error,
        )

    @tool
    def graph_community(ref: str) -> str:
        """Consulta uma comunidade existente e suas características compartilhadas."""
        return _run(
            "graph_community",
            lambda snap: community_payload(ref, snap),
            unavailable_msg=unavailable, error_msg=error,
        )

    return [graph_strategy, graph_explore, graph_reason, graph_community]
