"""core/kg/canonicalize.py — higiene + canonicalização dos nós Conceito (KG v2 PR3).

Spec docs/specs/kg-redesign.md (PR3). O módulo tem duas metades com contratos
distintos:

  • PROPOSE (LLM/embeddings — julgamento): validação batch dos Conceitos
    (descarte de não-conceito, correção de `dim`, reclassificação de Ator,
    promoção dos ex-Entidade) e clustering por cosseno + adjudicação LLM das
    duplicatas semânticas cross-arquivo. Produz PLANOS auditáveis (dicts JSON-
    serializáveis) — nunca escreve nos grafos.
  • APPLY (mecânico): `canonicalize_graph` aplica o CANON MAP a um grafo v2 —
    puro, determinístico, idempotente, zero LLM. Mesmo padrão do migrador
    (core/kg/migrate_v2): retorna um NOVO dict, re-aponta arestas por id.

O produto durável é o CANON MAP (artefato `concept_canon` no kg_store),
consumido por:
  • scripts/canonicalize_concepts.py — o passe de build sobre o corpus inteiro;
  • ingest (core/retrieval/hyper_extractor) — oportunidade nova entra
    canonicalizada aplicando o mesmo canon (replay determinístico).

Guardrail da spec: merges são logados com score e amostrados manualmente antes
do apply (risco de over-merge). Descartes viram log auditável, não deleção cega.
"""
from __future__ import annotations

import json
import logging
import os
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone

from core.kg.migrate_v2 import ENTIDADE_V1_ORIGEM
from core.kg.schema import slugify

logger = logging.getLogger(__name__)

CANON_VERSION = 1

# Modelo do passe de higiene — build-time, desacoplado do OPENAI_MODEL global
# (mesma razão do HYPER_EXTRACT_MODEL: artefato de build não muda porque o tier
# de runtime trocou). Override consciente via env.
CANON_MODEL = os.environ.get("CANON_MODEL", "gpt-4o-mini")
# Adjudicação de merge usa um modelo mais forte: o mini funde RELACIONADOS
# (medido 2026-07-04: ~20/87 grupos over-merged — "arranjo em rede"+"arranjo
# simples", guarda-chuvas inventados). São só ~10² calls por passe, custo baixo.
CANON_MERGE_MODEL = os.environ.get("CANON_MERGE_MODEL", "gpt-4o")

VALIDATION_BATCH = 40
MACRO_BATCH = 12

# Threshold de cosseno p/ candidatar um par de Conceitos a merge. Conservador de
# propósito (over-merge é o risco; under-merge só deixa estrada por construir).
# A adjudicação LLM decide DENTRO do cluster — o threshold só limita o recall
# dos candidatos. Calibrável olhando a distribuição impressa pelo propose.
MERGE_THRESHOLD = float(os.environ.get("CANON_MERGE_THRESHOLD", "0.85"))
_MAX_CLUSTER = 20  # cap de membros por chamada de adjudicação


# ---------------------------------------------------------------------------
# Inventário
# ---------------------------------------------------------------------------

def inventory_concepts(graphs: dict[str, dict]) -> dict[str, dict]:
    """Inventário dos Conceitos únicos do corpus: id → agregado cross-arquivo.

    O mesmo (Conceito, name) tem o mesmo id em qualquer subgrafo (PR1), então o
    id é a unidade de curadoria. Agrega: `dim` = moda entre as instâncias (o
    extractor confunde dims cross-arquivo), `description` = a mais longa (mais
    sinal p/ o LLM), `files` = fan-in observado, `ex_entidade` = True se ALGUMA
    instância carrega a marca `origem=entidade_v1` (D5)."""
    inv: dict[str, dict] = {}
    dims: dict[str, Counter] = defaultdict(Counter)
    for fk, g in graphs.items():
        for n in g.get("nodes", []):
            if n.get("type") != "Conceito" or not n.get("id"):
                continue
            nid = n["id"]
            e = inv.setdefault(nid, {
                "id": nid, "name": n.get("name") or "", "description": "",
                "files": [], "ex_entidade": False,
            })
            if fk not in e["files"]:
                e["files"].append(fk)
            desc = n.get("description") or ""
            if len(desc) > len(e["description"]):
                e["description"] = desc
            if n.get("dim"):
                dims[nid][n["dim"]] += 1
            if n.get("origem") == ENTIDADE_V1_ORIGEM:
                e["ex_entidade"] = True
    for nid, e in inv.items():
        e["dim"] = dims[nid].most_common(1)[0][0] if dims[nid] else "tema"
        e["fan_in"] = len(e["files"])
    return inv


