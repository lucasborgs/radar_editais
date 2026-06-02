"""
ReflectionService — síntese de outcomes em insights semânticos (Fase 2 #17–#19).

Inspirado no módulo de Reflection do paper Generative Agents (Park et al., 2023)
e na arquitetura CoALA (ações de aprendizado). Lê application_log + events
de um workspace, gera questões/observações via LLM, sintetiza padrões,
persiste em reflection_insights. Conservador quanto à atualização automática
de matching_weights — apenas sugere; aplicação fica manual ou em iteração futura.

ADR M9 reforça o escopo: insights alimentam a WritingSession (writing-side),
NÃO o matching. Embora o resultado eventual seja ajustar pesos do match, a
atualização passa por revisão humana ou (Fase 3) por agregação cross-workspace
com salvaguardas.

Trigger principal (ADR §4.3): a cada 5 outcomes (aprovada/reprovada/submetida)
acumulados em application_log desde a última reflexão. Por enquanto exposed
como task on-demand — periodicidade entra na pipeline de jobs futura.
"""
from __future__ import annotations

import json
import logging
import os
import re

from supabase import Client

logger = logging.getLogger(__name__)

# Status que contam como outcome (ADR C1: terminais + submetida)
OUTCOME_STATUSES = ("aprovada", "reprovada", "submetida")

# Mínimo de outcomes necessários para gerar reflexão útil
MIN_OUTCOMES_FOR_REFLECTION = 5

# Limite de outcomes considerados em uma única reflexão (evita prompt blowup)
MAX_OUTCOMES_PER_REFLECTION = 30


_REFLECT_SYSTEM = """Você é um analista sênior que estuda padrões em captação de recursos para P&D.
A partir dos resultados de aplicações a editais de uma empresa, identifique observações
factuais (nível 1) e padrões interpretativos (nível 2) que ajudem essa empresa a melhorar
sua estratégia de captação.

NÃO especule sem evidência. NÃO repita observações triviais. NÃO julgue o usuário.
SEMPRE responda com JSON válido."""


_REFLECT_USER = """Outcomes da empresa (mais recentes primeiro):

{outcomes}

Sua tarefa:

1. OBSERVAÇÕES (nível 1) — 3 a 5 observações factuais agregando os outcomes.
   Cada uma deve referenciar ids específicos da lista acima como evidência.
   Exemplo: "Aplicou a 3 editais com TRL ≥ 6 e foi aprovada em 2; aplicou a 4
   com TRL ≤ 5 e foi reprovada em 4."

2. PADRÕES (nível 2) — 1 a 3 padrões interpretativos sintetizando as observações.
   Cada um deve conectar pelo menos 2 observações.
   Exemplo: "O problema da empresa não é alinhamento temático, é posicionamento
   de maturidade tecnológica — editais que exigem TRL alto têm taxa de aprovação
   maior."

3. SUGESTÕES DE PESO (opcional) — se algum padrão indicar que uma dimensão de
   matching deveria pesar mais ou menos para esta empresa, sugira ajustes ao
   array `weight_suggestions`. Cada item: {{"dimension": "...", "delta": +/-N,
   "rationale": "..."}}. dimensions válidas: elegibilidade, tematico, trl,
   mecanismo, contrapartida. delta ∈ [-10, +10]. Use no máximo 2 sugestões.
   Deixe `weight_suggestions: []` se evidência for fraca.

Responda com JSON:
{{
  "observations": [
    {{"text": "...", "evidence_ids": ["uuid1", "uuid2"]}},
    ...
  ],
  "patterns": [
    {{"text": "...", "observation_indices": [0, 2]}},
    ...
  ],
  "weight_suggestions": [
    {{"dimension": "trl", "delta": 5, "rationale": "..."}}
  ],
  "confidence": "low" | "medium" | "high"
}}"""


def _make_client():
    from core.llm_client import make_client
    return make_client(api_key=os.environ["OPENAI_API_KEY"]), os.getenv("OPENAI_MODEL", "gpt-4o-mini")


