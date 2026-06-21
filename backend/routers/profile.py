"""Extração de perfil da empresa: URL (onboarding público), documento avulso e
item da library. "AI drafts, human reviews": sempre retorna sugestão, nunca salva."""

from __future__ import annotations

import os

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from pydantic import BaseModel

from backend.library_routes import MAX_UPLOAD_MB
from backend.rate_limit import limiter
from core.auth import CurrentUserId, DbClient
from core.profile_extractor import ExtractResult, ProfileExtractor
from core.profile_inference import infer_financiamento
from core.services.content_library import extract_document_text, get_item, get_workspace_id

router = APIRouter(tags=["profile"])


class ProfileExtractRequest(BaseModel):
    url: str


def _serialize_extract_result(result: ExtractResult) -> dict:
    """Serializa um ExtractResult no formato de resposta dos endpoints /profile/extract*.

    Centraliza o dict campo-a-campo do perfil para os três endpoints
    (extract, extract-from-document, extract-from-library).
    """
    # Decisão 1 (docs/specs/onboarding-input-ux.md): infere mecanismos de
    # financiamento candidatos quando a extração não os trouxe. PROPOSTA pré-marcada
    # — humano confirma/desmarca no review; eval-gated (dimensão mecanismo, peso 15).
    # Não sobrescreve valor já presente (ex.: vindo de edição manual/library).
    tipos_financiamento = (
        result.profile.tipos_financiamento_interesse
        or infer_financiamento(result.profile)
    )
    return {
        "profile": {
            "nome": result.profile.nome,
            "cnpj": result.profile.cnpj,
            "tipo_entidade": result.profile.tipo_entidade,
            "one_liner": result.profile.one_liner,
            "solution_summary": result.profile.solution_summary,
            "descricao_atividades": result.profile.descricao_atividades,
            "portfolio_projetos": result.profile.portfolio_projetos,
            "tamanho_empresa": result.profile.tamanho_empresa,
            "capital_social": result.profile.capital_social,
            "uf": result.profile.uf,
            "faturamento_anual": result.profile.faturamento_anual,
            "ano_fundacao": result.profile.ano_fundacao,
            "trl": result.profile.trl,
            "equipe_resumo": result.profile.equipe_resumo,
            "tipos_financiamento_interesse": tipos_financiamento,
        },
        "confidence": result.confidence,
        "source_title": result.source_title,
        "low_confidence": result.low_confidence,
        "error": result.error,
    }


@router.post("/profile/extract", summary="Extrai perfil da empresa a partir de URL")
@limiter.limit("3/minute")
def extract_profile(request: Request, req: ProfileExtractRequest):
    """
    Recebe a URL do site da empresa, faz scraping e usa LLM para inferir
    os campos do CompanyProfile. Retorna perfil parcial + mapa de confiança.

    Sprint 4 do Cenário B: quando AGENT_PROFILE_EXTRACTOR_DEFAULT_ENABLED=true,
    roda o agente Anthropic com 4 tools (fetch_page, list_links_matching,
    lookup_cnpj, submit_profile) — investigação multi-página + enriquecimento
    via BrasilAPI. Default OFF — segue o pipeline determinístico (1 fetch
    da home + 1 LLM call). Sem workspace (endpoint público no onboarding),
    rollout é binário via env, igual ao /kg-explore.
    """
    url = req.url.strip()
    if not url.startswith(("http://", "https://")):
        raise HTTPException(status_code=422, detail="URL deve começar com http:// ou https://")

    agent_enabled = os.getenv(
        "AGENT_PROFILE_EXTRACTOR_DEFAULT_ENABLED", "false",
    ).lower() == "true"
    result = ProfileExtractor().extract(url, agent_enabled=agent_enabled)

    return _serialize_extract_result(result)


@router.post(
    "/profile/extract-from-document",
    summary="Extrai perfil a partir de uma proposta antiga (PDF/DOCX/TXT/MD) — onboarding",
)
@limiter.limit("3/minute")
async def extract_profile_from_document(
    request: Request, user_id: CurrentUserId, file: UploadFile = File(...),
):
    """Extrai CompanyProfile sugerido a partir de uma proposta antiga.

    Autenticado (delta B3, gate de anexos da decisão D3): diferente de
    /profile/extract (URL, público), o upload de documento exige login. Extrai o
    texto do documento (multi-formato, mesmo pipeline da library) e roda o LLM.
    "AI drafts, human reviews": só RETORNA a sugestão + confiança, nunca salva.
    Texto ilegível/vazio retorna erro controlado (não 500).
    """
    content = await file.read()
    if len(content) > MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(status_code=413, detail=f"Arquivo excede {MAX_UPLOAD_MB}MB")

    try:
        text = extract_document_text(content, file.filename or "")
    except RuntimeError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e

    result = ProfileExtractor().extract_from_text(text, source_title=file.filename or "")
    return _serialize_extract_result(result)


@router.post(
    "/profile/extract-from-library/{item_id}",
    summary="Extrai perfil a partir do texto de um item da library",
)
def extract_profile_from_library(item_id: str, user_id: CurrentUserId, db: DbClient):
    """Extrai CompanyProfile sugerido a partir do content de um library_item.

    Autenticado. Aceita qualquer item de texto (tipicamente uma proposta antiga).
    "AI drafts, human reviews": só RETORNA a sugestão + confiança, nunca salva —
    o save continua no PUT /me/profile.
    """
    workspace_id = get_workspace_id(db, user_id)
    item = get_item(db, item_id, workspace_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Item não encontrado")

    result = ProfileExtractor().extract_from_text(
        item.get("content") or "", source_title=item.get("title") or "",
    )
    return _serialize_extract_result(result)