def corpus_stats(graphs: dict[str, dict]) -> dict:
    """Métricas do corpus p/ o antes/depois do passe (fan-in é A métrica: mede
    se o 'mapa' ganhou estradas — spec, critério de pronto do PR3)."""
    types = Counter()
    dims = Counter()
    concept_files: dict[str, set[str]] = defaultdict(set)
    ex_entidade = 0
    n_edges = 0
    for fk, g in graphs.items():
        n_edges += len(g.get("edges", []))
        for n in g.get("nodes", []):
            types[n.get("type")] += 1
            if n.get("type") == "Conceito":
                concept_files[n.get("id") or n.get("name", "")].add(fk)
                dims[n.get("dim")] += 1
                if n.get("origem") == ENTIDADE_V1_ORIGEM:
                    ex_entidade += 1
    fanin = [len(v) for v in concept_files.values()] or [0]
    single = sum(1 for x in fanin if x == 1)
    return {
        "arquivos": len(graphs),
        "nos_por_tipo": dict(types),
        "arestas": n_edges,
        "conceitos_instancias": types.get("Conceito", 0),
        "conceitos_ids_unicos": len(concept_files),
        "conceitos_ex_entidade": ex_entidade,
        "dims": dict(dims),
        "fan_in_medio": round(sum(fanin) / len(fanin), 3),
        "ids_em_1_arquivo": single,
        "pct_em_1_arquivo": round(100 * single / max(len(fanin), 1), 1),
    }


# ---------------------------------------------------------------------------
# LLM (propose) — validação
# ---------------------------------------------------------------------------

def _make_llm():
    from core.llm.llm_client import make_client
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("OPENAI_API_KEY não definida (o passe de higiene usa LLM)")
    return make_client(api_key=api_key, max_retries=6), CANON_MODEL


_VALIDATION_SYSTEM = """Você é curador do grafo de conhecimento do ecossistema brasileiro de \
fomento à inovação (editais, ICTs, investidores, programas).

Os nós `Conceito` do grafo devem ser DIMENSÕES TEMÁTICAS comparáveis por similaridade \
entre a demanda (o que uma empresa faz) e a oferta (o que um edital cobre): temas \
(saúde, agronegócio), tecnologias (IA, visão computacional) e aplicações (diagnóstico \
por imagem, monitoramento de safra). A extração deixou passar ruído — seu trabalho é \
classificá-lo.

Para CADA item da lista, dê um veredicto:
- "manter"    — conceito temático legítimo;
- "descartar" — NÃO é conceito: geografia/região ("Brasil", "norte", "Santa Catarina"), \
boilerplate legal/orçamentário (leis, decretos, "LDO 2026", rubricas, portarias), \
administrativo/formulário ("PROPOSTA", nomes de sistema de submissão, seções de \
documento, cronogramas, cadastros e certidões como CNPJ), datas/valores/percentuais, \
classe genérica de ator ("startups", "empresas", "ICTs", "pesquisadores" — público-alvo, \
não tema), fragmento truncado sem sentido;
- "ator"      — é uma ORGANIZAÇÃO ESPECÍFICA com nome próprio (ex.: JUCESC, EMBRAPII, \
Ministério da Saúde, Sebrae) classificada errado como conceito. Classe genérica de \
organização NÃO é ator — é descarte (categoria "generico").

Campos por veredicto:
- "manter": informe SEMPRE "dim" — tema (domínio amplo) | tecnologia (capacidade \
técnica) | aplicacao (caso de uso concreto, uma operação aplicada a um objeto). \
Ecoe a dim atual se estiver certa; corrija se estiver errada.
- "descartar": informe "categoria" — geografia | legal | administrativo | sistema | \
orcamentario | generico | outro.
- "ator": informe "ator_kind" — agencia | fap | ict | corporate | aceleradora | investidor.

Responda JSON: {"itens": [{"id": "...", "veredicto": "...", "dim": "...", \
"categoria": "...", "ator_kind": "..."}]}
Exatamente um item de saída por item de entrada, com o MESMO id. Na dúvida entre \
manter e descartar, mantenha (o descarte remove o nó do grafo)."""