def _load_outcomes(db: Client, workspace_id: str) -> list[dict]:
    """Carrega application_log com status terminal/submetida ordenado por updated_at desc."""
    result = (
        db.table("application_log")
        .select("id, edital_id, status, match_score, match_dimensions, feedback_notas, updated_at, created_at")
        .eq("workspace_id", workspace_id)
        .in_("status", list(OUTCOME_STATUSES))
        .order("updated_at", desc=True)
        .limit(MAX_OUTCOMES_PER_REFLECTION)
        .execute()
    )
    return result.data or []


def _format_outcomes_for_prompt(outcomes: list[dict]) -> str:
    parts = []
    for o in outcomes:
        dims = o.get("match_dimensions") or {}
        dims_str = ", ".join(f"{k}={v}" for k, v in dims.items()) if dims else "—"
        parts.append(
            f"id={o['id']}: edital={o['edital_id']}, status={o['status']}, "
            f"score={o.get('match_score', '—')}, dimensions={{{dims_str}}}, "
            f"feedback={o.get('feedback_notas') or '—'}, "
            f"updated_at={o['updated_at']}"
        )
    return "\n".join(parts)


def reflect_workspace(db: Client, workspace_id: str) -> dict:
    """Gera reflexão sobre outcomes do workspace e persiste em reflection_insights.

    Returns:
        dict com chaves:
          - outcomes_considered (int)
          - observations_inserted (int)
          - patterns_inserted (int)
          - weight_suggestions (list)
          - confidence (str)
          - skipped_reason (str) se reflexão não foi gerada
    """
    outcomes = _load_outcomes(db, workspace_id)
    if len(outcomes) < MIN_OUTCOMES_FOR_REFLECTION:
        return {
            "outcomes_considered": len(outcomes),
            "observations_inserted": 0,
            "patterns_inserted": 0,
            "weight_suggestions": [],
            "confidence": None,
            "skipped_reason": f"poucos outcomes ({len(outcomes)} < {MIN_OUTCOMES_FOR_REFLECTION})",
        }

    client, model = _make_client()
    user_msg = _REFLECT_USER.format(outcomes=_format_outcomes_for_prompt(outcomes))

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _REFLECT_SYSTEM},
                {"role": "user", "content": user_msg},
            ],
            temperature=0.2,
            max_tokens=2000,
        )
        raw = response.choices[0].message.content.strip()
        if "```" in raw:
            raw = re.sub(r"```(?:json)?", "", raw).strip()
        data = json.loads(raw)
    except Exception as e:
        logger.error("reflect_workspace: LLM falhou para workspace=%s: %s", workspace_id, e)
        return {
            "outcomes_considered": len(outcomes),
            "observations_inserted": 0,
            "patterns_inserted": 0,
            "weight_suggestions": [],
            "confidence": None,
            "skipped_reason": f"LLM error: {e}",
        }

    observations = data.get("observations", [])
    patterns = data.get("patterns", [])
    weight_suggestions = data.get("weight_suggestions", []) or []
    confidence = data.get("confidence", "low")

    # Janela temporal usada na reflexão (alimenta outcomes_window_start/end)
    window_end = outcomes[0]["updated_at"]
    window_start = outcomes[-1]["updated_at"]

    # Normaliza confidence pro check constraint da coluna (low|medium|high).
    confidence_col = confidence if confidence in ("low", "medium", "high") else "low"

    rows_to_insert = []
    for obs in observations:
        rows_to_insert.append({
            "workspace_id": workspace_id,
            "level": 1,
            "insight": obs.get("text", ""),
            "evidence": json.dumps(obs.get("evidence_ids", [])),
            "outcomes_window_start": window_start,
            "outcomes_window_end": window_end,
            "confidence": confidence_col,
        })
    for pat in patterns:
        rows_to_insert.append({
            "workspace_id": workspace_id,
            "level": 2,
            "insight": pat.get("text", ""),
            "evidence": json.dumps({
                "observation_indices": pat.get("observation_indices", []),
                "weight_suggestions": weight_suggestions,
                "confidence": confidence,
            }),
            "outcomes_window_start": window_start,
            "outcomes_window_end": window_end,
            "confidence": confidence_col,
        })

    if rows_to_insert:
        db.table("reflection_insights").insert(rows_to_insert).execute()

    # Weight suggestions são apenas sugestões — NÃO aplicamos automaticamente
    # em matching_weights nesta versão. Log para revisão humana.
    if weight_suggestions and confidence == "high":
        logger.info(
            "reflect_workspace: workspace=%s tem %d sugestões de peso (conf=high) — "
            "revisar para aplicação manual em matching_weights",
            workspace_id, len(weight_suggestions),
        )

    return {
        "outcomes_considered": len(outcomes),
        "observations_inserted": len(observations),
        "patterns_inserted": len(patterns),
        "weight_suggestions": weight_suggestions,
        "confidence": confidence,
        "skipped_reason": None,
    }


