"""Testes do orçamento de contexto nas tool-results (spec 02).

Cobre:
  (a) tool que devolve 50k chars → result capado com marcador de truncamento;
  (b) output abaixo do cap passa intacto;
  (c) cap por-chunk em search_edital (via mock de retrieve_chunks).
"""
from __future__ import annotations

from core.llm import agent_runtime
from core.llm.agent_runtime import (
    TOOL_RESULT_CHAR_CAP,
    _cap,
    _LLMStep,
    run_agent,
    tool,
)


# ---------------------------------------------------------------------------
# (a) + (b): helper _cap e cap central no loop
# ---------------------------------------------------------------------------

def test_cap_trunca_e_marca():
    text = "x" * 50_000
    out = _cap(text, 8000)
    assert len(out) < len(text)
    assert out.startswith("x" * 8000)
    assert "…[truncado:" in out
    assert "42000 chars omitidos" in out  # 50000 - 8000


def test_cap_passa_intacto_abaixo_do_limite():
    text = "conteúdo curto"
    assert _cap(text, 8000) == text


def test_cap_limite_zero_ou_negativo_nao_trunca():
    text = "x" * 100
    assert _cap(text, 0) == text
    assert _cap(text, -5) == text


def _step(*, tool_uses, text=""):
    return _LLMStep(
        stop_reason="tool_use" if tool_uses else "end_turn",
        text=text,
        tool_uses=tool_uses,
        assistant_message={"role": "assistant", "content": text},
        usage={"input_tokens": 0, "output_tokens": 0},
        raw_response=None,
    )


def test_cap_central_no_loop_trunca_tool_result(monkeypatch):
    """Uma tool que devolve 50k chars deve ter o output capado no histórico."""

    @tool
    def big_tool() -> str:
        """Devolve um output enorme."""
        return "y" * 50_000

    captured: dict[str, str] = {}

    real_format = agent_runtime._format_tool_results_anthropic

    def spy_format(tool_results):
        # captura o output já capado que iria pro histórico
        captured["output"] = tool_results[0]["output"]
        return real_format(tool_results)

    # Primeira chamada do modelo: pede a tool. Segunda: encerra o turno.
    steps = iter([
        _step(tool_uses=[{"id": "t1", "name": "big_tool", "input": {}}]),
        _step(tool_uses=[], text="pronto"),
    ])

    monkeypatch.setattr(
        agent_runtime, "_call_anthropic", lambda *a, **k: next(steps),
    )
    monkeypatch.setattr(
        agent_runtime, "_format_tool_results_anthropic", spy_format,
    )

    result = run_agent(
        system="sys",
        initial_messages=[{"role": "user", "content": "vai"}],
        tools=[big_tool],
        model="fake",
        provider="anthropic",
        max_steps=4,
    )

    out = captured["output"]
    assert len(out) <= TOOL_RESULT_CHAR_CAP + 80  # cap + marcador
    assert "…[truncado:" in out
    assert result.final_text == "pronto"


# ---------------------------------------------------------------------------
# (c): cap por-chunk em search_edital
# ---------------------------------------------------------------------------

class _FakeSession:
    mode = "proposal"
    session_id = "sess-test"
    _scope_edital_ids = ["EDITAL-1"]
    _db = object()


def test_search_edital_cap_por_chunk(monkeypatch):
    from core.llm.agent_tools import writing_tools

    big_chunk_text = "z" * 10_000
    fake_chunks = [
        {"text": big_chunk_text, "section": "Seção A", "source_file": "edital.pdf",
         "edital_id": "EDITAL-1"},
    ]

    monkeypatch.setattr(
        writing_tools, "retrieve_chunks", lambda *a, **k: fake_chunks,
    )

    tools = writing_tools.build_writing_tools(_FakeSession())
    search_edital = next(t for t in tools if t.name == "search_edital")

    out = search_edital.call({"query": "requisitos", "k": 5})

    # O chunk de 10k deve ter sido truncado para ~1500 chars + marcador.
    assert "z" * writing_tools.SEARCH_EDITAL_CHUNK_CHAR_CAP in out
    assert "z" * (writing_tools.SEARCH_EDITAL_CHUNK_CHAR_CAP + 100) not in out
    assert "…[truncado:" in out
