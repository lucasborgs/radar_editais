"""core/kg/entity_catalog.py — catálogo + explore lidos SÓ de SQL (v3, Fase 3
PR-B — docs/specs/v3-unified.md §8, §10).

Fonte única do catálogo (stats, listas, fichas) e do mapeamento do ecossistema
(busca semântica, tags compartilhadas, vizinhança estrutural), lendo do schema
gold (migration 036, populado por `radar.core.kg.gold`). SUBSTITUI e DELETA o caminho
legado `hypergraph_catalog`/`load_all_hypergraphs` — não há mais dispatcher nem
flag `CATALOG_BACKEND`: SQL é o único radar.api.

Dois regimes de acesso ao Postgres (mesma fronteira do resto do v3):
  • Leituras ESTRUTURADAS (cards, listas, arestas) — via cliente Supabase
    (PostgREST). Dado pequeno (~150 editais + ~120 curados); scans são baratos.
  • Busca SEMÂNTICA (`search_entities`) — via psycopg + numpy (snapshot em
    memória), porque supabase-py corrompe colunas `vector` (mesma razão de
    `match_v3`/`company_chunks`). Precisa de DATABASE_URL.

Mapeamento gold → shape legado de `edital_card(full=True)` (contrato lido por
writing_session/planning_node/checklist/frontend):
  title←name · deadline (dd/mm/yyyy — formato que `schema.parse_deadline` lê) ·
  objective←description · themes←setores · technologies←tecnologias_tags ·
  aperture←formato · mecanismo←[mecanismo] · fonte_recurso←arestas `operado_por` ·
  programs←`subordinado_a` · icts←relação opcional `exige_parceria_com` · value←ticket_min/max ·
  status←recomputado do deadline · publico_alvo/eligible_entities←metadata.publico_alvo ·
  exclusoes←metadata.exclusoes · constraints←constraints (call B do gold, §6).
"""
from __future__ import annotations

import logging
import re
from datetime import date
from typing import Any

from radar.core.kg.provenance_read import public_provenance

logger = logging.getLogger(__name__)


# ===========================================================================
# Conexão + utilitários de shape
# ===========================================================================

def _client():
    from radar.core.infra.db import get_supabase_service
    return get_supabase_service()


def _as_list(v: Any) -> list:
    """Defensivo: metadata pode trazer string/None onde o card espera lista."""
    if isinstance(v, list):
        return v
    if v in (None, "", []):
        return []
    return [v]


def _to_date(raw: Any) -> date | None:
    if not raw:
        return None
    try:
        return date.fromisoformat(str(raw)[:10])
    except ValueError:
        return None


def _status_from_row(row: dict) -> str:
    """ABERTA/ENCERRADA pelo deadline (recomputado a cada leitura, não confia no
    valor congelado da coluna `status`); fallback ao status extraído."""
    d = _to_date(row.get("deadline"))
    if d:
        return "ABERTA" if d >= date.today() else "ENCERRADA"
    raw = (row.get("status") or "").strip().lower()
    if raw.startswith("abert") or raw == "ativa":
        return "ABERTA"
    if raw.startswith(("encerr", "fechad", "conclu", "expir")) or raw == "inativa":
        return "ENCERRADA"
    return "Desconhecido"


def _deadline_display(row: dict) -> str:
    """dd/mm/yyyy — único formato que `radar.core.kg.schema.parse_deadline` lê; a coluna
    SQL é ISO."""
    d = _to_date(row.get("deadline"))
    return d.strftime("%d/%m/%Y") if d else ""


def _value_display(row: dict) -> str | None:
    tmin, tmax = row.get("ticket_min"), row.get("ticket_max")
    if tmin is None and tmax is None:
        return None
    if tmin is not None and tmax is not None and tmin != tmax:
        return f"R$ {tmin:,.0f} – R$ {tmax:,.0f}"
    return f"R$ {(tmin if tmin is not None else tmax):,.0f}"


# ── Casamento de tema (token-bidirecional, tolerante a frase natural) ────────
_THEME_STOPWORDS = {
    "de", "da", "do", "das", "dos", "e", "em", "no", "na", "nos", "nas",
    "para", "por", "com", "sem", "ou", "the", "of", "for", "and", "que",
    "uma", "um", "sobre", "como",
}


