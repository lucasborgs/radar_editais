"""
Weight approval — aprovação humana de weight_suggestions vindas do
ReflectionService (Gap 2, fecha o Loop C).

Fluxo:
  1. ReflectionService gera reflection_insights com weight_suggestions no
     evidence JSONB de level 2.
  2. Usuário lista sugestões pendentes via GET /me/weights/pending
  3. Aprova seletivamente via POST /me/weights/approve — materializa em
     matching_weights com `source='reflection'` e `approved_from_insight_id`
     populado (audit).
  4. Próximo match() do HybridMatchService usa o peso novo (após 60s de TTL
     do cache em get_weights ou invalidação explícita).

Princípio: nada é aplicado automaticamente. Mesmo com confidence=high, a
sugestão fica em matching_weights apenas após chamada explícita do endpoint
de aprovação. Coerente com a tipologia "decisões de estado da empresa →
sempre humano".
"""
from __future__ import annotations

import json
import logging

from supabase import Client

logger = logging.getLogger(__name__)

VALID_DIMENSIONS = {"elegibilidade", "tematico", "trl", "mecanismo", "contrapartida"}
DELTA_MIN = -10
DELTA_MAX = 10
WEIGHT_MIN = 0.0
WEIGHT_MAX = 100.0


def _parse_evidence(raw) -> dict:
    """`evidence` é jsonb no schema mas chega como dict OU string crua dependendo
    de como foi gravado (json.dumps no INSERT vs nativo no UPDATE). Normaliza."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            return {}
    return {}


def _current_workspace_weight(
    rows: list[dict], dimension: str, fallback: float
) -> float:
    """Peso atual do workspace para uma dimensão, com fallback ao global."""
    for r in rows:
        if r["dimension"] == dimension:
            return float(r["weight"])
    return fallback


def list_pending_suggestions(db: Client, workspace_id: str) -> list[dict]:
    """Lista insights ativos com weight_suggestions, anotando o status de cada
    sugestão (pending | approved | superseded).

    - pending:    nenhuma row em matching_weights pra essa dimensão neste workspace
    - approved:   matching_weights tem row com approved_from_insight_id = este insight
    - superseded: matching_weights tem row pra essa dimensão, mas vinda de outro
                  insight (ou manual) — sugestão ficou para trás

    Returns list ordenado por created_at desc.
    """
    # Insights ativos com weight_suggestions não-vazias.
    insights = (
        db.table("reflection_insights")
        .select("id, insight, confidence, evidence, created_at")
        .eq("workspace_id", workspace_id)
        .eq("level", 2)
        .is_("deactivated_at", "null")
        .order("created_at", desc=True)
        .execute()
    )
    rows = insights.data or []

    # Pesos atuais do workspace (1 query) — usado pra computar status de cada sugestão.
    weights_rows = (
        db.table("matching_weights")
        .select("dimension, weight, approved_from_insight_id, source")
        .eq("workspace_id", workspace_id)
        .execute()
    ).data or []

    weights_by_dim = {w["dimension"]: w for w in weights_rows}

    pending: list[dict] = []
    for row in rows:
        evidence = _parse_evidence(row.get("evidence"))
        raw_suggestions = evidence.get("weight_suggestions") or []
        if not raw_suggestions:
            continue

        annotated: list[dict] = []
        for sug in raw_suggestions:
            if not isinstance(sug, dict):
                continue
            dimension = sug.get("dimension")
            if dimension not in VALID_DIMENSIONS:
                continue
            try:
                delta = int(sug.get("delta", 0))
            except (TypeError, ValueError):
                delta = 0
            delta = max(DELTA_MIN, min(DELTA_MAX, delta))

            current = weights_by_dim.get(dimension)
            if current is None:
                status = "pending"
            elif current.get("approved_from_insight_id") == row["id"]:
                status = "approved"
            else:
                status = "superseded"

            annotated.append({
                "dimension": dimension,
                "delta": delta,
                "rationale": sug.get("rationale", ""),
                "status": status,
            })

        if not annotated:
            continue

        pending.append({
            "insight_id": row["id"],
            "insight": row.get("insight", ""),
            "confidence": row.get("confidence") or evidence.get("confidence") or "low",
            "created_at": row.get("created_at"),
            "suggestions": annotated,
        })

    return pending


def list_workspace_weights(db: Client, workspace_id: str) -> list[dict]:
    """Pesos efetivos do workspace: merge entre globais e overrides do workspace.

    Para cada dimensão válida retorna:
      - dimension, weight, source, scope ('workspace' | 'global'),
      - approved_from_insight_id (se vier de aprovação)
    """
    global_rows = (
        db.table("matching_weights")
        .select("dimension, weight, source")
        .is_("workspace_id", "null")
        .execute()
    ).data or []
    global_by_dim = {r["dimension"]: r for r in global_rows}

    ws_rows = (
        db.table("matching_weights")
        .select("dimension, weight, source, approved_from_insight_id, approved_at, updated_at")
        .eq("workspace_id", workspace_id)
        .execute()
    ).data or []
    ws_by_dim = {r["dimension"]: r for r in ws_rows}

    out: list[dict] = []
    for dim in VALID_DIMENSIONS:
        if dim in ws_by_dim:
            r = ws_by_dim[dim]
            out.append({
                "dimension": dim,
                "weight": float(r["weight"]),
                "source": r["source"],
                "scope": "workspace",
                "approved_from_insight_id": r.get("approved_from_insight_id"),
                "approved_at": r.get("approved_at"),
                "updated_at": r.get("updated_at"),
            })
        elif dim in global_by_dim:
            r = global_by_dim[dim]
            out.append({
                "dimension": dim,
                "weight": float(r["weight"]),
                "source": r["source"],
                "scope": "global",
            })
    return out


def approve_suggestions(
    db: Client,
    workspace_id: str,
    insight_id: str,
    suggestions: list[dict],
) -> list[dict]:
    """Aplica weight_suggestions aprovadas como rows em matching_weights.

    Cada sugestão vira um UPSERT em (workspace_id, dimension):
      - novo peso = clamp(peso_atual + delta, 0, 100)
      - peso_atual = override do workspace se existir, senão o global
      - source = 'reflection', approved_from_insight_id = insight_id

    Args:
        workspace_id: workspace alvo (RLS exige que pertença ao usuário).
        insight_id: insight de onde a sugestão veio (audit).
        suggestions: lista de {dimension, delta}.

    Returns:
        Lista de rows upsertadas (uma por sugestão válida).

    Raises:
        ValueError: se alguma sugestão tem dimension inválida ou delta fora
        do range — preserve atomicidade do batch (tudo ou nada).
    """
    if not suggestions:
        return []

    # Valida ANTES de tocar no DB (atomicidade do batch).
    normalized: list[tuple[str, int]] = []
    for sug in suggestions:
        dim = sug.get("dimension")
        if dim not in VALID_DIMENSIONS:
            raise ValueError(f"dimension inválida: {dim!r}")
        try:
            delta = int(sug.get("delta", 0))
        except (TypeError, ValueError) as e:
            raise ValueError(f"delta inválido em {dim}: {sug.get('delta')!r}") from e
        if delta < DELTA_MIN or delta > DELTA_MAX:
            raise ValueError(f"delta fora do range [{DELTA_MIN}, {DELTA_MAX}]: {delta}")
        normalized.append((dim, delta))

    # Lê estado atual em 2 queries pra computar o peso final por dimensão.
    from core.hybrid_match_service import get_weights
    current_weights = get_weights(workspace_id)

    upserted: list[dict] = []
    for dim, delta in normalized:
        new_weight = max(WEIGHT_MIN, min(WEIGHT_MAX, current_weights.get(dim, 0.0) + delta))
        # UPSERT via on_conflict — UNIQUE (workspace_id, dimension) cobre o caso.
        result = (
            db.table("matching_weights")
            .upsert({
                "workspace_id": workspace_id,
                "dimension": dim,
                "weight": new_weight,
                "source": "reflection",
                "approved_from_insight_id": insight_id,
                "approved_at": "now()",
            }, on_conflict="workspace_id,dimension")
            .execute()
        )
        if result.data:
            upserted.append(result.data[0])

    # Invalida cache do get_weights para que o próximo match use os pesos novos
    # imediatamente (sem esperar o TTL de 60s).
    _invalidate_weights_cache(workspace_id)

    logger.info(
        "approve_suggestions: workspace=%s insight=%s aplicadas=%d",
        workspace_id, insight_id, len(upserted),
    )
    return upserted


def revert_workspace_weight(
    db: Client, workspace_id: str, dimension: str
) -> bool:
    """Remove o override do workspace para uma dimensão (volta ao global).

    Returns True se uma row foi removida, False se não havia override.
    """
    if dimension not in VALID_DIMENSIONS:
        raise ValueError(f"dimension inválida: {dimension!r}")

    result = (
        db.table("matching_weights")
        .delete()
        .eq("workspace_id", workspace_id)
        .eq("dimension", dimension)
        .execute()
    )
    if result.data:
        _invalidate_weights_cache(workspace_id)
        return True
    return False


def _invalidate_weights_cache(workspace_id: str) -> None:
    """Limpa entradas do cache do get_weights pro workspace e pro global merge.

    O cache do hybrid_match_service.get_weights guarda por chave (workspace_id
    ou "__global__"). Após mudança, removemos ambas para evitar dados stale no
    próximo match.
    """
    try:
        from core.hybrid_match_service import _weights_cache
        _weights_cache.pop(workspace_id, None)
        _weights_cache.pop("__global__", None)
    except Exception as e:
        logger.debug("Falha ao invalidar weights cache: %s", e)
