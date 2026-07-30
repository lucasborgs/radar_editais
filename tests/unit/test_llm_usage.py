from types import SimpleNamespace

import pytest

from radar.core.infra import telemetry
from radar.core.llm.usage import aggregate_usage, normalize_usage

pytestmark = pytest.mark.unit


def test_openai_raw_read_and_write():
    usage = SimpleNamespace(
        prompt_tokens=100, completion_tokens=20,
        prompt_tokens_details=SimpleNamespace(cached_tokens=40, cache_write_tokens=12),
    )
    assert normalize_usage(usage) == {
        "input_tokens": 100, "output_tokens": 20,
        "cache_read_tokens": 40, "cache_write_tokens": 12,
    }


def test_openai_raw_zero_is_not_absence():
    usage = SimpleNamespace(
        prompt_tokens=0, completion_tokens=0,
        prompt_tokens_details=SimpleNamespace(cached_tokens=0, cache_write_tokens=0),
    )
    assert normalize_usage(usage)["cache_read_tokens"] == 0
    assert normalize_usage(usage)["cache_write_tokens"] == 0
    assert "cache_read_tokens" not in normalize_usage(SimpleNamespace(prompt_tokens=0, completion_tokens=0))


def test_anthropic_raw_read_and_creation():
    usage = SimpleNamespace(
        input_tokens=100, output_tokens=20,
        cache_read_input_tokens=40, cache_creation_input_tokens=12,
    )
    assert normalize_usage(usage)["cache_read_tokens"] == 40
    assert normalize_usage(usage)["cache_write_tokens"] == 12


def test_langchain_normalized_read_and_creation():
    usage = {
        "input_tokens": 100, "output_tokens": 20,
        "input_token_details": {"cache_read": 40, "cache_creation": 12},
    }
    assert normalize_usage(usage) == {
        "input_tokens": 100, "output_tokens": 20,
        "cache_read_tokens": 40, "cache_write_tokens": 12,
    }


def test_aggregate_multiple_messages_and_unknown_shape():
    assert aggregate_usage([
        {"input_tokens": 10, "output_tokens": 2, "cache_read_tokens": 4},
        {"input_tokens": 20, "output_tokens": 3, "cache_read_tokens": 0,
         "cache_write_tokens": 5},
        normalize_usage(SimpleNamespace(total_tokens=999)),
    ]) == {
        "input_tokens": 30, "output_tokens": 5,
        "cache_read_tokens": 4, "cache_write_tokens": 5,
    }


def test_turn_log_has_no_prompt_content(caplog):
    secret = "prompt-secret-profile-and-tool-result"
    telemetry.record_agent_turn(
        None, provider="openai", model="m", mode="explore", llm_calls=2,
        stop_reason="end_turn", runtime="langgraph", usage={"input_tokens": 1},
    )
    assert secret not in caplog.text
