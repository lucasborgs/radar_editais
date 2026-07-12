"""Reflexão longitudinal: supersede de insights + gate de threshold.

Cobre o fechamento do loop de reflexão (Gap 1 — level 1 e level 2 separados):
  - reflect_workspace gera SÓ observações (level 1) e desativa apenas o lote
    anterior de level 1 antes de inserir o novo (level 2 fica intocado);
  - o gate MIN_OUTCOMES_FOR_REFLECTION pula quando há poucos outcomes;
  - sem substituto (LLM devolve vazio) não desativa nada;
  - synthesize_patterns lê o corpus de level 1 acumulado e gera level 2,
    desativando só o level 2 anterior e gateando em MIN_LEVEL1_FOR_SYNTHESIS.

O auto-trigger em si (PUT /applications/{id}/status -> defer) é validado em
nível de endpoint/integração; aqui focamos na lógica de síntese, sem rede.

F6 (D3 — congelamento da memória auto-escrita): um autouse fixture seta
AUTO_MEMORY_WRITE=1 para preservar a intenção original dos testes (exercitar
a lógica de escrita). Testes separados provam o no-op sob a flag off.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

import core.reflection_service as rs


@pytest.fixture(autouse=True)
def _enable_auto_memory_write(monkeypatch: pytest.MonkeyPatch) -> None:
    """F6 (D3): re-ativa a escrita automática para preservar a intenção dos
    testes originais (exercitar a lógica de síntese sob AUTO_MEMORY_WRITE=1)."""
    monkeypatch.setenv("AUTO_MEMORY_WRITE", "1")


class _FakeTable:
    def __init__(self, name, select_data, log):
        self.name = name
        self._select_data = select_data
        self._log = log
        self._mode = None
        self._payload = None

    def select(self, *a, **k):
        self._mode = "select"
        return self

    def insert(self, payload):
        self._mode = "insert"
        self._payload = payload
        return self

    def update(self, payload):
        self._mode = "update"
        self._payload = payload
        return self

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
    def __init__(self, outcomes, level1=None):
        self.log: list[tuple] = []
        self._outcomes = outcomes
        self._level1 = level1 or []

    def table(self, name):
        if name == "application_log":
            data = self._outcomes
        elif name == "reflection_insights":
            data = self._level1
        else:
            data = []
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


def test_reflect_generates_only_level1_and_supersedes_before_insert(monkeypatch):
    monkeypatch.setattr(rs, "MIN_OUTCOMES_FOR_REFLECTION", 3)
    # Mesmo que a LLM devolva patterns/weight_suggestions, reflect_workspace os
    # ignora — só observações (level 1) entram nesta etapa.
    _patch_llm(monkeypatch, {
        "observations": [{"text": "obs A", "evidence_ids": ["o1"]}],
        "patterns": [{"text": "pat B", "observation_indices": [0]}],
        "weight_suggestions": [{"dimension": "trl", "delta": 5}],
        "confidence": "medium",
    })
    db = _FakeDb([_outcome(1), _outcome(2), _outcome(3)])

    res = rs.reflect_workspace(db, "ws-1")

    assert res["skipped_reason"] is None
    assert res["observations_inserted"] == 1
    # Gap 1: reflect_workspace não retorna mais patterns nem weight_suggestions.
    assert "patterns_inserted" not in res
    assert "weight_suggestions" not in res

    ops = [(t, m) for (t, m, _p) in db.log if t == "reflection_insights"]
    assert ("reflection_insights", "update") in ops  # supersede ocorreu
    assert ("reflection_insights", "insert") in ops
    # supersede ANTES do insert
    assert ops.index(("reflection_insights", "update")) < ops.index(("reflection_insights", "insert"))
    # o update carimba deactivated_at
    upd = next(p for (t, m, p) in db.log if t == "reflection_insights" and m == "update")
    assert "deactivated_at" in upd and upd["deactivated_at"]

    # Só insere level 1 — nenhum level 2 nesta etapa.
    inserted = next(p for (t, m, p) in db.log if t == "reflection_insights" and m == "insert")
    assert all(r["level"] == 1 for r in inserted)


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


# =============================================================================
# synthesize_patterns (Gap 1)
# =============================================================================

def _level1(i):
    return {
        "id": f"l{i}", "insight": f"observação {i}",
        "created_at": f"2026-05-1{i}T00:00:00Z",
    }


def test_synthesize_skips_below_threshold(monkeypatch):
    monkeypatch.setattr(rs, "MIN_LEVEL1_FOR_SYNTHESIS", 3)
    # 2 level-1 < 3 -> pula sem chamar LLM (não setamos client)
    db = _FakeDb([], level1=[_level1(1), _level1(2)])

    res = rs.synthesize_patterns(db, "ws-1")

    assert res["patterns_inserted"] == 0
    assert "poucas observações" in res["skipped_reason"]
    assert not [x for x in db.log if x[0] == "reflection_insights"]


def test_synthesize_generates_level2_and_supersedes_only_level2(monkeypatch):
    monkeypatch.setattr(rs, "MIN_LEVEL1_FOR_SYNTHESIS", 3)
    _patch_llm(monkeypatch, {
        "patterns": [{"text": "pat X", "observation_indices": [0, 1]}],
        "weight_suggestions": [],
        "confidence": "medium",
    })
    db = _FakeDb([], level1=[_level1(1), _level1(2), _level1(3)])

    res = rs.synthesize_patterns(db, "ws-1")

    assert res["skipped_reason"] is None
    assert res["level1_considered"] == 3
    assert res["patterns_inserted"] == 1

    ops = [(t, m) for (t, m, _p) in db.log if t == "reflection_insights"]
    assert ("reflection_insights", "update") in ops  # supersede de level 2
    assert ("reflection_insights", "insert") in ops
    assert ops.index(("reflection_insights", "update")) < ops.index(("reflection_insights", "insert"))

    # Só insere level 2.
    inserted = next(p for (t, m, p) in db.log if t == "reflection_insights" and m == "insert")
    assert all(r["level"] == 2 for r in inserted)

    # O supersede carrega a reason específica da síntese.
    upd = next(p for (t, m, p) in db.log if t == "reflection_insights" and m == "update")
    assert upd.get("deactivation_reason") == "superseded by synthesize_patterns"


# =============================================================================
# F6 (D3) — no-op sob AUTO_MEMORY_WRITE=0 (default)
# =============================================================================


def test_reflect_noop_when_auto_memory_write_off(monkeypatch):
    """Sob AUTO_MEMORY_WRITE=0 (default), reflect_workspace é no-op."""
    # Garante que a flag está OFF (não deve setar).
    monkeypatch.delenv("AUTO_MEMORY_WRITE", raising=False)
    # Se a LLM fosse chamada, _patch_llm não foi invocada — test falharia.
    db = _FakeDb([_outcome(1), _outcome(2), _outcome(3)])
    res = rs.reflect_workspace(db, "ws-1")
    assert res["skipped_reason"] == "auto_memory_write_disabled"
    assert res["observations_inserted"] == 0
    # Nenhuma escrita em reflection_insights.
    assert not [x for x in db.log if x[0] == "reflection_insights"]


def test_synthesize_noop_when_auto_memory_write_off(monkeypatch):
    """Sob AUTO_MEMORY_WRITE=0 (default), synthesize_patterns é no-op."""
    monkeypatch.delenv("AUTO_MEMORY_WRITE", raising=False)
    db = _FakeDb([], level1=[_level1(1), _level1(2), _level1(3)])
    res = rs.synthesize_patterns(db, "ws-1")
    assert res["skipped_reason"] == "auto_memory_write_disabled"
    assert res["patterns_inserted"] == 0
    assert not [x for x in db.log if x[0] == "reflection_insights"]
