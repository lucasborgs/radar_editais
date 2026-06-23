"""Memória do ExploreAgent entre sessões (Fase 3A — exploration_log).

Cobre a tool log_exploration_decision (normalização, idempotência via upsert,
erros-como-string) e o loader load_recent_exploration_decisions (formatação +
degradação silenciosa). Não bate em DB real — usa fakes do client Supabase.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.llm.agent_tools.explore_tools import (  # noqa: E402
    build_exploration_log_tools,
    load_recent_exploration_decisions,
)


def _tool(db):
    (t,) = build_exploration_log_tools(db, "ws_1")
    return t


def test_log_decision_upserts_with_conflict_target():
    db = MagicMock()
    out = _tool(db).invoke(
        {"edital_id": "finep:773", "decision": "recommended", "reason": "fit forte"}
    )
    db.table.assert_called_once_with("exploration_log")
    upsert = db.table.return_value.upsert
    row, kwargs = upsert.call_args.args[0], upsert.call_args.kwargs
    assert row["workspace_id"] == "ws_1"
    assert row["edital_id"] == "finep:773"
    assert row["decision"] == "recommended"
    assert row["reason"] == "fit forte"
    assert row["decided_at"]  # timestamp explícito (UPDATE não reaplica default)
    assert kwargs["on_conflict"] == "workspace_id,edital_id"
    assert "recomendado" in out


def test_log_decision_normalizes_pt_variants():
    db = MagicMock()
    _tool(db).invoke({"edital_id": "finep:1", "decision": "descartar"})
    row = db.table.return_value.upsert.call_args.args[0]
    assert row["decision"] == "discarded"
    assert row["reason"] is None  # reason vazia → None (não string vazia)


def test_log_decision_invalid_decision_no_write():
    db = MagicMock()
    out = _tool(db).invoke({"edital_id": "finep:1", "decision": "talvez"})
    assert "inválida" in out.lower()
    db.table.assert_not_called()


def test_log_decision_empty_edital_no_write():
    db = MagicMock()
    out = _tool(db).invoke({"edital_id": "  ", "decision": "recommended"})
    assert "edital_id" in out
    db.table.assert_not_called()


class _FakeSelectChain:
    """Encadeia select().eq().order().limit().execute() devolvendo `rows`."""

    def __init__(self, rows):
        self._rows = rows

    def select(self, *_a, **_k):
        return self

    def eq(self, *_a, **_k):
        return self

    def order(self, *_a, **_k):
        return self

    def limit(self, *_a, **_k):
        return self

    def execute(self):
        return type("R", (), {"data": self._rows})()


def _db_with_rows(rows):
    db = MagicMock()
    db.table.return_value = _FakeSelectChain(rows)
    return db


def test_loader_empty_returns_blank():
    assert load_recent_exploration_decisions(_db_with_rows([]), "ws_1") == ""


def test_loader_formats_rows():
    rows = [
        {"edital_id": "finep:773", "decision": "recommended",
         "reason": "fit forte", "decided_at": "2026-06-20T10:00:00+00:00"},
        {"edital_id": "finep:50", "decision": "discarded",
         "reason": None, "decided_at": "2026-06-19T09:00:00+00:00"},
    ]
    block = load_recent_exploration_decisions(_db_with_rows(rows), "ws_1")
    assert "DECISÕES ANTERIORES" in block
    assert "recomendou finep:773 — fit forte" in block
    assert "descartou finep:50" in block
    assert "2026-06-20" in block


def test_loader_swallows_db_error():
    db = MagicMock()
    db.table.side_effect = RuntimeError("boom")
    assert load_recent_exploration_decisions(db, "ws_1") == ""


# --- wiring de _explore_agent (proxy automático do gate manual 2-sessões) -----


def _capture_explore_agent(monkeypatch, *, workspace_id, db):
    """Roda _explore_agent com run_agent stubbado; devolve (system, tool_names)."""
    import core.llm.agent_runtime as runtime
    from core.services.kg_match_service import KGMatchService

    captured: dict = {}

    def fake_run_agent(*, system, tools, **_):
        captured["system"] = system
        captured["tools"] = [t.name for t in tools]
        return type("R", (), {"stop_reason": "stop", "final_text": "ok", "steps": []})()

    monkeypatch.setattr(runtime, "run_agent", fake_run_agent)
    monkeypatch.setattr(runtime, "resolve_agent_provider", lambda *_a, **_k: ("anthropic", "m"))

    svc = KGMatchService.__new__(KGMatchService)
    svc._index = {}
    monkeypatch.setattr(svc, "_load_index", lambda: None)
    monkeypatch.setattr(svc, "_explore_tools", lambda: [])

    svc._explore_agent("oi", None, None, None, None, workspace_id=workspace_id, db=db)
    return captured


def test_explore_agent_injects_memory_when_authenticated(monkeypatch):
    rows = [{"edital_id": "finep:773", "decision": "recommended",
             "reason": "fit", "decided_at": "2026-06-20T10:00:00+00:00"}]
    cap = _capture_explore_agent(monkeypatch, workspace_id="ws_1", db=_db_with_rows(rows))
    assert "log_exploration_decision" in cap["tools"]
    assert "MEMÓRIA ENTRE SESSÕES" in cap["system"]
    assert "DECISÕES ANTERIORES" in cap["system"]
    assert "finep:773" in cap["system"]


def test_explore_agent_stateless_when_anonymous(monkeypatch):
    cap = _capture_explore_agent(monkeypatch, workspace_id=None, db=None)
    assert "log_exploration_decision" not in cap["tools"]
    assert "DECISÕES ANTERIORES" not in cap["system"]
    assert "MEMÓRIA ENTRE SESSÕES" not in cap["system"]
