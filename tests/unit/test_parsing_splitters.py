"""
Testes dos splitters estrutura-aware de L1 (pipeline/adapters/base.py).

Funções puras que preservam a estrutura de seções — sem I/O.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

def test_blocks_from_typed_section_path_por_numeracao():
    from pipeline.adapters.base import blocks_from_typed
    items = [
        ("heading", "1. Objetivo"),
        ("paragraph", "Texto do objetivo."),
        ("heading", "4. Critérios"),
        ("heading", "4.1. Eliminatórios"),
        ("paragraph", "São eliminatórios..."),
        ("heading", "4.2. Classificatórios"),
        ("list", "a) primeiro"),
        ("heading", "5. Prazos"),
        ("paragraph", "Datas."),
    ]
    blocks = blocks_from_typed(items)
    sp = {b["text"]: b["section_path"] for b in blocks}
    # 4.1 herda 4 como ancestral; 4.2 substitui 4.1 mantendo 4; 5 reseta
    assert sp["São eliminatórios..."] == ["4. Critérios", "4.1. Eliminatórios"]
    assert sp["a) primeiro"] == ["4. Critérios", "4.2. Classificatórios"]
    assert sp["Datas."] == ["5. Prazos"]
    assert sp["Texto do objetivo."] == ["1. Objetivo"]


def test_blocks_from_numbered_text():
    from pipeline.adapters.base import blocks_from_numbered_text
    text = "Preâmbulo.\n\n1) Objetivo\nFomentar.\n\n2) Elegibilidade\nEmpresas.\n\n2.1) Porte\nME e EPP."
    blocks = blocks_from_numbered_text(text)
    sp = {b["text"][:20]: b["section_path"] for b in blocks if b["kind"] == "paragraph"}
    assert sp["Fomentar."] == ["1) Objetivo"]
    assert sp["ME e EPP."] == ["2) Elegibilidade", "2.1) Porte"]
