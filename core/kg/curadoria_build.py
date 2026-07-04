"""Build DETERMINÍSTICO dos catálogos curados (KG v2, PR4.1) — SEM LLM.

`investidores.json` / `programas.json` são curados à mão com facetas
ESTRUTURADAS (tese, temas controlados, estágio, ticket, elegibilidade, URLs). O
build antigo (`run_hyper_extract_catalog`) os passava pelo extractor-LLM, que
ACHATAVA tudo num nó formato-edital — jogando a estrutura fora (spec §Motivação
/ §PR4). Aqui reconstruímos os hipergrados direto do JSON, preservando cada
faceta e a URL POR ITEM, e aplicando o **desdobramento D2**:

  investidor → `Ator(investidor)` (identidade, casa como hoje pela tese)
             + `Oportunidade(kind=investimento, aperture=continua)` (a OFERTA:
               "captar com o fundo X" — mecanismo/estágio/ticket/URL)
  programa   → `Oportunidade(kind=programa, aperture=recorrente)` enriquecida

Os `Conceito` (de `tese_keywords`/`setores`) ligam-se à ENTIDADE que casa (Ator
do fundo / Oportunidade do programa) por `viabiliza` — o mesmo padrão das ICTs,
que faz a atribuição concept→entidade no match. Passa pela higiene canônica
(replay determinístico, `llm_new=False`) p/ alinhar ao vocabulário do ecossistema.
"""
from __future__ import annotations

import hashlib
import json
import logging

from config import KNOWLEDGE_GRAPH_DIR
from core.kg.schema import macro_temas_vocab, slugify
from core.skills import _normalize_mechanism

logger = logging.getLogger(__name__)

# setores genéricos que não são Conceito de afinidade (ruído de "casa com tudo").
_SKIP_SECTOR = {"multissetorial", "", "outros"}


