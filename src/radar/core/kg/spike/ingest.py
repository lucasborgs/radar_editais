"""core/kg/spike/ingest.py — Fase 1: populador determinístico (zero LLM).

SPEC §8. Lê (somente-leitura) `public.entities` + `public.entity_relationships`
e materializa no schema `kg_spike`:

  1. `nodes`          — espelho das substâncias (id, kind, native_id, name, desc, embedding)
  2. `quality_nodes`  — setor / tecnologia / estagio / uf / mecanismo / faixa_trl
  3. `edges`          — tem_setor, tem_tecnologia, busca_estagio, tem_uf,
                        usa_mecanismo, tem_trl_faixa + cópia das estruturais
                        (operado_por, subordinado_a, credenciada_por).
                        Origem classificada: `factual_catalogada` (cópia do
                        gold, preserva properties), `deterministica_derivada`
                        (tem_* / busca_estagio / usa_mecanismo /
                        potencial_parceria), `similaridade_derivada` (similar_a).
  4. `similar_a`      — cosseno entre embeddings (threshold ~0.75), weight no properties
  5. `communities`    — roda features.py (Louvain)

Idempotente: TRUNCATE do schema de conteúdo antes de popular (o spike é
derivado e reconstituível a qualquer momento — postura do gold). A escala
(~550 nós, ~2k arestas) torna a reconstrução completa trivial.

Uso:
    DATABASE_URL=... python -m radar.core.kg.spike.ingest
"""
from __future__ import annotations

import json
import logging
import unicodedata
from typing import Any

import numpy as np
import psycopg

from radar.core.kg.spike import graph_store
from radar.core.kg.spike.graph_store import SCHEMA, get_dsn

logger = logging.getLogger(__name__)

# Faixas TRL (schema.md §5.8).
_TRL_FAIXAS: list[tuple[str, int, int]] = [
    ("faixa_trl:pesquisa", 1, 3),
    ("faixa_trl:prototipo", 4, 6),
    ("faixa_trl:industrial", 7, 9),
]

SIMILARITY_THRESHOLD = 0.75
SIMILARITY_TOP_K = 10  # arestas similar_a por entidade (teto anti-explosão)

# Origem das arestas (classificação do KG Fase 1). Cada bucket da Fase 1
# (`fase1_structural`, `fase1_deterministic`, `fase1_tech_bridge`,
# `fase1_similarity`) foi mapeado para uma classificação de provenance — sem
# duplicar as regras de qual aresta pertence a qual bucket.
SOURCE_FACTUAL_CATALOGADA = "factual_catalogada"          # rels copiadas do gold (operado_por, subordinado_a, ...)
SOURCE_DETERMINISTICA_DERIVADA = "deterministica_derivada"  # tem_* / busca_estagio / usa_mecanismo / potencial_parceria
SOURCE_SIMILARIDADE_DERIVADA = "similaridade_derivada"     # similar_a (cosseno)


def _deburr(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s or "") if not unicodedata.combining(c))


def _node_id(kind: str, native_id: str) -> str:
    """Id de nó do spike: `<kind>:<native_id>` (ex.: `edital:finep:589`).

    `native_id` do gold já pode carregar o prefixo de kind (ex.: agência tem
    `native_id='agencia:finep'`). Nesse caso o prefixo não é duplicado —
    `agencia:agencia:finep` nunca deve existir.
    """
    prefix = f"{kind}:"
    if native_id.startswith(prefix):
        return native_id
    return f"{prefix}{native_id}"


def _trl_faixa_id(trl_min: Any, trl_max: Any) -> list[str]:
    """Faixas TRL sobrepostas (regra de overlap de schema.md §5.8)."""
    if trl_min is None and trl_max is None:
        return []
    a, b = trl_min, trl_max
    if a is None:
        a = 1
    if b is None:
        b = 9
    return [fid for fid, lo, hi in _TRL_FAIXAS if max(a, lo) <= min(b, hi)]


# ─────────────────────────────────────────────────────────────────────────────
# Leituras do gold (somente-leitura)
# ─────────────────────────────────────────────────────────────────────────────

_ENTITY_SQL = """
select id, kind, source, native_id, name, description,
       setores, tecnologias_tags, uf, mecanismo, constraints, metadata, embedding
from public.entities
"""


def _trl_range_from_constraints(constraints: Any) -> tuple[int | None, int | None]:
    """TRL derivado dos constraints `{tipo: trl}` (não há coluna `trl_range`):
    gte define o piso, lte o teto. Sem constraint de trl → (None, None)."""
    lo = hi = None
    for c in constraints or []:
        if not isinstance(c, dict) or c.get("tipo") != "trl":
            continue
        op, val = c.get("op"), c.get("valor")
        if not isinstance(val, int):
            continue
        if op in ("gte", "in") and (lo is None or val > lo):
            lo = val
        elif op == "lte" and (hi is None or val < hi):
            hi = val
    return lo, hi


