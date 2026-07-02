"""Testes do purge de checkpoints LangGraph (hardening PR6.1).

Valida a orquestração de `_purge_stale_checkpoints` com psycopg fake (zero DB):
seleção de threads stale parametrizada por retention, delete nas 3 tabelas do
saver com os mesmos thread_ids, no-op sem threads stale e sem DATABASE_URL.
A correção do SQL em si é território de integração (roda no Postgres real).
"""
from __future__ import annotations

import pytest

import core.tasks as tasks


class _FakeCursor:
    def __init__(self, stale_rows):
        self.stale_rows = stale_rows
        self.executed: list[tuple[str, tuple]] = []

    def execute(self, sql, params=None):
        self.executed.append((" ".join(sql.split()), params))

    def fetchall(self):
        return [(t,) for t in self.stale_rows]

    @property
    def rowcount(self):
        return len(self.stale_rows)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FakeConn:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _wire(monkeypatch, stale_rows):
    cur = _FakeCursor(stale_rows)
    monkeypatch.setenv("DATABASE_URL", "postgresql://fake")
    monkeypatch.setattr(
        "psycopg.connect", lambda dsn, autocommit: _FakeConn(cur),
    )
    return cur


def test_purge_deletes_stale_threads_from_all_tables(monkeypatch):
    cur = _wire(monkeypatch, ["ws1:sess1:1", "ws2:sess9:3"])

    counts = tasks._purge_stale_checkpoints(30)

    assert counts["threads"] == 2
    select_sql, select_params = cur.executed[0]
    assert "agent_memory.checkpoints" in select_sql
    assert "checkpoint->>'ts'" in select_sql
    assert select_params == (30,)  # retention parametrizado

    deletes = cur.executed[1:]
    tables = [sql.split("FROM ")[1].split(" ")[0] for sql, _ in deletes]
    assert tables == [
        "agent_memory.checkpoint_writes",
        "agent_memory.checkpoint_blobs",
        "agent_memory.checkpoints",
    ]
    for _, params in deletes:
        assert params == (["ws1:sess1:1", "ws2:sess9:3"],)


def test_purge_no_stale_threads_skips_deletes(monkeypatch):
    cur = _wire(monkeypatch, [])

    counts = tasks._purge_stale_checkpoints(30)

    assert counts == {"threads": 0}
    assert len(cur.executed) == 1  # só o SELECT


def test_purge_without_database_url_raises(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(RuntimeError, match="DATABASE_URL"):
        tasks._purge_stale_checkpoints(30)


@pytest.mark.asyncio
async def test_cron_task_is_noop_without_database_url(monkeypatch, caplog):
    monkeypatch.delenv("DATABASE_URL", raising=False)

    def _boom(*a, **k):
        raise AssertionError("não deveria tocar o DB")

    monkeypatch.setattr(tasks, "_purge_stale_checkpoints", _boom)
    await tasks.purge_agent_checkpoints(1234567890)  # não levanta