def _theme_tokens(text: str) -> list[str]:
    raw = re.findall(r"\w+", (text or "").lower(), flags=re.UNICODE)
    return [t for t in raw if len(t) >= 4 and t not in _THEME_STOPWORDS]


def _theme_match(needle: str, themes: list[str]) -> bool:
    """Vazio casa tudo; senão substring direto OU token bidirecional (≥4 chars).
    Recall-first: o explore prefere oferecer demais a devolver vazio."""
    n = (needle or "").strip().lower()
    if not n:
        return True
    blob = " ".join(t or "" for t in themes).lower()
    if n in blob:
        return True
    ntoks, ttoks = _theme_tokens(n), _theme_tokens(blob)
    return any(nt in tt or tt in nt for nt in ntoks for tt in ttoks)


# ===========================================================================
# Arestas (entity_relationships) — nomes dos vizinhos por tipo, em lote
# ===========================================================================

def _rel_names_batch(client, entity_ids: list[str], rel_type: str) -> dict[str, list[str]]:
    """{source_id: [nomes dos targets]} para uma aresta, em lote (evita N+1)."""
    if not entity_ids:
        return {}
    rels = (
        client.table("entity_relationships")
        .select("source_id,target_id")
        .in_("source_id", entity_ids)
        .eq("type", rel_type)
        .execute()
        .data
    ) or []
    if not rels:
        return {}
    target_ids = sorted({r["target_id"] for r in rels})
    ents = client.table("entities").select("id,name").in_("id", target_ids).execute().data or []
    name_by_id = {e["id"]: e.get("name", "") for e in ents}
    out: dict[str, list[str]] = {}
    for r in rels:
        nm = name_by_id.get(r["target_id"])
        if nm:
            out.setdefault(r["source_id"], []).append(nm)
    return {k: sorted(set(v)) for k, v in out.items()}


# ===========================================================================
# Card do edital (shape legado de edital_card(full=True))
# ===========================================================================

def _row_to_card(
    row: dict, *, programs: dict[str, list[str]], icts: dict[str, list[str]],
    agencies: dict[str, list[str]],
) -> dict:
    from radar.core.skills import mechanism_display

    eid = row["id"]
    meta = row.get("metadata") or {}
    mecanismo = [row["mecanismo"]] if row.get("mecanismo") else []
    tags = list(row.get("tecnologias_tags") or [])
    publico_alvo = _as_list(meta.get("publico_alvo"))
    return {
        "id": row["native_id"],
        "source": row["source"],
        "title": row.get("name") or "",
        "status": _status_from_row(row),
        "deadline": _deadline_display(row),
        "themes": list(row.get("setores") or []),
        "technologies": tags,
        "programs": programs.get(eid, []),
        "publico_alvo": publico_alvo,
        "fonte_recurso": agencies.get(eid, []),
        "opportunity_type": "edital",
        "official_url": meta.get("url") or "",
        "aperture": row.get("formato") or "",
        "macro_temas": [],
        "kind": row.get("kind") or "edital",
        "objective": row.get("description") or "",
        "mecanismo": mecanismo,
        "mechanism": ", ".join(mechanism_display(m) for m in mecanismo),
        # publico_alvo tipado é o "público elegível" (paridade com o legado, onde
        # eligible_entities == _publico_alvo).
        "eligible_entities": publico_alvo,
        "key_requirements": list(row.get("requisitos_texto") or []),
        "exclusoes": _as_list(meta.get("exclusoes")),
        "value": _value_display(row),
        "icts": icts.get(eid, []),
        "investidores": [],
        "constraints": list(row.get("constraints") or []),
        "document_urls": [],
        "collected_at": row.get("updated_at") or "",
        "provenance": public_provenance(row.get("provenance")),
    }


