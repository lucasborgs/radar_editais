from __future__ import annotations

import pytest

from core import environment as env


@pytest.fixture(autouse=True)
def _clean_environment(monkeypatch):
    for name in (
        "ENVIRONMENT",
        "DATABASE_URL",
        "SUPABASE_URL",
        "ALLOW_PRODUCTION_MUTATION",
        "CONFIRM_PROJECT_REF",
    ):
        monkeypatch.delenv(name, raising=False)


def test_remote_target_without_declared_environment_is_unknown(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:secret@db.example.com/postgres")
    assert env.resolve_environment() is env.Environment.UNKNOWN


def test_local_target_can_be_inferred(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://postgres:secret@127.0.0.1:54322/postgres")
    assert env.resolve_environment() is env.Environment.LOCAL
    assert env.database_identity().is_local is True


def test_test_environment_rejects_remote_mutation(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:secret@db.example.com/postgres")
    with pytest.raises(env.DatabaseTargetError, match="só pode mutar um banco local"):
        env.assert_database_target("test operation")


def test_mixed_local_and_remote_credentials_are_rejected(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "staging")
    monkeypatch.setenv("DATABASE_URL", "postgresql://postgres:secret@127.0.0.1:54322/postgres")
    monkeypatch.setenv("SUPABASE_URL", "https://stageref.supabase.co")
    with pytest.raises(env.DatabaseTargetError, match="localidades diferentes"):
        env.assert_database_target("migration")


def test_sentinel_must_match_declared_environment(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "local")
    monkeypatch.setenv("DATABASE_URL", "postgresql://postgres:secret@127.0.0.1:54322/postgres")
    monkeypatch.setattr(
        env,
        "read_environment_metadata",
        lambda: {
            "environment": "test",
            "project_ref": "local",
            "schema_version": "040",
            "dataset_version": "test-seed-v1",
        },
    )
    with pytest.raises(env.DatabaseTargetError, match="sentinela=test"):
        env.assert_database_target("gold ingest")


def test_production_requires_double_confirmation(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:secret@db.prod.example/postgres")
    monkeypatch.setenv("SUPABASE_URL", "https://prodref.supabase.co")
    monkeypatch.setattr(
        env,
        "read_environment_metadata",
        lambda: {
            "environment": "production",
            "project_ref": "prodref",
            "schema_version": "040",
            "dataset_version": "prod-v1",
        },
    )

    with pytest.raises(env.DatabaseTargetError, match="ALLOW_PRODUCTION_MUTATION"):
        env.assert_database_target("reindex")

    monkeypatch.setenv("ALLOW_PRODUCTION_MUTATION", "1")
    with pytest.raises(env.DatabaseTargetError, match="CONFIRM_PROJECT_REF"):
        env.assert_database_target("reindex")

    monkeypatch.setenv("CONFIRM_PROJECT_REF", "prodref")
    assert env.assert_database_target("reindex")["environment"] == "production"


def test_runtime_boot_rejects_sentinel_mismatch(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "staging")
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:secret@db.stage.example/postgres")
    monkeypatch.setenv("SUPABASE_URL", "https://stageref.supabase.co")
    monkeypatch.setattr(
        env,
        "read_environment_metadata",
        lambda: {
            "environment": "production",
            "project_ref": "prodref",
            "schema_version": "040",
            "dataset_version": "prod-v1",
        },
    )
    with pytest.raises(env.DatabaseTargetError, match="sentinela=production"):
        env.assert_runtime_environment("backend API")
