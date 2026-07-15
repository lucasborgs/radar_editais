from __future__ import annotations

import pytest

from core.eval import __main__ as cli


@pytest.mark.parametrize(
    ("status", "exit_code"),
    [("passed", 0), ("diagnostic", 0), ("failed", 1), ("skipped", 1), ("error", 2)],
)
def test_cli_exit_codes(monkeypatch, status, exit_code):
    monkeypatch.setattr(cli, "get_suite", lambda _: object())
    monkeypatch.setattr(cli, "run_suite", lambda *args, **kwargs: {"status": status})
    assert cli.main(["run", "matching"]) == exit_code


def test_cli_gate_forwards_intent_and_publication(monkeypatch):
    calls = {}
    monkeypatch.setattr(cli, "get_suite", lambda _: object())

    def fake_run(*args, **kwargs):
        calls.update(kwargs)
        return {"status": "passed"}

    monkeypatch.setattr(cli, "run_suite", fake_run)
    assert cli.main(["gate", "extraction", "--publish"]) == 0
    assert calls == {"intent": "gate", "publish": True, "limit": None}


def test_legacy_command_is_local_run(monkeypatch, capsys):
    calls = {}
    monkeypatch.setattr(cli, "get_suite", lambda _: object())

    def fake_run(*args, **kwargs):
        calls.update(kwargs)
        return {"status": "diagnostic"}

    monkeypatch.setattr(cli, "run_suite", fake_run)
    assert cli.main(["matching", "--no-push", "--limit", "1"]) == 0
    assert calls == {"intent": "run", "publish": False, "limit": 1}
    assert "deprecated" in capsys.readouterr().err