def _resolve_native(client, ref: str, kinds: list[str]) -> dict | None:
    """Linha de entidade por id público. Aceita native_id exato ("finep:589",
    "investidor:x") e — só quando `ref` não tem fonte — o sufixo nativo puro
    ("589"), único por-fonte."""
    eid = (ref or "").strip()
    if not eid:
        return None
    q = client.table("entities").select("*").in_("kind", kinds)
    rows = q.eq("native_id", eid).limit(1).execute().data or []
    if rows:
        return rows[0]
    if ":" not in eid:  # sufixo nativo puro → casa "%:{eid}", único por fonte
        rows = (
            client.table("entities").select("*").in_("kind", kinds)
            .like("native_id", f"%:{eid}").execute().data or []
        )
        return rows[0] if len(rows) == 1 else None
    return None


def get_edital(edital_id: str) -> dict | None:
    client = _client()
    row = _resolve_native(client, edital_id, ["edital"])
    if row is None:
        return None
    rid = row["id"]
    programs = _rel_names_batch(client, [rid], "subordinado_a")
    icts = _rel_names_batch(client, [rid], "exige_parceria_com")
    agencies = _rel_names_batch(client, [rid], "operado_por")
    return _row_to_card(row, programs=programs, icts=icts, agencies=agencies)


def list_editais(
    status: str | None = None, tema: str | None = None, limit: int = 200,
) -> list[dict]:
    client = _client()
    rows = client.table("entities").select("*").eq("kind", "edital").execute().data or []
    ids = [r["id"] for r in rows]
    programs = _rel_names_batch(client, ids, "subordinado_a")
    icts = _rel_names_batch(client, ids, "exige_parceria_com")
    agencies = _rel_names_batch(client, ids, "operado_por")
    cards = [_row_to_card(r, programs=programs, icts=icts, agencies=agencies) for r in rows]

    if status:
        cards = [c for c in cards if c["status"].upper() == status.upper()]
    if tema:
        cards = [
            c for c in cards
            if _theme_match(tema, c.get("themes", []) + c.get("technologies", []) + c.get("programs", []))
        ]
    cards.sort(key=lambda c: (c["status"] != "ABERTA", c["title"].lower()))
    return cards[:limit]


# ===========================================================================
# Ficha unificada (edital | programa | investidor) — D1/PR8
# ===========================================================================

_CURATED_STATUS = {"ativa": "ABERTA", "inativa": "ENCERRADA"}


def _curated_card(row: dict, *, agencies: dict[str, list[str]]) -> dict:
    """Ficha de um curado (programa/investidor) — mesmo shape de `_curated_card`
    do legado, derivado da linha de `entities` + arestas."""
    from radar.core.skills import mechanism_display

    meta = row.get("metadata") or {}
    mecanismo = [row["mecanismo"]] if row.get("mecanismo") else []
    tags = list(row.get("tecnologias_tags") or [])
    estagio = _as_list(meta.get("estagio_alvo"))
    return {
        "id": row["native_id"],
        "source": row["source"],
        "kind": row.get("kind") or "",
        "aperture": row.get("formato") or "",
        "title": row.get("name") or "",
        "status": _CURATED_STATUS.get((row.get("status") or "").strip().lower(), "Desconhecido"),
        "deadline": _deadline_display(row),
        "objective": row.get("description") or "",
        "mecanismo": mecanismo,
        "mechanism": ", ".join(mechanism_display(m) for m in mecanismo),
        "macro_temas": [],
        "themes": list(row.get("setores") or []),
        "technologies": tags,
        "programs": [],
        "publico_alvo": _as_list(meta.get("publico_alvo")),
        "eligible_entities": _as_list(meta.get("publico_alvo")),
        "key_requirements": list(row.get("requisitos_texto") or []),
        "exclusoes": _as_list(meta.get("exclusoes")),
        "constraints": list(row.get("constraints") or []),
        "value": _value_display(row),
        "estagio_alvo": ", ".join(str(e) for e in estagio),
        "ticket_range": _value_display(row) or "",
        "lead_follow": meta.get("lead_follow") or "",
        "icts": [],
        "investidores": [],
        "fonte_recurso": agencies.get(row["id"], []),
        "opportunity_type": row.get("kind") or "",
        "official_url": meta.get("site") or meta.get("url") or "",
        "document_urls": [],
        "collected_at": row.get("updated_at") or "",
        "provenance": public_provenance(row.get("provenance")),
    }


