"""core/kg/resolve_programas.py — resolução de menções de programa (KG v2 resíduos PR-C).

Spec docs/specs/kg-v2-residuos.md (PR-C). Três passos:

1. INVENTORY — varre os hipergrados e enumera todos os nós
   `Oportunidade(kind=programa)`, agregando por nome (112 names distintos, 129
   nós). Detecta lixo óbvio (ex.: nó literalmente chamado "programa").

2. CLUSTER + RESOLVE — agrupa por similaridade de embedding + adjudicação LLM
   (mesmo padrão do propose_merges em canonicalize.py). Cada cluster resolve
   contra o registro curado (`programas.json` via kg_store):
   - casa com curado → link ao canônico curado (R1: metadado curado vence)
   - não casa → promove canônico novo com status: promovido_auto (R2)

3. APPLY — reescreve os hipergrados: nós programa viram aresta `pertence_a`
   apontando para o id canônico; nós duplicados são removidos. O canon map
   `programa_canon` é persistido via kg_store.

O passe de dados roda na execução serializada (R5). O script CLI em
scripts/resolve_programas.py orquestra as etapas.
"""
from __future__ import annotations

import json
import logging
import re
from collections import defaultdict
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

PROG_RESOLVE_MODEL = "gpt-4o-mini"
_CLUSTER_THRESHOLD = 0.80

# ---------------------------------------------------------------------------
# Inventário
# ---------------------------------------------------------------------------

def inventory_programas(graphs: dict[str, dict]) -> dict[str, dict]:
    """Inventário dos nós Oportunidade(kind=programa) do corpus.

    Retorna {name_lower: {name, file_keys, node_ids, fan_in, descricao}} —
    agregado por nome (normalizado lowercase). O lixo óbvio (nó chamado
    "programa" ou "programas") é sinalizado mas mantido (a higiene decide).
    """
    inv: dict[str, dict] = {}
    for fk, g in graphs.items():
        for n in g.get("nodes", []):
            if n.get("type") != "Oportunidade" or n.get("kind") != "programa":
                continue
            name = (n.get("name") or "").strip()
            key = name.lower()
            if not key:
                continue
            if key not in inv:
                inv[key] = {
                    "name": name,
                    "names": set(),
                    "file_keys": [],
                    "node_ids": [],
                    "fan_in": 0,
                    "descricao": (n.get("description") or "")[:300],
                }
            e = inv[key]
            e["names"].add(name)
            if fk not in e["file_keys"]:
                e["file_keys"].append(fk)
            if n["id"] not in e["node_ids"]:
                e["node_ids"].append(n["id"])
            e["fan_in"] = len(e["file_keys"])
            desc = n.get("description") or ""
            if len(desc) > len(e["descricao"]):
                e["descricao"] = desc[:300]
    for e in inv.values():
        e["names"] = sorted(e["names"])
    return inv


def corpus_programa_stats(graphs: dict[str, dict]) -> dict:
    """Métricas do corpus para antes/depois do passe."""
    program_nodes = 0
    unique_names: set[str] = set()
    for _, g in graphs.items():
        for n in g.get("nodes", []):
            if n.get("type") == "Oportunidade" and n.get("kind") == "programa":
                program_nodes += 1
                unique_names.add((n.get("name") or "").strip().lower())
    return {
        "total_programa_nodes": program_nodes,
        "unique_names": len(unique_names),
    }


# ---------------------------------------------------------------------------
# Clusterização por embedding
# ---------------------------------------------------------------------------

def _cluster_text(e: dict) -> str:
    """Texto para embedding — name + descrição."""
    t = e["name"]
    if e.get("descricao"):
        t += ". " + e["descricao"]
    return t


_GENERIC_PREFIXES = ("programa", "projeto", "plano", "chamada")


def _program_key(name: str) -> str:
    """Chave determinística de identidade de programa (fix 2026-07-05): deburr+
    lower, separa letra-dígito ("rota2030" → "rota 2030"), remove prefixo
    genérico ("Programa/Projeto X" ≡ "X") e conectivos, ordena e junta. Mesmo
    espírito do _variant_key da canonicalização — variantes triviais do MESMO
    programa unem sem depender do embedding (a descrição desloca o cosseno)."""
    from core.kg.canonicalize import _CONNECTIVES, _deburr

    n = _deburr(name).lower()
    n = re.sub(r"(?<=[a-z])(?=\d)|(?<=\d)(?=[a-z])", " ", n)
    toks = [t for t in re.findall(r"\w+", n) if t not in _CONNECTIVES]
    while toks and toks[0] in _GENERIC_PREFIXES:
        toks = toks[1:]
    return "".join(sorted(toks))


