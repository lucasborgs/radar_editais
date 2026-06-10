"""Rúbrica de avaliação do match-por-tese de INVESTIDOR (kind_class=entidade).

Espelha `core/matching_eval.py`, mas o substrato é a TESE do fundo, não a
elegibilidade/vigência de um edital — entidade não tem gate nem deadline (spec
§3.8). O juiz LLM pontua três eixos de alinhamento:
  • fit_tese   (0-2): a tese/temas do fundo casam com o que a startup faz?
  • fit_estagio(0-2): o estágio alvo do fundo casa com o estágio da startup?
  • fit_setor  (0-2): o setor do fundo casa com o setor da startup?

A partir do veredicto por fundo, computa precisão@K. `expected_in_top` é reusado
de `matching_eval` (genérico sobre listas de id).
"""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass

logger = logging.getLogger(__name__)


def _eval_model() -> str:
    return os.getenv("OPENAI_MODEL_EVAL") or os.getenv("OPENAI_MODEL_PRO") or "gpt-4o"


def _strip_code_fence(raw: str) -> str:
    if "```" in raw:
        raw = re.sub(r"```(?:json)?", "", raw).strip().rstrip("`").strip()
    return raw


_RUBRIC_SYSTEM = (
    "Você é um avaliador de match startup↔fundo de venture capital (match por TESE). "
    "Dada uma startup e um fundo, atribua três scores inteiros:\n"
    "  • fit_tese (0-2): 0=sem relação com a tese/temas do fundo, 1=relação parcial, "
    "2=forte alinhamento com a tese/temas.\n"
    "  • fit_estagio (0-2): 0=estágio incompatível (ex.: fundo só série-A, startup pré-seed), "
    "1=adjacente, 2=estágio alvo do fundo bate com o da startup.\n"
    "  • fit_setor (0-2): 0=setor sem relação, 1=parcial, 2=setor do fundo cobre o da startup "
    "(fundo GENERALISTA/multissetorial conta como 2).\n"
    "Seja rigoroso e literal: baseie-se só no que o fundo e a startup dizem. NÃO penalize "
    "por ausência de informação. Responda APENAS com JSON "
    '{"fit_tese": <0-2>, "fit_estagio": <0-2>, "fit_setor": <0-2>, "rationale": "1 frase"}.'
)


@dataclass
class ThesisVerdict:
    fit_tese: int       # 0-2
    fit_estagio: int    # 0-2
    fit_setor: int      # 0-2
    rationale: str = ""

    def is_hit(self) -> bool:
        """Match 'bom': alinhamento de tese E estágio (setor reforça, não trava).

        Entidade não elimina (sem gate) — o hit mede QUALIDADE do ranking, não
        elegibilidade. Exige tese≥1 e estágio≥1; setor torto não desqualifica
        (fundo multissetorial é o caso comum).
        """
        return self.fit_tese >= 1 and self.fit_estagio >= 1


def _parse_rubric(raw: str) -> tuple[int, int, int, str]:
    """Parser puro → (fit_tese, fit_estagio, fit_setor, rationale), clampados a [0,2]."""
    try:
        data = json.loads(_strip_code_fence(raw))
    except Exception:
        return 0, 0, 0, ""
    if not isinstance(data, dict):
        return 0, 0, 0, ""

    def _clamp(v) -> int:
        try:
            return max(0, min(2, int(v)))
        except (TypeError, ValueError):
            return 0

    return (
        _clamp(data.get("fit_tese")),
        _clamp(data.get("fit_estagio")),
        _clamp(data.get("fit_setor")),
        str(data.get("rationale", "")),
    )


def judge_thesis_fit(profile_context: str, fund_summary: str) -> tuple[int, int, int, str]:
    """Juiz LLM da rúbrica para um par (perfil, fundo). Falha → (0,0,0,"")."""
    user = f"STARTUP:\n{profile_context}\n\nFUNDO:\n{fund_summary}"
    try:
        from core.llm_client import make_client
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
        logger.warning("judge_thesis_fit falhou: %s", e)
        return 0, 0, 0, ""


def precision_at_k(verdicts: list[ThesisVerdict], k: int) -> float:
    """Fração dos top-k que são 'hits' (tese+estágio). 0 se k≤0 ou vazio."""
    if k <= 0 or not verdicts:
        return 0.0
    topk = verdicts[:k]
    return sum(1 for v in topk if v.is_hit()) / len(topk)


__all__ = ["ThesisVerdict", "judge_thesis_fit", "precision_at_k"]