def load_active_insights(db: Client, workspace_id: str, max_total: int = 6) -> list[dict]:
    """Retorna insights ativos do workspace para injeção em WritingSession (#18).

    Prioriza level 2 (padrões) e cai para level 1 (observações) se faltar.
    Fonte da verdade é `deactivated_at IS NULL` (Gap 3b). A coluna `active`
    legacy é mantida sincronizada via trigger pra leitores antigos.
    """
    result = (
        db.table("reflection_insights")
        .select("level, insight, created_at")
        .eq("workspace_id", workspace_id)
        .is_("deactivated_at", "null")
        .order("level", desc=True)
        .order("created_at", desc=True)
        .limit(max_total)
        .execute()
    )
    return result.data or []


def search_insights_for_tool(db: Client, workspace_id: str) -> str:
    """Retorna insights ativos formatados para consumo como tool response.

    Versão inicial: devolve todos os insights ativos (max 6) sem filtragem
    semântica — o conjunto costuma ser pequeno e toda informação é relevante.
    Evolução futura: filtrar por similaridade ao `topic` da query via embeddings.
    """
    try:
        insights = load_active_insights(db, workspace_id, max_total=6)
    except Exception as e:
        logger.warning("search_insights_for_tool: falha ao carregar: %s", e)
        return "Erro ao acessar aprendizados — tente novamente."
    if not insights:
        return "Nenhum aprendizado registrado para este workspace ainda."
    parts = ["Aprendizados de aplicações anteriores desta empresa:"]
    for ins in insights:
        label = "Padrão estratégico" if ins.get("level") == 2 else "Observação"
        parts.append(f"• [{label}] {ins.get('insight', '')}")
    return "\n".join(parts)


def deactivate_insight(
    db: Client,
    insight_id: str,
    deactivated_by_insight_id: str | None,
    reason: str,
) -> bool:
    """Marca um reflection_insight como desativado, com audit trail (Gap 3b).

    Consumido por Gap 3a (full mode reflection) quando um insight novo supera
    um antigo. Idempotente sobre insights já desativados — o filtro
    `is_("deactivated_at", "null")` garante que o timestamp original seja
    preservado.

    Args:
        insight_id: id do insight a desativar.
        deactivated_by_insight_id: id do insight que substituiu este (audit).
            Pode ser None se a desativação for manual/admin.
        reason: justificativa em texto livre.

    Returns:
        True se uma row foi atualizada, False se já estava desativado, não
        existe, ou pertence a outro workspace (RLS).
    """
    payload: dict = {
        "deactivated_at": "now()",
        "deactivation_reason": reason,
    }
    if deactivated_by_insight_id:
        payload["deactivated_by_insight_id"] = deactivated_by_insight_id

    result = (
        db.table("reflection_insights")
        .update(payload)
        .eq("id", insight_id)
        .is_("deactivated_at", "null")
        .execute()
    )
    return bool(result.data)
