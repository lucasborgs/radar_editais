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
    out = tool.call({})  # sem param skill_type (só compliance existe)
    assert "REGRAS DA FONTE (finep, compliance)" in out
    assert len(out) > 50  # trouxe conteúdo real do .md, não só o cabeçalho


def test_load_skill_graceful_when_no_source(monkeypatch):
    """Edital sem source (ex.: pitch/fundo) → mensagem amigável, não quebra."""
    tool = _build_load_skill(monkeypatch, {})
    out = tool.call({})
    assert "não tem regras específicas de fonte" in out


def test_load_skill_skips_placeholder_skill(monkeypatch):
    """source com skill PLACEHOLDER (fapesp scaffolding) → tratada como ausente,
    não vaza TODOs como regras (guard em core.skills.load_skill)."""
    tool = _build_load_skill(monkeypatch, {"source": "fapesp"})
    out = tool.call({})
    assert "Sem regras de compliance carregáveis" in out
    assert "TODO" not in out


def test_load_skill_is_in_writer_toolset(monkeypatch):
    """A tool é exposta no toolset do Redator (model-routed)."""
    monkeypatch.setattr("core.kg.kg_store.load_wiki_page", lambda eid: {"source": "finep"})
    names = {t.name for t in writing_tools.build_writing_tools(_FakeSession())}
    assert "load_skill" in names
    assert "plan_writing_session" not in names  # removida no spec 04


# ---------------------------------------------------------------------------
# Guard de placeholder no loader de skills (core.skills) — beneficia tanto a
# tool load_skill quanto o ComplianceMonitor de uma vez.
# ---------------------------------------------------------------------------

def test_core_load_skill_returns_real_content_for_finep():
    from core.skills import load_skill
    assert "Contrapartida" in load_skill("finep", "compliance")


def test_core_load_skill_skips_placeholder():
    """fapesp_compliance.md é scaffolding (marcador PLACEHOLDER) → "" (ausente)."""
    from core.skills import load_skill
    assert load_skill("fapesp", "compliance") == ""
