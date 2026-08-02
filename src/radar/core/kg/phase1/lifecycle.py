"""Lifecycle de produção da projeção da Fase 1 (KG-P1B-2).

`refresh_after_gold` roda o produtor real `radar.core.kg.phase1.ingest.build()`
após o COMMIT do gold — best-effort, idempotente por `source_hash` (skip quando
o gold não mudou) e com retorno/log SEMPRE sanitizados: apenas trigger, outcome,
duração, generation/contagens (ints) e categoria/tipo do erro. Nunca expõe DSN,
URL, SQL, mensagem de exceção, perfil, conteúdo documental ou segredo.

Ativação explícita via `KG_PHASE1_AUTO_REFRESH_ENABLED=true` (default off). Sem a
flag o refresh NÃO abre conexão e NÃO toca o banco — degrada para outcome
``disabled`` no próprio retorno. Falha NUNCA levanta: o chamador (ETL/promoção)
não pode ter sua run quebrada por este passo (o produtor já registra a geração
``failed`` no ledger, best-effort).

Autoridade: src/radar/core/kg/phase1/PROJECTION.md + relatório KG-P1B2.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any

logger = logging.getLogger(__name__)

AUTO_REFRESH_FLAG = "KG_PHASE1_AUTO_REFRESH_ENABLED"

_OUTCOMES = frozenset({"disabled", "built", "skipped", "failed"})
_COUNT_KEYS = ("nodes", "edges", "quality_nodes", "communities", "similar_a", "potencial_parceria")


def auto_refresh_enabled() -> bool:
    """Default OFF. ``true`` liga o refresh automático pós-gold."""
    return os.getenv(AUTO_REFRESH_FLAG, "false").lower() == "true"


def refresh_after_gold(*, trigger: str) -> dict[str, Any]:
    """Reconstrói a projeção da Fase 1 depois do commit do gold, se habilitado.

    Contrato (KG-P1B-2):

    - flag off → ``{"trigger", "outcome": "disabled"}`` — sem conexão, sem log
      de tool, sem tocar o banco;
    - idempotente: ``build(skip_unchanged=True)`` compara ``source_hash`` e
      mantém a geração corrente quando o gold não mudou (outcome ``skipped``);
    - best-effort: exceção do build → outcome ``failed`` (categoria + tipo do
      erro, NUNCA a mensagem) — o chamador nunca vê exceção;
    - sanitizado: retorno contém apenas ``trigger``, ``outcome``,
      ``duration_ms``, ``generation``/contagens (ints) e, em falha,
      ``error: {category, type}``. Nenhum outro conteúdo do build é repassado
      (em particular ``source_hash`` fica de fora).
    """
    started = time.perf_counter()
    if not auto_refresh_enabled():
        return {"trigger": trigger, "outcome": "disabled"}

    from radar.core.kg.phase1 import ingest

    try:
        out = ingest.build(skip_unchanged=True)
    except Exception as exc:  # noqa: BLE001
        category = ingest._category_of(exc)
        result = {
            "trigger": trigger,
            "outcome": "failed",
            "duration_ms": int((time.perf_counter() - started) * 1000),
            "error": {"category": category, "type": type(exc).__name__},
        }
        logger.info(
            "kg_phase1 refresh outcome=%s trigger=%s duration_ms=%s category=%s type=%s",
            "failed", trigger, result["duration_ms"], category, type(exc).__name__,
        )
        return result

    outcome = "skipped" if out.get("skipped") else "built"
    result: dict[str, Any] = {
        "trigger": trigger,
        "outcome": outcome,
        "duration_ms": int((time.perf_counter() - started) * 1000),
    }
    if isinstance(out.get("generation"), int):
        result["generation"] = out["generation"]
    for key in _COUNT_KEYS:
        if isinstance(out.get(key), int):
            result[key] = out[key]

    logger.info(
        "kg_phase1 refresh outcome=%s trigger=%s duration_ms=%s generation=%s",
        outcome, trigger, result["duration_ms"], result.get("generation"),
    )
    return result
