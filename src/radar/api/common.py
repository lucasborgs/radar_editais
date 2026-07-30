"""Dependências compartilhadas pelos routers (extraído de backend/api.py).

Singletons de serviço + schema de perfil + helpers de carregamento que mais de
um router consome. Routers importam daqui; `backend/api.py` fica só com app,
middleware e wiring — nunca importe `radar.api.app` a partir de um router.
"""

from __future__ import annotations

from radar.core.services.content_library import get_item
from radar.core.services.explore_agent import ExploreAgent
from radar.domain.profile_schema import PROFILE_FIELD_NAMES, CompanyProfilePayload
from radar.domain.user_profile import CompanyProfile as PyCompanyProfile

# =============================================================================
# SINGLETONS
# =============================================================================

explore_agent = ExploreAgent()


# =============================================================================
# SCHEMA DE PERFIL (compartilhado por matching/writing/brief)
# =============================================================================


CompanyProfileSchema = CompanyProfilePayload


def to_py_profile(schema: CompanyProfileSchema) -> PyCompanyProfile:
    return PyCompanyProfile(**schema.model_dump())


def load_library_items(db, workspace_id: str, item_ids: list[str]) -> list[dict]:
    if not item_ids:
        return []
    return [
        item for item_id in item_ids
        if (item := get_item(db, item_id, workspace_id)) is not None
    ]


def profile_from_workspace(db, workspace_id: str) -> PyCompanyProfile:
    """Lê workspaces.profile e instancia CompanyProfile.

    Fallback usado quando o cliente não envia o profile no payload (resumir
    sessão a partir de session_id puro).
    """
    result = (
        db.table("workspaces")
        .select("profile")
        .eq("id", workspace_id)
        .maybe_single()
        .execute()
    )
    raw = ((result.data if result else None) or {}).get("profile") or {}
    return PyCompanyProfile(**{k: raw[k] for k in PROFILE_FIELD_NAMES if k in raw})
