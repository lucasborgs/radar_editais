"""Company corpus → hipergrado durável da empresa (Sprint 2, lado DEMANDA do match).

Hoje o match re-extrai os nós da empresa do texto do perfil a cada sessão (efêmero).
Aqui o hipergrado da empresa é construído UMA vez a partir do corpus (perfil +
content_items já no DB), com score de completude, e persistido por workspace em
`company_hypergraphs`. O match passa a reusar esses nós duráveis (ver match_tools /
explore_agent) com fallback para a extração efêmera quando não há corpus construído.

Mínimo durável: NÃO re-scrapeia o site (corpus = o que já está no DB). Re-scrape é
fatia adicional (ver BACKLOG / spec). Storage company-side fica aqui (não no kg_store,
que é ecossistema-global) — dado per-tenant, vizinho de content_library.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from supabase import Client

logger = logging.getLogger(__name__)

_TABLE = "company_hypergraphs"


def completude_score(n_nos: int, n_docs: int) -> float:
    """Quão rico é o perfil para descobertas não-óbvias (0..1). Spec §Completude:
    nós dominam (0.6), documentos complementam (0.4). Satura em 1.0."""
    return min(1.0, (n_nos / 50) * 0.6 + (n_docs / 3) * 0.4)


def completude_message(score: float) -> str:
    """Mensagem de feedback ao usuário por faixa de completude (spec §Completude)."""
    if score < 0.3:
        return "Perfil ralo — adicione documentos para descobertas não-óbvias"
    if score < 0.7:
        return "Perfil básico — um pitch deck ou relatório técnico enriquece os resultados"
    return "Perfil rico"


# ---------------------------------------------------------------------------
# Storage (tabela dedicada company_hypergraphs)
# ---------------------------------------------------------------------------

def save_company_hypergraph(
    db: Client,
    workspace_id: str,
    *,
    nodes: list[dict],
    edges: list[dict],
    completude: float,
    corpus_urls: list[str],
    n_docs: int,
) -> None:
    """Upsert do hipergrado da empresa (1 row por workspace). `db` deve ser
    service-role (a task que constrói bypassa RLS, escrevendo só a row do tenant)."""
    db.table(_TABLE).upsert(
        {
            "workspace_id": workspace_id,
            "nodes": nodes,
            "edges": edges,
            "completude": completude,
            "corpus_urls": corpus_urls,
            "n_docs": n_docs,
            # ISO timestamp (não a string literal "now()": PostgREST gravaria texto
            # inválido no timestamptz). Bump explícito a cada rebuild.
            "updated_at": datetime.now(timezone.utc).isoformat(),
        },
        on_conflict="workspace_id",
    ).execute()
    logger.info("company_corpus: hipergrado salvo workspace=%s nós=%d completude=%.2f",
                workspace_id, len(nodes), completude)


def load_company_hypergraph(db: Client, workspace_id: str) -> dict | None:
    """Lê a row do hipergrado da empresa (ou None). `db` carrega a RLS do caller —
    passe o cliente autenticado do request para isolar tenants."""
    res = (
        db.table(_TABLE)
        .select("nodes, edges, completude, corpus_urls, n_docs, updated_at")
        .eq("workspace_id", workspace_id)
        .maybe_single()
        .execute()
    )
    return res.data if res else None


# ---------------------------------------------------------------------------
# Construção (corpus → hipergrado)
# ---------------------------------------------------------------------------

@dataclass
class CorpusBundle:
    text: str
    corpus_urls: list[str]
    n_docs: int


def gather_corpus(db: Client, workspace_id: str) -> CorpusBundle:
    """Junta o corpus textual da empresa do que JÁ está no DB: o perfil
    (workspaces.profile → CompanyProfile.to_context()) + o conteúdo dos
    content_items não-arquivados. Sem scraping novo."""
    from domain.user_profile import CompanyProfile

    ws = (
        db.table("workspaces").select("profile").eq("id", workspace_id)
        .maybe_single().execute()
    )
    profile_raw = (ws.data or {}).get("profile") or {} if ws else {}
    allowed = set(CompanyProfile.__dataclass_fields__.keys())
    profile = CompanyProfile(**{k: v for k, v in profile_raw.items() if k in allowed})

    items = (
        db.table("content_items").select("content, source_url")
        .eq("workspace_id", workspace_id).is_("archived_at", "null").execute()
    )
    rows = items.data or []

    parts = [profile.to_context()] if profile.to_context().strip() else []
    corpus_urls: list[str] = []
    if profile.url_site:
        corpus_urls.append(profile.url_site)
    for r in rows:
        if r.get("content"):
            parts.append(r["content"])
        if r.get("source_url"):
            corpus_urls.append(r["source_url"])

    return CorpusBundle(
        text="\n\n".join(parts),
        corpus_urls=list(dict.fromkeys(corpus_urls)),  # dedup preservando ordem
        n_docs=len(rows),
    )


def build_company_hypergraph(db: Client, workspace_id: str) -> dict:
    """Orquestra corpus → Hyper-Extract → completude → persistência. Chamada pela
    task (service-role). Retorna {nodes, edges, completude, corpus_urls, n_docs}.
    Custa 1 LLM call (extração) + embeddings dos nós."""
    from core.retrieval.hyper_extractor import run_hyper_extract_company

    corpus = gather_corpus(db, workspace_id)
    if not corpus.text.strip():
        logger.warning("company_corpus: corpus vazio workspace=%s — nada a extrair", workspace_id)
        record = {"nodes": [], "edges": [], "completude": 0.0,
                  "corpus_urls": corpus.corpus_urls, "n_docs": corpus.n_docs}
    else:
        result = run_hyper_extract_company(corpus.text, company_id=workspace_id)
        completude = completude_score(len(result.nodes), corpus.n_docs)
        record = {"nodes": result.nodes, "edges": result.edges, "completude": completude,
                  "corpus_urls": corpus.corpus_urls, "n_docs": corpus.n_docs}

    save_company_hypergraph(
        db, workspace_id,
        nodes=record["nodes"], edges=record["edges"], completude=record["completude"],
        corpus_urls=record["corpus_urls"], n_docs=record["n_docs"],
    )
    return record
