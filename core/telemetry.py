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


def record_usage(span: Any, response: Any) -> None:
    """Registra usage_details num span de generation a partir da resposta LLM.

    Por que existe: sem usage_details consistente nos spans, o Langfuse não
    consegue calcular custo por turno/sessão (ele precisa de input/output tokens
    + model pra precificar). Antes cada call site lembrava — ou esquecia — de
    montar o dict manualmente, com keys divergentes. Este helper centraliza a
    extração e garante o contrato esperado pelo Langfuse: usage_details com keys
    canônicas 'input' e 'output' (e, quando disponíveis, 'cache_read'/'reasoning').

    Detecta o shape pela presença dos atributos (defensivo, sem isinstance):
      • OpenAI Chat Completions: response.usage.prompt_tokens / completion_tokens
        (+ prompt_tokens_details.cached_tokens, completion_tokens_details.reasoning_tokens)
      • Anthropic Messages:       response.usage.input_tokens / output_tokens
        (+ cache_read_input_tokens / cache_creation_input_tokens)

    Nunca levanta exceção: no-op se span é None, se response não tem usage, ou
    se qualquer acesso/atualização falhar. Telemetria jamais derruba a request.
    """
    if span is None:
        return
    try:
        usage = getattr(response, "usage", None)
        if usage is None:
            return

        details: dict[str, int] = {}

        # Shape OpenAI: prompt_tokens / completion_tokens
        prompt = getattr(usage, "prompt_tokens", None)
        completion = getattr(usage, "completion_tokens", None)
        # Shape Anthropic: input_tokens / output_tokens
        in_tok = getattr(usage, "input_tokens", None)
        out_tok = getattr(usage, "output_tokens", None)

        if prompt is not None or completion is not None:
            details["input"] = prompt or 0
            details["output"] = completion or 0
            prompt_details = getattr(usage, "prompt_tokens_details", None)
            cached = getattr(prompt_details, "cached_tokens", None)
            if cached:
                details["cache_read"] = cached
            completion_details = getattr(usage, "completion_tokens_details", None)
            reasoning = getattr(completion_details, "reasoning_tokens", None)
            if reasoning:
                details["reasoning"] = reasoning
        elif in_tok is not None or out_tok is not None:
            details["input"] = in_tok or 0
            details["output"] = out_tok or 0
            cache_read = getattr(usage, "cache_read_input_tokens", None)
            if cache_read:
                details["cache_read"] = cache_read
            cache_creation = getattr(usage, "cache_creation_input_tokens", None)
            if cache_creation:
                details["cache_write"] = cache_creation
        else:
            # Sem nenhum dos shapes conhecidos — nada a registrar.
            return

        span.update(usage_details=details)
    except Exception as e:  # pragma: no cover - guard defensivo
        logger.debug("record_usage falhou (ignorado): %s", e)


@contextmanager
def llm_generation(
    name: str,
    *,
    model: str,
    input: Any = None,
    metadata: dict | None = None,
):
    """Span de uma chamada LLM dentro do loop do agente (as_type=generation).

    Captura input (messages), model e usage_details. Para registrar o usage
    após a chamada, use `record_usage(span, response)` com a resposta crua do
    SDK (OpenAI ou Anthropic) — ele extrai input/output (+ cache/reasoning) no
    formato que o Langfuse usa pra precificar.
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
