"""Matching: editais (híbrido), investidores (Q3), programas recorrentes e radar unificado (L2)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from backend.common import CompanyProfileSchema, to_py_profile, wiki_matcher
from backend.rate_limit import limiter
from core.auth import CurrentUserId, DbClient, OptionalDbClient, OptionalUserId
from core.services.content_library import get_workspace_id

router = APIRouter(tags=["matching"])


class MatchRequest(BaseModel):
    profile: CompanyProfileSchema
    top_k: int = 10


class RadarRequest(BaseModel):
    profile: CompanyProfileSchema
    top_k: int = 10
    # Cap por quadrante (anti-inundação): máx. de itens por opportunity_type no
    # ranking. None = sem cap. Ver core.services.radar_service.
    per_type_cap: int | None = None


@router.post("/match", summary="Rankeamento de editais via LLM (Karpathy-style)")
@limiter.limit("10/minute")
def match_editais(request: Request, req: MatchRequest, user_id: CurrentUserId, db: DbClient):
    """
    Recebe um perfil de empresa e retorna editais FINEP rankeados por relevância.
    A LLM lê o catálogo completo e justifica cada recomendação por dimensão.

    O workspace_id do usuário é derivado do JWT e plumbado para o matcher
    para permitir pesos customizados (matching_weights) com fallback global.
    """
    profile = to_py_profile(req.profile)
    if not profile.is_complete():
        raise HTTPException(
            status_code=422,
            detail="Perfil incompleto. Preencha pelo menos nome e descricao_atividades.",
        )
    workspace_id = get_workspace_id(db, user_id)
    return {
        "matches": wiki_matcher.match(
            profile=profile, top_k=req.top_k, workspace_id=workspace_id
        )
    }


@router.post("/match/investidores", summary="Rankeamento de investidores (Q3) por tese/estágio")
@limiter.limit("10/minute")
def match_investidores_endpoint(
    request: Request, req: MatchRequest, user_id: CurrentUserId, db: DbClient
):
    """Perfil da startup → fundos/investidores (Q3) rankeados por aderência:
    especialistas casam por TESE/tema/setor; generalistas por ESTÁGIO. SEM gate —
    entidade não elimina. Caminho isolado: não toca o
    matcher de edital."""
    from core.services.investor_match import match_investidores
    profile = to_py_profile(req.profile)
    return {"matches": match_investidores(profile, top_k=req.top_k)}


@router.post("/match/programas", summary="Rankeamento de programas recorrentes por estágio/aderência")
@limiter.limit("10/minute")
def match_programas_endpoint(
    request: Request, req: MatchRequest, user_id: CurrentUserId, db: DbClient
):
    """Perfil da startup → programas recorrentes (aceleração/incubação/subvenção/
    capacitação) rankeados por aderência: ESTÁGIO + elegibilidade + tema/setor
    (quando o programa tem tese). SEM gate — entidade não elimina. Caminho isolado:
    não toca o matcher de edital nem o de investidor."""
    from core.services.programa_match import match_programas
    profile = to_py_profile(req.profile)
    return {"matches": match_programas(profile, top_k=req.top_k)}


@router.post("/match/radar", summary="Radar unificado (L2): eventos + investidores + programas num ranking só")
@limiter.limit("10/minute")
def match_radar(
    request: Request, req: RadarRequest, user_id: OptionalUserId, db: OptionalDbClient,
):
    """Funde o match de eventos (edital/desafio/programa) e o de investidores num
    ÚNICO ranking (Layer 2, spec §3.8): normaliza scores, anexa o sinal "por que
    agora" e aplica cap por quadrante. Orquestra os 2 matchers sem tocá-los —
    `/match` e `/match/investidores` seguem disponíveis.

    Auth OPCIONAL (delta B2, porta pública do front-door): sem JWT → workspace_id
    None e `build_radar` usa pesos globais default. Com JWT → comportamento atual
    idêntico (pesos por workspace). O perfil já vem no body em ambos os casos."""
    profile = to_py_profile(req.profile)
    # Gate mínimo (≠ is_complete, que exige 8 campos): o front-door roda o radar
    # cedo com perfil parcial e melhora conforme a conversa preenche o resto.
    if not (profile.nome and profile.descricao_atividades):
        raise HTTPException(
            status_code=422,
            detail="Perfil incompleto. Preencha pelo menos nome e descricao_atividades.",
        )
    # Só resolve workspace no caminho autenticado (sem JWT não há cliente per-request).
    workspace_id = get_workspace_id(db, user_id) if (user_id and db is not None) else None
    from core.services.radar_service import build_radar
    return build_radar(
        profile, top_k=req.top_k, per_type_cap=req.per_type_cap, workspace_id=workspace_id,
    )
