from __future__ import annotations

from types import SimpleNamespace

from radar.core.eval import prompt_cache_smoke as smoke


def test_report_distinguishes_hit_from_absent_metric():
    hit = smoke.SmokeSample("sync", 2, 1300, 2, 1024, None, None, 0.2, "stop")
    absent = smoke.SmokeSample("streaming", 2, 1300, 2, None, None, 0.1, 0.2, "stop")

    assert "HIT confirmado" in smoke.report([hit], model="model")
    assert "Inconclusivo" in smoke.report([absent], model="model")


def test_sync_normalizes_usage_and_does_not_invent_ttft(monkeypatch):
    usage = SimpleNamespace(
        prompt_tokens=1280,
        completion_tokens=2,
        prompt_tokens_details=SimpleNamespace(cached_tokens=1024),
    )
    response = SimpleNamespace(
        usage=usage,
        choices=[SimpleNamespace(finish_reason="stop")],
    )
    client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **kwargs: response))
    )

    sample = smoke._run_sync(client, "gpt-4o-mini", "prefix", 1)

    assert sample.input_tokens == 1280
    assert sample.cache_read_tokens == 1024
    assert sample.ttft_seconds is None


def test_stream_uses_final_usage_and_first_text_ttft(monkeypatch):
    usage = SimpleNamespace(input_tokens=1280, output_tokens=2)
    chunks = iter([
        SimpleNamespace(usage=None, choices=[SimpleNamespace(
            delta=SimpleNamespace(content="OK"), finish_reason=None,
        )]),
        SimpleNamespace(usage=usage, choices=[SimpleNamespace(
            delta=SimpleNamespace(content=""), finish_reason="stop",
        )]),
    ])
    client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **kwargs: chunks))
    )

    sample = smoke._run_stream(client, "gpt-4o-mini", "prefix", 1)

    assert sample.input_tokens == 1280
    assert sample.ttft_seconds is not None
    assert sample.stop_reason == "stop"