def get_opportunity(opp_id: str) -> dict | None:
    """Ficha unificada (D1): resolve edital OU curado (programa/investidor).
    Superset de `get_edital` — o router /oportunidades/{id} chama esta. ICT não
    é ficha direta (paridade com o legado)."""
    card = get_edital(opp_id)
    if card is not None:
        return card
    client = _client()
    row = _resolve_native(client, opp_id, ["programa", "investidor"])
    if row is None:
        return None
    agencies = _rel_names_batch(client, [row["id"]], "operado_por")
    return _curated_card(row, agencies=agencies)


# ===========================================================================
# Catálogo de entidades (ict / investidores / programas) + stats + merge
# ===========================================================================

# catalog_key → kind gold.
_CATALOG_KIND: dict[str, str] = {
    "ict": "ict",
    "investidores": "investidor",
    "programas": "programa",
}
_KIND_TYPE = {"ict": "Ator", "investidor": "Ator", "programa": "Oportunidade"}


def list_entity_catalog(
    catalog_key: str, *, tema: str = "", limit: int = 50,
) -> list[dict]:
    """Entidades de um catálogo (ict, investidores, programas) filtrando por tema.
    Retorna dicts com `id` (native_id), `name`, `description`, `themes` (setores +
    tags) e `type` — compat com list_icts/list_investidores."""
    kind = _CATALOG_KIND.get(catalog_key)
    if kind is None:
        return []
    client = _client()
    rows = client.table("entities").select(
        "native_id,name,description,setores,tecnologias_tags"
    ).eq("kind", kind).execute().data or []
    out: list[dict] = []
    for r in rows:
        themes = list(r.get("setores") or []) + list(r.get("tecnologias_tags") or [])
        if tema and not _theme_match(tema, themes):
            continue
        out.append({
            "id": r["native_id"],
            "name": (r.get("name") or "").strip(),
            "description": r.get("description") or "",
            "themes": sorted(set(themes)),
            "type": _KIND_TYPE[kind],
        })
        if len(out) >= limit:
            break
    return out


def _count(client, kind: str) -> int:
    return (
        client.table("entities").select("id", count="exact")
        .eq("kind", kind).limit(1).execute().count
    ) or 0


def get_stats() -> dict:
    client = _client()
    editais = list_editais(limit=10_000)
    by_status: dict[str, int] = {}
    themes: set[str] = set()
    fontes: set[str] = set()
    for c in editais:
        by_status[c["status"]] = by_status.get(c["status"], 0) + 1
        themes.update(c["themes"])
        fontes.update(c["fonte_recurso"])
    n_programas = _count(client, "programa")
    n_investidores = _count(client, "investidor")
    n_icts = _count(client, "ict")
    total = len(editais)
    return {
        "total_editais": total,
        "last_updated": "",
        "by_status": by_status,
        "n_themes": len(themes),
        "n_fontes": len(fontes),
        "n_programas": n_programas,
        "n_investidores": n_investidores,
        "n_icts": n_icts,
        "total_oportunidades": total + n_programas + n_investidores + n_icts,
    }


def list_opportunities(tipo: str | None = None, limit: int = 200) -> list[dict]:
    """Merge editais + programas + investidores + ICTs com discriminador `type`.
    O `id` é o native_id (round-trip direto para `get_opportunity`)."""
    result: list[dict] = []

    if tipo in (None, "edital"):
        for c in list_editais(limit=limit):
            result.append({
                "id": c["id"], "title": c["title"], "type": "edital",
                "themes": c.get("themes", []), "status": c.get("status", "Desconhecido"),
                "deadline": c.get("deadline", ""), "fonte_recurso": c.get("fonte_recurso", []),
                "aperture": c.get("aperture", ""), "macro_temas": c.get("macro_temas", []),
            })
    for want, catalog_key, t in (
        ("programa", "programas", "programa"),
        ("investidor", "investidores", "investidor"),
        ("ict", "ict", "ict"),
    ):
        if tipo in (None, want):
            for e in list_entity_catalog(catalog_key, limit=limit):
                result.append({
                    "id": e["id"], "title": e["name"], "type": t,
                    "themes": e.get("themes", []), "description": e.get("description", ""),
                })
    return result[:limit]


