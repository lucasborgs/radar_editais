"""core/kg/entity_catalog.py — bridge SQL sobre `entities`/`entity_relationships`
(v3, Fase 3 PR-A — docs/specs/v3-unified.md §8, §10).

Espelha as assinaturas e o SHAPE dos dicts de `hypergraph_catalog` (`get_edital`,
`list_editais`) lendo do schema gold (migration 036, populado por `core.kg.gold`)
em vez do hipergrado v2. Os consumidores do Grupo A (writing_session,
checklist_service, writing_tools, critic_agent, planning_node, routers
applications/writing) importam ESTE módulo — nunca `hypergraph_catalog`
diretamente — e nunca espalham `if CATALOG_BACKEND == ...` no próprio código;
o dispatch vive só aqui.

Flag `CATALOG_BACKEND` (env): `hypergraph` (default) | `sql`. Default
permanece `hypergraph` porque o Postgres REMOTO ainda não tem a migration 036
nem o ingest — um default `sql` quebraria writing/applications em prod no
próximo deploy. O flip é decisão de deploy separada (fora desta mudança);
com o default, o comportamento é idêntico ao de `hypergraph_catalog` (mera
redireção de import).

Mapeamento de campos (gold → shape legado de `edital_card(full=True)`):
  1:1                 → title←name, deadline (reformatada dd/mm/yyyy — é o
                        formato que `core.kg.schema.parse_deadline` entende),
                        objective←description, key_requirements←requisitos_texto,
                        constraints←constraints, mecanismo←[mecanismo] (coluna é
                        singular no gold; lista no shape legado)
  status              → recomputado a partir do deadline (mesma regra de
                        `hypergraph_catalog._status`, não o valor congelado da
                        coluna — evita staleness entre re-ingests)
  themes/technologies  → setores / tecnologias_tags (mudança semântica: taxonomia
                        fechada de 16 vs folksonomia livre — ver spec §4.3;
                        mesmo NOME de campo, universo de valores diferente)
  aperture            → formato (edital_periodico/fluxo_continuo/credenciamento)
  fonte_recurso        → nomes das entidades ligadas por aresta `operado_por`
  programs             → nomes ligados por `subordinado_a`
  icts                 → nomes ligados por `exige_parceria_com`
  value                → derivado de ticket_min/ticket_max (numéricos no gold;
                        o campo legado vinha de texto livre do LLM — perde
                        precisão de formatação, não de conteúdo)
  collected_at         → `updated_at` da entidade (aproximação: hora do último
                        upsert, não a data de coleta original do bronze)
  SEM equivalente no gold (ficam [] / None — não existe fonte, não é bug):
    publico_alvo, eligible_entities, aplicacoes, exclusoes, document_urls,
    investidores (editais não têm aresta para investidor no schema novo),
    macro_temas (setores já ocupa esse papel; duplicar seria redundante)
"""
from __future__ import annotations

import logging
import os
from datetime import date
from typing import Any

logger = logging.getLogger(__name__)

_BACKEND_ENV = "CATALOG_BACKEND"


def _backend() -> str:
    return os.environ.get(_BACKEND_ENV, "hypergraph").strip().lower()


# ===========================================================================
# API pública (dispatcher — os consumidores só chamam isto)
# ===========================================================================

def get_edital(edital_id: str) -> dict | None:
    if _backend() == "sql":
        return _sql_get_edital(edital_id)
    from core.kg import hypergraph_catalog
    return hypergraph_catalog.get_edital(edital_id)


def list_editais(
    status: str | None = None, tema: str | None = None, limit: int = 200,
) -> list[dict]:
    if _backend() == "sql":
        return _sql_list_editais(status=status, tema=tema, limit=limit)
    from core.kg import hypergraph_catalog
    return hypergraph_catalog.list_editais(status=status, tema=tema, limit=limit)


# ===========================================================================
# Backend SQL — leitura de `entities`/`entity_relationships`
# ===========================================================================

def _client():
    from core.db import get_supabase_service
    return get_supabase_service()


