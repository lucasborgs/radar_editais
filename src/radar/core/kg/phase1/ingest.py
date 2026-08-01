"""core/kg/phase1/ingest.py — build determinístico da projeção da Fase 1 (zero LLM).

Aprovação da spike `kg-structure-aware` (SPEC §8): a Fase 1 é reprojeção
DETERMINÍSTICA do gold — nenhuma LLM, nenhuma re-leitura de fonte. Este módulo a
promove a produção com modelo de GERAÇÕES e troca atômica:

  * lê (read-only) `public.entities` + `public.entity_relationships`;
  * constrói nós, nós de qualidade e arestas EM MEMÓRIA (funções puras);
  * grava uma NOVA geração no schema `kg_phase1` dentro de UMA transação;
  * no MESMO commit, a geração nova vira `is_current=true` (swap atômico) —
    leitores nunca observam geração incompleta;
  * falha → rollback → a última geração saudável permanece corrente; um registro
    `failed` (best-effort, transação separada) fica no ledger.

Idempotente: se a geração corrente tem o mesmo `source_hash` do gold, o build
pula (`--no-skip` força). Origens preservadas em `edges.origin` (CHECK fechado):
phase1_deterministic | phase1_structural | phase1_similarity | phase1_tech_bridge
(similar_a e potencial_parceria são DERIVADAS — nunca fato documental).

Uso (sempre com ambiente local — o .env aponta pro remoto):
    DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:54322/postgres \\
    python -m radar.core.kg.phase1.ingest [--no-skip] [--no-communities]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
import unicodedata
from typing import Any

import numpy as np
import psycopg

from radar.core.kg.phase1 import features, store
from radar.core.kg.phase1.store import SCHEMA

logger = logging.getLogger(__name__)

BUILD_VERSION = "kg-phase1-v1"

# Faixas TRL (docs/domain/schema.md §5.8 — overlap).
_TRL_FAIXAS: list[tuple[str, int, int]] = [
    ("faixa_trl:pesquisa", 1, 3),
    ("faixa_trl:prototipo", 4, 6),
    ("faixa_trl:industrial", 7, 9),
]

SIMILARITY_THRESHOLD = 0.75
SIMILARITY_TOP_K = 10  # arestas similar_a por entidade (teto anti-explosão)

# Hub `setor:multissetorial` (opção 4 SPEC §7): existe na topologia, mas não
# expande vizinhança — a travessia passa `min_weight >= 0.5`.
_HUB_SETOR = "multissetorial"
_HUB_WEIGHT = 0.1


# ─────────────────────────────────────────────────────────────────────────────
# Helpers puros (determinismo de IDs)
# ─────────────────────────────────────────────────────────────────────────────

def _deburr(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s or "") if not unicodedata.combining(c))


def _node_id(kind: str, native_id: str) -> str:
    """Id de nó da projeção: `<kind>:<native_id>` (ex.: `edital:finep:589`)."""
    return f"{kind}:{native_id}"


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


def _parse_vec(raw: Any) -> np.ndarray | None:
    """psycopg devolve `vector` como string crua ("[a,b,c]") sem adapter."""
    if raw is None:
        return None
    if isinstance(raw, str):
        return np.array(raw.strip("[]").split(","), dtype=np.float32)
    arr = np.asarray(raw, dtype=np.float32)
    return arr if arr.size else None


def _vec_literal(emb: Any) -> str | None:
    """pgvector aceita literal textual '[0.1,...]'. A entrada vinda do gold já é
    essa forma; arrays locais são serializados de forma estável."""
    if emb is None:
        return None
    if isinstance(emb, str):
        return emb
    return "[" + ",".join(f"{float(x):.7f}" for x in emb) + "]"


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


# ─────────────────────────────────────────────────────────────────────────────
# Construção das linhas (PURAS — deterministicamente reconstruíveis do gold)
# ─────────────────────────────────────────────────────────────────────────────

_EDGE_KEYS = ("source_id", "target_id", "type", "weight", "properties", "origin")


def _edge(source_id: str, target_id: str, type_: str, *,
          weight: float = 1.0, properties: dict[str, Any] | None = None,
          origin: str) -> dict[str, Any]:
    return dict(zip(_EDGE_KEYS, (source_id, target_id, type_, weight, properties or {}, origin), strict=True))


def _node_rows(entities: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {"id": _node_id(e["kind"], e["native_id"]), "kind": e["kind"],
         "native_id": e["native_id"], "name": e.get("name") or "",
         "description": e.get("description") or "", "embedding": e.get("embedding")}
        for e in entities
    ]


def _quality_rows(entities: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Nós de qualidade com id determinístico `<family>:<deburr(lower)>`."""
    rows: dict[str, tuple[str, str]] = {}

    def _q(family: str, value: str) -> str:
        v = _deburr(value.strip().lower()) or "sem-valor"
        vid = f"{family}:{v}"
        rows.setdefault(vid, (family, value.strip()))
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
        for fid in _trl_faixa_id(*e["trl_range"]):
            label = fid.split(":")[1]
            rows.setdefault(fid, ("faixa_trl", label))
    return [{"id": vid, "family": fam, "value": val} for vid, (fam, val) in rows.items()]


