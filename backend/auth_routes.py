"""
Endpoints de perfil autenticado.

Magic link e verificação de token são tratados diretamente pelo Supabase Auth
(frontend usa @supabase/supabase-js). O backend apenas gerencia o workspace/perfil.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from core.infra.auth import CurrentUserId, DbClient, get_current_user, is_admin_payload

router = APIRouter(prefix="", tags=["auth"])


# =============================================================================
# SCHEMAS
# =============================================================================

class ProfilePayload(BaseModel):
    nome: str = ""
    cnpj: str = ""
    url_site: str = ""
    tipo_entidade: str = "empresa"
    one_liner: str = ""
    solution_summary: str = ""
    descricao_atividades: str = ""
    portfolio_projetos: str = ""
    tamanho_empresa: str = ""
    capital_social: float | None = None
    uf: str = ""
    faturamento_anual: float | None = None
    ano_fundacao: int | None = None
    equipe_resumo: str = ""
    trl: int | None = None
    tipos_financiamento_interesse: list[str] = []


# =============================================================================
# HELPERS
# =============================================================================

def _ensure_workspace(user_id: str, db) -> dict:
    """Busca ou cria workspace para o usuário.

    Usa o cliente Supabase autenticado com JWT do request, portanto sujeito
    a RLS. A política precisa permitir SELECT/INSERT em workspaces onde
    user_id = auth.uid().
    """
    result = db.table("workspaces").select("*").eq("user_id", user_id).maybe_single().execute()
    if result.data:
        return result.data

    created = db.table("workspaces").insert({"user_id": user_id, "profile": {}}).execute()
    if not created.data:
        raise HTTPException(status_code=500, detail="Falha ao criar workspace")
    return created.data[0]


# =============================================================================
# ENDPOINTS
# =============================================================================

@router.get("/me", summary="Retorna usuário atual e seu perfil")
def get_me(
    user_id: CurrentUserId,
    db: DbClient,
    payload: Annotated[dict, Depends(get_current_user)],
):
    workspace = _ensure_workspace(user_id, db)
    return {
        "user_id": user_id,
        "workspace_id": workspace["id"],
        "profile": workspace.get("profile", {}),
        "updated_at": workspace.get("updated_at"),
        # Operador do sistema (ADMIN_EMAILS) — o front usa para exibir/ocultar
        # ferramentas de gestão (ex.: fila da Descoberta).
        "is_admin": is_admin_payload(payload),
    }


@router.put("/me/profile", summary="Salva perfil da empresa no workspace")
def update_profile(payload: ProfilePayload, user_id: CurrentUserId, db: DbClient):
    workspace = _ensure_workspace(user_id, db)

    result = (
        db.table("workspaces")
        .update({"profile": payload.model_dump()})
        .eq("id", workspace["id"])
        .execute()
    )
    if not result.data:
        raise HTTPException(status_code=500, detail="Falha ao salvar perfil")

    return {"success": True, "profile": result.data[0]["profile"]}


# =============================================================================
# PROFILE DRIFT (Gap 4)
# =============================================================================

@router.get(
    "/me/profile/drift",
    summary="Detecta se o CompanyProfile pode estar desatualizado (Gap 4)",
)
def get_profile_drift(user_id: CurrentUserId, db: DbClient):
    """Heurística simples (sem LLM): combina idade do profile + volume de
    items novos na library. Retorna sinal pro frontend exibir banner —
    decisão de atualizar fica com o usuário."""
    from core.profile_drift import detect_profile_drift
    workspace = _ensure_workspace(user_id, db)
    return detect_profile_drift(db, workspace["id"])
