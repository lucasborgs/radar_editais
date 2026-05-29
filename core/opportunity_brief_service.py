"""
OpportunityBriefService — Brief de Oportunidade (Fase 3 #21).

Evolução do HybridMatch Stage 1: ao invés de só retornar um score numérico
(0-100), gera um documento estruturado com:
  • Decision matrix (5 dimensões, score/max/rationale)
  • Avaliação narrativa (forças, riscos, próximos passos)
  • Recomendação GO | GO_COM_RESSALVAS | NO_GO baseada no score total

Persiste o resultado em application_log com status='brief_gerado' (trigger
em application_events registra o evento automaticamente — ver migration 004).

ADR M9: NÃO usa RAG. Opera sobre a wiki page do edital (resumo estruturado) +
matching_weights por workspace + LLM para narrativa. Matching e brief estão
do mesmo lado da arquitetura (decisão), não do lado da escrita (geração).
"""
from __future__ import annotations

import json
import logging
import os
import re

from core.edital_id import wiki_page_path
from core.hybrid_match_service import get_weights, score_stage1
from domain.user_profile import CompanyProfile
from supabase import Client

logger = logging.getLogger(__name__)

# Thresholds de recomendação (sobre score 0-100)
_GO_THRESHOLD = 75
_GO_RESSALVAS_THRESHOLD = 50


_BRIEF_SYSTEM = """Você é um analista sênior de captação que avalia se uma empresa deve
aplicar a um edital específico. Sua avaliação é direta: aponte forças concretas, riscos
não-óbvios, e próximos passos acionáveis. NÃO seja diplomático demais — se o ajuste é
fraco, diga. Responda APENAS com JSON válido."""


_BRIEF_USER = """PERFIL DA EMPRESA:
{profile_context}

EDITAL: {edital_id}
{edital_summary}

DECISION MATRIX (scoring determinístico já calculado):
{matrix_summary}

Sua tarefa:
Produza uma avaliação narrativa em 3 seções para complementar o decision matrix.

1. STRENGTHS — 2 a 4 forças CONCRETAS desta empresa para este edital. Cite dimensões
   do matrix ou itens do perfil. Evite generalidades.

2. RISKS — 2 a 4 riscos NÃO-ÓBVIOS de aderência. Inclua riscos que o scoring
   determinístico pode ter subestimado (ex: contrapartida em $ que a empresa
   sinaliza no perfil mas pode não ter capacidade real; alinhamento temático
   nominal mas portfólio fraco; TRL declarado vs comprovável).

3. NEXT_STEPS — 2 a 4 ações acionáveis se a empresa decidir aplicar. Inclua o que
   precisa ser produzido (documento X, evidência Y) antes de iniciar a proposta.

JSON:
{{
  "strengths": ["string", ...],
  "risks": ["string", ...],
  "next_steps": ["string", ...]
}}"""


# Pontuação máxima de cada dimensão para exibição no decision matrix.
# Vem dos pesos do HybridMatch (default _WEIGHTS — mesmo da Fase 2 #16).
_DIMENSION_MAX = {
    "elegibilidade": 30,
    "tematico":      25,
    "trl":           20,
    "mecanismo":     15,
    "contrapartida": 10,
}


def _make_client(model_override: str | None = None):
    """Constrói client OpenAI. `model_override` aceita resolução de tier (Fase 4 #24)."""
    from core.llm_client import make_client
    model = model_override or os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    return make_client(api_key=os.environ["OPENAI_API_KEY"]), model


def _load_edital(edital_id: str) -> dict | None:
    """Lê a wiki page do edital do KG (não toca em edital_chunks — ADR M9)."""
    wiki_file = wiki_page_path(edital_id)
    if not wiki_file.exists():
        logger.warning("OpportunityBrief: wiki page não encontrada para %s", edital_id)
        return None
    try:
        data = json.loads(wiki_file.read_text(encoding="utf-8"))
        # Garante chave "id" usada pelo score_stage1
        data.setdefault("id", edital_id)
        return data
    except Exception as e:
        logger.warning("OpportunityBrief: falha ao ler wiki page %s: %s", edital_id, e)
        return None


def _build_matrix_summary(breakdown: dict[str, int]) -> str:
    lines = []
    for dim, max_val in _DIMENSION_MAX.items():
        score = breakdown.get(dim, 0)
        lines.append(f"  • {dim}: {score} / {max_val}")
    return "\n".join(lines)


def _build_edital_summary(edital: dict) -> str:
    """Resumo enxuto da wiki page para o prompt — evita dump completo."""
    parts = []
    if t := edital.get("titulo"):
        parts.append(f"Título: {t}")
    if obj := edital.get("objective"):
        parts.append(f"Objetivo: {obj}")
    if mech := edital.get("mechanism"):
        parts.append(f"Mecanismo: {mech}")
    if entities := edital.get("eligible_entities"):
        parts.append(f"Público-alvo: {', '.join(entities) if isinstance(entities, list) else entities}")
    if trl_range := edital.get("trl_range"):
        parts.append(f"TRL aceito: {trl_range}")
    if value := edital.get("value_range"):
        parts.append(f"Valor: {value}")
    if deadline := edital.get("deadline"):
        parts.append(f"Deadline: {deadline}")
    if reqs := edital.get("key_requirements"):
        reqs_str = "; ".join(reqs[:5]) if isinstance(reqs, list) else str(reqs)
        parts.append(f"Requisitos-chave: {reqs_str}")
    return "\n".join(parts)


