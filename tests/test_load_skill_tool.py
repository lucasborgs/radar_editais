"""Testes do playbook loader (core.skills) e da tool load_skill do Redator.

Modelo: skill = competência keyed por
`mechanism`, composta em 3 camadas (mechanism base + source/global + source/mech),
merge POR SEÇÃO, com cada `##` roteando a um consumidor. Regra dura é RAG, não
playbook.
"""
from __future__ import annotations

from core import skills
from core.llm.agent_tools import writing_tools
from core.skills import load_playbook

# ── Loader: composição em 3 camadas, merge por seção ────────────────────────

def test_compose_base_plus_source_overlay_by_section():
    """finep+subvencao compõe mechanism/subvencao.md + source/finep/{global,subvencao}.md."""
    pb = load_playbook("subvencao", "finep")
    assert pb.mechanism == "subvencao"
    assert pb.source == "finep"
    assert pb.confidence == "ok"
    # Lente capturada (prosa antes do 1º ##).
    assert "lente" in pb.lente.lower()
    # Seção do base presente.
    assert skills.SECTION_WRITING in pb.sections
    # Praxe da agência veio do overlay finep (global + subvencao mesclados na seção).
    praxe = pb.sections.get(skills.SECTION_PRAXE, "")
    assert "FINEP" in praxe or "industrial" in praxe.lower()
    assert "propositivo" in praxe.lower()  # veio do global.md


def test_writer_payload_has_lente_and_writing_not_heuristics():
    """for_writer = Lente + Padrões de escrita + Praxe; NÃO leva heurísticas de avaliação."""
    pb = load_playbook("subvencao", "finep")
    writer = pb.for_writer()
    assert f"## {skills.SECTION_LENTE}" in writer
    assert f"## {skills.SECTION_WRITING}" in writer
    assert skills.SECTION_HEURISTICS not in writer
    assert skills.SECTION_ANTIPATTERNS not in writer


def test_monitor_payload_has_heuristics_not_lente():
    """for_monitor = Heurísticas + Anti-padrões (+ Praxe); sem Lente nem Padrões."""
    pb = load_playbook("subvencao", "finep")
    monitor = pb.for_monitor()
    assert f"## {skills.SECTION_HEURISTICS}" in monitor
    assert f"## {skills.SECTION_ANTIPATTERNS}" in monitor
    assert f"## {skills.SECTION_LENTE}" not in monitor
    assert f"## {skills.SECTION_WRITING}" not in monitor


def test_footer_fato_craft_is_dropped():
    """O rodapé '**Fato ...**' (após '---') é meta de autoria — não vaza p/ prompt."""
    pb = load_playbook("subvencao", "finep")
    blob = pb.for_writer() + pb.for_monitor()
    assert "vem do edital via RAG" not in blob
    assert "Regra de bolso" not in blob


def test_unknown_mechanism_falls_back_low_confidence():
    """mechanism None/desconhecido → confidence low + marcador; NÃO quebra (D3)."""
    pb = load_playbook(None, "finep")
    assert pb.confidence == "low"
    pb2 = load_playbook("instrumento-inexistente", "finep")
    assert pb2.mechanism is None
    assert pb2.confidence == "low"


def test_investimento_synonym_maps_to_equity():
    """'investimento' normaliza para equity (KG v2 PR2 — fix da colisão com o
    display 'Investimento' de equity; antes mapeava p/ credito)."""
    pb = load_playbook("investimento", "finep")
    assert pb.mechanism == "equity"


def test_equity_loads_without_source():
    """equity (pitch) compõe só o base, sem overlay de fonte."""
    pb = load_playbook("equity", "")
    assert pb.mechanism == "equity"
    assert skills.SECTION_WRITING in pb.sections


# ── Tool load_skill do Redator (pull do playbook de escrita) ────────────────

class _FakeSession:
    mode = "proposal"
    session_id = "sess-skill"
    _scope_edital_ids = ["finep:734"]
    _db = object()
    edital_id = "finep:734"  # agência = prefixo (não o campo `source` da wiki)


class _FakePitchSession(_FakeSession):
    mode = "pitch"
    edital_id = "investidor:fundo-x"


def _build_load_skill(monkeypatch, card_data, session=None):
    mock_get = lambda eid: {"mechanism": card_data.get("mechanism", ""), "key_requirements": []} if card_data else None  # noqa: E731
    monkeypatch.setattr("core.kg.hypergraph_catalog.get_edital", mock_get)
    tools = writing_tools.build_writing_tools(session or _FakeSession())
    return next(t for t in tools if t.name == "load_skill")


def test_tool_keys_agency_from_edital_id_prefix_not_source_field(monkeypatch):
    """edital_id=finep:734 → overlay FINEP casa, mesmo sem campo source."""
    tool = _build_load_skill(
        monkeypatch, {"mechanism": "subvencao"},
    )
    out = tool.invoke({})
    assert "PLAYBOOK DE ESCRITA (subvencao · finep)" in out
    assert "Padrões de escrita" in out
    assert "propositivo" in out.lower()  # veio do overlay source/finep/global.md
    assert len(out) > 100


def test_tool_graceful_when_no_mechanism(monkeypatch):
    """Sem mechanism resolvível → mensagem amigável (fallback genérico vazio)."""
    tool = _build_load_skill(monkeypatch, {"mechanism": ""})
    out = tool.invoke({})
    assert "Sem playbook de escrita" in out


def test_tool_pitch_session_uses_equity(monkeypatch):
    """mode=pitch → mechanism=equity, sem depender de wiki page de edital."""
    tool = _build_load_skill(monkeypatch, None, session=_FakePitchSession())
    out = tool.invoke({})
    assert "PLAYBOOK DE ESCRITA (equity" in out


def test_tool_is_in_writer_toolset(monkeypatch):
    monkeypatch.setattr(
        "core.kg.hypergraph_catalog.get_edital",
        lambda eid: {"mechanism": "subvencao", "key_requirements": []},
    )
    names = {t.name for t in writing_tools.build_writing_tools(_FakeSession())}
    assert "load_skill" in names
    assert "plan_writing_session" not in names  # removida no spec 04
