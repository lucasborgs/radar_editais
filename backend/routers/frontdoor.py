"""Front-door conversacional (público): turno híbrido sobre o catálogo.

Porta de entrada do produto (spec_frontdoor_ux §5, delta B1). Evolui o
`/kg-explore`: o agente conversa sobre a base de conhecimento JÁ com o perfil
parcial no contexto e, num passo SEPARADO, propõe um `profile_diff` estruturado
(campos do CompanyProfile que a última mensagem preenche/altera). NUNCA roda
match nem persiste nada — o front dispara `/match/radar` após o usuário aceitar
o diff (decisão D4, "AI drafts, humans decide").

Público (sem auth) + rate-limit por IP (controle de custo da porta pública,
junto com o tier barato do extrator de diff — B5).
"""

from __future__ import annotations

import os

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from backend.common import CompanyProfileSchema, kg_service
from backend.rate_limit import limiter
from core.profile_extractor import ProfileExtractor

router = APIRouter(tags=["frontdoor"])


class FrontdoorTurnRequest(BaseModel):
    message: str
    history: list[dict] = []
    # Perfil parcial mantido pelo cliente (localStorage no anônimo). Opcional.
    profile: CompanyProfileSchema | None = None


def _profile_context_block(profile: CompanyProfileSchema | None) -> str:
    """Resumo leve dos campos preenchidos, para o agente comentar com
    conhecimento do perfil. Vazio quando não há nada útil."""
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
        if val not in (None, "", [], "empresa"):  # "empresa" é o default de tipo
            filled.append(f"{label}: {val}")
    if not filled:
        return ""
    return "[Perfil parcial da empresa do usuário] " + " · ".join(filled)


@router.post("/frontdoor/turn", summary="Turno do front-door: resposta + proposta de diff de perfil (público)")
@limiter.limit("10/minute")
def frontdoor_turn(request: Request, req: FrontdoorTurnRequest):
    """Um turno da conversa da porta de entrada.

    `answer`: reusa o caminho do `kg_explore` (mesma flag de agente). O perfil
    parcial entra como um bloco de contexto leve prefixado à mensagem (seam
    mínimo-invasivo: não altera a assinatura de `kg_service.explore`).

    `profile_diff`: chamada LLM SEPARADA e focada (tier barato) que devolve só
    os campos que a última mensagem do usuário preenche/altera. None quando nada
    é extraído. O endpoint não aplica o diff nem roda match — isso é o front.
    """
    message = req.message.strip()
    if not message:
        raise HTTPException(status_code=422, detail="Mensagem vazia.")

    agent_enabled = os.getenv("AGENT_EXPLORE_DEFAULT_ENABLED", "false").lower() == "true"

    # Perfil no contexto do explore: prefixo leve à mensagem (sem mexer no explore).
    ctx = _profile_context_block(req.profile)
    explore_message = f"{ctx}\n\n{message}" if ctx else message
    answer = kg_service.explore(
        explore_message, req.history, [], None, None, agent_enabled=agent_enabled,
    )

    # Diff de perfil: passo separado, tier barato (B5). Perfil atual como dict.
    current = req.profile.model_dump() if req.profile is not None else {}
    diff = ProfileExtractor().extract_diff_from_message(message, current)

    return {"answer": answer, "profile_diff": diff or None}
