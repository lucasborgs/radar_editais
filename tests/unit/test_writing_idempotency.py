"""`_check_idempotency` / `_record_idempotency` — guarda do `None` do supabase-py.

O supabase-py devolve `None` de `maybe_single().execute()` quando não há linha
(vs. `data=None` quando há resposta vazia). O bug de prod (500 em todo primeiro
turno) era `row.data` sem guarda. Testa os dois shapes + o hit com resposta.
"""

from radar.api.routers.writing import _check_idempotency, _record_idempotency


class _FakeRow:
    def __init__(self, data):
        self.data = data


class _FakeQuery:
    def __init__(self, result):
        self._result = result

    def select(self, *args, **kwargs):
        return self

    def eq(self, *args, **kwargs):
        return self

    def maybe_single(self):
        return self

    def execute(self):
        return self._result


class _FakeDb:
    def __init__(self, result):
        self._result = result
        self.inserted = []

    def table(self, name):
        self._table = name
        return self

    def select(self, *args, **kwargs):
        return _FakeQuery(self._result)

    def eq(self, *args, **kwargs):
        return _FakeQuery(self._result)

    def maybe_single(self):
        return _FakeQuery(self._result)

    def insert(self, payload):
        self.inserted.append(payload)
        return _FakeQuery(_FakeRow([payload]))

    def execute(self):
        return self._result


def test_check_idempotency_without_key_returns_none():
    db = _FakeDb(_FakeRow([]))
    assert _check_idempotency(db, None, "session-1") is None
    assert db.inserted == []


def test_check_idempotency_execute_returns_none():
    """Cenário real de prod: maybe_single sem linha → execute() devolve None."""
    db = _FakeDb(None)
    assert _check_idempotency(db, "key-1", "session-1") is None


def test_check_idempotency_empty_data_returns_none():
    db = _FakeDb(_FakeRow(None))
    assert _check_idempotency(db, "key-1", "session-1") is None
    db = _FakeDb(_FakeRow([]))
    assert _check_idempotency(db, "key-1", "session-1") is None


def test_check_idempotency_hit_returns_cached_response():
    cached = {"draft": "resposta", "compliance_flags": []}
    db = _FakeDb(_FakeRow({"response_json": cached}))
    assert _check_idempotency(db, "key-1", "session-1") == cached


def test_record_idempotency_ignores_missing_key():
    db = _FakeDb(_FakeRow([]))
    _record_idempotency(db, None, "session-1", {"ok": True})
    assert db.inserted == []


def test_record_idempotency_inserts_row():
    db = _FakeDb(_FakeRow([]))
    response = {"draft": "x"}
    _record_idempotency(db, "key-1", "session-1", response)
    assert db.inserted == [
        {"idempotency_key": "key-1", "session_id": "session-1", "response_json": response},
    ]
