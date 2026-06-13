"""Testes da tool load_skill do Redator (spec 05 — skills model-routed híbrido).

Plumbing: a tool resolve a fonte do edital uma vez (via wiki page) e puxa
skills/<source>_<type>.md sob demanda. O ComplianceMonitor (baseline de injeção
automática) não é tocado — aqui só validamos o novo caminho de pull granular.
"""
from __future__ import annotations

from core.llm.agent_tools import writing_tools


class _FakeSession:
    mode = "proposal"
    session_id = "sess-skill"
    _scope_edital_ids = ["EDITAL-1"]
    _db = object()
    edital_id = "EDITAL-1"


def _build_load_skill(monkeypatch, wiki):
    monkeypatch.setattr("core.kg.kg_store.load_wiki_page", lambda eid: wiki)
    tools = writing_tools.build_writing_tools(_FakeSession())
    return next(t for t in tools if t.name == "load_skill")


def test_load_skill_returns_source_rules(monkeypatch):
    """source com skill existente (finep_compliance.md) → devolve o conteúdo."""
    tool = _build_load_skill(monkeypatch, {"source": "finep"})
    out = tool.call({"skill_type": "compliance"})
    assert "REGRAS DA FONTE (finep, compliance)" in out
    assert len(out) > 50  # trouxe conteúdo real do .md, não só o cabeçalho


def test_load_skill_graceful_when_no_source(monkeypatch):
    """Edital sem source (ex.: pitch/fundo) → mensagem amigável, não quebra."""
    tool = _build_load_skill(monkeypatch, {})
    out = tool.call({"skill_type": "compliance"})
    assert "não tem regras específicas de fonte" in out


def test_load_skill_graceful_when_skill_type_absent(monkeypatch):
    """source válido mas sem skills/<source>_writing.md → ausência amigável."""
    tool = _build_load_skill(monkeypatch, {"source": "finep"})
    out = tool.call({"skill_type": "writing"})
    assert "Sem skill 'writing'" in out


def test_load_skill_is_in_writer_toolset(monkeypatch):
    """A tool é exposta no toolset do Redator (model-routed)."""
    monkeypatch.setattr("core.kg.kg_store.load_wiki_page", lambda eid: {"source": "finep"})
    names = {t.name for t in writing_tools.build_writing_tools(_FakeSession())}
    assert "load_skill" in names
    assert "plan_writing_session" not in names  # removida no spec 04
