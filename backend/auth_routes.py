"""
Endpoints de perfil autenticado.

Magic link e verificação de token são tratados diretamente pelo Supabase Auth
(frontend usa @supabase/supabase-js). O backend apenas gerencia o workspace/perfil.
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from core.auth import CurrentUserId, DbClient

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
    capital_social: float | None = None
    certificacoes: list[str] = []
    equipe_resumo: str = ""
    trl: int | None = None
    tipos_financiamento_interesse: list[str] = []
    uso_financiamento: list[str] = []
    valor_buscado: float | None = None


class PreferencesPayload(BaseModel):
    """Toggle de consent para agregação global de pesos (ADR C3)."""
    contribute_to_global_weights: bool


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
def get_me(user_id: CurrentUserId, db: DbClient):
    workspace = _ensure_workspace(user_id, db)
    # contribute_to_global_weights pode não existir se migration 004 ainda
    # não tiver sido aplicada — default False para compatibilidade.
    consent = bool(workspace.get("contribute_to_global_weights", False))
    return {
        "user_id": user_id,
        "workspace_id": workspace["id"],
        "profile": workspace.get("profile", {}),
        "contribute_to_global_weights": consent,
        "updated_at": workspace.get("updated_at"),
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


@router.put(
    "/me/preferences",
    summary="Atualiza consent para agregação global de pesos (ADR C3)",
)
def update_preferences(
    payload: PreferencesPayload, user_id: CurrentUserId, db: DbClient
):
    """Toggle do opt-in `contribute_to_global_weights` no workspace do usuário."""
    workspace = _ensure_workspace(user_id, db)

    result = (
        db.table("workspaces")
        .update({"contribute_to_global_weights": payload.contribute_to_global_weights})
        .eq("id", workspace["id"])
        .execute()
    )
    if not result.data:
        raise HTTPException(status_code=500, detail="Falha ao atualizar preferências")

    return {
        "success": True,
        "contribute_to_global_weights": bool(
            result.data[0].get(
                "contribute_to_global_weights", payload.contribute_to_global_weights
            )
        ),
    }


@router.post("/me/reflect", summary="Dispara ReflectionService on-demand (Fase 2 #17)")
def trigger_reflection(user_id: CurrentUserId, db: DbClient):
    """Roda síntese de outcomes do workspace e persiste em reflection_insights.

    Inline (não via fila procrastinate) para retornar o resultado imediatamente
    ao usuário. Custo: 1 chamada LLM (~1500 tokens). Se nenhum outcome
    qualificado existir (< 5), retorna skipped sem chamar LLM.
    """
    from core.reflection_service import reflect_workspace
    workspace = _ensure_workspace(user_id, db)
    result = reflect_workspace(db, workspace["id"])
    return result
