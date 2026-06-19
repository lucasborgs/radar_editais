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


class PreferencesPayload(BaseModel):
    """Toggle de consent para agregação global de pesos (ADR C3)."""
    contribute_to_global_weights: bool


class FeedbackPayload(BaseModel):
    """Feedback livre do tester (build-in-public). `context` carrega metadados
    leves do cliente (ex.: {"page": "/pipeline"}) — sem PII."""
    message: str
    context: dict = {}


class WeightSuggestionApproval(BaseModel):
    dimension: str
    delta: int


class ApproveSuggestionsPayload(BaseModel):
    insight_id: str
    suggestions: list[WeightSuggestionApproval]


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


@router.post("/feedback", summary="Feedback livre do tester (build-in-public)")
def submit_feedback(payload: FeedbackPayload, user_id: CurrentUserId, db: DbClient):
    """Grava feedback do usuário em `user_feedback` (RLS por user_id).

    Canal de baixa fricção para o beta: o tester relata um problema/ideia e o
    fundador lê via service role. `user_id` vem do JWT (não do payload) e casa
    com a policy RLS de INSERT (`user_id = auth.uid()`).
    """
    message = payload.message.strip()[:5000]
    if not message:
        raise HTTPException(status_code=400, detail="Mensagem de feedback vazia")
    result = (
        db.table("user_feedback")
        .insert({"user_id": user_id, "message": message, "context": payload.context or {}})
        .execute()
    )
    if not result.data:
        raise HTTPException(status_code=500, detail="Falha ao salvar feedback")
    return {"success": True, "id": result.data[0]["id"]}


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


@router.post(
    "/me/synthesize",
    summary="Dispara a síntese de padrões (level 2) on-demand (Gap 1)",
)
async def trigger_synthesis(user_id: CurrentUserId, db: DbClient):
    """Enfileira `synthesize_patterns_task` para o workspace do usuário.

    Diferente de /me/reflect (que roda inline e gera observações level 1), a
    síntese destila padrões (level 2) sobre o corpus acumulado de observações.
    Vai pela fila procrastinate porque a síntese é uma etapa de longo prazo
    (não precisa de retorno imediato) e self-gateia se houver poucas
    observações ativas.
    """
    workspace = _ensure_workspace(user_id, db)
    try:
        from core.tasks import app as tasks_app
        async with tasks_app.open_async():
            await tasks_app.configure_task("synthesize_patterns").defer_async(
                workspace_id=str(workspace["id"])
            )
    except Exception as e:
        raise HTTPException(
            status_code=503,
            detail="Falha ao enfileirar a síntese — tente novamente.",
        ) from e
    return {"status": "enqueued", "workspace_id": workspace["id"]}


# =============================================================================
# WEIGHT APPROVAL (Gap 2 — fecha Loop C)
# =============================================================================
# Sugestões geradas pelo ReflectionService só viram pesos reais via aprovação
# explícita aqui. Princípio: nada é aplicado automaticamente.

@router.get("/me/weights", summary="Pesos efetivos do workspace (merge global + workspace)")
def get_workspace_weights(user_id: CurrentUserId, db: DbClient):
    from core.weight_approval import list_workspace_weights
    workspace = _ensure_workspace(user_id, db)
    return {"weights": list_workspace_weights(db, workspace["id"])}


@router.get(
    "/me/weights/pending",
    summary="Sugestões de peso pendentes de aprovação (Gap 2)",
)
def get_pending_weight_suggestions(user_id: CurrentUserId, db: DbClient):
    """Lista insights ativos com weight_suggestions, com status por sugestão:
    pending (nunca aplicada) / approved (aplicada deste insight) / superseded
    (peso atual veio de outro insight ou manual)."""
    from core.weight_approval import list_pending_suggestions
    workspace = _ensure_workspace(user_id, db)
    return {"insights": list_pending_suggestions(db, workspace["id"])}


@router.post(
    "/me/weights/approve",
    summary="Aprova sugestões de peso de um reflection_insight (Gap 2)",
)
def approve_weight_suggestions(
    payload: ApproveSuggestionsPayload, user_id: CurrentUserId, db: DbClient
):
    """Aplica sugestões aprovadas como rows em matching_weights.

    Cada sugestão vira: novo_peso = clamp(peso_atual + delta, 0, 100).
    `source='reflection'`, `approved_from_insight_id` preenchido para audit.
    Invalida o cache TTL de 60s para que o próximo match use os pesos novos.
    """
    from core.weight_approval import approve_suggestions
    workspace = _ensure_workspace(user_id, db)
    try:
        upserted = approve_suggestions(
            db, workspace["id"], payload.insight_id,
            [s.model_dump() for s in payload.suggestions],
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    return {"applied": upserted}


@router.delete(
    "/me/weights/{dimension}",
    summary="Remove override do workspace para uma dimensão (volta ao global)",
)
def revert_weight(dimension: str, user_id: CurrentUserId, db: DbClient):
    from core.weight_approval import revert_workspace_weight
    workspace = _ensure_workspace(user_id, db)
    try:
        reverted = revert_workspace_weight(db, workspace["id"], dimension)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    if not reverted:
        raise HTTPException(status_code=404, detail="Sem override para esta dimensão")
    return {"success": True, "dimension": dimension}


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
