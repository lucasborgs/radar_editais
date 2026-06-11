"""Testes do matchmaking ICT ↔ edital (core/ict_match — Fase C peça 2)."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import ict_match as im

_ICTS = [
    {"id": "embrapii:a", "name": "Alpha", "kind": "embrapii_unit",
     "themes": ["tecnologias digitais e conectividade"], "areas_raw": ["x", "y"],
     "contact": {"email": "a@x"}, "url": "http://a"},
    {"id": "embrapii:b", "name": "Beta", "kind": "embrapii_unit",
     "themes": ["tecnologias digitais e conectividade", "saúde e ciências da vida"],
     "areas_raw": ["x", "y", "z"], "contact": {}, "url": "http://b"},
    {"id": "embrapii:c", "name": "Gamma", "kind": "embrapii_unit",
     "themes": ["agro - bioeconomia e alimentos"], "areas_raw": ["w"]},
]


def test_rank_orders_by_overlap_then_breadth():
    # Edital com 2 temas: Beta casa 2 (digitais+saúde), Alpha casa 1.
    out = im.rank_partners(
        ["tecnologias digitais e conectividade", "saúde e ciências da vida"], _ICTS
    )
    assert [p.id for p in out] == ["embrapii:b", "embrapii:a"]
    assert out[0].score == 2 and out[0].themes_match == [
        "saúde e ciências da vida", "tecnologias digitais e conectividade"
    ]
    assert out[1].score == 1


def test_no_shared_theme_excluded():
    out = im.rank_partners(["mobilidade e logística"], _ICTS)
    assert out == []


def test_empty_edital_themes_returns_empty():
    assert im.rank_partners([], _ICTS) == []


def test_tiebreak_by_areas_breadth():
    # Mesmo overlap (1, em digitais): Beta tem mais areas_raw (3) que Alpha (2).
    out = im.rank_partners(["tecnologias digitais e conectividade"], _ICTS)
    assert [p.id for p in out] == ["embrapii:b", "embrapii:a"]


def test_k_limits_results():
    out = im.rank_partners(
        ["tecnologias digitais e conectividade"], _ICTS, k=1
    )
    assert len(out) == 1


def test_find_partners_smoke_real_artifacts():
    """Se icts.json e index.json existem, find_partners não deve quebrar e deve
    devolver só ICTs com tema compartilhado."""
    from core.kg import kg_store

    index = kg_store.load_index()
    editais = index.get("editais", [])
    if not editais or not kg_store.load_icts():
        return
    eid = editais[0]["id"]
    edital_themes = set(editais[0].get("themes", []))
    for p in im.find_partners(eid, k=5):
        assert set(p.themes_match) <= edital_themes
        assert p.score >= 1