def _recommendation_for(score: int) -> str:
    if score >= _GO_THRESHOLD:
        return "GO"
    if score >= _GO_RESSALVAS_THRESHOLD:
        return "GO_COM_RESSALVAS"
    return "NO_GO"


def _llm_narrative(
    profile_context: str,
    edital_id: str,
    edital_summary: str,
    matrix_summary: str,
    model_override: str | None = None,
) -> dict:
    """Chama LLM para gerar strengths/risks/next_steps. Fallback robusto se falhar."""
    user_msg = _BRIEF_USER.format(
        profile_context=profile_context,
        edital_id=edital_id,
        edital_summary=edital_summary,
        matrix_summary=matrix_summary,
    )
    try:
        client, model = _make_client(model_override)
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _BRIEF_SYSTEM},
                {"role": "user", "content": user_msg},
            ],
            temperature=0.2,
            max_tokens=1200,
        )
        raw = response.choices[0].message.content.strip()
        if "```" in raw:
            raw = re.sub(r"```(?:json)?", "", raw).strip()
        data = json.loads(raw)
        return {
            "strengths": data.get("strengths", []) or [],
            "risks": data.get("risks", []) or [],
            "next_steps": data.get("next_steps", []) or [],
        }
    except Exception as e:
        logger.warning("OpportunityBrief: LLM narrativa falhou: %s", e)
        return {"strengths": [], "risks": [], "next_steps": []}


def _persist_brief(
    db: Client,
    workspace_id: str,
    edital_id: str,
    total_score: int,
    breakdown: dict[str, int],
    recommendation: str,
    narrative: dict,
) -> str:
    """UPSERT em application_log com status='brief_gerado'.

    Se já existe (workspace_id, edital_id), atualiza; senão cria.
    O trigger log_application_event registra o INSERT/UPDATE em application_events.
    """
    payload = {
        "workspace_id": workspace_id,
        "edital_id": edital_id,
        "match_score": float(total_score),
        "match_dimensions": json.dumps({
            **breakdown,
            "recommendation": recommendation,
            "narrative": narrative,
        }),
        "status": "brief_gerado",
    }
    result = (
        db.table("application_log")
        .upsert(payload, on_conflict="workspace_id,edital_id")
        .execute()
    )
    if result.data:
        return result.data[0]["id"]
    # Fallback: SELECT após upsert para extrair id
    fetch = (
        db.table("application_log")
        .select("id")
        .eq("workspace_id", workspace_id)
        .eq("edital_id", edital_id)
        .maybe_single()
        .execute()
    )
    return fetch.data["id"] if fetch and fetch.data else ""


def generate_brief(
    db: Client,
    workspace_id: str,
    profile: CompanyProfile,
    edital_id: str,
    model_tier: str | None = None,
) -> dict:
    """Gera Brief de Oportunidade completo para um edital.

    Returns:
      {
        "application_id": str,    # row criada/atualizada em application_log
        "edital_id": str,
        "recommendation": "GO" | "GO_COM_RESSALVAS" | "NO_GO",
        "total_score": int,        # 0-100
        "decision_matrix": [
          {"dimension": "...", "score": int, "max": int}
        ],
        "narrative": {"strengths": [...], "risks": [...], "next_steps": [...]},
      }

    Falhas em qualquer etapa retornam o brief com narrative vazia mas decisão
    determinística sempre presente — o usuário recebe um veredicto utilizável.
    """
    edital = _load_edital(edital_id)
    if edital is None:
        return {
            "application_id": "",
            "edital_id": edital_id,
            "recommendation": "NO_GO",
            "total_score": 0,
            "decision_matrix": [],
            "narrative": {
                "strengths": [],
                "risks": ["Edital não encontrado no knowledge graph"],
                "next_steps": ["Indexar o edital ou verificar o ID"],
            },
            "error": "edital not found",
        }

    weights = get_weights(workspace_id)
    stage1 = score_stage1(edital, profile, weights)

    recommendation = _recommendation_for(stage1.score)

    profile_context = profile.to_context()
    edital_summary = _build_edital_summary(edital)
    matrix_summary = _build_matrix_summary(stage1.breakdown)
    from core.llm_router import resolve_model
    narrative = _llm_narrative(
        profile_context, edital_id, edital_summary, matrix_summary,
        model_override=resolve_model(model_tier),
    )

    application_id = _persist_brief(
        db, workspace_id, edital_id, stage1.score, stage1.breakdown,
        recommendation, narrative,
    )

    decision_matrix = [
        {"dimension": dim, "score": stage1.breakdown.get(dim, 0), "max": max_val}
        for dim, max_val in _DIMENSION_MAX.items()
    ]

    return {
        "application_id": application_id,
        "edital_id": edital_id,
        "recommendation": recommendation,
        "total_score": stage1.score,
        "decision_matrix": decision_matrix,
        "narrative": narrative,
    }