def propose_validation(
    concepts: dict[str, dict], *, batch_size: int = VALIDATION_BATCH,
    client=None, model: str | None = None, max_workers: int = 6,
) -> dict[str, dict]:
    """Passe LLM 1 (validação): id → {veredicto, dim?, categoria?, ator_kind?}.

    Batches de `batch_size` itens (JSON mode, temperature 0), paralelos por
    thread (batches independentes). Fail-open por batch: erro de chamada/parse
    vira veredicto "manter" p/ os itens do batch (higiene nunca destrói dado
    por falha de infra) — logado como warning."""
    from concurrent.futures import ThreadPoolExecutor

    if client is None:
        client, model = _make_llm()
    model = model or CANON_MODEL

    items = list(concepts.values())
    batches = [items[s:s + batch_size] for s in range(0, len(items), batch_size)]

    def _judge(batch: list[dict]) -> dict[str, dict]:
        payload = [
            {
                "id": c["id"], "name": c["name"], "dim": c["dim"],
                "descricao": (c.get("description") or "")[:200],
                "aparece_em_n_arquivos": c.get("fan_in", 1),
                # Sinal p/ o LLM: veio do catch-all Entidade v1 (mais suspeito).
                "ex_entidade": bool(c.get("ex_entidade")),
            }
            for c in batch
        ]
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": _VALIDATION_SYSTEM},
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                ],
                temperature=0,
                response_format={"type": "json_object"},
            )
            out = json.loads(resp.choices[0].message.content).get("itens", [])
            return {it.get("id"): it for it in out if it.get("id")}
        except Exception as e:  # noqa: BLE001 — fail-open (ver docstring)
            logger.warning("propose_validation: batch de %d falhou (%s) — manter todos", len(batch), e)
            return {}

    plan: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        for batch, got in zip(batches, ex.map(_judge, batches), strict=True):
            for c in batch:
                it = got.get(c["id"])
                if it is None or it.get("veredicto") not in ("manter", "descartar", "ator"):
                    it = {"veredicto": "manter", "fail_open": True}
                entry = {k: v for k, v in it.items() if k != "id" and v}
                if entry["veredicto"] == "manter":
                    # dim só entra quando CORRIGE a atual (canon enxuto); ex-Entidade
                    # mantido é PROMOVIDO (perde a marca → afinidade, D5/PR3).
                    if entry.get("dim") == c["dim"]:
                        entry.pop("dim", None)
                    if c.get("ex_entidade") and not entry.get("fail_open"):
                        entry["promote"] = True
                plan[c["id"]] = entry
            logger.info("propose_validation: +%d itens (total %d/%d)", len(batch), len(plan), len(items))
    return plan


# ---------------------------------------------------------------------------
# Embeddings + LLM (propose) — merges
# ---------------------------------------------------------------------------

def _concept_text(c: dict) -> str:
    """Texto p/ embedding de clustering — dim + name + descrição agregada (mais
    contexto que o `_node_text` do match, que embeda instância por instância)."""
    return f"{c['dim']}: {c['name']}. {c.get('description', '')}".strip()


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


_MERGE_SYSTEM = """Você é curador de vocabulário do grafo de conhecimento do ecossistema \
brasileiro de fomento à inovação. Recebe um GRUPO de conceitos candidatos a serem o \
MESMO conceito (agrupados por similaridade de embedding) e decide o que funde.

O teste: dois itens só fundem se forem INTERCAMBIÁVEIS — quem procura um aceitaria \
o outro como exatamente a mesma coisa. Fundem: sinônimos, traduções ("machine \
learning" ≡ "aprendizado de máquina"), siglas ("TRL" ≡ "Technology Readiness \
Level"), variações de grafia/pontuação da MESMA expressão.

NÃO fundem (mantenha separados, mesmo que o embedding os aproxime):
- um-contém-o-outro / especializações: "saúde" ≠ "saúde digital"; "eficiência \
energética" ≠ "eficiência energética de equipamentos";
- irmãos de uma mesma família: "redes elétricas interligadas" ≠ "redes elétricas \
isoladas"; "produção animal" ≠ "produção vegetal"; hardware ≠ software;
- itens distintos de uma lista/guarda-chuva: NUNCA crie um termo guarda-chuva \
para abrigar vários conceitos diferentes ("adesivos" + "plásticos" + "tintas" \
NÃO viram "materiais diversos").
Na dúvida, SEPARE — grupo unitário é o default seguro.

- "nome_canonico": escolha O NAME DE UM DOS MEMBROS do grupo (o mais claro/completo \
em português) — não invente um termo novo. Normalização leve é permitida \
(minúsculas, expandir sigla que já aparece no name escolhido).
- "dim": tema | tecnologia | aplicacao do conceito canônico.

Responda JSON: {"grupos": [{"membros": ["<id>", ...], "nome_canonico": "...", "dim": "..."}]}
Todo id de entrada aparece em EXATAMENTE um grupo. Grupo unitário = não funde."""

# Guardas mecânicos anti-over-merge (aplicados sobre a resposta do LLM):
_MAX_GROUP = 6          # grupo maior que isso é quase sempre guarda-chuva
_CANON_MIN_RATIO = 0.5  # containment slug-canônico↔slug-membro (mata nome inventado)


