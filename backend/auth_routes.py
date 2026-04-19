"""
Endpoints de perfil autenticado.

Magic link e verificação de token são tratados diretamente pelo Supabase Auth
(frontend usa @supabase/supabase-js). O backend apenas gerencia o workspace/perfil.
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional

from core.auth import CurrentUserId
from core.db import get_supabase

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
    problem_statement: str = ""
    solution_summary: str = ""
    descricao_atividades: str = ""
    portfolio_projetos: str = ""
    tamanho_empresa: str = ""
    faturamento_anual_faixa: str = ""
    localizacao: str = ""
    capital_social: Optional[float] = None
    certificacoes: list[str] = []
    equipe_resumo: str = ""
    trl: Optional[int] = None
    tipos_financiamento_interesse: list[str] = []
    uso_financiamento: list[str] = []
    valor_buscado: Optional[float] = None


# =============================================================================
# HELPERS
# =============================================================================

def _ensure_workspace(user_id: str) -> dict:
    """Busca ou cria workspace para o usuário."""
    db = get_supabase()
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
def get_me(user_id: CurrentUserId):
    workspace = _ensure_workspace(user_id)
    return {
        "user_id": user_id,
        "workspace_id": workspace["id"],
        "profile": workspace.get("profile", {}),
        "updated_at": workspace.get("updated_at"),
    }


@router.put("/me/profile", summary="Salva perfil da empresa no workspace")
def update_profile(payload: ProfilePayload, user_id: CurrentUserId):
    workspace = _ensure_workspace(user_id)
    db = get_supabase()

    result = (
        db.table("workspaces")
        .update({"profile": payload.model_dump()})
        .eq("id", workspace["id"])
        .execute()
    )
    if not result.data:
        raise HTTPException(status_code=500, detail="Falha ao salvar perfil")

    return {"success": True, "profile": result.data[0]["profile"]}
