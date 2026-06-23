"""Front-door conversacional (público): turno híbrido sobre o catálogo.

Porta de entrada do produto. Evolui o
`/kg-explore`: o agente conversa sobre a base de conhecimento JÁ com o perfil
parcial no contexto e, num passo SEPARADO, propõe um `profile_diff` estruturado
(campos do CompanyProfile que a última mensagem preenche/altera). NUNCA roda
match nem persiste nada — o front dispara `/match/radar` após o usuário aceitar
o diff (decisão D4, "AI drafts, humans decide").

Público (sem auth) + rate-limit por IP (controle de custo da porta pública,
junto com o tier barato do extrator de diff — B5).
"""

from __future__ import annotations

import logging
import os

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from backend.common import CompanyProfileSchema, kg_service
from backend.rate_limit import limiter
from core.auth import OptionalDbClient, OptionalUserId
from core.profile_extractor import ProfileExtractor
from core.services.content_library import get_workspace_id
from core.services.writing_session import persist_frontdoor_turn

logger = logging.getLogger(__name__)

router = APIRouter(tags=["frontdoor"])


class FrontdoorTurnRequest(BaseModel):
    message: str
    history: list[dict] = []
    # Perfil parcial mantido pelo cliente (localStorage no anônimo). Opcional.
    profile: CompanyProfileSchema | None = None
    # Retomada de conversa (usuário logado): quando presente, o turno é anexado
    # à conversa existente em vez de criar uma nova. Ignorado no anônimo.
    session_id: str | None = None


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


@router.post("/frontdoor/turn", summary="Turno do front-door: resposta + proposta de diff de perfil (auth opcional)")
@limiter.limit("10/minute")
def frontdoor_turn(
    request: Request,
    req: FrontdoorTurnRequest,
    user_id: OptionalUserId,
    db: OptionalDbClient,
):
    """Um turno da conversa da porta de entrada.

    Auth é OPCIONAL (porta pública, rate-limit por IP inalterado):
      - Anônimo (sem JWT): comportamento de hoje byte a byte — nada persiste, a
        response NÃO carrega session_id/entry_ids (o front usa sessionStorage).
      - Logado: a conversa é persistida (kind='frontdoor'), retomável via
        session_id. A persistência é tolerante a falha — se o DB cair, logamos
        um warning e devolvemos a resposta normal (a conversa vale mais que o
        histórico, spec fase 2).

    `answer` + `profile_diff`: no caminho default (1-shot), `kg_service.explore_turn`
    devolve OS DOIS numa única chamada LLM — a resposta E os campos do
    CompanyProfile que a última mensagem preenche/altera (decisão 2026-06-21:
    elimina a 2ª passada que o extract_diff_from_message fazia sobre a mesma
    mensagem). No caminho agente (flag ON) a resposta vem do `explore` agêntico e
    o diff continua numa chamada focada separada. `profile_diff` é None quando
    nada é extraído. O endpoint não aplica o diff nem roda match — isso é o front.
    """
    message = req.message.strip()
    if not message:
        raise HTTPException(status_code=422, detail="Mensagem vazia.")

    agent_enabled = os.getenv("AGENT_EXPLORE_DEFAULT_ENABLED", "false").lower() == "true"

    # Perfil no contexto do explore: prefixo leve à mensagem (sem mexer no explore).
    ctx = _profile_context_block(req.profile)
    explore_message = f"{ctx}\n\n{message}" if ctx else message
    current = req.profile.model_dump() if req.profile is not None else {}

    # Workspace para a memória do ExploreAgent (Fase 3A): resolvido cedo e só no
    # caminho AUTENTICADO. Best-effort — falha aqui não derruba o turno (o explore
    # cai no modo stateless). Reaproveitado depois na persistência do turno.
    workspace_id: str | None = None
    if user_id is not None and db is not None:
        try:
            workspace_id = get_workspace_id(db, user_id)
        except Exception as e:
            logger.warning("Falha ao resolver workspace_id no front-door: %s", e)

    # Resposta + diff de perfil. Caminho 1-shot (default): `explore_turn` devolve
    # ambos numa só chamada LLM (decisão 2026-06-21 — elimina a 2ª passada que o
    # extract_diff_from_message fazia sobre a MESMA mensagem). Caminho agente
    # (LangGraph, flag ON) não extrai inline → mantém a chamada focada separada.
    if agent_enabled:
        # Memória entre sessões (Fase 3A) só no caminho autenticado: passa
        # workspace_id+db ao agente. Anônimo → ambos None → agente stateless.
        answer = kg_service.explore(
            explore_message, req.history, [], None, None, agent_enabled=True,
            workspace_id=workspace_id,
            db=db if workspace_id else None,
        )
        diff = ProfileExtractor().extract_diff_from_message(message, current)
    else:
        answer, profile_updates = kg_service.explore_turn(
            explore_message, req.history, [], None, None,
        )
        diff = ProfileExtractor().diff_from_updates(profile_updates, current)

    response: dict = {"answer": answer, "profile_diff": diff or None}

    # Persistência só no caminho autenticado. db pode ser None mesmo logado em
    # cenários degenerados; guardamos contra isso. Falha NÃO derruba o turno.
    # Reusa o workspace_id já resolvido acima (None se a resolução falhou).
    if user_id is not None and db is not None and workspace_id:
        try:
            persisted = persist_frontdoor_turn(
                db=db,
                workspace_id=workspace_id,
                user_message=message,
                assistant_message=answer,
                profile_diff=diff or None,
                session_id=req.session_id,
            )
            response["session_id"] = persisted["session_id"]
            response["entry_ids"] = persisted["entry_ids"]
        except Exception as e:
            logger.warning("Falha ao persistir turno do front door (segue sem histórico): %s", e)

    return response