def _canonical_derives_from_member(nome: str, member_ids: list[str]) -> bool:
    """True se slug(nome_canonico) deriva de algum membro: um contém o outro com
    razão de tamanho ≥ _CANON_MIN_RATIO. Aceita "cidades inteligentes" p/ membro
    `con:cidades-inteligentes-smart-cities`; rejeita "wzl gear toolbox" p/ membros
    `con:bevelcut`/`con:geargrind3d` (guarda-chuva inventado)."""
    cslug = slugify(nome)
    if not cslug:
        return False
    for mid in member_ids:
        mslug = mid.split(":", 1)[-1]
        shorter, longer = sorted((cslug, mslug), key=len)
        if shorter in longer and len(shorter) / len(longer) >= _CANON_MIN_RATIO:
            return True
    return False


def _cluster_candidates(
    concepts: dict[str, dict], *, threshold: float,
) -> tuple[list[list[str]], dict[tuple[str, str], float]]:
    """Componentes conexos de pares com cosseno ≥ threshold. Retorna (clusters
    com ≥2 membros, scores dos pares) — os scores vão pro log de auditoria."""
    import numpy as np

    from core.retrieval.embedder import embed_texts

    ids = list(concepts)
    if len(ids) < 2:
        return [], {}
    emb = np.asarray(embed_texts([_concept_text(concepts[i]) for i in ids]), dtype=np.float32)
    emb = emb / (np.linalg.norm(emb, axis=1, keepdims=True) + 1e-9)
    sims = emb @ emb.T

    uf = _UnionFind()
    pair_scores: dict[tuple[str, str], float] = {}
    ii, jj = np.where(np.triu(sims, k=1) >= threshold)
    for i, j in zip(ii.tolist(), jj.tolist(), strict=True):
        a, b = ids[i], ids[j]
        pair_scores[(a, b)] = float(sims[i, j])
        uf.union(a, b)

    groups: dict[str, list[str]] = defaultdict(list)
    for nid in {x for pair in pair_scores for x in pair}:
        groups[uf.find(nid)].append(nid)
    clusters = sorted((sorted(v) for v in groups.values() if len(v) >= 2), key=len, reverse=True)
    return clusters, pair_scores


def _split_oversized(cluster: list[str], pair_scores: dict, cap: int = _MAX_CLUSTER) -> list[list[str]]:
    """Quebra clusters acima do cap re-thresholdando os pares internos (evita o
    componente-gigante por encadeamento e mantém o prompt de adjudicação sano)."""
    if len(cluster) <= cap:
        return [cluster]
    inside = {p: s for p, s in pair_scores.items() if p[0] in cluster and p[1] in cluster}
    if not inside:
        return [cluster[:cap]]
    thr = min(inside.values())
    for _ in range(50):
        thr += 0.01
        uf = _UnionFind()
        for (a, b), s in inside.items():
            if s >= thr:
                uf.union(a, b)
        groups: dict[str, list[str]] = defaultdict(list)
        for nid in cluster:
            groups[uf.find(nid)].append(nid)
        parts = [sorted(v) for v in groups.values()]
        if all(len(p) <= cap for p in parts):
            return [p for p in parts if len(p) >= 2] or [cluster[:cap]]
    return [cluster[i:i + cap] for i in range(0, len(cluster), cap)]


