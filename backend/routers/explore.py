"""Chat exploratório sobre o catálogo + extração de perfil (auth opcional).

Unifica os antigos endpoints:
  - POST /kg-explore (graph.py)   — chat sem perfil, anônimo
  - POST /frontdoor/turn          — chat com perfil + extração, auth opcional

Per spec match-evolution.md Fase 2.
"""

from __future__ import annotations

import logging

import jwt
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from slowapi.util import get_remote_address

from backend.common import CompanyProfileSchema, explore_agent
from backend.rate_limit import limiter
from core.auth import OptionalDbClient, OptionalUserId
from core.llm.agent_tools.match_tools import _company_nodes
from core.profile_extractor import ProfileExtractor
from core.services.content_library import get_workspace_id
from core.services.hypergraph_match import find_matching_editais
from core.services.writing_session import persist_frontdoor_turn

logger = logging.getLogger(__name__)

router = APIRouter(tags=["explore"])


class ExploreRequest(BaseModel):
    message: str
    history: list[dict] = []
    profile: CompanyProfileSchema | None = None
    edital_ids: list[str] = []
    node_id: str | None = None
    node_type: str | None = None
    session_id: str | None = None


def _profile_context_block(profile: CompanyProfileSchema | None) -> str:
    if profile is None:
        return ""
    filled: list[str] = []
    for field, label in (
        ("nome", "Empresa"),
        ("tipo_entidade", "Tipo"),
        ("one_liner", "Proposta"),
        ("solution_summary", "Solução"),
        ("descricao_atividades", "Atividades"),
        ("tamanho_empresa", "Porte"),
        ("uf", "UF"),
        ("trl", "TRL"),
        ("estagio", "Estágio"),
    ):
        val = getattr(profile, field, None)
        if val not in (None, "", [], "empresa"):
            filled.append(f"{label}: {val}")
    if not filled:
        return ""
    return "[Perfil parcial da empresa do usuário] " + " · ".join(filled)


def _explore_key(request: Request) -> str:
    """Key function for explore rate limit.

    Autenticado → bucket auth:{sub} com rate 10/min.
    Anônimo → bucket anon:{ip} com rate 3/min.
    """
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        try:
            payload = jwt.decode(auth[7:], options={"verify_signature": False})
            sub = payload.get("sub")
            if sub:
                return f"auth:{sub}"
        except Exception:
            pass
    return f"anon:{get_remote_address(request)}"


def _explore_limit(key: str) -> str:
    """Dynamic limit: autenticado 10/min, anônimo 3/min."""
    if key.startswith("auth:"):
        return "10/minute"
    return "3/minute"


@router.post(
    "/explore",
    summary="Chat exploratório + extração de perfil (auth opcional)",
)
@limiter.limit(_explore_limit, key_func=_explore_key)
def explore(
    request: Request,
    req: ExploreRequest,
    user_id: OptionalUserId,
    db: OptionalDbClient,
):
    """Conversa exploratória sobre o catálogo + extração de perfil.

    ExploreAgent classifica internamente a intenção (factual/reasoning/agent)
    e roteia para a rota adequada. Com perfil presente, a resposta é enriquecida
    com extração de profile_updates via ProfileExtractor.

    - Anônimo (sem JWT): rate 3/min, sem extração de perfil, sem persistência.
    - Autenticado (com JWT): rate 10/min, com extração e persistência da
      conversa nas tabelas writing_sessions/session_turns (kind='frontdoor').

    O histórico é enviado pelo cliente a cada turno; a persistência permite
    retomar a conversa pelo sidebar (/conversations).
    """
    message = req.message.strip()
    if not message:
        raise HTTPException(status_code=422, detail="Mensagem vazia.")

    ctx = _profile_context_block(req.profile)
    explore_message = f"{ctx}\n\n{message}" if ctx else message
    current = req.profile.model_dump() if req.profile is not None else {}

    answer = explore_agent.explore(
        explore_message, req.history, req.edital_ids, req.node_id,
        req.node_type, has_profile=req.profile is not None,
        profile_text=ctx or None,
    )

    result: dict = {"answer": answer}
    diff = None
    if req.profile is not None:
        diff = ProfileExtractor().extract_diff_from_message(message, current)
        result["profile_diff"] = diff or None

    # Structured match data: converte profile → nós → find_matching_editais →
    # dicts serializáveis. Roda independente do agente (que também faz match
    # internamente, mas só devolve texto). Custo: só cosseno, sem LLM extra
    # quando o perfil já foi extraído (cache de _company_nodes por hash).
    if req.profile is not None and ctx:
        try:
            company_nodes = _company_nodes(ctx)
            if company_nodes:
                matches = find_matching_editais(company_nodes, top_k=8)
                result["matched_editais"] = [m.to_dict() for m in matches]
        except Exception as e:
            logger.warning("explore: falha ao extrair matched_editais: %s", e)

    if user_id and db:
        try:
            workspace_id = get_workspace_id(db, user_id)
            profile_diff_list = diff if diff else None
            persisted = persist_frontdoor_turn(
                db, workspace_id, message, answer, profile_diff_list, req.session_id,
            )
            result["session_id"] = persisted["session_id"]
            result["entry_ids"] = persisted["entry_ids"]
        except Exception as e:
            logger.warning("explore: falha ao persistir turno: %s", e)

    return result
