"""Endpoints da staging da torneira de descoberta (Parte C).

`discovered_opportunities` é a fila onde a torneira (cron diário) deixa os
achados como `pending`. Esta é a fila de revisão humana: o usuário PROMOVE o que
vale (a URL vira `web_sources` → o WebScraper a trata como fonte curada → entra
no KG) ou REJEITA (some da fila). Nada entra no RAG sem promoção.

Enriquecimento HITL (edital_link): quando a extração automática do discovery é
insuficiente (ex.: SPA sem conteúdo server-side), o revisor humano pode colar
o link direto do PDF do edital. O sistema baixa o PDF, extrai o texto, salva
no bronze web e dispara o chunking imediatamente.

GLOBAL (não workspace-scoped): a torneira é cron de sistema. Auth = OPERADOR
apenas (gate via AdminUserId / ADMIN_EMAILS — decisão de produto 2026-07-03: a
Descoberta é ferramenta de quem gerencia o sistema, não do cliente final). As
escritas tocam `web_sources` (RLS service-role-only), então usamos o cliente
service-role — o gate de auth é o AdminUserId, não o RLS.

Wiring em backend/api.py:
    from backend.routers.discovered import router as discovered_router
    app.include_router(discovered_router)
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from config import BRONZE_DIR
from core.auth import AdminUserId
from core.db import get_supabase_service
from core.net_guard import safe_get, safe_head
from core.web_identity import normalize_web_url, web_url_hash

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/discovered-opportunities", tags=["discovery"])

TTL_DAYS = 30

_LIST_COLS = (
    "id, url, title, agency, fonte, descricao, prazo_envio, publico_alvo, tema, "
    "opportunity_type, status, extraction_quality, edital_link, "
    "created_at, reviewed_at, promoted_web_source_id"
)

_WEB_RAW_DIR = BRONZE_DIR / "web_raw"
_PDF_TIMEOUT = 30


# ── Helpers ──────────────────────────────────────────────────────────────


def _is_pdf_url(url: str) -> bool:
    url_lower = url.strip().lower()
    if url_lower.endswith(".pdf"):
        return True
    try:
        resp = safe_head(url, timeout=10,
                         headers={"User-Agent": "Mozilla/5.0"})
        ct = resp.headers.get("content-type", "")
        return "application/pdf" in ct or "pdf" in ct
    except Exception:
        return url_lower.endswith(".pdf")


def _download_pdf(url: str) -> bytes:
    resp = safe_get(url, timeout=_PDF_TIMEOUT,
                    headers={"User-Agent": "Mozilla/5.0"})
    resp.raise_for_status()
    return resp.content


def _extract_pdf_text(pdf_bytes: bytes) -> str:
    import io

    import pdfplumber
    texts: list[str] = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            t = page.extract_text()
            if t:
                texts.append(t)
    return "\n\n".join(texts)


def _save_web_bronze(entry: dict) -> None:
    _WEB_RAW_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    path = _WEB_RAW_DIR / f"web_promoted_{ts}.json"
    path.write_text(json.dumps([entry], ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("bronze web salvo: %s (%d chars)", path, len(entry.get("texto_cru", "")))


def _process_edital_pdf(edital_link: str, opp: dict) -> dict:
    """Baixa o PDF, extrai texto, salva no bronze web e dispara chunking.

    Retorna dict com {url_hash, n_chars} para o response.
    Levanta HTTPException 400 se o PDF for inválido.
    """
    try:
        pdf_bytes = _download_pdf(edital_link)
        text = _extract_pdf_text(pdf_bytes)
    except Exception as e:
        logger.warning("falha ao processar PDF %s: %s", edital_link, e)
        raise HTTPException(
            status_code=400,
            detail=f"PDF inválido ou link quebrado: {e}",
        ) from e

    norm_url = normalize_web_url(edital_link)
    url_hash = web_url_hash(edital_link)

    bronze_entry = {
        "url": norm_url,
        "url_hash": url_hash,
        "title": (opp.get("title") or "Promovido via edital_link")[:200],
        "texto_cru": text,
        "html": "",
        "descricao": opp.get("descricao") or "",
        "agency": opp.get("agency") or "",
        "fonte": opp.get("fonte") or "Web (promoção)",
        "status": "ABERTA",
        "tema": opp.get("tema") or "",
        "opportunity_type": opp.get("opportunity_type") or "edital",
        "prazo_envio": opp.get("prazo_envio") or "",
        "publico_alvo": opp.get("publico_alvo") or "",
        "verificacao": "promovido",
        "data_extracao": datetime.now(timezone.utc).date().isoformat(),
    }
    _save_web_bronze(bronze_entry)

    edital_id = f"web:{url_hash}"
    try:
        from core.tasks import app
        # RAG lazy (chunk_edital) + catálogo/match (ingest_promoted_edital): o
        # promovido segue o MESMO caminho silver→gold do ETL diário, entrando no
        # match, não só no RAG (spec docs/specs/v3-unified.md §10).
        app.configure_task("chunk_edital").defer(edital_id=edital_id)
        app.configure_task("ingest_promoted_edital").defer(edital_id=edital_id)
        logger.info("chunk_edital + ingest_promoted_edital enfileirados para %s", edital_id)
    except Exception as e:
        logger.warning("falha ao enfileirar processamento de %s: %s", edital_id, e)

    return {"url_hash": url_hash, "n_chars": len(text)}


# ── Endpoints ────────────────────────────────────────────────────────────


@router.get("", summary="Fila de oportunidades descobertas")
def list_discovered(user_id: AdminUserId, include_reviewed: bool = False):
    """Lista a fila. Default: só `pending` e dentro do TTL de 30 dias.
    `include_reviewed=true` traz também promovidos/rejeitados (sem filtro de TTL),
    mais recentes primeiro."""
    db = get_supabase_service()
    q = db.table("discovered_opportunities").select(_LIST_COLS)
    if not include_reviewed:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=TTL_DAYS)).isoformat()
        q = q.eq("status", "pending").gte("created_at", cutoff)
    res = q.order("created_at", desc=True).execute()
    return {"opportunities": res.data or []}


class PromoteBody(BaseModel):
    edital_link: str | None = None
    """Link direto pro PDF do edital. Se fornecido, o sistema baixa, extrai
    texto, salva no bronze web e dispara chunking imediatamente."""


@router.post("/{opp_id}/promote", status_code=201,
             summary="Promove um achado: URL vira fonte rastreada (web_sources)")
def promote_discovered(opp_id: str, user_id: AdminUserId, body: PromoteBody | None = None):
    """Promove uma oportunidade.

    Sem `edital_link`: insere a URL original em `web_sources` (fonte curada)
    para o WebScraper indexar no próximo ciclo.

    Com `edital_link` apontando pra PDF: baixa o PDF, extrai texto, salva
    no bronze web e enfileira chunk_edital imediatamente. O conteúdo entra
    no RAG sem esperar o próximo ETL.
    """
    db = get_supabase_service()
    res = (db.table("discovered_opportunities").select("*")
             .eq("id", opp_id).maybe_single().execute())
    opp = res.data if res else None
    if opp is None:
        raise HTTPException(status_code=404, detail="Oportunidade não encontrada")
    if opp["status"] != "pending":
        raise HTTPException(status_code=409,
                            detail=f"Já revisada (status={opp['status']})")

    edital_link = (body.edital_link or "").strip() if body else ""
    process_result = None

    update = {
        "status": "promoted",
        "edital_link": edital_link or None,
        "reviewed_at": datetime.now(timezone.utc).isoformat(),
    }

    if edital_link and _is_pdf_url(edital_link):
        promoted_url = edital_link
        web_source_id = None
    elif edital_link:
        promoted_url = edital_link
        label = (opp.get("title") or opp.get("agency") or "Descoberta")[:200]
        ws_res = (db.table("web_sources")
                    .upsert({"url": edital_link, "label": label, "active": True},
                            on_conflict="url")
                    .execute())
        web_source_id = (ws_res.data or [{}])[0].get("id")
    else:
        promoted_url = opp["url"]
        label = (opp.get("title") or opp.get("agency") or "Descoberta")[:200]
        ws_res = (db.table("web_sources")
                    .upsert({"url": opp["url"], "label": label, "active": True},
                            on_conflict="url")
                    .execute())
        web_source_id = (ws_res.data or [{}])[0].get("id")

    if web_source_id:
        update["promoted_web_source_id"] = web_source_id
    db.table("discovered_opportunities").update(update).eq("id", opp_id).execute()

    if edital_link and _is_pdf_url(edital_link):
        process_result = _process_edital_pdf(edital_link, opp)

    resp = {"promoted": True, "url": promoted_url}
    if process_result:
        resp["edital_processed"] = process_result
    if web_source_id:
        resp["web_source_id"] = web_source_id
    return resp


class RejectBody(BaseModel):
    reason: str | None = None


@router.post("/{opp_id}/reject", summary="Rejeita um achado (some da fila)")
def reject_discovered(opp_id: str, user_id: AdminUserId, body: RejectBody | None = None):
    """Marca o achado como `rejected`. O ledger da torneira já impede que a mesma
    URL volte à fila em runs futuras."""
    db = get_supabase_service()
    res = (db.table("discovered_opportunities").select("id, status")
             .eq("id", opp_id).maybe_single().execute())
    opp = res.data if res else None
    if opp is None:
        raise HTTPException(status_code=404, detail="Oportunidade não encontrada")
    if opp["status"] != "pending":
        raise HTTPException(status_code=409,
                            detail=f"Já revisada (status={opp['status']})")

    db.table("discovered_opportunities").update({
        "status": "rejected",
        "reject_reason": (body.reason if body else None),
        "reviewed_at": datetime.now(timezone.utc).isoformat(),
    }).eq("id", opp_id).execute()

    return {"rejected": True}


class PatchEditalLinkBody(BaseModel):
    edital_link: str


@router.patch("/{opp_id}/edital-link",
              summary="Atualiza edital_link de uma oportunidade pendente")
def patch_edital_link(opp_id: str, user_id: AdminUserId, body: PatchEditalLinkBody):
    """Permite que o revisor preencha/atualize o `edital_link` ANTES de
    promover. Só funciona em oportunidades com status `pending`."""
    db = get_supabase_service()
    res = (db.table("discovered_opportunities").select("id, status")
             .eq("id", opp_id).maybe_single().execute())
    opp = res.data if res else None
    if opp is None:
        raise HTTPException(status_code=404, detail="Oportunidade não encontrada")
    if opp["status"] != "pending":
        raise HTTPException(status_code=409,
                            detail=f"Já revisada (status={opp['status']})")

    link = body.edital_link.strip()
    db.table("discovered_opportunities").update({
        "edital_link": link,
    }).eq("id", opp_id).execute()

    return {"updated": True, "edital_link": link}