def _load_entities(cur) -> list[dict[str, Any]]:
    cur.execute(_ENTITY_SQL)
    cols = [d.name for d in cur.description]
    rows = []
    for r in cur.fetchall():
        row = dict(zip(cols, r, strict=True))
        row["id"] = str(row["id"])
        row["trl_range"] = _trl_range_from_constraints(row.get("constraints"))
        rows.append(row)
    return rows


def _load_relationships(cur) -> list[tuple[str, str, str, dict]]:
    """Rels estruturais do gold (somente-leitura), preservando `properties`."""
    cur.execute("select source_id, target_id, type, properties from public.entity_relationships")
    return [
        (str(s), str(t), ty, props or {}) for s, t, ty, props in cur.fetchall()
    ]


# ─────────────────────────────────────────────────────────────────────────────
# Ingest principal
# ─────────────────────────────────────────────────────────────────────────────

def ingest(*, skip_features: bool = False) -> dict[str, int]:
    """Popula o schema `kg_spike`. Retorna contadores para log/diagnóstico."""
    from radar.core.kg.spike import features

    graph_store.init_schema()
    graph_store.reset()

    with psycopg.connect(get_dsn(), autocommit=True) as conn:
        with conn.cursor() as cur:
            entities = _load_entities(cur)
            rels = _load_relationships(cur)

            # 1) nós
            cur.executemany(
                f"insert into {SCHEMA}.nodes (id, kind, native_id, name, description, embedding) "
                "values (%s, %s, %s, %s, %s, %s)",
                [
                    (_node_id(e["kind"], e["native_id"]), e["kind"], e["native_id"],
                     e.get("name") or "", e.get("description") or "", e.get("embedding"))
                    for e in entities
                ],
            )

            # 2) nós de qualidade
            quality: dict[str, tuple[str, str]] = {}

            def _q(family: str, value: str) -> str:
                v = _deburr(value.strip().lower()) or "sem-valor"
                vid = f"{family}:{v}"
                quality.setdefault(vid, (family, value.strip()))
                return vid

            for e in entities:
                for s in e.get("setores") or []:
                    _q("setor", s)
                for t in e.get("tecnologias_tags") or []:
                    _q("tecnologia", t)
                meta = e.get("metadata") or {}
                for st in meta.get("estagio_alvo") or []:
                    _q("estagio", st)
                if e.get("uf"):
                    _q("uf", e["uf"])
                if e.get("mecanismo"):
                    _q("mecanismo", e["mecanismo"])
                for fid in _trl_faixa_id(
                    *e["trl_range"]
                ):
                    label = fid.split(":")[1]
                    quality.setdefault(fid, ("faixa_trl", label))

            cur.executemany(
                f"insert into {SCHEMA}.quality_nodes (id, family, value) values (%s, %s, %s)",
                [(vid, fam, val) for vid, (fam, val) in quality.items()],
            )

            # 3) arestas de qualidade + estruturais
            edges: list[tuple[str, str, str, float, dict, str]] = []
            for e in entities:
                nid = _node_id(e["kind"], e["native_id"])
                for s in e.get("setores") or []:
                    # Opção 4 (SPEC §7): hub `multissetorial` recebe peso baixo —
                    # existe na topologia mas não expande vizinhança (MIN_WEIGHT).
                    hub = _deburr(s.strip().lower()) == "multissetorial"
                    weight = 0.1 if hub else 1.0
                    props = {"hub": True} if hub else {}
                    edges.append((nid, _q("setor", s), "tem_setor", weight, props, SOURCE_DETERMINISTICA_DERIVADA))
                for t in e.get("tecnologias_tags") or []:
                    edges.append((nid, _q("tecnologia", t), "tem_tecnologia", 1.0, {}, SOURCE_DETERMINISTICA_DERIVADA))
                meta = e.get("metadata") or {}
                for st in meta.get("estagio_alvo") or []:
                    edges.append((nid, _q("estagio", st), "busca_estagio", 1.0, {}, SOURCE_DETERMINISTICA_DERIVADA))
                if e.get("uf"):
                    edges.append((nid, _q("uf", e["uf"]), "tem_uf", 1.0, {}, SOURCE_DETERMINISTICA_DERIVADA))
                if e.get("mecanismo"):
                    edges.append((nid, _q("mecanismo", e["mecanismo"]), "usa_mecanismo", 1.0, {}, SOURCE_DETERMINISTICA_DERIVADA))
                for fid in _trl_faixa_id(
                    *e["trl_range"]
                ):
                    edges.append((nid, fid, "tem_trl_faixa", 1.0, {}, SOURCE_DETERMINISTICA_DERIVADA))

            node_id_by_pk: dict[str, str] = {
                str(e["id"]): _node_id(e["kind"], e["native_id"]) for e in entities
            }
            for s, t, rtype, rprops in rels:
                sn, tn = node_id_by_pk.get(s), node_id_by_pk.get(t)
                if sn and tn:
                    edges.append((sn, tn, rtype, 1.0, rprops or {}, SOURCE_FACTUAL_CATALOGADA))

            cur.executemany(
                f"insert into {SCHEMA}.edges (source_id, target_id, type, weight, properties, source) "
                "values (%s, %s, %s, %s, %s, %s) "
                "on conflict (source_id, target_id, type) do nothing",
                [(*e[:4], json.dumps(e[4]), e[5]) for e in edges],
            )

            # 4) similar_a (cosseno entre embeddings existentes)
            n_similar = _insert_similarity(cur, entities)

            # 5) potencial_parceria (edital↔ICT via tecnologia compartilhada)
            n_partnerships = _insert_partnerships(cur, entities)

    # 5) comunidades
    n_communities = 0
    if not skip_features:
        n_communities = features.run_communities()

    counts = {
        "nodes": len(entities),
        "quality_nodes": len(quality),
        "edges": len(edges),
        "similar_a": n_similar,
        "potencial_parceria": n_partnerships,
        "communities": n_communities,
    }
    logger.info("kg_spike ingest concluído: %s", counts)
    return counts