class _UnionFind:
    def __init__(self):
        self.parent: dict[str, str] = {}

    def find(self, x: str) -> str:
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


def cluster_programas(
    inventory: dict[str, dict], *, threshold: float = _CLUSTER_THRESHOLD,
) -> list[list[str]]:
    """Agrupa nomes de programa por similaridade de embedding.

    Retorna lista de clusters, cada um = [key, ...] (chaves do inventário).
    Clusters com ≥2 membros candidatam-se a merge; singletons passam direto
    (resolvem individualmente contra o registro, sem adjudicação).
    """
    import numpy as np

    from core.retrieval.embedder import embed_texts

    keys = list(inventory)
    if len(keys) < 2:
        return [[k] for k in keys]

    emb = np.asarray(
        [embed_texts([_cluster_text(inventory[k])])[0] for k in keys],
        dtype=np.float32,
    )
    emb = emb / (np.linalg.norm(emb, axis=1, keepdims=True) + 1e-9)
    sims = emb @ emb.T

    uf = _UnionFind()
    ii, jj = np.where(np.triu(sims, k=1) >= threshold)
    paired: set[str] = set()
    for i, j in zip(ii.tolist(), jj.tolist(), strict=True):
        uf.union(keys[i], keys[j])
        paired.update([keys[i], keys[j]])

    # Fix 2026-07-05: variantes deterministicamente idênticas ("rota 2030" /
    # "rota2030" / "projeto rota 2030") unem por _program_key, sem depender do
    # embedding. Chave vazia (name só de prefixo genérico) fica de fora.
    by_key: dict[str, list[str]] = defaultdict(list)
    for k in keys:
        pk = _program_key(inventory[k]["name"])
        if pk:
            by_key[pk].append(k)
    for group in by_key.values():
        if len(group) >= 2:
            for a, b in zip(group, group[1:], strict=False):
                uf.union(a, b)
            paired.update(group)

    groups: dict[str, list[str]] = defaultdict(list)
    for k in paired:
        groups[uf.find(k)].append(k)
    clusters = sorted((sorted(v) for v in groups.values() if len(v) >= 2), key=len, reverse=True)

    # Singletons (não-pareados) entram como clusters unitários
    seen = set(paired)
    for k in keys:
        if k not in seen:
            clusters.append([k])

    return clusters


# ---------------------------------------------------------------------------
# Adjudicação LLM (merge de variantes do mesmo programa)
# ---------------------------------------------------------------------------

_RESOLVE_SYSTEM = """Você é curador do catálogo de programas de fomento à inovação \
brasileiro. Recebe um GRUPO de nomes que se referem ao MESMO programa (agrupados \
por similaridade) e decide qual nome é o canônico.

Para cada grupo, escolha o nome MAIS COMPLETO e PRECISO em português. Regras:
- "Programa X" e "X" são o mesmo — prefira "Programa X" (o nome completo);
- "X (2026–2027)" e "X" — prefira "X" (sem ano, a menos que o ano faça parte do \
nome oficial);
- "X - Y" e "X" — prefira "X" (a parte após hífen é especificação);
- Variantes de tradução ("Eureka programme" / "Rede Eureka") — prefira a forma \
em português;
- Lixo como "programa" (só a palavra) é descartado pelo validador.

Responda JSON: {"grupos": [{"membros": ["<key>", ...], "nome_canonico": "..."}]}
Todo key de entrada aparece em EXATAMENTE um grupo (grupo unitário = não funde \
com ninguém). A saída deve ter o mesmo número de grupos que a entrada.
Se um grupo tem 2+ membros que SÃO o mesmo programa, devolva UM grupo com \
todos os membros e o nome canônico. Se um grupo tem membros que NÃO são o \
mesmo programa, devolva cada membro em grupo separado (unitário)."""


