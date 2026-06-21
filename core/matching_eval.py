"""
core/matching_eval.py — Rúbrica e métricas do eval de matching (Front 2).

Nunca validamos se o `/match` rankeia bem — é o topo do funil. Este módulo
julga o top-K de um match contra a rúbrica única da spec (por edital):
  • fit temático 0-2, elegibilidade 0-2 (juiz LLM), vigência sim/não
    (determinístico, via core.kg.temporal).

A partir do veredicto por edital, computa precisão@K e se um edital esperado
(ex.: finep:612 para iFlorestal) aparece no topo.

Juiz com modelo FIXO — comparabilidade entre runs. Default gpt-4o-mini
(decisão pré-lançamento 2026-06-21: sem usuários reais nem baseline em produção
a preservar, o custo do juiz pesa mais que a comparabilidade histórica; régua
mais forte continua disponível via MATCHING_EVAL_MODEL=gpt-4o p/ um estudo de
concordância). Funções de PARSE/MÉTRICA são puras (testáveis sem rede); só
`judge_match_rubric` toca a OpenAI.
"""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)


def _eval_model() -> str:
    # Régua do juiz. gpt-4o-mini por default (barato); bump explícito para uma
    # régua mais forte via MATCHING_EVAL_MODEL (ex.: gpt-4o). Desacoplado do
    # OPENAI_MODEL_PRO de propósito — o juiz agora é deliberadamente leve.
    return os.getenv("MATCHING_EVAL_MODEL") or "gpt-4o-mini"


def _strip_code_fence(raw: str) -> str:
    raw = raw.strip()
    if "```" in raw:
        raw = re.sub(r"```(?:json)?", "", raw).strip().rstrip("`").strip()
    return raw


# =============================================================================
# Rúbrica por edital (juiz LLM)
# =============================================================================

_RUBRIC_SYSTEM = (
    "Você é um avaliador de matching empresa↔edital de fomento. Dada uma "
    "empresa e um edital, atribua dois scores inteiros:\n"
    "  • fit_tematico (0-2): 0=sem relação temática, 1=relação parcial, "
    "2=forte alinhamento de setor/tema.\n"
    "  • elegibilidade (0-2): 0=claramente inelegível (entidade/porte/TRL/"
    "mecanismo incompatíveis), 1=elegibilidade dúbia, 2=claramente elegível.\n"
    "Seja rigoroso e literal: baseie-se só no que o edital e o perfil dizem. "
    'Responda APENAS com JSON {"fit_tematico": <0-2>, "elegibilidade": <0-2>, '
    '"rationale": "1 frase"}.'
)


@dataclass
class RubricVerdict:
    fit_tematico: int       # 0-2
    elegibilidade: int      # 0-2
    vigente: bool           # determinístico
    rationale: str = ""

    def is_hit(self) -> bool:
        """Resultado 'bom': vigente E elegibilidade≥1 E fit≥1."""
        return self.vigente and self.elegibilidade >= 1 and self.fit_tematico >= 1


def _parse_rubric(raw: str) -> tuple[int, int, str]:
    """Parser puro → (fit_tematico, elegibilidade, rationale), clampados a [0,2]."""
    try:
        data = json.loads(_strip_code_fence(raw))
    except Exception:
        return 0, 0, ""
    if not isinstance(data, dict):
        return 0, 0, ""

    def _clamp(v) -> int:
        try:
            return max(0, min(2, int(v)))
        except (TypeError, ValueError):
            return 0

    return (
        _clamp(data.get("fit_tematico")),
        _clamp(data.get("elegibilidade")),
        str(data.get("rationale", "")),
    )


def judge_match_rubric(profile_context: str, edital_summary: str) -> tuple[int, int, str]:
    """Juiz LLM da rúbrica para um par (perfil, edital). Falha → (0, 0, "")."""
    user = f"EMPRESA:\n{profile_context}\n\nEDITAL:\n{edital_summary}"
    try:
        from core.llm.llm_client import make_client
        client = make_client(api_key=os.environ["OPENAI_API_KEY"])
        resp = client.chat.completions.create(
            model=_eval_model(),
            messages=[
                {"role": "system", "content": _RUBRIC_SYSTEM},
                {"role": "user", "content": user},
            ],
            temperature=0.0,
            max_tokens=300,
        )
        return _parse_rubric(resp.choices[0].message.content or "")
    except Exception as e:
        logger.warning("judge_match_rubric falhou: %s", e)
        return 0, 0, ""


# =============================================================================
# Métricas (puras)
# =============================================================================

def precision_at_k(verdicts: list[RubricVerdict], k: int) -> float:
    """Fração dos top-k que são 'hits' (vigente + elegível + fit). 0 se k≤0."""
    if k <= 0 or not verdicts:
        return 0.0
    topk = verdicts[:k]
    return sum(1 for v in topk if v.is_hit()) / len(topk)


def expected_in_top(result_ids: list[str], expected_id: str, n: int) -> bool:
    """True se `expected_id` está nas n primeiras posições do ranking."""
    return expected_id in result_ids[:n]


def aggregate_matching_runs(per_profile: list[dict]) -> dict:
    """Agrega métricas entre perfis avaliados.

    Cada item pode conter: precision_at_3, precision_at_5, expected_hit (bool),
    n_expired_in_topk (int), n_ineligible_in_topk (int).
    """
    if not per_profile:
        return {}
    n = len(per_profile)

    def _mean(key: str) -> float | None:
        vals = [p[key] for p in per_profile if key in p]
        return (sum(vals) / len(vals)) if vals else None

    out: dict = {"n_profiles": n}
    for key in ("precision_at_3", "precision_at_5"):
        m = _mean(key)
        if m is not None:
            out[f"mean_{key}"] = m
    expected = [p for p in per_profile if "expected_hit" in p]
    if expected:
        out["expected_hit_rate"] = sum(1 for p in expected if p["expected_hit"]) / len(expected)
    out["total_expired_in_topk"] = sum(p.get("n_expired_in_topk", 0) for p in per_profile)
    out["total_ineligible_in_topk"] = sum(p.get("n_ineligible_in_topk", 0) for p in per_profile)
    return out


__all__ = [
    "RubricVerdict",
    "aggregate_matching_runs",
    "expected_in_top",
    "judge_match_rubric",
    "precision_at_k",
]