def propose_merges(
    concepts: dict[str, dict], *, threshold: float = MERGE_THRESHOLD,
    client=None, model: str | None = None,
) -> dict:
    """Passe LLM 2 (canonicalização): clustering por cosseno + adjudicação.

    Retorna o plano auditável:
      {"threshold": …, "clusters": [{"members": […], "score_medio": …,
        "grupos": [{"membros": […], "nome_canonico": …, "dim": …}]}],
       "rejeitados": [{"membros": …, "nome_canonico": …, "motivo": …}]}
    Só grupos com ≥2 membros geram merge no `build_canon` (grupo unitário e
    rename drive-by de singleton são ignorados de propósito). Grupos que violam
    os guardas anti-over-merge (_MAX_GROUP, canônico inventado) são desfeitos em
    singletons e ficam em `rejeitados` p/ auditoria."""
    if client is None:
        client, _ = _make_llm()
    model = model or CANON_MERGE_MODEL

    raw_clusters, pair_scores = _cluster_candidates(concepts, threshold=threshold)
    clusters: list[list[str]] = []
    for c in raw_clusters:
        clusters.extend(_split_oversized(c, pair_scores))

    def _mean_score(members: list[str]) -> float:
        vals = [s for (a, b), s in pair_scores.items() if a in members and b in members]
        return round(sum(vals) / len(vals), 4) if vals else 0.0

    plan: dict = {"threshold": threshold, "model": model, "clusters": [], "rejeitados": []}
    for members in clusters:
        payload = [
            {
                "id": nid, "name": concepts[nid]["name"], "dim": concepts[nid]["dim"],
                "descricao": (concepts[nid].get("description") or "")[:160],
                "aparece_em_n_arquivos": concepts[nid].get("fan_in", 1),
            }
            for nid in members
        ]
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": _MERGE_SYSTEM},
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                ],
                temperature=0,
                response_format={"type": "json_object"},
            )
            grupos = json.loads(resp.choices[0].message.content).get("grupos", [])
        except Exception as e:  # noqa: BLE001 — fail-open: cluster sem merge
            logger.warning("propose_merges: adjudicação do cluster %s falhou (%s)", members[:3], e)
            grupos = [{"membros": [m], "nome_canonico": concepts[m]["name"]} for m in members]
        # Sanitiza: só membros do cluster, cada um em no máximo um grupo.
        seen: set[str] = set()
        clean = []
        for g in grupos:
            ms = [m for m in g.get("membros", []) if m in members and m not in seen]
            if not ms:
                continue
            seen.update(ms)
            nome = (g.get("nome_canonico") or concepts[ms[0]]["name"]).strip()
            # Guardas anti-over-merge: grupo grande = guarda-chuva; canônico que
            # não deriva de nenhum membro = termo inventado. Desfaz em singletons.
            motivo = None
            if len(ms) > _MAX_GROUP:
                motivo = f"grupo com {len(ms)} membros (> {_MAX_GROUP})"
            elif len(ms) >= 2 and not _canonical_derives_from_member(nome, ms):
                motivo = "nome canônico não deriva de nenhum membro"
            if motivo:
                plan["rejeitados"].append({"membros": ms, "nome_canonico": nome, "motivo": motivo})
                clean.extend(
                    {"membros": [m], "nome_canonico": concepts[m]["name"], "dim": concepts[m]["dim"]}
                    for m in ms
                )
                continue
            clean.append({
                "membros": ms,
                "nome_canonico": nome,
                "dim": g.get("dim") or concepts[ms[0]]["dim"],
            })
        for m in members:
            if m not in seen:
                clean.append({"membros": [m], "nome_canonico": concepts[m]["name"], "dim": concepts[m]["dim"]})
        plan["clusters"].append({
            "members": members, "score_medio": _mean_score(members), "grupos": clean,
        })
    logger.info(
        "propose_merges: %d clusters candidatos (threshold=%.2f), %d grupos com merge",
        len(clusters), threshold,
        sum(1 for c in plan["clusters"] for g in c["grupos"] if len(g["membros"]) >= 2),
    )
    return plan


# ---------------------------------------------------------------------------
# Canon map (compilação dos planos) + apply mecânico
# ---------------------------------------------------------------------------

_VALID_ATOR_KINDS = frozenset({"agencia", "fap", "ict", "corporate", "aceleradora", "investidor"})
_VALID_DIMS = frozenset({"tema", "tecnologia", "aplicacao"})
# Kinds que o LLM inventa mas têm equivalente legítimo no enum (§6.4).
_ATOR_KIND_ALIASES = {"startup": "corporate", "incubadora": "aceleradora", "associacao": "corporate"}


