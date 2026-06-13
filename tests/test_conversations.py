"""Testes da fase 2 (persistência de conversas do front door + router
/conversations) — SEM rede/banco real.

Estilo do repo: chama os handlers/helpers direto, com um fake do cliente
Supabase (query-builder em memória) no lugar do `db`. Os parâmetros
CurrentUserId/DbClient/OptionalUserId são markers de Depends — passamos posicional.

Cobre:
  (a) listar unificado expõe kind/title (GET /conversations);
  (b) front door logado cria conversa frontdoor no 1º turno e reusa no 2º;
  (c) entradas voltam ordenadas por turn_index na retomada (GET /conversations/{id});
  (d) POST/PATCH de entries com validação de workspace (404 para workspace alheio);
  (e) turno anônimo não persiste nada (response sem session_id);
  (f) falha de persistência não derruba o turno (warning + resposta normal).
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fastapi import HTTPException  # noqa: E402

from backend.rate_limit import limiter  # noqa: E402


# =============================================================================
# Fake Supabase client (query-builder em memória)
# =============================================================================
# Cobre só o subconjunto de operações usado pelos helpers de conversations:
#   .table(t).select(...).eq(...).order(...).maybe_single().execute()
#   .table(t).insert({...}).execute()
#   .table(t).update({...}).eq(...).execute()
# Mantém duas "tabelas": writing_sessions e session_turns. RLS é simulada só
# pelos filtros eq() explícitos dos helpers (que é o que validamos).


class _Result:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, rows: list[dict]):
        self._rows = list(rows)
        self._filters: list[tuple[str, object]] = []
        self._order: list[tuple[str, bool]] = []
        self._limit: int | None = None
        self._maybe_single = False

    def select(self, *_a, **_k):
        return self

    def eq(self, col, val):
        self._filters.append((col, val))
        return self

    def in_(self, col, vals):
        self._filters.append((col, ("__in__", list(vals))))
        return self

    def order(self, col, desc=False):
        self._order.append((col, desc))
        return self

    def limit(self, n):
        self._limit = n
        return self

    def maybe_single(self):
        self._maybe_single = True
        return self

    def _apply(self) -> list[dict]:
        rows = self._rows
        for col, val in self._filters:
            if isinstance(val, tuple) and val and val[0] == "__in__":
                rows = [r for r in rows if r.get(col) in val[1]]
            else:
                rows = [r for r in rows if r.get(col) == val]
        for col, desc in reversed(self._order):
            rows = sorted(rows, key=lambda r: r.get(col), reverse=desc)
        if self._limit is not None:
            rows = rows[: self._limit]
        return rows

    def execute(self):
        rows = self._apply()
        if self._maybe_single:
            return _Result(rows[0] if rows else None)
        return _Result(rows)


class _Table:
    def __init__(self, db: "FakeDb", name: str):
        self._db = db
        self._name = name

    def select(self, *a, **k):
        return _Query(self._db.tables[self._name]).select(*a, **k)

    def insert(self, payload: dict):
        self._db.fail_if_set()
        row = dict(payload)
        if "id" not in row:
            if self._name == "session_turns":
                row["id"] = self._db.next_turn_id()
            else:
                row["id"] = f"sess-{len(self._db.tables[self._name]) + 1}"
        if self._name == "writing_sessions":
            row.setdefault("created_at", "2026-06-12T00:00:00Z")
            row.setdefault("updated_at", "2026-06-12T00:00:00Z")
        self._db.tables[self._name].append(row)
        return _Inserted([row])

    def update(self, patch: dict):
        return _Update(self._db.tables[self._name], patch)

    def delete(self):  # não usado aqui, mas mantém a interface
        return _Delete(self._db.tables[self._name])


class _Inserted:
    def __init__(self, rows):
        self._rows = rows

    def execute(self):
        return _Result(self._rows)


class _Update:
    def __init__(self, rows: list[dict], patch: dict):
        self._rows = rows
        self._patch = patch
        self._filters: list[tuple[str, object]] = []

    def eq(self, col, val):
        self._filters.append((col, val))
        return self

    def execute(self):
        updated = []
        for r in self._rows:
            if all(r.get(c) == v for c, v in self._filters):
                r.update(self._patch)
                updated.append(r)
        return _Result(updated)


class _Delete:
    def __init__(self, rows):
        self._rows = rows
        self._filters: list[tuple[str, object]] = []

    def eq(self, col, val):
        self._filters.append((col, val))
        return self

    def execute(self):
        kept = [r for r in self._rows if not all(r.get(c) == v for c, v in self._filters)]
        self._rows[:] = kept
        return _Result([])


class FakeDb:
    def __init__(self):
        self.tables: dict[str, list[dict]] = {
            "writing_sessions": [],
            "session_turns": [],
        }
        self._turn_seq = 0
        self._fail = False

    def table(self, name: str) -> _Table:
        self.tables.setdefault(name, [])
        return _Table(self, name)

    def next_turn_id(self) -> int:
        self._turn_seq += 1
        return self._turn_seq

    def set_fail(self):
        """Força o próximo insert a explodir (simula DB indisponível)."""
        self._fail = True

    def fail_if_set(self):
        if self._fail:
            raise RuntimeError("DB indisponível (simulado)")


WS = "ws-1"
OTHER_WS = "ws-2"


@pytest.fixture(autouse=True)
def _disable_rate_limit():
    prev = limiter.enabled
    limiter.enabled = False
    yield
    limiter.enabled = prev


@pytest.fixture(autouse=True)
def _patch_get_workspace_id(monkeypatch):
    """get_workspace_id é resolvido por user_id nos handlers; mapeamos
    user→workspace de forma determinística (sem tocar a tabela workspaces)."""
    mapping = {"user-1": WS, "user-2": OTHER_WS}

    def fake(db, user_id):
        return mapping[user_id]

    # Patcha onde os módulos importaram o símbolo.
    import backend.routers.conversations as conv
    import backend.routers.frontdoor as fd
    monkeypatch.setattr(conv, "get_workspace_id", fake)
    monkeypatch.setattr(fd, "get_workspace_id", fake)
    yield


def _fake_request() -> MagicMock:
    return MagicMock()


# ============================================================================
# (b)+(e)+(f) front door: persistência logado / anônimo / falha tolerada
# ============================================================================

def _stub_frontdoor_llm(monkeypatch, answer="resposta", diff=None):
    import backend.routers.frontdoor as fd
    monkeypatch.setattr(fd.kg_service, "explore", lambda *a, **k: answer)
    monkeypatch.setattr(
        fd.ProfileExtractor, "extract_diff_from_message",
        lambda self, message, current: diff or [],
    )


def test_anonymous_turn_does_not_persist(monkeypatch):
    import backend.routers.frontdoor as fd
    _stub_frontdoor_llm(monkeypatch, answer="oi")

    out = fd.frontdoor_turn(
        _fake_request(),
        fd.FrontdoorTurnRequest(message="uma mensagem qualquer"),
        user_id=None,
        db=None,
    )
    assert out["answer"] == "oi"
    assert "session_id" not in out
    assert "entry_ids" not in out


def test_authenticated_first_turn_creates_frontdoor_session(monkeypatch):
    import backend.routers.frontdoor as fd
    diff = [{"field": "trl", "label": "TRL", "old": None, "new": 6}]
    _stub_frontdoor_llm(monkeypatch, answer="resposta 1", diff=diff)
    db = FakeDb()

    out = fd.frontdoor_turn(
        _fake_request(),
        fd.FrontdoorTurnRequest(message="Somos uma healthtech com TRL 6 em SP"),
        user_id="user-1",
        db=db,
    )
    sid = out["session_id"]
    assert sid
    sessions = db.tables["writing_sessions"]
    assert len(sessions) == 1
    sess = sessions[0]
    assert sess["kind"] == "frontdoor"
    assert sess["workspace_id"] == WS
    assert sess["edital_id"] is None
    assert sess["title"] == "Somos uma healthtech com TRL 6 em SP"[:60]

    # user msg + assistant msg + diff
    turns = db.tables["session_turns"]
    assert len(turns) == 3
    kinds = [t["entry_kind"] for t in turns]
    assert kinds == ["msg", "msg", "diff"]
    diff_turn = turns[2]
    assert diff_turn["payload"]["status"] == "pending"
    assert diff_turn["payload"]["origin"] == "turn"
    assert diff_turn["payload"]["items"] == diff
    assert out["entry_ids"]["diff"] == diff_turn["id"]


def test_authenticated_second_turn_reuses_session(monkeypatch):
    import backend.routers.frontdoor as fd
    _stub_frontdoor_llm(monkeypatch, answer="r1", diff=None)
    db = FakeDb()

    first = fd.frontdoor_turn(
        _fake_request(),
        fd.FrontdoorTurnRequest(message="primeira mensagem"),
        user_id="user-1",
        db=db,
    )
    sid = first["session_id"]

    _stub_frontdoor_llm(monkeypatch, answer="r2", diff=None)
    second = fd.frontdoor_turn(
        _fake_request(),
        fd.FrontdoorTurnRequest(message="segunda mensagem", session_id=sid),
        user_id="user-1",
        db=db,
    )
    assert second["session_id"] == sid
    # Uma única conversa; 4 turnos (2 user + 2 assistant), sem diff.
    assert len(db.tables["writing_sessions"]) == 1
    turns = db.tables["session_turns"]
    assert len(turns) == 4
    # turn_index monotônico e crescente.
    indices = [t["turn_index"] for t in turns]
    assert indices == sorted(indices)
    assert indices[-1] > indices[0]


def test_persistence_failure_does_not_break_turn(monkeypatch):
    import backend.routers.frontdoor as fd
    _stub_frontdoor_llm(monkeypatch, answer="resposta apesar do erro")
    db = FakeDb()
    db.set_fail()  # qualquer insert explode

    out = fd.frontdoor_turn(
        _fake_request(),
        fd.FrontdoorTurnRequest(message="mensagem que falha ao persistir"),
        user_id="user-1",
        db=db,
    )
    # A conversa vale mais que o histórico: resposta normal, sem session_id.
    assert out["answer"] == "resposta apesar do erro"
    assert "session_id" not in out


# ============================================================================
# (a)+(c) listar unificado e retomar com entries ordenadas
# ============================================================================

def test_list_conversations_exposes_kind_and_title(monkeypatch):
    import backend.routers.conversations as conv
    db = FakeDb()
    # Uma conversa de escrita e uma de front door no mesmo workspace.
    db.tables["writing_sessions"].extend([
        {
            "id": "w1", "workspace_id": WS, "edital_id": "finep:001",
            "kind": "writing", "title": None, "status": "active",
            "created_at": "2026-06-10T00:00:00Z", "updated_at": "2026-06-10T00:00:00Z",
        },
        {
            "id": "f1", "workspace_id": WS, "edital_id": None,
            "kind": "frontdoor", "title": "Somos uma healthtech", "status": "active",
            "created_at": "2026-06-11T00:00:00Z", "updated_at": "2026-06-11T00:00:00Z",
        },
    ])

    out = conv.list_conversations(user_id="user-1", db=db)
    convos = {c.session_id: c for c in out["conversations"]}
    assert convos["w1"].kind == "writing"
    assert convos["w1"].title is None
    assert convos["f1"].kind == "frontdoor"
    assert convos["f1"].title == "Somos uma healthtech"
    # ordenado por updated_at desc → frontdoor (mais recente) primeiro
    assert out["conversations"][0].session_id == "f1"


def test_get_conversation_entries_ordered(monkeypatch):
    import backend.routers.conversations as conv
    db = FakeDb()
    db.tables["writing_sessions"].append({
        "id": "f1", "workspace_id": WS, "edital_id": None, "kind": "frontdoor",
        "title": "t", "status": "active",
        "created_at": "2026-06-11T00:00:00Z", "updated_at": "2026-06-11T00:00:00Z",
    })
    # Inseridas fora de ordem de propósito.
    db.tables["session_turns"].extend([
        {"id": 3, "session_id": "f1", "turn_index": 3, "entry_kind": "diff",
         "role": "assistant", "content": "", "payload": {"status": "pending"}},
        {"id": 1, "session_id": "f1", "turn_index": 1, "entry_kind": "msg",
         "role": "user", "content": "oi", "payload": None},
        {"id": 2, "session_id": "f1", "turn_index": 2, "entry_kind": "msg",
         "role": "assistant", "content": "olá", "payload": None},
    ])

    detail = conv.get_conversation_detail("f1", user_id="user-1", db=db)
    idx = [e.turn_index for e in detail.entries]
    assert idx == [1, 2, 3]
    assert detail.entries[0].entry_kind == "msg"
    assert detail.entries[0].role == "user"
    assert detail.entries[2].entry_kind == "diff"
    assert detail.entries[2].payload == {"status": "pending"}


def test_get_conversation_other_workspace_404(monkeypatch):
    import backend.routers.conversations as conv
    db = FakeDb()
    db.tables["writing_sessions"].append({
        "id": "f1", "workspace_id": OTHER_WS, "edital_id": None, "kind": "frontdoor",
        "title": "alheia", "status": "active",
        "created_at": "x", "updated_at": "x",
    })
    with pytest.raises(HTTPException) as exc:
        conv.get_conversation_detail("f1", user_id="user-1", db=db)
    assert exc.value.status_code == 404


# ============================================================================
# (d) POST/PATCH de entries com validação de workspace
# ============================================================================

def _seed_session(db, sid="f1", ws=WS):
    db.tables["writing_sessions"].append({
        "id": sid, "workspace_id": ws, "edital_id": None, "kind": "frontdoor",
        "title": "t", "status": "active", "created_at": "x", "updated_at": "x",
    })


def test_append_entry_radar(monkeypatch):
    import backend.routers.conversations as conv
    db = FakeDb()
    _seed_session(db)

    entry = conv.append_conversation_entry(
        "f1",
        conv.AppendEntryRequest(entry_kind="radar", payload={"items": [{"id": "x"}]}),
        user_id="user-1",
        db=db,
    )
    assert entry.entry_kind == "radar"
    assert entry.role == "assistant"
    assert entry.content == ""
    assert entry.turn_index == 1
    assert entry.payload == {"items": [{"id": "x"}]}


def test_append_entry_other_workspace_404(monkeypatch):
    import backend.routers.conversations as conv
    db = FakeDb()
    _seed_session(db, ws=OTHER_WS)
    with pytest.raises(HTTPException) as exc:
        conv.append_conversation_entry(
            "f1",
            conv.AppendEntryRequest(entry_kind="radar", payload={}),
            user_id="user-1",
            db=db,
        )
    assert exc.value.status_code == 404


def test_patch_entry_updates_payload(monkeypatch):
    import backend.routers.conversations as conv
    db = FakeDb()
    _seed_session(db)
    db.tables["session_turns"].append({
        "id": 10, "session_id": "f1", "turn_index": 3, "entry_kind": "diff",
        "role": "assistant", "content": "",
        "payload": {"items": [], "status": "pending", "origin": "turn"},
    })

    updated = conv.patch_conversation_entry(
        "f1",
        10,
        conv.UpdateEntryRequest(payload={"items": [], "status": "accepted", "origin": "turn"}),
        user_id="user-1",
        db=db,
    )
    assert updated.payload["status"] == "accepted"
    assert db.tables["session_turns"][0]["payload"]["status"] == "accepted"


def test_patch_entry_other_workspace_404(monkeypatch):
    import backend.routers.conversations as conv
    db = FakeDb()
    _seed_session(db, ws=OTHER_WS)
    db.tables["session_turns"].append({
        "id": 10, "session_id": "f1", "turn_index": 3, "entry_kind": "diff",
        "role": "assistant", "content": "", "payload": {"status": "pending"},
    })
    with pytest.raises(HTTPException) as exc:
        conv.patch_conversation_entry(
            "f1",
            10,
            conv.UpdateEntryRequest(payload={"status": "accepted"}),
            user_id="user-1",
            db=db,
        )
    assert exc.value.status_code == 404


def test_patch_entry_missing_404(monkeypatch):
    import backend.routers.conversations as conv
    db = FakeDb()
    _seed_session(db)
    with pytest.raises(HTTPException) as exc:
        conv.patch_conversation_entry(
            "f1",
            999,  # inexistente
            conv.UpdateEntryRequest(payload={"status": "accepted"}),
            user_id="user-1",
            db=db,
        )
    assert exc.value.status_code == 404
