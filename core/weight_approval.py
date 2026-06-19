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

# Guardas do auto-apply (Item 1, Sprint 1). Mais apertados que o fluxo manual:
# o auto-apply só dispara quando os 3 passam SIMULTANEAMENTE.
AUTO_APPLY_MIN_OUTCOMES = 5
AUTO_APPLY_MAX_ABS_DELTA = 5


def _clamp_weight(value: float) -> float:
    return max(WEIGHT_MIN, min(WEIGHT_MAX, value))


def _materialize_weight(
    db: Client,
    workspace_id: str,
    dimension: str,
    new_weight: float,
    insight_id: str,
) -> dict | None:
    """Upsert canônico de um peso em matching_weights.

    Compartilhado por approve_suggestions (manual) e auto_apply_suggestions
    (automático). source='reflection', approved_from_insight_id e approved_at
    populados para audit. on_conflict cobre o UNIQUE (workspace_id, dimension).

    Returns a row upsertada (ou None se o DB não retornou data).
    """
    result = (
        db.table("matching_weights")
        .upsert({
            "workspace_id": workspace_id,
            "dimension": dimension,
            "weight": new_weight,
            "source": "reflection",
            "approved_from_insight_id": insight_id,
            "approved_at": "now()",
        }, on_conflict="workspace_id,dimension")
        .execute()
    )
    return result.data[0] if result.data else None


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
    from core.services.hybrid_match_service import get_weights
    current_weights = get_weights(workspace_id)

    upserted: list[dict] = []
    for dim, delta in normalized:
        new_weight = _clamp_weight(current_weights.get(dim, 0.0) + delta)
        row = _materialize_weight(db, workspace_id, dim, new_weight, insight_id)
        if row:
            upserted.append(row)

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


def auto_apply_suggestions(
    db: Client,
    workspace_id: str,
    insight_id: str,
    suggestions: list[dict],
    outcomes_considered: int,
    confidence: str,
) -> list[dict]:
    """Auto-aplica weight_suggestions quando os 3 guardas passam SIMULTANEAMENTE.

    Coexiste com o fluxo manual (approve_suggestions): o que NÃO passa nos
    guardas continua disponível em /me/weights/pending para aprovação humana —
    esta função apenas materializa o subconjunto "óbvio o suficiente" sem
    intervenção, e deixa rastro reversível em weight_change_log.

    Guardas (todos obrigatórios):
      - confidence == "high"
      - outcomes_considered >= 5  (AUTO_APPLY_MIN_OUTCOMES)
      - |delta| <= 5 por dimensão  (AUTO_APPLY_MAX_ABS_DELTA) — cap mais apertado
        que o DELTA_MIN/MAX=±10 do fluxo manual; validado aqui sem relaxar o cap
        manual. Sugestões com |delta| > 5 são puladas (caem no fluxo manual).

    Para cada sugestão que passa: new_weight = clamp(current + delta, 0, 100),
    upsert idêntico ao manual (source='reflection', approved_from_insight_id),
    e INSERT em weight_change_log (old/new/delta, confidence, outcomes_window,
    rationale, insight_id). Invalida o cache de pesos no fim.

    Returns lista das mudanças aplicadas (uma por dimensão materializada). [] se
    os guardas globais falham ou nenhuma sugestão individual passa.
    """
    if confidence != "high" or outcomes_considered < AUTO_APPLY_MIN_OUTCOMES:
        return []
    if not suggestions:
        return []

    from core.services.hybrid_match_service import get_weights
    current_weights = get_weights(workspace_id)

    applied: list[dict] = []
    for sug in suggestions:
        if not isinstance(sug, dict):
            continue
        dim = sug.get("dimension")
        if dim not in VALID_DIMENSIONS:
            continue
        try:
            delta = int(sug.get("delta", 0))
        except (TypeError, ValueError):
            continue
        if abs(delta) > AUTO_APPLY_MAX_ABS_DELTA:
            # Cap apertado do auto — sobra para o fluxo manual.
            continue

        old_weight = float(current_weights.get(dim, 0.0))
        new_weight = _clamp_weight(old_weight + delta)
        row = _materialize_weight(db, workspace_id, dim, new_weight, insight_id)
        if not row:
            continue

        log = (
            db.table("weight_change_log")
            .insert({
                "workspace_id": workspace_id,
                "dimension": dim,
                "old_weight": old_weight,
                "new_weight": new_weight,
                "delta": delta,
                "confidence": confidence,
                "outcomes_window": outcomes_considered,
                "rationale": sug.get("rationale", ""),
                "insight_id": insight_id,
            })
            .execute()
        )
        applied.append(log.data[0] if log.data else {
            "dimension": dim,
            "old_weight": old_weight,
            "new_weight": new_weight,
            "delta": delta,
        })

    if applied:
        _invalidate_weights_cache(workspace_id)

    logger.info(
        "auto_apply_suggestions: workspace=%s insight=%s conf=%s outcomes=%d aplicadas=%d/%d",
        workspace_id, insight_id, confidence, outcomes_considered,
        len(applied), len(suggestions),
    )
    return applied