def build_canon(validation_plan: dict[str, dict] | None, merge_plan: dict | None) -> dict:
    """Compila os planos num CANON MAP versionado — o artefato durável
    (`concept_canon` no kg_store) que `canonicalize_graph` aplica e o ingest
    reaplica em extração fresca. Aceita planos parciais (None = etapa ausente).

    Guard do veredicto "ator": kind fora do enum §6.4 (o LLM responde "generico"
    p/ classes de ator como "startups"/"agente público" apesar da instrução) é
    tratado como o que É — descarte por classe genérica, não um Ator novo."""
    canon: dict = {
        "version": CANON_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "discards": {},    # id → categoria (auditoria; nó some do grafo)
        "to_ator": {},     # id → ator_kind (reclassificação Conceito → Ator)
        "dim_fixes": {},   # id → dim corrigida
        "promotions": [],  # ids ex-Entidade validados → perdem origem (entram na afinidade)
        "aliases": {},     # id → {"name": forma canônica, "dim": …} (merge/rename)
        "validated": [],   # TODOS os ids já julgados — o ingest só valida os inéditos
    }
    for nid, v in (validation_plan or {}).items():
        verdict = v.get("veredicto")
        if verdict == "descartar":
            canon["discards"][nid] = v.get("categoria") or "outro"
        elif verdict == "ator":
            kind = v.get("ator_kind") or ""
            kind = _ATOR_KIND_ALIASES.get(kind, kind)
            if kind in _VALID_ATOR_KINDS:
                canon["to_ator"][nid] = kind
            else:
                canon["discards"][nid] = "sistema" if kind == "sistema" else "generico"
        elif verdict == "renomear":
            # Veredicto MANUAL (curadoria, não sai do LLM): tira prefixo scaffold
            # ("Linha Temática N: X" → "X"), fundindo com o nó canônico existente.
            if v.get("name"):
                canon["aliases"][nid] = {
                    "name": v["name"],
                    "dim": v.get("dim") if v.get("dim") in _VALID_DIMS else "tema",
                }
        elif verdict == "manter":
            if v.get("dim") in _VALID_DIMS:  # o LLM ecoa lixo às vezes (ex.: "dim")
                canon["dim_fixes"][nid] = v["dim"]
            if v.get("promote"):
                canon["promotions"].append(nid)
    for cl in (merge_plan or {}).get("clusters", []):
        for g in cl.get("grupos", []):
            if len(g.get("membros", [])) < 2:
                continue
            name = (g.get("nome_canonico") or "").strip()
            if not name:
                continue
            dim = g.get("dim") if g.get("dim") in _VALID_DIMS else "tema"
            for m in g["membros"]:
                if m in canon["discards"] or m in canon["to_ator"]:
                    continue  # validação tem precedência sobre merge
                canon["aliases"][m] = {"name": name, "dim": dim}
    validated = set(validation_plan or {})
    validated.update(f"con:{slugify(a['name'])}" for a in canon["aliases"].values())
    canon["validated"] = sorted(validated)
    return canon


def merge_canon(base: dict, extra: dict) -> dict:
    """União de dois canon maps (o do build + os vereditos novos do ingest).
    `extra` vence em conflito de chave; listas são unidas sem duplicata."""
    out = dict(base)
    for k in ("discards", "to_ator", "dim_fixes", "aliases"):
        out[k] = {**base.get(k, {}), **extra.get(k, {})}
    for k in ("promotions", "validated"):
        out[k] = sorted(set(base.get(k, [])) | set(extra.get(k, [])))
    out["generated_at"] = extra.get("generated_at") or base.get("generated_at")
    out["version"] = max(base.get("version", 1), extra.get("version", 1))
    return out


@dataclass
class CanonStats:
    """Contadores de auditoria de um apply (log do script, espelha MigrationStats)."""
    discarded: int = 0
    retyped_ator: int = 0
    promoted: int = 0
    dim_fixed: int = 0
    renamed: int = 0
    deduped: int = 0          # nós do MESMO arquivo fundidos num só pós-rename
    dropped_members: int = 0
    dropped_edges: int = 0
    discard_names: list[str] = field(default_factory=list)


def canonicalize_graph(graph: dict, canon: dict, stats: CanonStats | None = None) -> dict:
    """Aplica o canon a UM grafo v2 — puro, determinístico, idempotente, zero LLM.

    Por nó Conceito (na ordem: descarte → retipagem Ator → promoção → dim →
    alias/rename com re-id `con:{slug}`); depois dedup intra-arquivo (dois nós
    que fundiram no mesmo id viram um — fica a descrição mais longa, arestas
    unificadas) e reescrita das arestas por id (membros de nós descartados caem;
    aresta com <2 membros cai — mesma regra do migrador). Retorna NOVO dict."""
    st = stats if stats is not None else CanonStats()
    discards: dict = canon.get("discards", {})
    to_ator: dict = canon.get("to_ator", {})
    dim_fixes: dict = canon.get("dim_fixes", {})
    promotions = set(canon.get("promotions", []))
    aliases: dict = canon.get("aliases", {})

    id_map: dict[str, str | None] = {}  # old id → new id (None = descartado)
    by_new_id: dict[str, dict] = {}
    order: list[str] = []  # preserva a ordem de primeira aparição

    def _keep(nid: str, node: dict) -> None:
        prev = by_new_id.get(nid)
        if prev is None:
            by_new_id[nid] = node
            order.append(nid)
            return
        # Dedup intra-arquivo: mantém a descrição mais longa e a união dos campos.
        st.deduped += 1
        if len(node.get("description") or "") > len(prev.get("description") or ""):
            prev["description"] = node.get("description")

    for n in graph.get("nodes", []):
        old_id = n.get("id")
        if n.get("type") != "Conceito" or not old_id:
            if old_id:
                id_map[old_id] = old_id
            _keep(old_id or f"anon:{len(order)}", dict(n))
            continue

        if old_id in discards:
            id_map[old_id] = None
            st.discarded += 1
            st.discard_names.append(n.get("name") or old_id)
            continue

        n2 = dict(n)
        if old_id in to_ator:
            n2["type"] = "Ator"
            n2["kind"] = to_ator[old_id]
            n2.pop("dim", None)
            n2.pop("origem", None)
            new_id = f"ator:{slugify(n2.get('name') or '')}"
            n2["id"] = new_id
            id_map[old_id] = new_id
            st.retyped_ator += 1
            _keep(new_id, n2)
            continue

        if old_id in promotions and n2.get("origem") == ENTIDADE_V1_ORIGEM:
            n2.pop("origem", None)
            st.promoted += 1
        if old_id in dim_fixes and n2.get("dim") != dim_fixes[old_id]:
            n2["dim"] = dim_fixes[old_id]
            st.dim_fixed += 1

        alias = aliases.get(old_id)
        if alias:
            if n2.get("name") != alias["name"]:
                n2["name"] = alias["name"]
                st.renamed += 1
            if alias.get("dim"):
                n2["dim"] = alias["dim"]
        new_id = f"con:{slugify(n2.get('name') or '')}"
        n2["id"] = new_id
        id_map[old_id] = new_id
        _keep(new_id, n2)

    new_edges: list[dict] = []
    for e in graph.get("edges", []):
        mids: list[str] = []
        for m in e.get("members", []):
            nm = id_map.get(m, m)
            if nm is None:
                st.dropped_members += 1
                continue
            mids.append(nm)
        mids = list(dict.fromkeys(mids))
        if len(mids) < 2:
            st.dropped_edges += 1
            continue
        new_edges.append({**e, "members": mids})

    return {**graph, "nodes": [by_new_id[i] for i in order], "edges": new_edges}


