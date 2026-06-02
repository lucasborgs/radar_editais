"""Reflexão longitudinal: supersede de insights + gate de threshold.

Cobre o fechamento do loop de reflexão:
  - reflect_workspace desativa o lote anterior antes de inserir o novo
    (idempotência — sem isto o auto-trigger por outcome acumularia lixo);
  - o gate MIN_OUTCOMES_FOR_REFLECTION pula quando há poucos outcomes;
  - sem substituto (LLM devolve vazio) não desativa nada.

O auto-trigger em si (PUT /applications/{id}/status -> defer) é validado em
nível de endpoint/integração; aqui focamos na lógica de síntese, sem rede.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import core.reflection_service as rs


class _FakeTable:
    def __init__(self, name, select_data, log):
        self.name = name
        self._select_data = select_data
        self._log = log
        self._mode = None
        self._payload = None

    def select(self, *a, **k):
        self._mode = "select"; return self

    def insert(self, payload):
        self._mode = "insert"; self._payload = payload; return self

    def update(self, payload):
        self._mode = "update"; self._payload = payload; return self

    # chaináveis no-op
    def eq(self, *a, **k): return self
    def in_(self, *a, **k): return self
    def is_(self, *a, **k): return self
    def order(self, *a, **k): return self
    def limit(self, *a, **k): return self

    def execute(self):
        if self._mode in ("insert", "update"):
            self._log.append((self.name, self._mode, self._payload))
            return SimpleNamespace(data=[])
        return SimpleNamespace(data=self._select_data)


class _FakeDb:
    def __init__(self, outcomes):
        self.log: list[tuple] = []
        self._outcomes = outcomes

    def table(self, name):
        data = self._outcomes if name == "application_log" else []
        return _FakeTable(name, data, self.log)


def _outcome(i):
    return {
        "id": f"o{i}", "edital_id": f"finep:{i}", "status": "reprovada",
        "match_score": 7, "match_dimensions": {}, "feedback_notas": "x",
        "updated_at": f"2026-05-1{i}T00:00:00Z", "created_at": "2026-05-01T00:00:00Z",
    }


def _patch_llm(monkeypatch, payload: dict):
    resp = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))]
    )
    client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=lambda **kw: resp)
        )
    )
    monkeypatch.setattr(rs, "_make_client", lambda: (client, "gpt-4o"))


def test_supersede_deactivates_before_insert(monkeypatch):
    monkeypatch.setattr(rs, "MIN_OUTCOMES_FOR_REFLECTION", 3)
    _patch_llm(monkeypatch, {
        "observations": [{"text": "obs A", "evidence_ids": ["o1"]}],
        "patterns": [{"text": "pat B", "observation_indices": [0]}],
        "confidence": "medium",
    })
    db = _FakeDb([_outcome(1), _outcome(2), _outcome(3)])

    res = rs.reflect_workspace(db, "ws-1")

    assert res["skipped_reason"] is None
    assert res["observations_inserted"] == 1
    assert res["patterns_inserted"] == 1

    ops = [(t, m) for (t, m, _p) in db.log if t == "reflection_insights"]
    assert ("reflection_insights", "update") in ops  # supersede ocorreu
    assert ("reflection_insights", "insert") in ops
    # supersede ANTES do insert
    assert ops.index(("reflection_insights", "update")) < ops.index(("reflection_insights", "insert"))
    # o update carimba deactivated_at
    upd = next(p for (t, m, p) in db.log if t == "reflection_insights" and m == "update")
    assert "deactivated_at" in upd and upd["deactivated_at"]


def test_skips_below_threshold(monkeypatch):
    monkeypatch.setattr(rs, "MIN_OUTCOMES_FOR_REFLECTION", 3)
    # se a LLM fosse chamada, falharia (não setamos client) — prova que nem tenta
    db = _FakeDb([_outcome(1), _outcome(2)])

    res = rs.reflect_workspace(db, "ws-1")

    assert res["observations_inserted"] == 0
    assert "poucos outcomes" in res["skipped_reason"]
    # nenhuma escrita em reflection_insights
    assert not [x for x in db.log if x[0] == "reflection_insights"]


def test_no_supersede_when_llm_returns_empty(monkeypatch):
    monkeypatch.setattr(rs, "MIN_OUTCOMES_FOR_REFLECTION", 3)
    _patch_llm(monkeypatch, {"observations": [], "patterns": [], "confidence": "low"})
    db = _FakeDb([_outcome(1), _outcome(2), _outcome(3)])

    res = rs.reflect_workspace(db, "ws-1")

    assert res["observations_inserted"] == 0
    # sem substituto -> não desativa nada (não apaga sem repor)
    assert not [x for x in db.log if x[0] == "reflection_insights"]