# ===========================================================================
# Fichas curadas cruas (writing) + ofertas de investimento
# ===========================================================================

def _fetch_one(client, kind: str, native_id: str) -> dict | None:
    rows = (
        client.table("entities").select("*")
        .eq("kind", kind).eq("native_id", (native_id or "").strip()).limit(1)
        .execute().data or []
    )
    return rows[0] if rows else None


def _ticket_dict(row: dict) -> dict:
    return {"min_brl": row.get("ticket_min"), "max_brl": row.get("ticket_max")}


def get_investidor(native_id: str) -> dict | None:
    """Nó do fundo em shape cru (compat `investidores.json`) para o card de
    escrita do pitch — reconstruído das colunas + metadata de `entities`."""
    row = _fetch_one(_client(), "investidor", native_id)
    if row is None:
        return None
    meta = row.get("metadata") or {}
    return {
        "id": row["native_id"],
        "name": row.get("name") or "",
        "tese": row.get("description") or "",
        "tese_themes": list(meta.get("tese_themes") or []),
        "setores": list(meta.get("verticais") or row.get("setores") or []),
        "estagio_alvo": list(meta.get("estagio_alvo") or []),
        "ticket_range": _ticket_dict(row),
        "lead_follow": meta.get("lead_follow") or "",
        "portfolio": list(meta.get("portfolio") or []),
        "co_investidores": list(meta.get("co_investidores") or []),
        "site": meta.get("site") or "",
        "source_urls": list(meta.get("source_urls") or []),
        "verificado_em": meta.get("verificado_em"),
        "provenance": public_provenance(row.get("provenance")),
    }


def get_programa(native_id: str) -> dict | None:
    """Nó do programa em shape cru (compat `programas.json`) para o card de
    escrita — reconstruído das colunas + metadata de `entities`."""
    row = _fetch_one(_client(), "programa", native_id)
    if row is None:
        return None
    meta = row.get("metadata") or {}
    return {
        "id": row["native_id"],
        "name": row.get("name") or "",
        "operador": meta.get("operador") or "",
        "tipo": meta.get("tipo") or "",
        "descricao": row.get("description") or "",
        "formato": row.get("formato") or "",
        "cadencia": meta.get("cadencia") or "",
        "beneficio": meta.get("beneficio") or "",
        "ticket_range": _ticket_dict(row),
        "estagio_alvo": list(meta.get("estagio_alvo") or []),
        "elegibilidade": meta.get("elegibilidade") or "",
        "site": meta.get("site") or "",
        "faq_url": meta.get("faq_url") or "",
        "provenance": public_provenance(row.get("provenance")),
    }


# ===========================================================================
# Consciência temporal (temporal.py) — deadline/status crus por edital
# ===========================================================================

def get_entity_temporal(native_id: str) -> dict | None:
    """{deadline (dd/mm/yyyy — `schema.parse_deadline` lê), status} de um edital/
    programa, ou None se ausente. Fonte do bloco `[CONTEXTO TEMPORAL]`."""
    client = _client()
    row = _resolve_native(client, native_id, ["edital", "programa"])
    if row is None:
        return None
    return {"deadline": _deadline_display(row) or None, "status": row.get("status")}


# ===========================================================================
# §8 — mapeamento do ecossistema (substitui as arestas semânticas do hipergrado)
# ===========================================================================

# ── §8.2 — vizinhança estrutural (BFS sobre entity_relationships) ────────────

