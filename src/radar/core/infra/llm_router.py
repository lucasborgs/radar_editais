"""
LLM router — mapeia tiers ('auto', 'fast', 'pro') para modelos concretos.

Fase 4 #24: a API aceita um `model_tier` opcional por request, permitindo ao
usuário escolher entre velocidade e qualidade. A camada central de mapeamento
fica neste módulo — quando A1 evoluir para multi-provider (Anthropic + Google),
basta alterar `_TIERS` aqui; nenhum call site precisa mudar.

Tiers atuais (OpenAI only, ADR A1):
  • fast  — gpt-4o-mini    (latência baixa, custo mínimo)
  • pro   — gpt-4o         (qualidade alta, custo ~10x maior)
  • auto  — gpt-4o-mini    (default; pode virar router heurístico no futuro)

Tiers futuros (ADR FUTURO multi-provider):
  • fast  — Haiku 4.5      (Anthropic) + fallback gpt-4o-mini
  • pro   — Sonnet 4.6     (Anthropic) + fallback gpt-4o
  • auto  — escolhido por tarefa (escrita ↦ pro, matching ↦ fast, ETL ↦ flash)
"""
from __future__ import annotations

import logging
import os
from typing import Literal

logger = logging.getLogger(__name__)

ModelTier = Literal["auto", "fast", "pro"]

_DEFAULT_TIER: ModelTier = "auto"


def _tier_map() -> dict[ModelTier, str]:
    """Mapeamento lazy: lê env vars para permitir override sem mexer no código.

    OPENAI_MODEL_FAST  (default: gpt-4o-mini)
    OPENAI_MODEL_PRO   (default: gpt-4o)
    OPENAI_MODEL_AUTO  (default: gpt-4o-mini, mesmo do fast)
    """
    fast = os.getenv("OPENAI_MODEL_FAST", "gpt-4o-mini")
    pro = os.getenv("OPENAI_MODEL_PRO", "gpt-4o-mini")
    auto = os.getenv("OPENAI_MODEL_AUTO", fast)
    return {"fast": fast, "pro": pro, "auto": auto}


def resolve_model(tier: str | None = None) -> str:
    """Tier (str | None) → nome do modelo OpenAI.

    Tier desconhecido ou None vira `auto`. Logs em debug para rastreabilidade
    sem poluir.
    """
    if not tier:
        tier = _DEFAULT_TIER
    tier_lower = tier.lower()
    mapping = _tier_map()
    if tier_lower not in mapping:
        logger.debug("resolve_model: tier desconhecido %r → auto", tier)
        tier_lower = "auto"
    return mapping[tier_lower]
