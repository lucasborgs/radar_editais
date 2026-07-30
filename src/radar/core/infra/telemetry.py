"""Telemetria Langfuse para o runtime agêntico (LangGraph) e serviços LLM.

Desde a Etapa 6 da migração LangGraph, os spans por-step (chamadas LLM e tools)
vêm da `langfuse.langchain.CallbackHandler` NATIVA — passada no `config` de cada
invocação do grafo (`make_callback_handler`), com timing real e usage automático.
Este módulo mantém:
  • agent_run            — span-raiz nomeado (as_type=agent) que ancora o trace e
                           sob o qual o handler aninha os spans do grafo;
  • make_callback_handler — factory da CallbackHandler (None se desabilitado);
  • current_trace_context — captura o parent para aninhar runs cross-thread (o
                           subagente/critic roda noutra thread → contextvar não cruza);
  • record_usage         — extrai usage de respostas CRUAS de SDK (fallback/legado;
                           o handler cobre o caminho do grafo).

Quando LANGFUSE_PUBLIC_KEY/LANGFUSE_SECRET_KEY não estão definidas, tudo vira no-op
silencioso — dev local sem conta Langfuse não paga overhead nem quebra. Em prod,
basta setar as duas vars que toda trace do runtime passa a ser exportada.

Custo (perf): client Langfuse usa OpenTelemetry batch exporter — chamadas
não-bloqueantes. Falhas de rede pro Langfuse jamais derrubam request do
usuário (try/except em cada span/factory, fallback debug log).
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
    trace_context: dict | None = None,
):
    """Span raiz de uma invocação de agente (as_type=agent).

    Use em volta da invocação do grafo — abre o trace, define o span como o
    observation corrente (a `CallbackHandler` de `make_callback_handler` aninha os
    spans nativos do LangGraph por baixo dele) e fecha quando o agente termina (com
    ou sem exceção). No-op quando Langfuse não está habilitado.

    `trace_context` (Etapa 6): ao receber `{"trace_id", "parent_span_id"}` de um run
    pai (capturado por `current_trace_context`), enraíza este span sob aquele parent
    REMOTO — é assim que o subagente (critic), que roda noutra thread/loop, aninha sob
    o turno da WritingSession (o contextvar OTel não cruza a fronteira de thread).
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
            trace_context=trace_context or None,
        ) as span:
            yield span
    except Exception as e:
        logger.debug("agent_run span '%s' falhou: %s", name, e)
        yield None


@contextmanager
def llm_span(
    name: str,
    *,
    model: str | None = None,
    input: Any = None,
    metadata: dict | None = None,
):
    """Span de generation para chamadas LLM 1-shot FORA do grafo (spec PR5).

    Cobre os call sites sem telemetria (F11): _call_openai da WritingSession,
    reflection_service, checklist_service, HyDE, contextual retrieval (1 span por
    batch). Com `model` + usage (via `record_usage` ou
    `span.update(usage_details=...)`), o Langfuse precifica a chamada → custo por
    sessão/workspace vem do metadata propagado pelo caller.

    Uso:
        with telemetry.llm_span("hyde", model=model, metadata={...}) as span:
            response = client.chat.completions.create(...)
            telemetry.record_usage(span, response)

    Contratos: no-op (yield None) sem Langfuse; falha de telemetria nunca derruba
    a chamada; exceção do CALLER propaga intacta (o span fecha marcado com erro).
    """
    if not is_enabled():
        yield None
        return
    cm = None
    span = None
    try:
        cm = _client.start_as_current_observation(
            name=name,
            as_type="generation",
            input=input,
            metadata=metadata or {},
        )
        span = cm.__enter__()
        if model is not None:
            span.update(model=model)
    except Exception as e:
        logger.debug("llm_span '%s' falhou ao abrir: %s", name, e)
        cm, span = None, None
    try:
        yield span
    except BaseException as e:
        if cm is not None:
            try:
                cm.__exit__(type(e), e, e.__traceback__)
            except Exception:
                logger.debug("llm_span '%s' falhou ao fechar com erro", name)
        raise
    if cm is not None:
        try:
            cm.__exit__(None, None, None)
        except Exception as e:
            logger.debug("llm_span '%s' falhou ao fechar: %s", name, e)


