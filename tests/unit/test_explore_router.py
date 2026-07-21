"""Contrato do texto introdório dos cards do Explorer."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from radar.api.routers.explore import _match_cards_intro

pytestmark = pytest.mark.unit


def test_intro_de_match_usa_a_contagem_exata_dos_cards():
    result = _match_cards_intro(
        [{"kind": "edital"}] * 8,
        [{"kind": "investidor"}] * 5,
    )

    assert "8 oportunidades de fomento" in result
    assert "5 potenciais parceiros de capital" in result
    assert "elegibilidade a confirmar" in result


def test_intro_de_match_soma_programas_a_oportunidades_de_fomento():
    result = _match_cards_intro(
        [{"kind": "edital"}],
        [{"kind": "programa"}, {"kind": "investidor"}],
    )

    assert "2 oportunidades de fomento" in result
    assert "1 potencial parceiro de capital" in result