def _insert_similarity(cur, entities: list[dict[str, Any]]) -> int:
    """Arestas `similar_a` por cosseno, top-k por entidade, acima do threshold."""
    from radar.core.services.company_chunks import parse_vec

    entries: list[tuple[str, str, str, np.ndarray]] = []
    for e in entities:
        emb = parse_vec(e.get("embedding")) if e.get("embedding") is not None else None
        if emb is None or not emb.size:
            continue
        entries.append((_node_id(e["kind"], e["native_id"]), e["kind"], e["native_id"],
                        emb.astype(np.float32)))

    n = 0
    for i, (nid, _k, _nid, vec) in enumerate(entries):
        norm = vec / (np.linalg.norm(vec) + 1e-9)
        scored: list[tuple[float, str]] = []
        for j, (other, _ok, _on, ovec) in enumerate(entries):
            if i == j:
                continue
            sim = float(norm @ (ovec / (np.linalg.norm(ovec) + 1e-9)))
            if sim >= SIMILARITY_THRESHOLD:
                scored.append((sim, other))
        scored.sort(key=lambda x: (-x[0], x[1]))
        for sim, other in scored[:SIMILARITY_TOP_K]:
            cur.execute(
                f"insert into {SCHEMA}.edges (source_id, target_id, type, weight, properties, source) "
                "values (%s, %s, %s, %s, %s, %s) "
                "on conflict (source_id, target_id, type) do nothing",
                (nid, other, "similar_a", round(sim, 4), json.dumps({"base": "cosine_embedding"}), SOURCE_SIMILARIDADE_DERIVADA),
            )
            n += 1
    return n


def _insert_partnerships(cur, entities: list[dict[str, Any]]) -> int:
    """Arestas `potencial_parceria` edital↔ICT por TECNOLOGIA compartilhada.

    Opção 1 (SPEC §7): pares (edital, ICT) que compartilham ≥1 `tem_tecnologia`
    ganham aresta direta com peso = Jaccard dos conjuntos de tecnologia. Só
    conecta quando há sobreposição REAL de tecnologia — nenhuma indicação
    temática solta vira aresta (postura "resposta honesta").
    """
    tech_by_id: dict[str, set[str]] = {}
    for e in entities:
        nid = _node_id(e["kind"], e["native_id"])
        tags = {_deburr(t.strip().lower()) for t in (e.get("tecnologias_tags") or []) if t}
        if tags:
            tech_by_id[nid] = tags

    editais = {nid for nid in tech_by_id if nid.startswith("edital:")}
    icts = {nid for nid in tech_by_id if nid.startswith("ict:")}
    if not editais or not icts:
        return 0

    n = 0
    for eid in editais:
        et = tech_by_id[eid]
        for iid in icts:
            it = tech_by_id[iid]
            shared = et & it
            if not shared:
                continue
            jaccard = len(shared) / (len(et | it) or 1)
            cur.execute(
                f"insert into {SCHEMA}.edges (source_id, target_id, type, weight, properties, source) "
                "values (%s, %s, %s, %s, %s, %s) "
                "on conflict (source_id, target_id, type) do nothing",
                (eid, iid, "potencial_parceria", round(jaccard, 4),
                 json.dumps({"n_shared": len(shared)}), SOURCE_DETERMINISTICA_DERIVADA),
            )
            n += 1
    return n


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    from radar.core.environment import assert_database_target, load_environment_profile

    load_environment_profile()
    assert_database_target("kg_spike ingest")
    print(ingest())