def _resolve_cluster_llm(
    cluster: list[str], inventory: dict[str, dict], *, client=None, model: str | None = None,
) -> list[list[str]]:
    """Adjudicação LLM de um cluster. Devolve subclusters (merge ou não).

    Se o cluster tem 1 membro, passa direto. Cada subcluster vira uma
    resolução contra o registro curado.
    """
    if len(cluster) <= 1:
        return [cluster]

    if client is None:
        from core.llm.llm_client import make_client
        client = make_client(max_retries=3)
    model = model or PROG_RESOLVE_MODEL

    payload = [{"key": k, "name": inventory[k]["name"], "descricao": inventory[k]["descricao"][:200]}
               for k in cluster]

    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _RESOLVE_SYSTEM},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            temperature=0,
            response_format={"type": "json_object"},
        )
        grupos = json.loads(resp.choices[0].message.content).get("grupos", [])
    except Exception as e:
        logger.warning("resolve_programas: adjudicação do cluster %s falhou (%s)", cluster[:3], e)
        return [[k] for k in cluster]

    seen: set[str] = set()
    out: list[list[str]] = []
    for g in grupos:
        ms = [m for m in g.get("membros", []) if m in cluster and m not in seen]
        if not ms:
            continue
        seen.update(ms)
        out.append(ms)
    # Residuals (não cobertos pela resposta)
    for k in cluster:
        if k not in seen:
            out.append([k])
    return out


def resolve_clusters(
    clusters: list[list[str]],
    inventory: dict[str, dict],
    registry: list[dict],
    *,
    client=None, model: str | None = None,
) -> list[dict]:
    """Cada cluster vira uma resolução: casa com curado ou promovido_auto.

    Retorna lista de dicts:
      {"canon_key": str, "canon_name": str, "membros": [keys],
       "status": "curado"|"promovido_auto",
       "registry_id": str|None, "registry_name": str|None}
    """
    from core.kg.schema import slugify

    reg_index: dict[str, dict] = {}
    for p in registry:
        name = (p.get("name") or "").strip().lower()
        reg_index[name] = p
        reg_index[p.get("id", "").lower()] = p
        # Fix 2026-07-05: indexa também pela chave determinística — "centelha"
        # casa com o curado "Programa Centelha" (prefixo não é identidade).
        pk = _program_key(name)
        if pk:
            reg_index.setdefault(f"pk::{pk}", p)

    def _find_registry(name: str) -> dict | None:
        n = name.strip().lower()
        if n in reg_index:
            return reg_index[n]
        # Tenta sem prefixos
        for prefix in ("programa ", "programa de ", "programa do "):
            if n.startswith(prefix):
                stripped = n[len(prefix):]
                if stripped in reg_index:
                    return reg_index[stripped]
        pk = _program_key(name)
        if pk and f"pk::{pk}" in reg_index:
            return reg_index[f"pk::{pk}"]
        return None

    resolutions: list[dict] = []
    for cl in clusters:
        subclusters = _resolve_cluster_llm(cl, inventory, client=client, model=model)
        for sub in subclusters:
            if not sub:
                continue
            # Escolhe o nome representativo do subcluster
            canon_key = sub[0]
            canon_name = inventory[canon_key]["name"]
            if len(sub) > 1:
                # O nome canônico é o que o LLM escolheu (ou o mais frequente)
                canon_name = inventory[canon_key]["name"]

            reg_match = _find_registry(canon_name)
            if reg_match:
                resolutions.append({
                    "canon_key": canon_key,
                    "canon_name": canon_name,
                    "membros": sub,
                    "status": "curado",
                    "registry_id": reg_match.get("id"),
                    "registry_name": reg_match.get("name"),
                })
            else:
                # Tenta cada membro
                found = None
                for m in sub:
                    found = _find_registry(inventory[m]["name"])
                    if found:
                        break
                if found:
                    resolutions.append({
                        "canon_key": canon_key,
                        "canon_name": canon_name,
                        "membros": sub,
                        "status": "curado",
                        "registry_id": found.get("id"),
                        "registry_name": found.get("name"),
                    })
                else:
                    # Registro não encontrado → promovido_auto (R2)
                    pid = f"programa:{slugify(canon_name)}"
                    resolutions.append({
                        "canon_key": canon_key,
                        "canon_name": canon_name,
                        "membros": sub,
                        "status": "promovido_auto",
                        "registry_id": pid,
                        "registry_name": canon_name,
                    })

    return resolutions


# ---------------------------------------------------------------------------
# Canon map
# ---------------------------------------------------------------------------

