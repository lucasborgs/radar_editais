"""Testes do scratchpad (core/llm/agent_tools/scratchpad_tools).

Chama Tool.call direto (sem LLM). Cobre write/read/overwrite, limites
(max_notes, max_chars), nota inexistente → erro-string.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.llm.agent_tools.scratchpad_tools import Scratchpad, build_scratchpad_tools


def _tools(pad: Scratchpad, **kw):
    tools = {t.name: t for t in build_scratchpad_tools(pad, **kw)}
    assert set(tools) == {"write_note", "read_note"}
    return tools


def test_write_then_read():
    pad = Scratchpad()
    tools = _tools(pad)
    out = tools["write_note"].call({"name": "uf", "content": "SP"})
    assert "salva" in out.lower() and "uf" in out
    assert pad.notes == {"uf": "SP"}
    assert tools["read_note"].call({"name": "uf"}) == "SP"


def test_overwrite_replaces_content():
    pad = Scratchpad()
    tools = _tools(pad)
    tools["write_note"].call({"name": "uf", "content": "SP"})
    tools["write_note"].call({"name": "uf", "content": "RJ"})
    assert pad.notes == {"uf": "RJ"}
    assert tools["read_note"].call({"name": "uf"}) == "RJ"


def test_read_missing_note_lists_available():
    pad = Scratchpad()
    tools = _tools(pad)
    tools["write_note"].call({"name": "cnpj", "content": "x"})
    out = tools["read_note"].call({"name": "uf"})
    assert out.startswith("Erro")
    assert "cnpj" in out  # lista os nomes disponíveis


def test_read_when_empty():
    pad = Scratchpad()
    out = _tools(pad)["read_note"].call({"name": "qualquer"})
    assert "nenhuma nota" in out.lower()


def test_max_chars_limit_returns_error():
    pad = Scratchpad()
    out = _tools(pad, max_chars=5)["write_note"].call(
        {"name": "n", "content": "123456"}
    )
    assert out.startswith("Erro")
    assert pad.notes == {}  # não gravou


def test_max_notes_limit_blocks_new_but_allows_overwrite():
    pad = Scratchpad()
    tools = _tools(pad, max_notes=2)
    tools["write_note"].call({"name": "a", "content": "1"})
    tools["write_note"].call({"name": "b", "content": "2"})
    # terceira nota nova → bloqueada
    out = tools["write_note"].call({"name": "c", "content": "3"})
    assert out.startswith("Erro")
    assert "c" not in pad.notes
    # sobrescrever existente ainda funciona (não conta como nova)
    out = tools["write_note"].call({"name": "a", "content": "novo"})
    assert "salva" in out.lower()
    assert pad.notes["a"] == "novo"


def test_empty_name_rejected():
    pad = Scratchpad()
    out = _tools(pad)["write_note"].call({"name": "  ", "content": "x"})
    assert out.startswith("Erro")
    assert pad.notes == {}
