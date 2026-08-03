"""Testes do prompt caching Anthropic (PR2 — hardening pré-beta).

Cobrem o contrato produtor/consumidor da flag `cache_hint`:
  • produtores de escrita/explore marcam
    os dicts-breakpoint com `"cache_hint": True`;
  • consumidor (`agent_graph._to_lc_messages` / `_build_system_message`) consome
    a flag SEMPRE (ela nunca vaza para a API) e só a converte em `cache_control`
    ephemeral quando provider == "anthropic" — nos demais providers o conteúdo
    fica byte-a-byte idêntico ao anterior.

E a posição do bloco temporal (§2.1): sai do prefixo estável e vai para o tail
dinâmico (depois do history), sem deixar de existir — muda diariamente
(days_remaining), o que é correto, mas não pode invalidar o prefixo de cache.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from radar.core.llm.agent_graph import (
    _build_system_message,
    _to_lc_messages,
)
from radar.core.services.writing_session import WritingSession

pytestmark = pytest.mark.unit

_CACHED = {"type": "ephemeral"}


def _mk_dicts() -> list[dict]:
    """Lista-fixture com um breakpoint marcado e mensagens sem marca."""
    return [
        {"role": "user", "content": "PERFIL DA EMPRESA:\nACME Bio", "cache_hint": True},
        {"role": "assistant", "content": "entendido"},
        {"role": "user", "content": "qual o prazo?", "cache_hint": True},
        {"role": "user", "content": "sem marca"},
    ]


# ============================================================================
# (a) cache_control só com provider anthropic
# ============================================================================

def test_cache_control_applied_only_with_anthropic():
    msgs = _to_lc_messages(_mk_dicts(), provider="anthropic")
    assert msgs[0].content == [
        {"type": "text", "text": "PERFIL DA EMPRESA:\nACME Bio", "cache_control": _CACHED},
    ]
    assert msgs[2].content == [
        {"type": "text", "text": "qual o prazo?", "cache_control": _CACHED},
    ]
    # Mensagens sem marca ficam como string mesmo no caminho Anthropic.
    assert msgs[1].content == "entendido"
    assert msgs[3].content == "sem marca"


def test_no_cache_control_for_openai_and_default():
    for kwargs in ({"provider": "openai"}, {"provider": None}, {}):
        msgs = _to_lc_messages(_mk_dicts(), **kwargs)
        for m in msgs:
            assert isinstance(m.content, str), f"content virou blocos com {kwargs!r}"
            assert "cache_control" not in str(m.content)


def test_roles_preserved_with_and_without_hint():
    msgs = _to_lc_messages(_mk_dicts(), provider="anthropic")
    assert isinstance(msgs[0], HumanMessage)
    assert isinstance(msgs[1], AIMessage)
    assert isinstance(msgs[2], HumanMessage)
    assert isinstance(msgs[3], HumanMessage)


# ============================================================================
# (b) a flag cache_hint é consumida em TODOS os providers (nunca vaza)
# ============================================================================

def test_cache_hint_popped_from_dicts_all_providers():
    for provider in ("anthropic", "openai", None):
        dicts = _mk_dicts()
        _to_lc_messages(dicts, provider=provider)
        for m in dicts:
            assert "cache_hint" not in m, f"cache_hint vazou com provider={provider!r}"


def test_cache_hint_popped_with_default_call():
    """O caminho batch chama sem kwarg de provider — a flag some do mesmo jeito."""
    dicts = _mk_dicts()
    _to_lc_messages(dicts)
    assert all("cache_hint" not in m for m in dicts)


# ============================================================================
# (c) o texto do content permanece idêntico
# ============================================================================

def test_text_identical_after_conversion():
    originals = [m["content"] for m in _mk_dicts()]
    anthropic_msgs = _to_lc_messages(_mk_dicts(), provider="anthropic")
    openai_msgs = _to_lc_messages(_mk_dicts(), provider="openai")

    for orig, m in zip(originals, openai_msgs, strict=True):
        assert m.content == orig  # byte-a-byte, sem blocos

    for orig, m in zip(originals, anthropic_msgs, strict=True):
        if isinstance(m.content, list):  # breakpoint → blocos
            assert m.content[0]["text"] == orig
        else:
            assert m.content == orig


def test_build_system_message_per_provider():
    system = "Você é o WritingAgent."
    anth = _build_system_message(system, "anthropic")
    assert isinstance(anth, SystemMessage)
    assert anth.content == [
        {"type": "text", "text": system, "cache_control": _CACHED},
    ]

    for provider in ("openai", None):
        other = _build_system_message(system, provider)
        assert other.content == system  # string simples, idêntica


# ============================================================================
# (d) bloco temporal no tail dinâmico (depois do history) nos builders
# ============================================================================

def _make_session() -> WritingSession:
    """WritingSession sem __init__ (que exige DB real) — mesmo padrão de
    tests/unit/test_writing_session_agent.py."""
    s = WritingSession.__new__(WritingSession)
    s.session_id = "sess_pc"
    s.workspace_id = "ws_pc"
    s.edital_id = "ed_pc"
    s._db = MagicMock()
    s._scope_edital_ids = ["ed_pc"]
    s._doc_sections = {}
    s._proposal_outline = ["1. Identificação", "2. Objeto"]
    s._library_item_ids = set()
    s._history = []
    s._history_summary = ""
    s._turn_count = 0
    s._profile_context = "Empresa: ACME Bio. Setor: bioeconomia."
    s._library_context = ""
    s._reflection_insights_context = ""
    s._temporal_block = "[CONTEXTO TEMPORAL: hoje é 2026-07-01. Prazo em 10 dia(s).]"
    s.mode = "proposal"
    s._source_card_context = ""
    s._programa_context = ""
    s._project_description = None
    s._pending_user_input = None
    s._plan = None
    s._plan_pending_confirmation = False
    s._playbook_writer_block = ""  # F5: vazio — nenhum mecanismo resolvido
    s._playbook_monitor_block = ""  # F5: vazio
    s._estilo_empresa_block = ""  # estilo de escrita — vazio por padrão (plano playbook-overlays)
    s.backend = "anthropic"
    s.model = "claude-sonnet-4-6"
    return s


def _index_of(msgs: list[dict], text: str) -> int:
    return next(i for i, m in enumerate(msgs) if text in str(m["content"]))


def test_temporal_in_tail_of_generation_builder():
    s = _make_session()
    s._project_description = "projeto piloto"

    msgs = s._build_generation_section_messages("2. Objeto")

    i_temporal = _index_of(msgs, "CONTEXTO TEMPORAL")
    i_outline = _index_of(msgs, "OUTLINE COMPLETO")
    assert i_temporal > i_outline, "temporal deve vir depois do prefixo estável"
    # Comando da seção segue sendo a última mensagem.
    assert "2. Objeto" in msgs[-1]["content"]
    # Batch é OpenAI por default (spec PR2): nenhum breakpoint marcado.
    assert not any(m.get("cache_hint") for m in msgs)


# ============================================================================
# Explore: breakpoint na mensagem atual do usuário
# ============================================================================

def test_explore_marks_current_user_message(monkeypatch):
    from radar.core.llm.agent_runtime import AgentResult
    from radar.core.services.explore_agent import ExploreAgent

    captured: dict = {}

    def fake_run_agent(**kw):
        captured["msgs"] = kw["initial_messages"]
        return AgentResult(final_text="ok", steps=[], stop_reason="end_turn",
                           usage={"input_tokens": 0, "output_tokens": 0})

    monkeypatch.setattr("radar.core.llm.agent_runtime.run_agent", fake_run_agent)
    monkeypatch.setattr(
        "radar.core.services.grounded_strategy.judge_grounding",
        lambda *args, **kwargs: {
            "requires_graph": False, "grounded": True, "unsupported_claims": [],
        },
    )

    svc = ExploreAgent()
    svc._explore_agent("qual o prazo?", history=[{"role": "user", "content": "antes"}],
                       edital_ids=None, node_id=None, node_type=None)

    msgs = captured["msgs"]
    assert msgs[-1]["content"] == "qual o prazo?"
    assert msgs[-1].get("cache_hint") is True
    # Só a mensagem atual é breakpoint no explore (system tem o dele no consumidor).
    assert not any(m.get("cache_hint") for m in msgs[:-1])
