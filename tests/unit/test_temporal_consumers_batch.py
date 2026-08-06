from __future__ import annotations

import pytest

from radar.api.routers import writing
from radar.core.kg import entity_catalog
from radar.core.services.temporal_read_model import TemporalReadModel
from radar.domain.data_quality import TemporalMode, ValidityState

pytestmark = pytest.mark.unit


def _program_temporal() -> TemporalReadModel:
    return TemporalReadModel(
        temporal_mode=TemporalMode.FIXED,
        validity_state=ValidityState.ACTIVE,
        temporal_value="2026-12-31",
        decision_source="source",
        last_verified_at="2026-07-29T12:00:00+00:00",
    )


def test_investor_preserves_legacy_card_without_temporal_payload():
    row = {
        "id": "investor-id", "native_id": "investidor:acme", "source": "curadoria",
        "kind": "investidor", "name": "Acme Capital", "description": "Tese",
        "status": "ativa", "deadline": None, "setores": [], "tecnologias_tags": [],
        "mecanismo": None, "formato": None, "requisitos_texto": [], "constraints": [],
        "ticket_min": None, "ticket_max": None, "metadata": {}, "updated_at": "",
    }

    card = entity_catalog._curated_card(row, agencies={})

    assert card["status"] == "ABERTA"
    assert not {"temporal_mode", "validity_state", "temporal_value", "decision_source", "last_verified_at"} & set(card)


def test_get_investor_opportunity_returns_none_and_does_not_load_temporal(monkeypatch):
    row = {
        "id": "investor-id", "native_id": "investidor:acme", "source": "curadoria",
        "kind": "investidor", "name": "Acme Capital", "description": "Tese",
        "status": "ativa", "deadline": None, "setores": [], "tecnologias_tags": [],
        "mecanismo": None, "formato": None, "requisitos_texto": [], "constraints": [],
        "ticket_min": None, "ticket_max": None, "metadata": {}, "updated_at": "",
    }
    monkeypatch.setattr(entity_catalog, "_client", lambda: object())
    monkeypatch.setattr(entity_catalog, "_resolve_native", lambda *_: row)
    monkeypatch.setattr(entity_catalog, "_rel_names_batch", lambda *_: {})
    monkeypatch.setattr(
        entity_catalog, "_temporal_for_rows",
        lambda _: (_ for _ in ()).throw(AssertionError("investidor não é temporal")),
    )

    card = entity_catalog.get_opportunity("investidor:acme")

    # Investidores desativados das superfícies ativas: a ficha nem chega a ser
    # montada (sem leitura temporal), o router responde 404.
    assert card is None


def test_program_card_receives_canonical_temporal_payload():
    row = {
        "id": "program-id", "native_id": "programa:acme", "source": "curadoria",
        "kind": "programa", "name": "Programa", "description": "Programa",
        "status": "ativa", "deadline": "2026-12-31", "setores": [], "tecnologias_tags": [],
        "mecanismo": None, "formato": None, "requisitos_texto": [], "constraints": [],
        "ticket_min": None, "ticket_max": None, "metadata": {}, "updated_at": "",
    }

    card = entity_catalog._curated_card(row, agencies={}, temporal=_program_temporal())

    assert card["status"] == "ABERTA"
    assert card["validity_state"] == "active"
    assert card["temporal_value"] == "2026-12-31"


def test_writing_session_titles_are_one_simple_batch_without_temporal(monkeypatch):
    sessions = [{"edital_id": "finep:1"}, {"edital_id": "programa:2"}, {"edital_id": "investidor:3"}]
    calls = []

    def titles(ids):
        calls.append(set(ids))
        return {"finep:1": "Edital", "programa:2": "Programa", "investidor:3": "Fundo"}

    monkeypatch.setattr(writing.entity_catalog, "get_opportunity_titles", titles)
    monkeypatch.setattr(
        writing.entity_catalog, "get_edital",
        lambda _: (_ for _ in ()).throw(AssertionError("lista usa só títulos")),
    )

    writing._attach_target_titles(sessions)

    assert calls == [{"finep:1", "programa:2", "investidor:3"}]
    assert [item["edital_title"] for item in sessions] == ["Edital", "Programa", "Fundo"]
