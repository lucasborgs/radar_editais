"""Telemetria Langfuse para agent_runtime e serviços LLM.

Três níveis de span (mapeados a tipos semânticos do Langfuse 4.x):
  • agent_run        — invocação inteira de um agente (raiz da trace)
  • llm_generation   — uma chamada LLM dentro do loop do agente (1 step)
  • tool_call        — execução de uma tool (1 step)

Quando LANGFUSE_PUBLIC_KEY/LANGFUSE_SECRET_KEY não estão definidas, os
context managers viram no-op silencioso — dev local sem conta Langfuse
não paga overhead nem quebra. Em prod, basta setar as duas vars que toda
trace do agent runtime passa a ser exportada.

Custo (perf): client Langfuse usa OpenTelemetry batch exporter — chamadas
não-bloqueantes. Falhas de rede pro Langfuse jamais derrubam request do
usuário (try/except em cada span, fallback debug log).
"""
from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from typing import Any

logger = logging.getLogger(__name__)


def _is_configured() -> bool:
    return bool(
        os.getenv("LANGFUSE_PUBLIC_KEY") and os.getenv("LANGFUSE_SECRET_KEY")
    )


_ENABLED = _is_configured()
_client: Any = None

if _ENABLED:
    try:
        from langfuse import Langfuse

        _client = Langfuse(
            public_key=os.environ["LANGFUSE_PUBLIC_KEY"],
            secret_key=os.environ["LANGFUSE_SECRET_KEY"],
            host=os.getenv("LANGFUSE_HOST", "https://cloud.langfuse.com"),
        )
        logger.info(
            "Langfuse habilitado: host=%s",
            os.getenv("LANGFUSE_HOST", "cloud.langfuse.com"),
        )
    except Exception as e:
        logger.warning("Langfuse falhou ao inicializar: %s — telemetria desativada", e)
        _ENABLED = False
        _client = None


def is_enabled() -> bool:
    return _ENABLED and _client is not None


@contextmanager
def agent_run(
    name: str,
    *,
    input: Any = None,
    metadata: dict | None = None,
):
    """Span raiz de uma invocação de agente (as_type=agent).

    Use em volta de `run_agent(...)` — abre o trace e fecha quando o agente
    termina (com ou sem exceção). No-op quando Langfuse não está habilitado.
    """
    if not is_enabled():
        yield None
        return
    try:
        with _client.start_as_current_observation(
            name=name,
            as_type="agent",
            input=input,
            metadata=metadata or {},
        ) as span:
            yield span
    except Exception as e:
        logger.debug("agent_run span '%s' falhou: %s", name, e)
        yield None


@contextmanager
def llm_generation(
    name: str,
    *,
    model: str,
    input: Any = None,
    metadata: dict | None = None,
):
    """Span de uma chamada LLM dentro do loop do agente (as_type=generation).

    Captura input (messages), model e usage_details (atualizar via span.update
    com usage_details={'input': N, 'output': M} após a chamada).
    """
    if not is_enabled():
        yield None
        return
    try:
        with _client.start_as_current_observation(
            name=name,
            as_type="generation",
            model=model,
            input=input,
            metadata=metadata or {},
        ) as span:
            yield span
    except Exception as e:
        logger.debug("llm_generation span '%s' falhou: %s", name, e)
        yield None


@contextmanager
def tool_call(
    name: str,
    *,
    input: Any = None,
    metadata: dict | None = None,
):
    """Span de uma execução de tool dentro do loop do agente (as_type=tool)."""
    if not is_enabled():
        yield None
        return
    try:
        with _client.start_as_current_observation(
            name=name,
            as_type="tool",
            input=input,
            metadata=metadata or {},
        ) as span:
            yield span
    except Exception as e:
        logger.debug("tool_call span '%s' falhou: %s", name, e)
        yield None


def flush() -> None:
    """Força flush de traces pendentes. Chamar antes de shutdown do processo
    (workers procrastinate, scripts CLI). Em FastAPI, hookar no shutdown event."""
    if is_enabled():
        try:
            _client.flush()
        except Exception as e:
            logger.warning("Langfuse flush falhou: %s", e)