def _bfs_edges(
    edges: list[tuple[str, str, str]], seed_id: str, depth: int,
) -> list[tuple[str, str, str]]:
    """BFS de arestas (tratadas como NÃO-direcionadas) a partir de `seed_id` até
    `depth` saltos. Retorna as arestas alcançadas, sem repetição. Cycle-safe via
    `visited` de nós. Função PURA (testável sem DB): profundidade e ciclos.

    `edges` = [(source_id, target_id, type)]. `entity_relationships` é minúscula
    (~centenas de linhas p/ o corpus pré-beta), então a travessia em processo é
    barata e evita uma RPC/CTE + superfície de deploy."""
    depth = max(1, int(depth))
    adj: dict[str, list[tuple[str, str]]] = {}
    for s, t, rtype in edges:
        adj.setdefault(s, []).append((t, rtype))
        adj.setdefault(t, []).append((s, rtype))  # não-direcionado

    visited: set[str] = {seed_id}
    frontier = {seed_id}
    seen_edges: set[frozenset] = set()
    collected: list[tuple[str, str, str]] = []
    for _ in range(depth):
        nxt: set[str] = set()
        for node in frontier:
            for neigh, rtype in adj.get(node, []):
                key = frozenset((node, neigh, rtype))
                if key in seen_edges:
                    continue
                seen_edges.add(key)
                collected.append((node, neigh, rtype))
                if neigh not in visited:
                    nxt.add(neigh)
        visited |= nxt
        frontier = nxt
        if not frontier:
            break
    return collected


def _resolve_ref_row(client, ref: str) -> dict | None:
    """Resolve um id/nome de entidade para a linha (id, kind, native_id, name).
    Tenta native_id exato → sufixo nativo → nome (case-insensitive)."""
    eid = (ref or "").strip()
    if not eid:
        return None
    sel = "id,kind,native_id,name,description,setores,tecnologias_tags"
    rows = client.table("entities").select(sel).eq("native_id", eid).limit(1).execute().data or []
    if rows:
        return rows[0]
    if ":" not in eid:
        rows = client.table("entities").select(sel).like("native_id", f"%:{eid}").execute().data or []
        if len(rows) == 1:
            return rows[0]
    rows = client.table("entities").select(sel).ilike("name", eid).limit(2).execute().data or []
    return rows[0] if rows else None


def get_node_neighborhood(entity_ref: str, depth: int = 1) -> str:
    """Vizinhança ESTRUTURAL de uma entidade (BFS sobre `entity_relationships`):
    operado_por/subordinado_a/exige_parceria_com/credenciada_por. Resolve o nó
    por native_id ou nome. String pronta para a tool de explore."""
    client = _client()
    center = _resolve_ref_row(client, entity_ref)
    if center is None:
        return (
            f"Nenhuma entidade '{entity_ref}' no catálogo. Tente o id (ex.: "
            "'finep:589'), o nome do edital ou de uma agência/ICT/programa."
        )
    rels = client.table("entity_relationships").select("source_id,target_id,type").execute().data or []
    edges = [(r["source_id"], r["target_id"], r["type"]) for r in rels]
    collected = _bfs_edges(edges, center["id"], depth)
    if not collected:
        return f"### {center.get('name', '')} [{center.get('kind', '')}]\n  (sem relações estruturais)"

    node_ids = {center["id"]}
    for s, t, _ in collected:
        node_ids.add(s)
        node_ids.add(t)
    ents = client.table("entities").select("id,name,kind").in_("id", sorted(node_ids)).execute().data or []
    label = {e["id"]: f"{e.get('name', '')} ({e.get('kind', '')})" for e in ents}

    lines = [f"### {center.get('name', '')} [{center.get('kind', '')}]", f"  relações ({len(collected)}):"]
    for s, t, rtype in collected:
        lines.append(f"    • {rtype}: {label.get(s, s)} → {label.get(t, t)}")
    return "\n".join(lines)


# ── §8.2 — tags compartilhadas (arestas semânticas implícitas, join GIN) ─────

def related_by_tags(entity_ref: str, *, kind: str | None = None, limit: int = 15) -> list[dict]:
    """Entidades que COMPARTILHAM `tecnologias_tags` com `entity_ref` (join por
    overlap — o `&&` do índice GIN). Rankeadas por nº de tags em comum. `kind`
    opcional filtra o tipo de vizinho. Exclui a própria entidade."""
    client = _client()
    center = _resolve_ref_row(client, entity_ref)
    if center is None:
        return []
    tags = list(center.get("tecnologias_tags") or [])
    if not tags:
        return []
    q = client.table("entities").select(
        "native_id,name,kind,description,tecnologias_tags"
    ).overlaps("tecnologias_tags", tags)
    if kind:
        q = q.eq("kind", kind)
    rows = q.execute().data or []
    want = set(tags)
    scored: list[tuple[int, dict]] = []
    for r in rows:
        if r["native_id"] == center["native_id"]:
            continue
        shared = sorted(want & set(r.get("tecnologias_tags") or []))
        if not shared:
            continue
        scored.append((len(shared), {
            "id": r["native_id"], "name": r.get("name") or "", "kind": r.get("kind") or "",
            "shared_tags": shared, "description": (r.get("description") or "")[:200],
        }))
    scored.sort(key=lambda x: (-x[0], x[1]["name"].lower()))
    return [d for _, d in scored[:limit]]


