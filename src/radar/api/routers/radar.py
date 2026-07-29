"""Radar explícito: match determinístico sem depender do ExploreAgent."""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from radar.api.common import CompanyProfileSchema
from radar.api.rate_limit import get_client_ip, limiter
from radar.core.infra.auth import OptionalDbClient, OptionalUserId
from radar.core.services import match_v3, match_verdict
from radar.core.services.content_library import get_workspace_id

logger = logging.getLogger(__name__)

router = APIRouter(tags=["radar"])


class RadarMatchesRequest(BaseModel):
    profile: CompanyProfileSchema


def _radar_key(request: Request) -> str:
    """Usa o mesmo bucket por identidade/IP do explore, sem exigir login."""
    import jwt

    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        try:
            payload = jwt.decode(auth[7:], options={"verify_signature": False})
            if payload.get("sub"):
                return f"auth:{payload['sub']}"
        except Exception:  # noqa: BLE001 - chave de rate limit é best-effort
            pass
    return f"anon:{get_client_ip(request)}"


def _radar_limit(key: str) -> str:
    return "10/minute" if key.startswith("auth:") else "3/minute"


def _missing_profile_fields(profile: CompanyProfileSchema) -> list[str]:
    return [
        field for field in ("nome", "descricao_atividades")
        if not str(getattr(profile, field, "") or "").strip()
    ]


def _enqueue_verdicts(workspace_id: str, items: list[dict], profile: dict) -> None:
    """Enfileira somente misses do cache, mantendo o primeiro render sem LLM."""
    from procrastinate.exceptions import AlreadyEnqueued

    from radar.core.tasks import app as tasks_app

    try:
        with tasks_app.open():
            tasks_app.configure_task(
                "compute_match_verdicts",
                queueing_lock=f"match_verdicts:{workspace_id}",
            ).defer(workspace_id=workspace_id, items=items, profile=profile)
    except AlreadyEnqueued:
        logger.info("radar: vereditos já na fila p/ workspace=%s", workspace_id)


@router.post("/radar/matches", summary="Retorna o Radar pessoal de forma determinística")
@limiter.limit(_radar_limit, key_func=_radar_key)
def radar_matches(
    request: Request,
    req: RadarMatchesRequest,
    user_id: OptionalUserId,
    db: OptionalDbClient,
):
    """Executa Stage 0–2 e, quando autenticado, anexa/enfileira Stage 3.

    A rota aceita perfil transitório para a experiência pública. Somente o
    caminho autenticado resolve um workspace e portanto pode consumir library
    chunks ou acessar o cache de vereditos daquele tenant.
    """
    missing = _missing_profile_fields(req.profile)
    if missing:
        raise HTTPException(
            status_code=422,
            detail={"error": "profile_incomplete", "missing_fields": missing},
        )

    profile = req.profile.model_dump()
    workspace_id = get_workspace_id(db, user_id) if user_id and db else None
    prepared_company = match_v3.prepare_company_side(
        profile, workspace_id=workspace_id, db=db if workspace_id else None,
    )
    opps = match_v3.find_matching_opportunities(
        profile,
        workspace_id=workspace_id,
        db=db if workspace_id else None,
        kinds=frozenset({"edital", "programa"}),
        top_k=8,
        prepared_company=prepared_company,
    )
    editais = [item.to_dict() for item in opps if item.kind == "edital"]
    programas = [item.to_dict() for item in opps if item.kind == "programa"]
    investidores = [
        item.to_dict()
        for item in match_v3.find_matching_investors(
            profile,
            workspace_id=workspace_id,
            db=db if workspace_id else None,
            top_k=5,
            prepared_company=prepared_company,
        )
    ]

    if workspace_id and db:
        items = [*editais, *programas, *investidores]
        misses = match_verdict.attach_cached_verdicts(db, workspace_id, items, profile)
        if misses:
            _enqueue_verdicts(workspace_id, misses, profile)

    return {
        "matched_editais": editais,
        "matched_programas": programas,
        "matched_investidores": investidores,
        "meta": {
            "ranking": "affinity",
            "uses_workspace_chunks": workspace_id is not None,
        },
    }