# ---------------------------------------------------------------------------
# Macro-temas (D8) — vocabulário controlado do WIKI.md
# ---------------------------------------------------------------------------

_MACRO_SYSTEM = """Você classifica oportunidades de fomento à inovação (editais, programas, \
ofertas de investimento) em MACRO-TEMAS de um vocabulário controlado.

Para cada oportunidade (título, descrição e os conceitos que ela cobre no grafo), \
escolha de 1 a 3 macro-temas do vocabulário — os que melhor descrevem o domínio da \
oportunidade. Use SOMENTE valores EXATOS do vocabulário. Oportunidade genérica/
transversal (qualquer setor): lista vazia.

Responda JSON: {"itens": [{"id": "...", "macro_temas": ["..."]}]}
Um item de saída por item de entrada, mesmo id."""


def _opportunity_concepts(graph: dict, op_id: str, cap: int = 25) -> list[str]:
    """Names dos Conceitos ligados a uma Oportunidade pelas arestas do subgrafo."""
    idx = {n["id"]: n for n in graph.get("nodes", []) if n.get("id")}
    out: list[str] = []
    for e in graph.get("edges", []):
        members = e.get("members", [])
        if op_id not in members:
            continue
        for m in members:
            nd = idx.get(m)
            if nd and nd.get("type") == "Conceito" and nd.get("name") not in out:
                out.append(nd["name"])
    return out[:cap]


def propose_macro_temas(
    graphs: dict[str, dict], vocab: list[str], *,
    batch_size: int = MACRO_BATCH, client=None, model: str | None = None,
) -> dict[str, list[str]]:
    """Mapeia cada nó Oportunidade a `macro_temas[]` do vocabulário (D8).

    Chave do plano: "{file_key}::{node_id}" (o mesmo programa pode aparecer em
    mais de um arquivo; a atribuição é por instância, guiada pelas arestas do
    arquivo). Fail-open: batch com erro → sem macro_temas (campo omitido)."""
    if client is None:
        client, model = _make_llm()
    model = model or CANON_MODEL

    items: list[dict] = []
    for fk, g in graphs.items():
        for n in g.get("nodes", []):
            if n.get("type") != "Oportunidade" or not n.get("id"):
                continue
            items.append({
                "id": f"{fk}::{n['id']}",
                "titulo": n.get("name") or "",
                "descricao": (n.get("description") or "")[:300],
                "conceitos": _opportunity_concepts(g, n["id"]),
            })

    plan: dict[str, list[str]] = {}
    vocab_set = set(vocab)
    user_prefix = "VOCABULÁRIO: " + json.dumps(vocab, ensure_ascii=False) + "\n\nOPORTUNIDADES: "
    for start in range(0, len(items), batch_size):
        batch = items[start:start + batch_size]
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": _MACRO_SYSTEM},
                    {"role": "user", "content": user_prefix + json.dumps(batch, ensure_ascii=False)},
                ],
                temperature=0,
                response_format={"type": "json_object"},
            )
            out = json.loads(resp.choices[0].message.content).get("itens", [])
        except Exception as e:  # noqa: BLE001 — fail-open
            logger.warning("propose_macro_temas: batch %d falhou (%s)", start, e)
            out = []
        for it in out:
            key = it.get("id")
            temas = [t for t in it.get("macro_temas", []) if t in vocab_set]
            if key and temas:
                plan[key] = temas
        logger.info("propose_macro_temas: %d/%d oportunidades", min(start + batch_size, len(items)), len(items))
    return plan