# ── §8.1 — busca semântica sobre entities.embedding (psycopg + numpy) ────────

_SEARCH_SNAPSHOT: dict | None = None

_SEARCH_SQL = """
select id, kind, native_id, name, description, setores, tecnologias_tags, embedding
from public.entities where embedding is not null
"""


def _search_probe(cur) -> tuple:
    cur.execute("select count(*), coalesce(max(updated_at)::text, '') from public.entities")
    return tuple(cur.fetchone())


def _load_search_snapshot(conn) -> dict:
    import numpy as np

    from radar.core.services.company_chunks import parse_vec

    with conn.cursor() as cur:
        probe = _search_probe(cur)
        cur.execute(_SEARCH_SQL)
        cols = [d.name for d in cur.description]
        rows = [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]
    if not rows:
        return {"probe": probe, "rows": [], "mat": np.empty((0, 0), dtype=np.float32)}
    mat = np.stack([parse_vec(r.pop("embedding")) for r in rows])
    mat = mat / (np.linalg.norm(mat, axis=1, keepdims=True) + 1e-9)
    for r in rows:
        r["id"] = str(r["id"])
    return {"probe": probe, "rows": rows, "mat": mat.astype(np.float32)}


def _get_search_snapshot() -> dict:
    """Snapshot dos embeddings de `entities`, revalidado por sonda barata (mesma
    postura de match_v3). Precisa de DATABASE_URL (supabase-py corrompe `vector`)."""
    global _SEARCH_SNAPSHOT
    import psycopg

    from radar.core.services.company_chunks import get_dsn

    with psycopg.connect(get_dsn(), autocommit=True) as conn:
        if _SEARCH_SNAPSHOT is not None:
            with conn.cursor() as cur:
                if _search_probe(cur) == _SEARCH_SNAPSHOT["probe"]:
                    return _SEARCH_SNAPSHOT
        _SEARCH_SNAPSHOT = _load_search_snapshot(conn)
    return _SEARCH_SNAPSHOT


def _rank_by_similarity(query_vec, snapshot: dict, kind: str | None, k: int) -> list[dict]:
    """Top-k por cosseno (função pura sobre o snapshot; testável com vetores
    fabricados). Filtra por `kind` ANTES do top-k."""
    import numpy as np

    rows, mat = snapshot["rows"], snapshot["mat"]
    if not rows or mat.size == 0:
        return []
    idxs = [i for i, r in enumerate(rows) if kind is None or r["kind"] == kind]
    if not idxs:
        return []
    q = np.asarray(query_vec, dtype=np.float32)
    q = q / (np.linalg.norm(q) + 1e-9)
    sims = mat[idxs] @ q
    order = np.argsort(-sims)[:k]
    out: list[dict] = []
    for j in order:
        r = rows[idxs[int(j)]]
        out.append({
            "id": r["native_id"], "name": r.get("name") or "", "kind": r.get("kind") or "",
            "description": (r.get("description") or "")[:240],
            "setores": list(r.get("setores") or []),
            "tecnologias_tags": list(r.get("tecnologias_tags") or []),
            "score": round(float(sims[int(j)]), 4),
        })
    return out


def search_entities(query: str, *, kind: str | None = None, k: int = 15) -> list[dict]:
    """Busca semântica sobre `entities.embedding` ("quais atores atuam em visão
    computacional?"). `kind` filtra edital/programa/investidor/ict/agencia.
    Retorna [{id, name, kind, description, setores, tecnologias_tags, score}]."""
    q = (query or "").strip()
    if not q:
        return []
    from radar.core.retrieval.embedder import embed_query

    snap = _get_search_snapshot()
    return _rank_by_similarity(embed_query(q), snap, kind, k)
