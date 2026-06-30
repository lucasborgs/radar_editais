"""Serialização do pipeline (GET /applications).

Foca na lógica pura de junção/serialização (sem rede/HTTP):
  - days_left derivado do prazo do card (futuro → +, passado → -, ausente → None);
  - progress_pct a partir da writing_session (preenchidas/total);
  - edital_title cai para None quando o card não existe.

O wiki page lookup é file-based e cacheado; mockamos `get_edital_by_id` para
isolar a serialização do estado do grafo em disco.
"""
from __future__ import annotations

from datetime import date, timedelta

import backend.routers.applications as api


def _row(**over):
    base = {
        "id": "app-1",
        "edital_id": "finep:1",
        "session_id": None,
        "match_score": 7.5,
        "status": "proposta_iniciada",
        "updated_at": "2026-06-01T00:00:00Z",
    }
    base.update(over)
    return base


# ---- days_left -------------------------------------------------------------

def test_days_left_future_deadline_positive():
    future = (date.today() + timedelta(days=10)).strftime("%d/%m/%Y")
    item = api._serialize_application(_row(), {"title": "Edital X", "deadline": future}, None)
    assert item["days_left"] == 10
    assert item["deadline"] == future
    assert item["edital_title"] == "Edital X"


def test_days_left_past_deadline_negative():
    past = (date.today() - timedelta(days=5)).strftime("%d/%m/%Y")
    item = api._serialize_application(_row(), {"title": "Edital Y", "deadline": past}, None)
    assert item["days_left"] == -5


def test_days_left_missing_deadline_none():
    item = api._serialize_application(_row(), {"title": "Edital Z"}, None)
    assert item["days_left"] is None
    assert item["deadline"] is None


def test_days_left_unparseable_deadline_none():
    item = api._serialize_application(_row(), {"title": "W", "deadline": "em breve"}, None)
    assert item["days_left"] is None


# ---- edital_title fallback -------------------------------------------------

def test_edital_title_none_when_card_missing():
    item = api._serialize_application(_row(), None, None)
    assert item["edital_title"] is None
    assert item["deadline"] is None
    assert item["days_left"] is None


# ---- progress_pct ----------------------------------------------------------

def test_progress_two_of_four_sections():
    session = {
        "proposal_outline": ["A", "B", "C", "D"],
        "section_drafts": {"A": "texto", "B": "mais texto", "C": "", "D": "   "},
    }
    assert api._session_progress_pct(session) == 50


def test_progress_no_session_zero():
    assert api._session_progress_pct(None) == 0


def test_progress_empty_outline_zero():
    session = {"proposal_outline": [], "section_drafts": {"A": "texto"}}
    assert api._session_progress_pct(session) == 0


def test_progress_all_filled():
    session = {
        "proposal_outline": ["A", "B"],
        "section_drafts": {"A": "x", "B": "y"},
    }
    assert api._session_progress_pct(session) == 100


def test_serialize_uses_session_progress():
    session = {
        "proposal_outline": ["A", "B", "C", "D"],
        "section_drafts": {"A": "x", "B": "y"},
    }
    item = api._serialize_application(
        _row(session_id="sess-1"), {"title": "E", "deadline": ""}, session
    )
    assert item["progress_pct"] == 50
    assert item["session_id"] == "sess-1"


# ---- _build_pipeline_items (junção wiki + sessions em lote) -----------------

def test_build_pipeline_items_maps_card_and_session(monkeypatch):
    future = (date.today() + timedelta(days=3)).strftime("%d/%m/%Y")
    cards = {
        "finep:1": {"title": "Edital Um", "deadline": future},
        "finep:2": None,  # card ausente
    }
    monkeypatch.setattr(api.hypergraph_catalog, "get_edital", lambda eid: cards.get(eid))

    rows = [
        _row(id="a1", edital_id="finep:1", session_id="s1"),
        _row(id="a2", edital_id="finep:2", session_id=None),
    ]
    sessions_by_id = {
        "s1": {"proposal_outline": ["A", "B"], "section_drafts": {"A": "x", "B": "y"}},
    }

    items = api._build_pipeline_items(rows, sessions_by_id)

    assert items[0]["application_id"] == "a1"
    assert items[0]["edital_title"] == "Edital Um"
    assert items[0]["days_left"] == 3
    assert items[0]["progress_pct"] == 100

    assert items[1]["application_id"] == "a2"
    assert items[1]["edital_title"] is None
    assert items[1]["days_left"] is None
    assert items[1]["progress_pct"] == 0