def _quality_edges(entities: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Arestas determinísticas entidade → nó de qualidade (fatos do gold)."""
    edges: list[dict[str, Any]] = []
    for e in entities:
        nid = _node_id(e["kind"], e["native_id"])
        for s in e.get("setores") or []:
            hub = _deburr(s.strip().lower()) == _HUB_SETOR
            edges.append(_edge(
                nid, f"setor:{_deburr(s.strip().lower())}", "tem_setor",
                weight=_HUB_WEIGHT if hub else 1.0,
                properties={"hub": True} if hub else {},
                origin="phase1_deterministic",
            ))
        for t in e.get("tecnologias_tags") or []:
            edges.append(_edge(nid, f"tecnologia:{_deburr(t.strip().lower())}", "tem_tecnologia",
                               origin="phase1_deterministic"))
        meta = e.get("metadata") or {}
        for st in meta.get("estagio_alvo") or []:
            edges.append(_edge(nid, f"estagio:{_deburr(st.strip().lower())}", "busca_estagio",
                               origin="phase1_deterministic"))
        if e.get("uf"):
            edges.append(_edge(nid, f"uf:{_deburr(str(e['uf']).strip().lower())}", "tem_uf",
                               origin="phase1_deterministic"))
        if e.get("mecanismo"):
            edges.append(_edge(nid, f"mecanismo:{_deburr(str(e['mecanismo']).strip().lower())}",
                               "usa_mecanismo", origin="phase1_deterministic"))
        for fid in _trl_faixa_id(*e["trl_range"]):
            edges.append(_edge(nid, fid, "tem_trl_faixa", origin="phase1_deterministic"))
    return edges


def _structural_edges(
    entities: list[dict[str, Any]], rels: list[tuple[str, str, str]]
) -> list[dict[str, Any]]:
    """Cópia das relações estruturais do gold (entity_relationships) — fatos."""
    node_by_pk: dict[str, str] = {
        str(e["id"]): _node_id(e["kind"], e["native_id"]) for e in entities
    }
    edges: list[dict[str, Any]] = []
    for s, t, rtype in rels:
        sn, tn = node_by_pk.get(s), node_by_pk.get(t)
        if sn and tn:
            edges.append(_edge(sn, tn, rtype, origin="phase1_structural"))
    return edges


def _similarity_edges(entities: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """`similar_a` por cosseno dos embeddings existentes (threshold, top-k).

    DERIVADA de embeddings — NÃO é fato documental (origin=phase1_similarity +
    properties.derived=true)."""
    entries: list[tuple[str, np.ndarray]] = []
    for e in entities:
        vec = _parse_vec(e.get("embedding"))
        if vec is None or not vec.size:
            continue
        entries.append((_node_id(e["kind"], e["native_id"]), vec.astype(np.float32)))

    edges: list[dict[str, Any]] = []
    for i, (nid, vec) in enumerate(entries):
        norm = vec / (np.linalg.norm(vec) + 1e-9)
        scored: list[tuple[float, str]] = []
        for j, (other, ovec) in enumerate(entries):
            if i == j:
                continue
            sim = float(norm @ (ovec / (np.linalg.norm(ovec) + 1e-9)))
            if sim >= SIMILARITY_THRESHOLD:
                scored.append((sim, other))
        scored.sort(key=lambda x: (-x[0], x[1]))
        for sim, other in scored[:SIMILARITY_TOP_K]:
            edges.append(_edge(
                nid, other, "similar_a", weight=round(sim, 4),
                properties={"base": "cosine_embedding", "derived": True},
                origin="phase1_similarity",
            ))
    return edges


def _partnership_edges(entities: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """`potencial_parceria` edital↔ICT por TECNOLOGIA compartilhada (Jaccard).

    HEURÍSTICA (SPEC §7, opção 1): só conecta quando há sobreposição REAL de
    tecnologia — nenhuma indicação temática solta vira aresta (postura "resposta
    honesta"). origin=phase1_tech_bridge + properties.derived=true → NÃO é fato
    documental."""
    tech_by_id: dict[str, set[str]] = {}
    for e in entities:
        nid = _node_id(e["kind"], e["native_id"])
        tags = {_deburr(t.strip().lower()) for t in (e.get("tecnologias_tags") or []) if t}
        if tags:
            tech_by_id[nid] = tags

    editais = {nid for nid in tech_by_id if nid.startswith("edital:")}
    icts = {nid for nid in tech_by_id if nid.startswith("ict:")}
    if not editais or not icts:
        return []

    edges: list[dict[str, Any]] = []
    for eid in editais:
        et = tech_by_id[eid]
        for iid in icts:
            shared = et & tech_by_id[iid]
            if not shared:
                continue
            jaccard = len(shared) / (len(et | tech_by_id[iid]) or 1)
            edges.append(_edge(
                eid, iid, "potencial_parceria", weight=round(jaccard, 4),
                properties={"n_shared": len(shared), "derived": True},
                origin="phase1_tech_bridge",
            ))
    return edges


def _dedup_edges(edges: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Dedup por (source, target, type) mantendo a 1ª ocorrência — espelha o
    `on conflict ... do nothing` do INSERT (contagens determinísticas)."""
    seen: set[tuple[str, str, str]] = set()
    out: list[dict[str, Any]] = []
    for e in edges:
        key = (e["source_id"], e["target_id"], e["type"])
        if key in seen:
            continue
        seen.add(key)
        out.append(e)
    return out


def _build_rows(
    entities: list[dict[str, Any]], rels: list[tuple[str, str, str]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """(nodes, quality_nodes, edges) — a projeção completa em memória.

    Determinístico: para o MESMO gold, devolve estruturas idênticas (reconstruível)."""
    nodes = _node_rows(entities)
    quality = _quality_rows(entities)
    edges = _dedup_edges([
        *_quality_edges(entities),
        *_structural_edges(entities, rels),
        *_similarity_edges(entities),
        *_partnership_edges(entities),
    ])
    return nodes, quality, edges


# ─────────────────────────────────────────────────────────────────────────────
# Hash determinístico do gold (idempotência)
# ─────────────────────────────────────────────────────────────────────────────

def _source_hash(entities: list[dict[str, Any]], rels: list[tuple[str, str, str]]) -> str:
    """Hash do SUBCONJUNTO do gold consumido pela projeção — estável entre
    builds para o mesmo conteúdo (independente de uuids)."""
    h = hashlib.sha256()

    def _u(s: str) -> bytes:
        return s.encode("utf-8")

    for e in sorted(entities, key=lambda x: (x["kind"], x["native_id"])):
        h.update(_u(f"{e['kind']}|{e['native_id']}|{e['name']}|{e['description']}"))
        h.update(_u(json.dumps(sorted(e.get("setores") or []), ensure_ascii=False)))
        h.update(_u(json.dumps(sorted(e.get("tecnologias_tags") or []), ensure_ascii=False)))
        meta = e.get("metadata") or {}
        h.update(_u(json.dumps(sorted(meta.get("estagio_alvo") or []), ensure_ascii=False)))
        h.update(_u(str(e.get("uf") or "")))
        h.update(_u(str(e.get("mecanismo") or "")))
        h.update(_u(json.dumps(e.get("constraints") or [], ensure_ascii=False)))
        h.update(_u(json.dumps(e.get("trl_range") or [])))
        vec = _parse_vec(e.get("embedding"))
        if vec is not None and vec.size:
            h.update(np.asarray(vec, dtype=np.float32).tobytes())

    node_by_uuid = {str(e["id"]): _node_id(e["kind"], e["native_id"]) for e in entities}
    resolved = sorted(
        ((node_by_uuid.get(s), node_by_uuid.get(t), ty) for s, t, ty in rels),
        key=lambda x: (str(x[0]), str(x[1]), x[2]),
    )
    for s, t, ty in resolved:
        h.update(_u(f"{s}|{t}|{ty}"))
    return h.hexdigest()


def _should_skip(current: dict[str, Any] | None, source_hash: str, skip_unchanged: bool) -> bool:
    """Decisão de idempotência: pula quando a geração corrente já reflete o gold
    lido. Corrente saudável é pré-condição (status/health checado pelo chamador)."""
    return bool(skip_unchanged and current and current.get("source_hash") == source_hash)


# ─────────────────────────────────────────────────────────────────────────────
# Leitura do gold (read-only)
# ─────────────────────────────────────────────────────────────────────────────

_ENTITY_SQL = """
select id, kind, source, native_id, name, description, setores, tecnologias_tags,
       uf, mecanismo, constraints, metadata, embedding
from public.entities
"""


def _load_gold(cur) -> tuple[list[dict[str, Any]], list[tuple[str, str, str]]]:
    cur.execute(_ENTITY_SQL)
    cols = [d.name for d in cur.description]
    entities: list[dict[str, Any]] = []
    for r in cur.fetchall():
        row = dict(zip(cols, r, strict=True))
        row["id"] = str(row["id"])
        row["trl_range"] = _trl_range_from_constraints(row.get("constraints"))
        entities.append(row)
    cur.execute("select source_id, target_id, type from public.entity_relationships")
    rels = [(str(s), str(t), ty) for s, t, ty in cur.fetchall()]
    return entities, rels


# ─────────────────────────────────────────────────────────────────────────────
# Transação do build + swap atômico
# ─────────────────────────────────────────────────────────────────────────────

def _insert_edges(cur, generation_id: int, edges: list[dict[str, Any]]) -> None:
    cur.executemany(
        f"insert into {SCHEMA}.edges (generation_id, source_id, target_id, type, "
        f"weight, properties, origin) values (%s, %s, %s, %s, %s, %s::jsonb, %s) "
        f"on conflict (generation_id, source_id, target_id, type) do nothing",
        [
            (generation_id, e["source_id"], e["target_id"], e["type"], e["weight"],
             json.dumps(e["properties"]), e["origin"])
            for e in edges
        ],
    )


def _build_tx(
    conn: psycopg.Connection, *, skip_unchanged: bool, run_communities: bool
) -> dict[str, Any]:
    """Executa o build dentro da transação ABERTA do chamador. Devolve o resumo;
    o commit/rollback é responsabilidade de `build()`."""
    with conn.cursor() as cur:
        entities, rels = _load_gold(cur)
        source_hash = _source_hash(entities, rels)

        current = store.current_generation(conn)
        if _should_skip(current, source_hash, skip_unchanged):
            logger.info("kg_phase1: gold inalterado — geração %s mantida", current["id"])
            return {"skipped": True, "generation": current["id"], "source_hash": source_hash}

        nodes, quality, edges = _build_rows(entities, rels)

        cur.execute(
            f"insert into {SCHEMA}.generations (status, build_version, source_hash) "
            f"values ('building', %s, %s) returning id",
            (BUILD_VERSION, source_hash),
        )
        generation_id = cur.fetchone()[0]

        cur.executemany(
            f"insert into {SCHEMA}.nodes (generation_id, id, kind, native_id, name, "
            f"description, embedding) values (%s, %s, %s, %s, %s, %s, %s::vector)",
            [
                (generation_id, n["id"], n["kind"], n["native_id"], n["name"],
                 n["description"], _vec_literal(n["embedding"]))
                for n in nodes
            ],
        )
        cur.executemany(
            f"insert into {SCHEMA}.quality_nodes (generation_id, id, family, value) "
            f"values (%s, %s, %s, %s)",
            [(generation_id, q["id"], q["family"], q["value"]) for q in quality],
        )
        _insert_edges(cur, generation_id, edges)

        n_communities = 0
        if run_communities:
            communities = features.detect_communities(edges)
            if communities:
                cur.executemany(
                    f"insert into {SCHEMA}.communities (generation_id, community_id, node_id) "
                    f"values (%s, %s, %s)",
                    [(generation_id, cid, nid) for cid, members in communities for nid in members],
                )
                n_communities = len(communities)

        counts = {
            "nodes": len(nodes),
            "quality_nodes": len(quality),
            "edges": len(edges),
            "similar_a": sum(1 for e in edges if e["type"] == "similar_a"),
            "potencial_parceria": sum(1 for e in edges if e["type"] == "potencial_parceria"),
            "communities": n_communities,
        }

        # SWAP atômico — última operação da transação: a geração nova vira a única
        # `is_current`; as anteriores caem. Commit único → leitores só observam o
        # estado final (nenhuma geração incompleta visível).
        cur.execute(
            f"update {SCHEMA}.generations set is_current = (id = %s) where is_current or id = %s",
            (generation_id, generation_id),
        )
        cur.execute(
            f"update {SCHEMA}.generations set status = 'healthy', finished_at = now(), "
            f"counts = %s::jsonb, error = '' where id = %s",
            (json.dumps(counts), generation_id),
        )

        logger.info("kg_phase1: geração %s construída (%s)", generation_id, counts)
        return {"skipped": False, "generation": generation_id, "source_hash": source_hash, **counts}


# ─────────────────────────────────────────────────────────────────────────────
# Orquestração + falha segura
# ─────────────────────────────────────────────────────────────────────────────

_DSN_RE = re.compile(r"(postgres(?:ql)?://[^\s]+)", re.IGNORECASE)


def _sanitize_error(exc: Exception) -> str:
    """Mensagem SANITIZADA para o ledger — sem conteúdo de documentos, URLs
    sensíveis (DSNs) ou payloads de query."""
    msg = str(exc).strip()
    if not msg:
        return type(exc).__name__
    msg = _DSN_RE.sub("<redacted>", msg).replace("\n", " ")[:300]
    return f"{type(exc).__name__}: {msg}"


def _record_failure(exc: Exception) -> None:
    """Registro best-effort (fora da transação do build) de uma geração `failed`.
    A última saudável permanece corrente — a transação que falhou rolou."""
    try:
        with psycopg.connect(store.get_dsn(), autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"insert into {SCHEMA}.generations (status, build_version, error, finished_at) "
                    f"values ('failed', %s, %s, now())",
                    (BUILD_VERSION, _sanitize_error(exc)),
                )
    except Exception:
        logger.exception("kg_phase1: não foi possível registrar geração 'failed'")
    logger.error("kg_phase1: build falhou (%s)", type(exc).__name__)


def build(*, skip_unchanged: bool = True, run_communities: bool = True) -> dict[str, Any]:
    """Constrói/sincroniza uma NOVA geração da projeção (troca atômica).

    Uma transação: lê o gold, monta em memória, insere a geração nova e faz o
    swap (`is_current`) no MESMO commit. Falha → rollback → a última geração
    saudável permanece corrente (registro `failed` no ledger, best-effort)."""
    conn = store.connect()
    try:
        with conn.transaction():
            return _build_tx(conn, skip_unchanged=skip_unchanged, run_communities=run_communities)
    except Exception as exc:
        _record_failure(exc)
        raise
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    ap = argparse.ArgumentParser(description="Projeção da Fase 1 do grafo (kg_phase1, zero LLM)")
    ap.add_argument("--no-skip", action="store_true",
                    help="rebuild mesmo com source_hash igual ao da geração corrente")
    ap.add_argument("--no-communities", action="store_true",
                    help="pula a detecção de comunidades (networkx)")
    args = ap.parse_args()

    from radar.core.environment import assert_database_target, load_environment_profile

    load_environment_profile()
    assert_database_target("kg_phase1 build")
    out = build(skip_unchanged=not args.no_skip, run_communities=not args.no_communities)
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
