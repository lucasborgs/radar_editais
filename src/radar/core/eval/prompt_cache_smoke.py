"""Smoke remoto, explícito e de custo mínimo para prompt cache OpenAI.

Não é uma suíte de avaliação: chama o provider duas vezes em cada transporte
(sync e streaming) com um prefixo sintético estável e uma variação curta no
fim.  Serve para verificar a métrica devolvida pelo provider, nunca para
alterar prompt, modelo ou política de cache da aplicação.
"""
from __future__ import annotations

import os
import time
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from typing import Any

from radar.core.llm.usage import normalize_usage

DEFAULT_MODEL = "gpt-4o-mini"
MIN_PREFIX_TOKENS = 1_280


@dataclass(frozen=True)
class SmokeSample:
    mode: str
    call: int
    input_tokens: int | None
    output_tokens: int | None
    cache_read_tokens: int | None
    cache_write_tokens: int | None
    ttft_seconds: float | None
    runtime_seconds: float
    stop_reason: str | None


def _text_from_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and isinstance(block.get("text"), str)
        )
    return ""


def _stable_prefix(model: str, target_tokens: int) -> str:
    """Generate only synthetic, deterministic text above the cache threshold."""
    try:
        import tiktoken

        try:
            encoder = tiktoken.encoding_for_model(model)
        except KeyError:
            encoder = tiktoken.get_encoding("o200k_base")
        unit = (
            "Este é um bloco sintético e público para medir cache de prompt. "
            "Não representa dados de usuário, empresa, edital ou produção. "
        )
        prefix = unit
        while len(encoder.encode(prefix)) < target_tokens:
            prefix += unit
        return prefix
    except Exception:
        # Fallback conservador para instalações sem tokenizer: texto bem acima
        # do limiar usual, sem depender de rede ou de conteúdo da aplicação.
        return (
            "Este é um bloco sintético e público para medir cache de prompt. "
            "Não representa dados de usuário, empresa, edital ou produção. "
        ) * 180


def _messages(prefix: str, call: int, mode: str) -> list[dict[str, str]]:
    # O marcador de modo evita que a primeira chamada sync aqueça a streaming
    # (ou vice-versa); entre chamadas do mesmo modo, apenas este sufixo varia.
    return [
        {"role": "system", "content": f"[cache-smoke:{mode}]\n{prefix}"},
        {
            "role": "user",
            "content": (
                f"Variação final {call}. Responda exatamente: OK. "
                "Não explique o bloco de teste."
            ),
        },
    ]


def _sample(
    *,
    mode: str,
    call: int,
    usage: Any,
    runtime_seconds: float,
    ttft_seconds: float | None,
    stop_reason: str | None,
) -> SmokeSample:
    normalized = normalize_usage(usage)
    return SmokeSample(
        mode=mode,
        call=call,
        input_tokens=normalized.get("input_tokens"),
        output_tokens=normalized.get("output_tokens"),
        cache_read_tokens=normalized.get("cache_read_tokens"),
        cache_write_tokens=normalized.get("cache_write_tokens"),
        ttft_seconds=ttft_seconds,
        runtime_seconds=runtime_seconds,
        stop_reason=stop_reason,
    )


def _run_sync(client: Any, model: str, prefix: str, call: int) -> SmokeSample:
    started = time.monotonic()
    response = client.chat.completions.create(
        model=model,
        messages=_messages(prefix, call, "sync"),
        max_tokens=8,
        temperature=0,
    )
    runtime = time.monotonic() - started
    choices = getattr(response, "choices", [])
    stop_reason = getattr(choices[0], "finish_reason", None) if choices else None
    # Chat Completions sync entrega apenas a resposta completa: TTFT não é
    # observável neste transporte e não deve ser confundido com latência total.
    return _sample(
        mode="sync",
        call=call,
        usage=getattr(response, "usage", None),
        runtime_seconds=runtime,
        ttft_seconds=None,
        stop_reason=stop_reason,
    )


def _run_stream(client: Any, model: str, prefix: str, call: int) -> SmokeSample:
    started = time.monotonic()
    first_text_at: float | None = None
    usage = None
    stop_reason = None
    stream: Iterable[Any] = client.chat.completions.create(
        model=model,
        messages=_messages(prefix, call, "streaming"),
        max_tokens=8,
        temperature=0,
        stream=True,
        stream_options={"include_usage": True},
    )
    for chunk in stream:
        chunk_usage = getattr(chunk, "usage", None)
        if chunk_usage is not None:
            usage = chunk_usage
        choices = getattr(chunk, "choices", [])
        if not choices:
            continue
        choice = choices[0]
        if first_text_at is None and _text_from_content(getattr(choice.delta, "content", None)):
            first_text_at = time.monotonic()
        if getattr(choice, "finish_reason", None):
            stop_reason = choice.finish_reason
    runtime = time.monotonic() - started
    return _sample(
        mode="streaming",
        call=call,
        usage=usage,
        runtime_seconds=runtime,
        ttft_seconds=(first_text_at - started) if first_text_at is not None else None,
        stop_reason=stop_reason,
    )


def run(*, model: str | None = None, prefix_tokens: int = MIN_PREFIX_TOKENS) -> list[SmokeSample]:
    """Run the minimal remote experiment. The caller owns explicit opt-in."""
    if not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY ausente; smoke remoto não pode ser executado.")
    if prefix_tokens < MIN_PREFIX_TOKENS:
        raise ValueError(f"prefix_tokens deve ser >= {MIN_PREFIX_TOKENS}.")

    from radar.core.llm.llm_client import make_client

    selected_model = model or os.getenv("OPENAI_MODEL", DEFAULT_MODEL)
    client = make_client()
    prefix = _stable_prefix(selected_model, prefix_tokens)
    samples: list[SmokeSample] = []
    for _mode, runner in (("sync", _run_sync), ("streaming", _run_stream)):
        for call in (1, 2):
            samples.append(runner(client, selected_model, prefix, call))
    return samples


def report(samples: list[SmokeSample], *, model: str) -> str:
    """Format a content-free, copyable operational report."""
    lines = [f"provider=openai model={model}", ""]
    for sample in samples:
        values = asdict(sample)
        fields = " ".join(f"{key}={value}" for key, value in values.items())
        lines.append(fields)

    reads = [s for s in samples if s.call >= 2 and (s.cache_read_tokens or 0) > 0]
    reported = [s for s in samples if s.cache_read_tokens is not None]
    if reads:
        verdict = "HIT confirmado: cache_read_tokens > 0 em chamada posterior."
    elif reported:
        verdict = "Sem hit: provider reportou cache_read_tokens, mas permaneceu em zero."
    else:
        verdict = "Inconclusivo: provider não expôs cache_read_tokens neste endpoint/modelo."
    lines.extend(("", f"veredito={verdict}", "custo=nao_exposto_pelo_payload"))
    return "\n".join(lines)
