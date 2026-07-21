"""Geração de proposta completa (batch) — orquestração paralela + agente por seção.

Zero rede: chat model scriptado + save_draft fake. Verifica que a orquestração
(asyncio.gather + Semaphore) despacha cada seção ao agente interno, classifica
done/failed pela persistência real (verify_saved), agrega o resultado e de fato
roda seções em paralelo (respeitando GENERATION_CONCURRENCY).
"""
from __future__ import annotations

import asyncio

import pytest
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import tool
from pydantic import PrivateAttr

import radar.core.llm.agent_graph as ag

pytestmark = pytest.mark.unit

# Estado de persistência dos saves do agente interno (reset por teste).
SAVED: dict[str, str] = {}


class ScriptedChatModel(BaseChatModel):
    """Devolve `responses[i]` na i-ésima chamada (persistente entre invokes —
    compartilhado por todas as runs de seção). Ordem só é determinística com
    GENERATION_CONCURRENCY=1."""
    responses: list
    _idx: int = PrivateAttr(default=0)

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        msg = self.responses[self._idx]
        self._idx += 1
        return ChatResult(generations=[ChatGeneration(message=msg)])

    def bind_tools(self, tools, **kwargs):  # noqa: ANN001
        return self


class ParallelProbeChatModel(BaseChatModel):
    """Modelo content-driven (independe da ordem de chegada): na 1ª chamada de
    cada seção emite o tool_call de save; na 2ª (pós-ToolMessage) encerra.
    Mede o overlap real de runs concorrentes via contador em `_agenerate`."""
    _active: int = PrivateAttr(default=0)
    _max_active: int = PrivateAttr(default=0)

    @property
    def _llm_type(self) -> str:
        return "parallel-probe"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        raise NotImplementedError("use o caminho async")

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        self._active += 1
        self._max_active = max(self._max_active, self._active)
        await asyncio.sleep(0.05)  # janela p/ overlap entre seções
        try:
            if isinstance(messages[-1], ToolMessage):
                msg = _ai("Pronto.")
            else:
                section = next(
                    str(m.content).removeprefix("escreva ")
                    for m in messages if isinstance(m, HumanMessage)
                )
                msg = _save_call(f"tc-{section}", section, f"corpo {section}")
            return ChatResult(generations=[ChatGeneration(message=msg)])
        finally:
            self._active -= 1

    def bind_tools(self, tools, **kwargs):  # noqa: ANN001
        return self


def _ai(text: str, tool_calls: list[dict] | None = None) -> AIMessage:
    return AIMessage(
        content=text,
        tool_calls=[
            {"id": tc["id"], "name": tc["name"], "args": tc["args"], "type": "tool_call"}
            for tc in (tool_calls or [])
        ],
        usage_metadata={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
    )


@tool
def fake_save_draft(section_title: str, content: str) -> str:
    """Salva a seção (fake — escreve no SAVED do teste)."""
    SAVED[section_title] = content
    return f"Rascunho salvo em '{section_title}' ({len(content)} chars)."


def _wire(monkeypatch, responses):
    """Injeta o chat model scriptado + força execução serial (ordem do script)."""
    scripted = ScriptedChatModel(responses=responses)
    monkeypatch.setattr(ag, "_build_chat_model", lambda *a, **k: scripted)
    monkeypatch.setenv("GENERATION_CONCURRENCY", "1")
    return scripted


def _save_call(tcid: str, title: str, content: str) -> AIMessage:
    return _ai("", [{"id": tcid, "name": "fake_save_draft",
                     "args": {"section_title": title, "content": content}}])


def _run(sections):
    return ag.run_generation_turn(
        system="sys",
        build_section_messages=lambda s: [{"role": "user", "content": f"escreva {s}"}],
        sections=sections,
        tools=[fake_save_draft],
        model="m",
        provider="anthropic",
        max_steps=5,
        thread_id="wsA:sess1:generation",
        verify_saved=lambda s: s in SAVED,
    )


def test_generation_writes_all_sections(monkeypatch):
    SAVED.clear()
    # 2 seções: por seção, [chama save_draft, fala final].
    _wire(monkeypatch, [
        _save_call("s1", "Sec A", "corpo A"),
        _ai("Pronto A."),
        _save_call("s2", "Sec B", "corpo B"),
        _ai("Pronto B."),
    ])

    out = _run(["Sec A", "Sec B"])

    assert out.sections_done == ["Sec A", "Sec B"]
    assert out.failed_sections == []
    assert SAVED == {"Sec A": "corpo A", "Sec B": "corpo B"}
    # usage agregado das 2 seções (2 chamadas LLM por seção).
    assert out.usage["input_tokens"] == 40
    assert out.usage["output_tokens"] == 20


def test_generation_marks_unsaved_section_as_failed(monkeypatch):
    SAVED.clear()
    # Seção A salva; seção B o agente encerra SEM salvar → verify_saved=False.
    _wire(monkeypatch, [
        _save_call("s1", "Sec A", "corpo A"),
        _ai("Pronto A."),
        _ai("não vou salvar a B"),  # encerra sem tool call
    ])

    out = _run(["Sec A", "Sec B"])

    assert out.sections_done == ["Sec A"]
    assert out.failed_sections == ["Sec B"]
    assert "Sec B" not in SAVED


def test_generation_empty_sections_is_noop(monkeypatch):
    SAVED.clear()
    _wire(monkeypatch, [])
    out = _run([])
    assert out.sections_done == []
    assert out.failed_sections == []


def test_generation_runs_sections_in_parallel(monkeypatch):
    SAVED.clear()
    probe = ParallelProbeChatModel()
    monkeypatch.setattr(ag, "_build_chat_model", lambda *a, **k: probe)
    monkeypatch.delenv("GENERATION_CONCURRENCY", raising=False)  # default = 4

    sections = ["Sec A", "Sec B", "Sec C", "Sec D"]
    out = _run(sections)

    # Contrato preservado: todas salvas, na ordem de entrada.
    assert out.sections_done == sections
    assert out.failed_sections == []
    assert SAVED == {s: f"corpo {s}" for s in sections}
    # Prova do paralelismo: houve chamadas LLM simultâneas de seções distintas.
    assert probe._max_active >= 2


def test_generation_concurrency_env_clamps_to_one(monkeypatch):
    SAVED.clear()
    probe = ParallelProbeChatModel()
    monkeypatch.setattr(ag, "_build_chat_model", lambda *a, **k: probe)
    monkeypatch.setenv("GENERATION_CONCURRENCY", "1")

    out = _run(["Sec A", "Sec B", "Sec C"])

    assert out.sections_done == ["Sec A", "Sec B", "Sec C"]
    assert probe._max_active == 1  # serial de fato