def _status_from_row(row: dict) -> str:
    """Mesma regra de `hypergraph_catalog._status`: deadline manda (recomputado
    a cada leitura, não confia no valor congelado da coluna `status`)."""
    d = _to_date(row.get("deadline"))
    if d:
        return "ABERTA" if d >= date.today() else "ENCERRADA"
    raw = (row.get("status") or "").strip().lower()
    if raw.startswith("abert"):
        return "ABERTA"
    if raw.startswith(("encerr", "fechad", "conclu", "expir")):
        return "ENCERRADA"
    return "Desconhecido"


def _to_date(raw: Any) -> date | None:
    if not raw:
        return None
    try:
        return date.fromisoformat(str(raw)[:10])
    except ValueError:
        return None


def _deadline_display(row: dict) -> str:
    """dd/mm/yyyy — é o único formato que `core.kg.schema.parse_deadline` (usado
    por backend/routers/applications.py) sabe ler; a coluna SQL é ISO."""
    d = _to_date(row.get("deadline"))
    return d.strftime("%d/%m/%Y") if d else ""


def _value_display(row: dict) -> str | None:
    tmin, tmax = row.get("ticket_min"), row.get("ticket_max")
    if tmin is None and tmax is None:
        return None
    if tmin is not None and tmax is not None and tmin != tmax:
        return f"R$ {tmin:,.0f} – R$ {tmax:,.0f}"
    return f"R$ {(tmin if tmin is not None else tmax):,.0f}"


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


def _row_to_card(
    row: dict, *, programs: dict[str, list[str]], icts: dict[str, list[str]],
    agencies: dict[str, list[str]],
) -> dict:
    from core.skills import mechanism_display

    eid = row["id"]
    mecanismo = [row["mecanismo"]] if row.get("mecanismo") else []
    meta = row.get("metadata") or {}
    return {
        "id": row["native_id"],
        "source": row["source"],
        "title": row.get("name") or "",
        "status": _status_from_row(row),
        "deadline": _deadline_display(row),
        "themes": list(row.get("setores") or []),
        "technologies": list(row.get("tecnologias_tags") or []),
        "programs": programs.get(eid, []),
        "publico_alvo": [],
        "fonte_recurso": agencies.get(eid, []),
        "opportunity_type": "edital",
        "official_url": meta.get("url") or "",
        "aperture": row.get("formato") or "",
        "macro_temas": [],
        "kind": row.get("kind") or "edital",
        "objective": row.get("description") or "",
        "mecanismo": mecanismo,
        "mechanism": ", ".join(mechanism_display(m) for m in mecanismo),
        "eligible_entities": [],
        "key_requirements": list(row.get("requisitos_texto") or []),
        "aplicacoes": [],
        "exclusoes": [],
        "value": _value_display(row),
        "icts": icts.get(eid, []),
        "investidores": [],
        "constraints": list(row.get("constraints") or []),
        "document_urls": [],
        "collected_at": row.get("updated_at") or "",
    }


def _sql_get_edital(edital_id: str) -> dict | None:
    eid = (edital_id or "").strip()
    if not eid:
        return None
    client = _client()
    rows = (
        client.table("entities").select("*")
        .eq("kind", "edital").eq("native_id", eid).limit(1)
        .execute().data
    ) or []
    if not rows:
        return None
    row = rows[0]
    rid = row["id"]
    programs = _rel_names_batch(client, [rid], "subordinado_a")
    icts = _rel_names_batch(client, [rid], "exige_parceria_com")
    agencies = _rel_names_batch(client, [rid], "operado_por")
    return _row_to_card(row, programs=programs, icts=icts, agencies=agencies)


def _sql_list_editais(
    *, status: str | None = None, tema: str | None = None, limit: int = 200,
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
        tl = tema.lower()
        cards = [
            c for c in cards
            if any(tl in t.lower() for t in c.get("themes", []))
            or any(tl in t.lower() for t in c.get("technologies", []))
            or any(tl in t.lower() for t in c.get("programs", []))
        ]
    cards.sort(key=lambda c: (c["status"] != "ABERTA", c["title"].lower()))
    return cards[:limit]
