"""Normalização pura de usage dos providers usados pelo runtime agêntico."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

CANONICAL_USAGE_KEYS = (
    "input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens",
)


def _get(value: Any, key: str) -> tuple[bool, Any]:
    if isinstance(value, Mapping):
        return key in value, value.get(key)
    try:
        return hasattr(value, key), getattr(value, key, None)
    except Exception:
        return False, None


def _int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def normalize_usage(value: Any) -> dict[str, int]:
    """Normalize only the raw provider/LangChain shapes in the installed runtime.

    Reported zeroes are retained; an omitted field remains absent.
    """
    if value is None:
        return {}
    result: dict[str, int] = {}
    input_found, input_value = _get(value, "input_tokens")
    output_found, output_value = _get(value, "output_tokens")
    prompt_found, prompt_value = _get(value, "prompt_tokens")
    completion_found, completion_value = _get(value, "completion_tokens")
    if input_found and _int(input_value) is not None:
        result["input_tokens"] = input_value
    elif prompt_found and _int(prompt_value) is not None:
        result["input_tokens"] = prompt_value
    if output_found and _int(output_value) is not None:
        result["output_tokens"] = output_value
    elif completion_found and _int(completion_value) is not None:
        result["output_tokens"] = completion_value

    prompt_details_found, prompt_details = _get(value, "prompt_tokens_details")
    if prompt_details_found:
        for source, target in (("cached_tokens", "cache_read_tokens"),
                               ("cache_write_tokens", "cache_write_tokens")):
            found, token_count = _get(prompt_details, source)
            if found and _int(token_count) is not None:
                result[target] = token_count
    for source, target in (
        ("cache_read_input_tokens", "cache_read_tokens"),
        ("cache_creation_input_tokens", "cache_write_tokens"),
    ):
        found, token_count = _get(value, source)
        if found and _int(token_count) is not None:
            result[target] = token_count

    details_found, details = _get(value, "input_token_details")
    if details_found:
        for source, target in (("cache_read", "cache_read_tokens"),
                               ("cache_creation", "cache_write_tokens")):
            found, token_count = _get(details, source)
            if found and _int(token_count) is not None:
                result[target] = token_count
    return result


def aggregate_usage(usages: list[Mapping[str, int] | None]) -> dict[str, int]:
    """Sum usage while preserving absence of metrics not reported by a provider."""
    result: dict[str, int] = {}
    for usage in usages:
        if not usage:
            continue
        for key in CANONICAL_USAGE_KEYS:
            if key in usage:
                result[key] = result.get(key, 0) + usage[key]
    return result