def _hash(obj) -> str:
    return hashlib.sha256(json.dumps(obj, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def _macro(themes) -> list[str]:
    """Filtra `tese_themes` ao vocabulário controlado de macro-temas (D8)."""
    vocab = set(macro_temas_vocab())
    fora = [t for t in (themes or []) if t not in vocab]
    if fora:
        logger.info("curadoria_build: macro_temas fora do vocab descartados: %s", fora)
    return [t for t in (themes or []) if t in vocab]


def _slug_of(item: dict) -> str:
    """Slug estável do item — do `id` curado (`investidor:indicator-capital`) se
    houver, senão do name."""
    raw = item.get("id") or ""
    return raw.split(":", 1)[1] if ":" in raw else slugify(item.get("name", ""))


def _mecanismos(values) -> list[str]:
    """Normaliza tipos de mecanismo p/ slugs canônicos (core/skills), descartando
    o que não casa."""
    out: list[str] = []
    for v in values or []:
        slug = _normalize_mechanism(v)
        if slug and slug not in out:
            out.append(slug)
    return out


def _concepts(terms, dim: str, owner_id: str) -> tuple[list[dict], list[dict]]:
    """Conceito(dim) por termo + aresta `viabiliza` ao dono (entidade que casa).
    O padrão das ICTs: a aresta atribui o conceito à entidade no match."""
    nodes, edges = [], []
    for t in terms or []:
        t = (t or "").strip()
        if not t or t.lower() in _SKIP_SECTOR:
            continue
        cid = f"con:{slugify(t)}"
        nodes.append({"id": cid, "type": "Conceito", "dim": dim, "name": t})
        edges.append({"type": "viabiliza", "members": [owner_id, cid], "description": ""})
    return nodes, edges


def _investidor(f: dict) -> tuple[list[dict], list[dict]]:
    slug = _slug_of(f)
    ator_id, op_id = f"ator:{slug}", f"op:{slug}-investimento"
    tese = f.get("tese", "") or ""
    ator = {"id": ator_id, "type": "Ator", "kind": "investidor",
            "name": f.get("name", ""), "description": tese}
    op = {"id": op_id, "type": "Oportunidade", "kind": "investimento",
          "aperture": "continua", "name": f"Investimento — {f.get('name', '')}",
          "description": tese, "mecanismo": ["equity"],
          "macro_temas": _macro(f.get("tese_themes"))}
    # facetas estruturadas preservadas (antes o LLM as achatava/perdia)
    for src, dst in (("estagio_alvo", "estagio_alvo"), ("ticket_range", "ticket_range"),
                     ("lead_follow", "lead_follow"), ("site", "url"),
                     ("source_urls", "urls_documentos")):
        if f.get(src):
            op[dst] = f[src]
    edges = [{"type": "pertence_a", "members": [op_id, ator_id], "description": ""}]
    ck, ce = _concepts(f.get("tese_keywords"), "tecnologia", ator_id)
    cs, cse = _concepts(f.get("setores"), "aplicacao", ator_id)
    return [ator, op] + ck + cs, edges + ce + cse


def _programa(p: dict) -> tuple[list[dict], list[dict]]:
    slug = _slug_of(p)
    op_id = f"op:{slug}"
    desc = (p.get("descricao") or "").strip()
    if p.get("beneficio"):
        desc = f"{desc} Benefício: {p['beneficio']}".strip()
    op = {"id": op_id, "type": "Oportunidade", "kind": "programa",
          "aperture": "recorrente", "name": p.get("name", ""), "description": desc,
          "mecanismo": _mecanismos([p.get("tipo")]),
          "macro_temas": _macro(p.get("tese_themes"))}
    if p.get("estagio_alvo"):
        op["estagio_alvo"] = p["estagio_alvo"]
    if p.get("ticket_range"):
        op["ticket_range"] = p["ticket_range"]
    if p.get("elegibilidade"):
        op["requisitos_texto"] = [p["elegibilidade"]]
    if p.get("site"):
        op["url"] = p["site"]
    docs = [u for u in (p.get("source_urls") or []) if u]
    if p.get("faq_url"):
        docs.append(p["faq_url"])
    if docs:
        op["urls_documentos"] = docs
    ck, ce = _concepts(p.get("tese_keywords"), "tecnologia", op_id)
    cs, cse = _concepts(p.get("setores"), "aplicacao", op_id)
    return [op] + ck + cs, ce + cse


def _assemble(nodes: list[dict], edges: list[dict], *, file_key: str, source_hash: str) -> dict:
    """Dedup por id + higiene canônica determinística (replay, `llm_new=False`) —
    alinha os Conceitos curados ao vocabulário do ecossistema sem chamar LLM."""
    from core.kg.canonicalize import canonicalize_fresh_graph
    from core.kg.migrate_v2 import migrate_to_v2

    seen: set[str] = set()
    uniq_nodes = []
    for n in nodes:
        if n["id"] not in seen:
            seen.add(n["id"])
            uniq_nodes.append(n)
    uniq_edges, eseen = [], set()
    for e in edges:
        key = (e["type"], tuple(e["members"]))
        if key not in eseen and all(m in seen for m in e["members"]):
            eseen.add(key)
            uniq_edges.append(e)

    graph = {"format_version": 2, "source_hash": source_hash,
             "proveniencia": {"fonte": "curadoria"}, "nodes": uniq_nodes, "edges": uniq_edges}
    graph = migrate_to_v2(graph)  # idempotente em v2 — preserva ids/props
    return canonicalize_fresh_graph(graph, file_key=file_key, llm_new=False)


def build_investidores_graph() -> dict | None:
    path = KNOWLEDGE_GRAPH_DIR / "investidores.json"
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    funds = data.get("investidores", [])
    nodes: list[dict] = []
    edges: list[dict] = []
    for f in funds:
        if not f.get("name"):
            continue
        n, e = _investidor(f)
        nodes += n
        edges += e
    graph = _assemble(nodes, edges, file_key="investidores", source_hash=_hash(data))
    logger.info("curadoria_build: investidores → %d nós, %d arestas (%d fundos)",
                len(graph["nodes"]), len(graph["edges"]), len(funds))
    return graph


def build_programas_graph() -> dict | None:
    path = KNOWLEDGE_GRAPH_DIR / "programas.json"
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    progs = data.get("programas", [])
    nodes: list[dict] = []
    edges: list[dict] = []
    for p in progs:
        if not p.get("name"):
            continue
        n, e = _programa(p)
        nodes += n
        edges += e
    graph = _assemble(nodes, edges, file_key="programas", source_hash=_hash(data))
    logger.info("curadoria_build: programas → %d nós, %d arestas (%d programas)",
                len(graph["nodes"]), len(graph["edges"]), len(progs))
    return graph


# (file_key, builder) — os catálogos curados que passam a ser DETERMINÍSTICOS
# (investidores/programas). `ict` NÃO entra: vem de bronze/ict_raw (narrativo),
# segue no extractor-LLM.
CURATED_BUILDERS = {
    "investidores": build_investidores_graph,
    "programas": build_programas_graph,
}


def build_curated_graphs() -> dict[str, dict]:
    """`{file_key: graph}` dos catálogos curados reconstruídos deterministicamente."""
    out: dict[str, dict] = {}
    for fk, builder in CURATED_BUILDERS.items():
        g = builder()
        if g:
            out[fk] = g
    return out
