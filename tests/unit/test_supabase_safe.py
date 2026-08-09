import importlib.util
import sys
from pathlib import Path

from radar.core.environment import Environment


def _load_supabase_safe():
    path = Path(__file__).parents[2] / "scripts" / "supabase_safe.py"
    spec = importlib.util.spec_from_file_location("supabase_safe", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_remote_push_uses_explicit_database_url(monkeypatch):
    module = _load_supabase_safe()
    identity = type(
        "Identity",
        (),
        {
            "is_local": False,
            "environment": Environment.PRODUCTION,
            "project_ref": "production-ref",
        },
    )()
    commands = []

    monkeypatch.setenv("DATABASE_URL", "postgresql://production.example/db")
    monkeypatch.setattr(module, "load_environment_profile", lambda: None)
    monkeypatch.setattr(module, "database_identity", lambda: identity)
    monkeypatch.setattr(module, "assert_database_target", lambda *args, **kwargs: {})
    monkeypatch.setattr(module, "initialize_environment_metadata", lambda *args: None)
    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda command, **kwargs: commands.append(command),
    )
    monkeypatch.setattr(sys, "argv", ["supabase_safe.py", "push"])

    assert module.main() == 0
    assert commands == [
        ["supabase", "db", "push", "--db-url", "postgresql://production.example/db"]
    ]


def test_local_push_does_not_use_remote_database_url(monkeypatch):
    module = _load_supabase_safe()
    identity = type(
        "Identity",
        (),
        {"is_local": True, "environment": Environment.TEST, "project_ref": "local"},
    )()
    commands = []

    monkeypatch.setenv("DATABASE_URL", "postgresql://127.0.0.1/db")
    monkeypatch.setattr(module, "load_environment_profile", lambda: None)
    monkeypatch.setattr(module, "database_identity", lambda: identity)
    monkeypatch.setattr(module, "assert_database_target", lambda *args, **kwargs: {})
    monkeypatch.setattr(module, "initialize_environment_metadata", lambda *args: None)
    monkeypatch.setattr(
        module.subprocess,
        "run",
        lambda command, **kwargs: commands.append(command),
    )
    monkeypatch.setattr(sys, "argv", ["supabase_safe.py", "push"])

    assert module.main() == 0
    assert commands == [["supabase", "db", "push"]]
