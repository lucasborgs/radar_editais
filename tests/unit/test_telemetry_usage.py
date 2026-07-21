"""Testes do helper radar.core.infra.telemetry.record_usage.

Garante que usage_details é extraído de forma consistente das respostas LLM
(OpenAI e Anthropic) e registrado no span no formato que o Langfuse usa pra
precificar (keys canônicas 'input'/'output' + cache/reasoning). Cobre os
contratos defensivos: span None, response sem usage, shape desconhecido e
span.update que levanta — nenhum deve propagar exceção.

Testes puros: fakes simples, sem rede/Langfuse real.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from radar.core.infra import telemetry  # noqa: E402

pytestmark = pytest.mark.unit

# ============================================================================
# Fakes
# ============================================================================

class _FakeSpan:
    """Span fake: captura os kwargs do último .update()."""

    def __init__(self):
        self.updates: list[dict] = []

    def update(self, **kwargs):
        self.updates.append(kwargs)

    @property
    def last_usage(self):
        for u in reversed(self.updates):
            if "usage_details" in u:
                return u["usage_details"]
        return None


def _openai_response(
    prompt=100, completion=20, cached=None, reasoning=None
):
    """Resposta OpenAI-shape: usage.prompt_tokens / completion_tokens."""
    usage_kwargs = {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
    }
    if cached is not None:
        usage_kwargs["prompt_tokens_details"] = SimpleNamespace(
            cached_tokens=cached
        )
    if reasoning is not None:
        usage_kwargs["completion_tokens_details"] = SimpleNamespace(
            reasoning_tokens=reasoning
        )
    return SimpleNamespace(usage=SimpleNamespace(**usage_kwargs))


def _anthropic_response(
    input_tokens=150, output_tokens=30, cache_read=None, cache_creation=None
):
    """Resposta Anthropic-shape: usage.input_tokens / output_tokens."""
    usage_kwargs = {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
    }
    if cache_read is not None:
        usage_kwargs["cache_read_input_tokens"] = cache_read
    if cache_creation is not None:
        usage_kwargs["cache_creation_input_tokens"] = cache_creation
    return SimpleNamespace(usage=SimpleNamespace(**usage_kwargs))


# ============================================================================
# Extração OpenAI-shape
# ============================================================================

def test_openai_shape_basic():
    span = _FakeSpan()
    telemetry.record_usage(span, _openai_response(prompt=100, completion=20))
    assert span.last_usage == {"input": 100, "output": 20}


def test_openai_shape_with_cached_and_reasoning():
    span = _FakeSpan()
    telemetry.record_usage(
        span, _openai_response(prompt=100, completion=20, cached=40, reasoning=8)
    )
    assert span.last_usage == {
        "input": 100,
        "output": 20,
        "cache_read": 40,
        "reasoning": 8,
    }


def test_openai_zero_cached_omitted():
    """cached/reasoning zerados não viram keys (ruído)."""
    span = _FakeSpan()
    telemetry.record_usage(
        span, _openai_response(prompt=10, completion=5, cached=0, reasoning=0)
    )
    assert span.last_usage == {"input": 10, "output": 5}


# ============================================================================
# Extração Anthropic-shape
# ============================================================================

def test_anthropic_shape_basic():
    span = _FakeSpan()
    telemetry.record_usage(
        span, _anthropic_response(input_tokens=150, output_tokens=30)
    )
    assert span.last_usage == {"input": 150, "output": 30}


def test_anthropic_shape_with_cache():
    span = _FakeSpan()
    telemetry.record_usage(
        span,
        _anthropic_response(
            input_tokens=150, output_tokens=30, cache_read=60, cache_creation=12
        ),
    )
    assert span.last_usage == {
        "input": 150,
        "output": 30,
        "cache_read": 60,
        "cache_write": 12,
    }


# ============================================================================
# Contratos defensivos — nunca propaga, no-op quando faltam dados
# ============================================================================

def test_span_none_is_noop():
    # Não deve levantar nem precisar de span.
    telemetry.record_usage(None, _openai_response())


def test_response_without_usage_is_noop():
    span = _FakeSpan()
    telemetry.record_usage(span, SimpleNamespace(usage=None))
    assert span.last_usage is None
    # Nenhum update com usage_details foi emitido.
    assert all("usage_details" not in u for u in span.updates)


def test_response_missing_usage_attr_is_noop():
    span = _FakeSpan()
    telemetry.record_usage(span, SimpleNamespace())  # sem .usage
    assert span.last_usage is None


def test_unknown_usage_shape_is_noop():
    """usage existe mas sem nenhuma das keys conhecidas — nada registrado."""
    span = _FakeSpan()
    weird = SimpleNamespace(usage=SimpleNamespace(total_tokens=999))
    telemetry.record_usage(span, weird)
    assert span.last_usage is None


def test_update_raising_does_not_propagate():
    class _BoomSpan:
        def update(self, **kwargs):
            raise RuntimeError("langfuse caiu")

    # Não deve propagar a exceção.
    telemetry.record_usage(_BoomSpan(), _openai_response())