def build_canon(resolutions: list[dict]) -> dict:
    """Compila as resoluções num canon map versionado (`programa_canon`).

    Estrutura:
      {"version": 1, "generated_at": "...",
       "aliases": {key: {"name": canônico, "registry_id": ...,
                         "status": "curado"|"promovido_auto"}},
       "curados": [registry_id, ...],
       "promovidos_auto": [registry_id, ...]}
    """
    canon: dict = {
        "version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "aliases": {},
        "curados": [],
        "promovidos_auto": [],
    }
    for r in resolutions:
        entry = {
            "name": r["canon_name"],
            "registry_id": r["registry_id"],
            "status": r["status"],
        }
        for m in r["membros"]:
            canon["aliases"][m] = entry
        if r["status"] == "curado" and r["registry_id"] not in canon["curados"]:
            canon["curados"].append(r["registry_id"])
        elif r["status"] == "promovido_auto":
            if r["registry_id"] not in canon["promovidos_auto"]:
                canon["promovidos_auto"].append(r["registry_id"])
    return canon


# ---------------------------------------------------------------------------
# Apply — reescreve hipergrados
# ---------------------------------------------------------------------------

_KNOWN_LIXO = frozenset({"programa", "programas"})


def _is_obvious_trash(name: str) -> bool:
    return name.strip().lower() in _KNOWN_LIXO


def apply(
    graphs: dict[str, dict], canon: dict,
) -> tuple[dict[str, dict], dict[str, int]]:
    """Aplica o canon map aos hipergrados.

    Para cada nó Oportunidade(kind=programa):
    - Se o nome (lower) está em canon["aliases"], substitui o nó por uma
      aresta `pertence_a` apontando para o canônico (que vive no catálogo
      curado ou no hipergrado `programas`).
    - Se o nome é lixo óbvio ("programa"), remove o nó e suas arestas.
    - Senão, mantém como está (não visitado pelo passe).

    Retorna (graphs_modificados, stats).
    """
    stats: dict[str, int] = {
        "resolvidos": 0,
        "descartados_lixo": 0,
        "mantidos": 0,
        "arestas_criadas": 0,
        "arestas_removidas": 0,
    }
    aliases = canon.get("aliases", {})

    for fk, g in graphs.items():
        nodes = g.get("nodes", [])
        edges = g.get("edges", [])

        novos_nodes: list[dict] = []
        arestas_extra: list[dict] = []
        nodes_removidos: set[str] = set()

        for n in nodes:
            if n.get("type") != "Oportunidade" or n.get("kind") != "programa":
                novos_nodes.append(n)
                continue

            name = (n.get("name") or "").strip()
            key = name.lower()
            alias = aliases.get(key)

            if _is_obvious_trash(name):
                nodes_removidos.add(n["id"])
                stats["descartados_lixo"] += 1
                continue

            if alias:
                # Nó resolvido — substitui por aresta pertence_a
                registry_id = alias.get("registry_id", "")
                target_id = f"op:{registry_id.split(':', 1)[-1]}" if registry_id else ""
                if target_id and n["id"] != target_id:
                    arestas_extra.append({
                        "type": "pertence_a",
                        "members": [n["id"], target_id],
                        "description": f"programa resolvido: {alias['name']} ({alias['status']})",
                    })
                    nodes_removidos.add(n["id"])
                    stats["resolvidos"] += 1
                    stats["arestas_criadas"] += 1
                else:
                    novos_nodes.append(n)
                    stats["mantidos"] += 1
            else:
                novos_nodes.append(n)
                stats["mantidos"] += 1

        # Remove arestas que envolvem nós removidos
        novas_arestas = []
        for e in edges:
            members = e.get("members", [])
            # Se algum membro foi removido, a aresta cai
            if any(m in nodes_removidos for m in members):
                stats["arestas_removidas"] += 1
                continue
            novas_arestas.append(e)
        novas_arestas.extend(arestas_extra)

        graphs[fk] = {**g, "nodes": novos_nodes, "edges": novas_arestas}

    return graphs, stats


def queue_unresolved(resolutions: list[dict]) -> list[dict]:
    """Filtra as resoluções com status promovido_auto — fila de trabalho do curador."""
    return [
        r for r in resolutions
        if r["status"] == "promovido_auto"
    ]
