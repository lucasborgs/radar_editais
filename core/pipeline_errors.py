"""
Pipeline error taxonomy (Pipeline #28).

Falhas do pipeline ETL agora são tipadas — permite retry diferenciado por
causa e alerta de degradação por categoria. Antes, scrapers/etl falhavam
silenciosamente ou com `Exception` genérico, e a lógica de retry era cega
(retentava timeouts E parse errors, gastando recursos em causas permanentes).

Uso típico em código de pipeline:

    from core.pipeline_errors import (
        ParseError, TimeoutError, log_pipeline_error,
    )

    try:
        text = scraper._fetch_page(url)
    except requests.Timeout as e:
        raise TimeoutError(f"FINEP request timeout: {url}") from e
    except Exception as e:
        raise ParseError(f"HTML inesperado em {url}") from e

E em handler central (`scripts/run_all.py`, jobs procrastinate):

    try:
        run_scraper(...)
    except PipelineError as e:
        log_pipeline_error(source="FINEP", error=e, context={"url": url})
        if e.retryable:
            raise  # procrastinate retenta
        # else: skip silently, log já registrou

ADR Gap 7 (RADAR §3.7).
"""
from __future__ import annotations

import logging
import traceback
from typing import ClassVar

logger = logging.getLogger(__name__)


# =============================================================================
# Exceções tipadas
# =============================================================================

class PipelineError(Exception):
    """Base para todas as falhas tipadas de pipeline.

    Atributos:
        category   — categoria (uma das do CHECK constraint em pipeline_errors)
        retryable  — se a falha deve disparar retry (procrastinate ou run_all)
    """
    category: ClassVar[str] = "unknown"
    retryable: ClassVar[bool] = False


class TimeoutError(PipelineError):  # noqa: A001 - intentional shadow of builtins.TimeoutError
    """Request lento ou conexão pendurada. Retry com backoff é apropriado."""
    category = "timeout"
    retryable = True


class ParseError(PipelineError):
    """HTML/JSON com estrutura diferente do esperado. NÃO retentar — requer
    intervenção manual no extrator."""
    category = "parse_error"
    retryable = False


class SchemaViolationError(PipelineError):
    """Edital com campos fora do vocabulário autorizado (WIKI.md). Entra em
    quarentena para revisão; não retenta."""
    category = "schema_violation"
    retryable = False


class LLMRefusalError(PipelineError):
    """LLM recusou processar conteúdo (filtro de segurança, política). Skip
    com flag — não retenta."""
    category = "llm_refusal"
    retryable = False


class DuplicateError(PipelineError):
    """Hash do conteúdo já existe no bronze. Skip silencioso (não-erro real)."""
    category = "duplicate"
    retryable = False


# =============================================================================
# Persistência
# =============================================================================

def log_pipeline_error(
    source: str,
    error: Exception,
    edital_id: str | None = None,
    context: dict | None = None,
) -> None:
    """Persiste a falha em pipeline_errors. Robusto a falhas (não levanta).

    Aceita PipelineError tipada (categoria derivada) ou Exception genérica
    (categoria 'unknown'). Sempre captura traceback se houver __traceback__.

    Args:
        source: nome da fonte ("FINEP", "FAPESP", etc.)
        error: a exception
        edital_id: id do edital se já identificado quando o erro ocorreu
        context: dict adicional (url, payload, parâmetros) — serializável como JSON
    """
    if isinstance(error, PipelineError):
        category = error.category
    else:
        category = "unknown"

    tb = ""
    if error.__traceback__ is not None:
        tb = "".join(traceback.format_exception(type(error), error, error.__traceback__))

    try:
        from core.db import get_supabase_service
        db = get_supabase_service()
        db.table("pipeline_errors").insert({
            "source": source,
            "edital_id": edital_id,
            "category": category,
            "message": str(error) or type(error).__name__,
            "traceback": tb or None,
            "context": context or {},
        }).execute()
    except Exception as log_err:
        # Falha ao logar não pode mascarar a falha original. Log local apenas.
        logger.error(
            "Falha ao persistir pipeline_error (%s/%s): %s — original error: %s",
            source, category, log_err, error,
        )


def classify_requests_error(error: Exception) -> PipelineError:
    """Helper: converte exception comum de `requests` em PipelineError tipada.

    Útil dentro de scrapers — pega RequestException e retorna o tipo certo.
    """
    import requests
    if isinstance(error, requests.Timeout):
        return TimeoutError(str(error))
    if isinstance(error, requests.ConnectionError):
        return TimeoutError(str(error))  # connection refused/reset → tratamos como retryable
    if isinstance(error, requests.HTTPError):
        status = error.response.status_code if error.response is not None else 0
        if status in (408, 429, 500, 502, 503, 504):
            return TimeoutError(f"HTTP {status}: {error}")
        return ParseError(f"HTTP {status}: {error}")  # 4xx ≠ 408/429 = permanente
    if isinstance(error, requests.RequestException):
        return TimeoutError(str(error))  # default conservador
    # Não classificável — retorna unknown PipelineError
    err = PipelineError(str(error))
    return err


__all__ = [
    "PipelineError",
    "TimeoutError",
    "ParseError",
    "SchemaViolationError",
    "LLMRefusalError",
    "DuplicateError",
    "log_pipeline_error",
    "classify_requests_error",
]
