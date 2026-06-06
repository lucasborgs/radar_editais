"""
Testes dos splitters estrutura-aware de L1 (pipeline/adapters/base.py).

`split_by_numbering` recupera a hierarquia numerada legal (`6.2.2)`, `7)`) que
o achatamento HTML→texto deixa só como numeração (caso FAPESP), alinhando as
units a fronteira de seção. Funções puras — sem I/O.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pipeline.adapters.base import (  # noqa: E402
    _UNIT_MAX_CHARS,
    split_by_numbering,
    split_into_units,
)


def test_segmenta_em_fronteira_de_secao_numerada():
    text = (
        "1) Introdução\nTexto da introdução do edital.\n\n"
        "2) Elegibilidade\nQuem pode participar.\n\n"
        "2.1) Proponente\nDetalhes do proponente.\n\n"
        "3) Cronograma\nDatas importantes."
    )
    units = split_by_numbering(text)
    # Seções pequenas são EMPACOTADAS (não 1 unit/cabeçalho); a 1ª unit começa
    # no 1º cabeçalho e todos os cabeçalhos sobrevivem (cobertura).
    assert units[0].lstrip().startswith("1)")
    joined = "\n".join(units)
    for h in ("1)", "2)", "2.1)", "3)"):
        assert h in joined
    # Invariante central: cabeçalho nunca é "colado" no meio — ou inicia a unit,
    # ou vem após uma fronteira de parágrafo (\n\n).
    for u in units:
        for h in ("2)", "2.1)", "3)"):
            i = u.find(f"{h} ")
            if i > 0:
                assert u[max(0, i - 2):i] == "\n\n", f"{h} colado no meio"


def test_nunca_corta_no_meio_e_respeita_teto():
    # Uma seção grande deve ser sub-dividida, mas o conjunto cobre todo o texto.
    big = "0) Capa\n\n" + "".join(
        f"{i}) Seção {i}\n" + ("parágrafo longo. " * 60) + "\n\n" for i in range(1, 12)
    )
    units = split_by_numbering(big, max_chars=_UNIT_MAX_CHARS)
    joined = " ".join(units)
    # Todo cabeçalho sobrevive (cobertura — nada é perdido na segmentação).
    for i in range(1, 12):
        assert f"{i}) Seção {i}" in joined
    # Sem unit absurdamente gigante (a patologia que motivou o trabalho).
    assert max(len(u) for u in units) <= _UNIT_MAX_CHARS * 2


def test_fallback_sem_numeracao():
    # Texto sem numeração legal → cai em split_into_units (mesmo contrato).
    plain = "Parágrafo um.\n\nParágrafo dois.\n\nParágrafo três."
    assert split_by_numbering(plain) == split_into_units(plain)


def test_vazio():
    assert split_by_numbering("") == []
    assert split_by_numbering("   \n  ") == []


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