def list_weight_changes(db: Client, workspace_id: str, limit: int = 50) -> list[dict]:
    """Feed de mudanças de peso (auto-apply + reverts) do workspace.

    Ordenado por applied_at desc. Inclui ativas (reverted_at NULL) e revertidas,
    para o usuário ver o histórico completo e decidir reverter.
    """
    result = (
        db.table("weight_change_log")
        .select("*")
        .eq("workspace_id", workspace_id)
        .order("applied_at", desc=True)
        .limit(limit)
        .execute()
    )
    return result.data or []


def revert_weight_change(db: Client, workspace_id: str, change_id: str) -> bool:
    """Reverte uma mudança de peso específica do log.

    Semântica escolhida — "desfazer aquele delta específico":
      1. Marca reverted_at=now() na linha original (idempotente: linha já
         revertida retorna False sem efeito).
      2. Aplica o delta INVERSO ao peso atual da dimensão:
         new = clamp(current - delta_original, 0, 100). Isto desfaz a
         contribuição daquela mudança independentemente de mudanças posteriores
         na mesma dimensão (composição de deltas), em vez de "restaurar
         old_weight" cego (que apagaria deltas legítimos aplicados depois).
      3. INSERE uma nova linha de log representando o próprio revert: delta
         inverso, old/new = estado antes/depois do revert, confidence/insight
         herdados como NULL, rationale="revert de <change_id>". Essa linha de
         revert nasce ativa (reverted_at NULL).
      4. Invalida o cache de pesos.

    Nota: se a dimensão não tem mais override em matching_weights (ex.: já foi
    revertida via /me/weights/{dimension}), o peso corrente vem do global via
    get_weights — o revert reaplica como override do workspace com o peso
    ajustado. Coerente: o log permanece a fonte da verdade do que foi auto.

    Returns True se reverteu, False se change_id não existe, é de outro
    workspace (RLS), ou já estava revertida.
    """
    rows = (
        db.table("weight_change_log")
        .select("*")
        .eq("id", change_id)
        .eq("workspace_id", workspace_id)
        .is_("reverted_at", "null")
        .execute()
    ).data or []
    if not rows:
        return False
    change = rows[0]

    dimension = change["dimension"]
    delta = float(change["delta"])

    # Marca a original como revertida.
    db.table("weight_change_log").update(
        {"reverted_at": "now()"}
    ).eq("id", change_id).eq("workspace_id", workspace_id).execute()

    # Aplica o delta inverso ao peso corrente.
    from core.services.hybrid_match_service import get_weights
    current_weights = get_weights(workspace_id)
    old_weight = float(current_weights.get(dimension, 0.0))
    new_weight = _clamp_weight(old_weight - delta)

    # Materializa. insight_id da linha original preserva a cadeia de audit no
    # peso (de onde a dimensão "veio"); aceitável herdar.
    _materialize_weight(
        db, workspace_id, dimension, new_weight, change.get("insight_id")
    )

    # Linha de log do próprio revert (delta inverso), nasce ativa.
    db.table("weight_change_log").insert({
        "workspace_id": workspace_id,
        "dimension": dimension,
        "old_weight": old_weight,
        "new_weight": new_weight,
        "delta": -delta,
        "confidence": None,
        "outcomes_window": None,
        "rationale": f"revert de {change_id}",
        "insight_id": change.get("insight_id"),
    }).execute()

    _invalidate_weights_cache(workspace_id)
    logger.info(
        "revert_weight_change: workspace=%s change=%s dim=%s delta_inverso=%s",
        workspace_id, change_id, dimension, -delta,
    )
    return True


def _invalidate_weights_cache(workspace_id: str) -> None:
    """Limpa entradas do cache do get_weights pro workspace e pro global merge.

    O cache do hybrid_match_service.get_weights guarda por chave (workspace_id
    ou "__global__"). Após mudança, removemos ambas para evitar dados stale no
    próximo match.
    """
    try:
        from core.services.hybrid_match_service import _weights_cache
        _weights_cache.pop(workspace_id, None)
        _weights_cache.pop("__global__", None)
    except Exception as e:
        logger.debug("Falha ao invalidar weights cache: %s", e)
