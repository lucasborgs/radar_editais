"""Testes do orçamento de contexto nas tool-results (spec 02).

Cobre:
  (a) helper `_cap` — trunca/marca, passa intacto abaixo do limite, limite ≤0;
  (b) pointer reference em search_edital (via mock de retrieve_chunks).

O cap CENTRAL no loop é testado no grafo (tests/test_agent_graph_golden.py
::test_graph_caps_tool_result) — pós-migração LangGraph ele vive no nó `tools`.
"""
from __future__ import annotations

from core.llm.agent_runtime import _cap

# ---------------------------------------------------------------------------
# (a): helper _cap
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


# ---------------------------------------------------------------------------
# (b): pointer reference em search_edital (sem InjectedState, sem DB)
# ---------------------------------------------------------------------------

class _FakeSession:
    mode = "proposal"
    session_id = "sess-test"
    _scope_edital_ids = ["EDITAL-1"]
    _db = object()


def test_search_edital_pointer_reference(monkeypatch):
    """search_edital retorna APENAS snippet (250 chars), nunca o texto completo."""
    from core.llm.agent_tools import writing_tools

    big_chunk_text = "z" * 10_000
    fake_chunks = [
        {"id": "chunk-001", "text": big_chunk_text, "section": "Seção A",
         "source_file": "edital.pdf", "edital_id": "EDITAL-1", "score": 0.95},
    ]

    monkeypatch.setattr(
        writing_tools, "retrieve_chunks", lambda *a, **k: fake_chunks,
    )

    tools = writing_tools.build_writing_tools(_FakeSession())
    search_edital = next(t for t in tools if t.name == "search_edital")

    out = search_edital.invoke({"query": "requisitos", "k": 5})

    # O texto de 10k NUNCA aparece na tool-result — só o snippet de 250 chars.
    assert "z" * 10000 not in out
    # O snippet é de no máximo 250 chars + marcador "…"
    assert "z" * 251 not in out
    # O chunk_id deve aparecer no ponteiro
    assert "chunk-001" in out
    # Deve orientar o agente a usar read_exact_chunk para texto completo
    assert "read_exact_chunk" in out