# ---------------------------------------------------------------------------
# Ingest (extração fresca) — replay do canon como etapa do build
# ---------------------------------------------------------------------------

# Desliga o passe LLM do ingest (validação de Conceitos inéditos + macro-temas)
# sem desligar o replay determinístico. Útil em dev/teste.
_FRESH_LLM_ENV = "CANON_FRESH_LLM"


def canonicalize_fresh_graph(graph: dict, *, file_key: str = "fresh", llm_new: bool | None = None) -> dict:
    """Higiene de uma extração FRESCA (etapa de build do ingest — PR3): a
    oportunidade nova entra no KG já canonicalizada.

    1. replay DETERMINÍSTICO do canon map persistido (`concept_canon` via
       kg_store): descartes/retipagens/renames conhecidos;
    2. com `llm_new` (default: env CANON_FRESH_LLM != "0"): valida via LLM só os
       Conceitos INÉDITOS (fora de `canon.validated`) e atribui `macro_temas[]`
       às Oportunidades sem o campo; os vereditos novos são mergeados de volta
       no artefato (o próximo ingest não re-julga).

    Espera grafo já no formato v2 (ids — `migrate_to_v2` antes). Fail-open em
    toda falha de LLM/storage: o ETL nunca morre por higiene. Retorna NOVO dict.
    """
    from core.kg import kg_store

    canon = kg_store.load("concept_canon", default={}) or {}
    if canon:
        graph = canonicalize_graph(graph, canon)

    if llm_new is None:
        llm_new = os.environ.get(_FRESH_LLM_ENV, "1") != "0"
    if not llm_new:
        return graph

    # Valida só os Conceitos que o corpus nunca julgou.
    validated = set(canon.get("validated", []))
    inv = inventory_concepts({file_key: graph})
    unseen = {nid: c for nid, c in inv.items() if nid not in validated}
    if unseen:
        try:
            vplan = propose_validation(unseen)
            mini = build_canon(vplan, None)
            graph = canonicalize_graph(graph, mini)
            kg_store.save("concept_canon", merge_canon(canon, mini) if canon else mini)
            logger.info(
                "canonicalize_fresh[%s]: %d Conceitos inéditos julgados (%d descartados)",
                file_key, len(unseen), len(mini["discards"]),
            )
        except Exception as e:  # noqa: BLE001 — higiene é best-effort no ingest
            logger.warning("canonicalize_fresh[%s]: validação de inéditos falhou (%s)", file_key, e)

    # Macro-temas (D8) p/ Oportunidades que ainda não têm. O propose roda no
    # grafo COMPLETO (precisa dos Conceitos ligados); o plano é filtrado aos
    # nós pendentes antes do apply.
    try:
        from core.kg.schema import macro_temas_vocab
        vocab = macro_temas_vocab()
        pending_ids = {
            n["id"] for n in graph.get("nodes", [])
            if n.get("type") == "Oportunidade" and n.get("id") and not n.get("macro_temas")
        }
        if vocab and pending_ids:
            plan = propose_macro_temas({file_key: graph}, vocab)
            plan = {k: v for k, v in plan.items() if k.partition("::")[2] in pending_ids}
            apply_macro_temas({file_key: graph}, plan)
    except Exception as e:  # noqa: BLE001 — best-effort
        logger.warning("canonicalize_fresh[%s]: macro_temas falhou (%s)", file_key, e)

    return graph


def apply_macro_temas(graphs: dict[str, dict], plan: dict[str, list[str]]) -> dict[str, int]:
    """Escreve `macro_temas[]` nos nós Oportunidade (in-place por grafo novo? —
    muta os dicts recebidos de propósito: o chamador é o script, que reescreve
    os arquivos na sequência). Retorna contagem por arquivo."""
    counts: dict[str, int] = defaultdict(int)
    for key, temas in plan.items():
        fk, _, nid = key.partition("::")
        g = graphs.get(fk)
        if not g:
            continue
        for n in g.get("nodes", []):
            if n.get("id") == nid:
                n["macro_temas"] = temas
                counts[fk] += 1
                break
    return dict(counts)