def current_trace_context() -> dict | None:
    """Captura o contexto da trace corrente (`{"trace_id", "parent_span_id"}`) para
    propagar a um run que roda noutra thread/loop (subagente). None se desabilitado
    ou sem observation ativo. Espelha o `TraceContext` do Langfuse v4."""
    if not is_enabled():
        return None
    try:
        trace_id = _client.get_current_trace_id()
        if not trace_id:
            return None
        ctx: dict = {"trace_id": trace_id}
        obs_id = _client.get_current_observation_id()
        if obs_id:
            ctx["parent_span_id"] = obs_id
        return ctx
    except Exception as e:  # noqa: BLE001
        logger.debug("current_trace_context falhou: %s", e)
        return None


def make_callback_handler(trace_context: dict | None = None):
    """Factory da `langfuse.langchain.CallbackHandler` (Etapa 6) — passada no
    `config={"callbacks": [...]}` de cada invocação do grafo. Gera spans nativos
    (chain/llm/tool) com timing real e usage automático, aninhados sob o observation
    corrente (o `agent_run`). None se Langfuse desabilitado → grafo roda sem overhead.

    `trace_context` raramente é necessário aqui: o aninhamento já vem do observation
    corrente via contextvar. Fica disponível para casos cross-thread onde o handler
    precise apontar o parent remoto explicitamente.
    """
    if not is_enabled():
        return None
    try:
        from langfuse.langchain import CallbackHandler

        return CallbackHandler(trace_context=trace_context or None)
    except Exception as e:  # noqa: BLE001 — telemetria nunca derruba a request
        logger.debug("make_callback_handler falhou: %s", e)
        return None


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

        from radar.core.llm.usage import normalize_usage
        canonical = normalize_usage(usage)
        if not canonical:
            return
        details: dict[str, int] = {}
        for source, target in (
            ("input_tokens", "input"),
            ("output_tokens", "output"),
            ("cache_read_tokens", "cache_read"),
            ("cache_write_tokens", "cache_write"),
        ):
            if source in canonical:
                details[target] = canonical[source]
        # Preserve the existing Langfuse reasoning detail; it is orthogonal to
        # the four canonical cache/usage metrics.
        reasoning_details = getattr(usage, "completion_tokens_details", None)
        reasoning = getattr(reasoning_details, "reasoning_tokens", None)
        if isinstance(reasoning, int) and not isinstance(reasoning, bool) and reasoning:
            details["reasoning"] = reasoning
        span.update(usage_details=details)
    except Exception as e:  # pragma: no cover - guard defensivo
        logger.debug("record_usage falhou (ignorado): %s", e)


def record_agent_turn(
    span: Any,
    *,
    provider: str,
    model: str,
    mode: str | None,
    llm_calls: int,
    stop_reason: str,
    runtime: str,
    usage: dict[str, int],
) -> None:
    """Record an aggregate turn without prompt, tool, or response content."""
    payload = {
        "provider": provider,
        "model": model,
        "mode": mode,
        "runtime": runtime,
        "llm_calls": llm_calls,
        "stop_reason": stop_reason,
        "usage": usage,
    }
    logger.info("llm_turn_metrics %s", payload)
    if span is None:
        return
    try:
        # Keep aggregate metrics in structured metadata. Native Langfuse child
        # generations remain the source of cost details; this avoids duplicating
        # costs on the root agent span and avoids relying on root usage_details.
        span.update(metadata={"turn_metrics": payload})
    except Exception as e:  # pragma: no cover - telemetry must never fail a turn
        logger.debug("record_agent_turn falhou (ignorado): %s", e)


def flush() -> None:
    """Força flush de traces pendentes. Chamar antes de shutdown do processo
    (workers procrastinate, scripts CLI). Em FastAPI, hookar no shutdown event."""
    if is_enabled():
        try:
            _client.flush()
        except Exception as e:
            logger.warning("Langfuse flush falhou: %s", e)
