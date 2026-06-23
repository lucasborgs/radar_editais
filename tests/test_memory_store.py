"""Etapa 5 — Store da memória cross-session (zero rede).

Exercita os helpers públicos (memory_put/search/delete) e a integração com
reflection_service / WritingSession usando um InMemoryStore + embed FAKE injetados
(como os testes do checkpointer injetam um InMemorySaver). Sem rede, sem token: o
embed fake mapeia presença de palavras-chave para um vetor determinístico.

Gates cobertos: isolamento por namespace (workspace), relevância semântica, delete,
degradação graciosa (Store off / query vazia), espelhamento dos inserts/deletes pela
projeção, e o fallback estático da WritingSession.
"""
from __future__ import annotations

import types

import pytest

import core.llm.agent_graph as ag

# Vocabulário do embed fake: cada dimensão conta ocorrências de uma palavra-chave.
_VOCAB = ["trl", "contrapartida", "orcamento", "prazo", "equipe", "mercado"]
_DIMS = 16


def _fake_embed(texts: list[str]) -> list[list[float]]:
    out: list[list[float]] = []
    for t in texts:
        tl = (t or "").lower()
        v = [float(tl.count(w)) for w in _VOCAB]
        v += [0.0] * (_DIMS - len(v))
        v[_DIMS - 1] = 0.1  # evita vetor de norma zero (cosseno indefinido)
        out.append(v)
    return out


@pytest.fixture
def store(monkeypatch):
    """InMemoryStore com index semântico fake, injetado no lugar do singleton real."""
    from langgraph.store.memory import InMemoryStore

    s = InMemoryStore(index={"dims": _DIMS, "embed": _fake_embed, "fields": ["insight"]})
    monkeypatch.setattr(ag, "_get_memory_store", lambda: s)
    return s


@pytest.fixture(scope="module", autouse=True)
def _teardown_bg_loop():
    """Para o loop dedicado ao fim do módulo (os helpers o sobem via _run_on_bg_loop)."""
    yield
    ag.shutdown_writing_runtime()


# ---------------------------------------------------------------------------
# Helpers públicos: namespace, relevância, delete, degradação
# ---------------------------------------------------------------------------

def test_namespace_isolates_workspaces(store):
    """GATE de segurança: search de um workspace NUNCA vê insight de outro (o Store
    bypassa RLS; isolamento = namespace por workspace_id)."""
    ag.memory_put("wsA", "i1", "TRL alto aumenta a aprovação", level=2)
    ag.memory_put("wsB", "i2", "contrapartida foi o gargalo", level=2)

    a = ag.memory_search("wsA", "qualquer tema", limit=6)
    b = ag.memory_search("wsB", "qualquer tema", limit=6)
    assert [x["insight"] for x in a] == ["TRL alto aumenta a aprovação"]
    assert [x["insight"] for x in b] == ["contrapartida foi o gargalo"]


def test_search_ranks_by_semantic_relevance(store):
    ag.memory_put("ws", "i1", "a empresa precisa melhorar o TRL dos projetos", level=2)
    ag.memory_put("ws", "i2", "contrapartida e orcamento foram o problema", level=1)

    top = ag.memory_search("ws", "maturidade TRL tecnologica", limit=1)
    assert len(top) == 1
    assert "TRL" in top[0]["insight"]
    assert top[0]["level"] == 2


def test_delete_removes_from_store(store):
    ag.memory_put("ws", "i1", "TRL alto", level=2)
    ag.memory_delete("ws", "i1")
    assert ag.memory_search("ws", "TRL", limit=6) == []


def test_search_off_returns_empty(monkeypatch):
    """Store off (None) → search vazio, put/delete no-op, sem exceção."""
    monkeypatch.setattr(ag, "_get_memory_store", lambda: None)
    assert ag.memory_search("ws", "trl") == []
    ag.memory_put("ws", "i", "x")   # no-op
    ag.memory_delete("ws", "i")     # no-op


def test_blank_query_skips_search(store):
    ag.memory_put("ws", "i1", "TRL alto", level=2)
    assert ag.memory_search("ws", "   ") == []


# ---------------------------------------------------------------------------
# Projeção reflection_insights → Store
# ---------------------------------------------------------------------------

def test_project_to_store_mirrors_put_and_delete(monkeypatch):
    puts: list = []
    dels: list = []
    monkeypatch.setattr(ag, "memory_put", lambda ws, k, t, **kw: puts.append((ws, k, t, kw.get("level"))))
    monkeypatch.setattr(ag, "memory_delete", lambda ws, k: dels.append((ws, k)))

    from core.reflection_service import _project_to_store
    _project_to_store(
        "ws",
        deleted=[{"id": "d1"}, {"id": "d2"}],
        inserted=[{"id": "n1", "insight": "padrão", "level": 2}, {"id": "n2", "insight": ""}],
    )
    assert puts == [("ws", "n1", "padrão", 2)]  # n2 sem texto → não espelha
    assert dels == [("ws", "d1"), ("ws", "d2")]


# ---------------------------------------------------------------------------
# Tool recall_company_learnings (search_insights_for_tool)
# ---------------------------------------------------------------------------

def test_tool_uses_semantic_with_query(monkeypatch):
    monkeypatch.setattr(ag, "memory_search", lambda ws, q, limit=6: [{"insight": "hit semântico", "level": 2}])
    from core.reflection_service import search_insights_for_tool
    out = search_insights_for_tool(db=None, workspace_id="ws", query="trl")
    assert "hit semântico" in out


def test_tool_falls_back_to_static_without_query(monkeypatch):
    import core.reflection_service as rs
    monkeypatch.setattr(rs, "load_active_insights", lambda db, ws, max_total=6: [{"level": 1, "insight": "estático"}])
    out = rs.search_insights_for_tool(db=object(), workspace_id="ws", query="")
    assert "estático" in out


# ---------------------------------------------------------------------------
# WritingSession: injeção query-conditioned com fallback
# ---------------------------------------------------------------------------

def _ws_stub() -> types.SimpleNamespace:
    from core.services.writing_session import WritingSession
    return types.SimpleNamespace(
        workspace_id="ws",
        _reflection_insights_context="BLOCO ESTÁTICO",
        _format_reflection_block=WritingSession._format_reflection_block,
    )


def _build_for_turn(stub, msg, hint):
    from core.services.writing_session import WritingSession
    return WritingSession._build_reflection_context_for_turn(stub, msg, hint)


def test_writing_uses_semantic_when_present(monkeypatch):
    monkeypatch.setattr(ag, "memory_search", lambda ws, q, limit=6: [{"insight": "padrão TRL", "level": 2}])
    out = _build_for_turn(_ws_stub(), "escreva sobre TRL", "Metodologia")
    assert "padrão TRL" in out
    assert "BLOCO ESTÁTICO" not in out


def test_writing_falls_back_to_static_when_store_empty(monkeypatch):
    monkeypatch.setattr(ag, "memory_search", lambda ws, q, limit=6: [])
    out = _build_for_turn(_ws_stub(), "escreva sobre TRL", None)
    assert out == "BLOCO ESTÁTICO"


def test_writing_flag_off_skips_semantic(monkeypatch):
    monkeypatch.setenv("WRITING_SEMANTIC_MEMORY", "0")
    called: list = []
    monkeypatch.setattr(ag, "memory_search", lambda *a, **k: called.append(1) or [])
    out = _build_for_turn(_ws_stub(), "escreva sobre TRL", None)
    assert out == "BLOCO ESTÁTICO"
    assert called == []  # nem chamou o Store
