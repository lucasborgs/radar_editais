"""Contrato do Radar explícito: sem agente e sem vazamento entre tenants."""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from radar.api.common import CompanyProfileSchema
from radar.api.routers import radar

pytestmark = pytest.mark.unit


def _request(**changes) -> radar.RadarMatchesRequest:
    profile = CompanyProfileSchema(
        nome="Acme",
        descricao_atividades="Visão computacional para inspeção industrial.",
        **changes,
    )
    return radar.RadarMatchesRequest(profile=profile)


def _http_request() -> Request:
    return Request({
        "type": "http", "method": "POST", "path": "/radar/matches",
        "headers": [], "client": ("127.0.0.1", 12345),
    })


def _opportunity(kind: str, name: str):
    return SimpleNamespace(kind=kind, to_dict=lambda: {"kind": kind, "name": name})


def _investor(name: str):
    return SimpleNamespace(to_dict=lambda: {"kind": "investidor", "name": name})


def test_profile_minimo_e_obrigatorio():
    with pytest.raises(HTTPException) as exc:
        radar.radar_matches(
            _http_request(), radar.RadarMatchesRequest(profile=CompanyProfileSchema()), None, None,
        )
    assert exc.value.status_code == 422
    assert exc.value.detail == {
        "error": "profile_incomplete",
        "missing_fields": ["nome", "descricao_atividades"],
    }


def test_anonimo_usa_chunks_efemeros_e_separa_trilhas(monkeypatch):
    seen: dict = {}

    def opportunities(profile, **kwargs):
        seen["opps"] = kwargs
        return [_opportunity("edital", "Edital"), _opportunity("programa", "Programa")]

    def investors(profile, **kwargs):
        seen["investors"] = kwargs
        return [_investor("Fundo")]

    monkeypatch.setattr(radar.match_v3, "find_matching_opportunities", opportunities)
    monkeypatch.setattr(radar.match_v3, "find_matching_investors", investors)
    monkeypatch.setattr(radar.match_verdict, "attach_cached_verdicts", lambda *_: pytest.fail("não deveria ler cache"))

    result = radar.radar_matches(_http_request(), _request(), None, None)

    assert [x["name"] for x in result["matched_editais"]] == ["Edital"]
    assert [x["name"] for x in result["matched_programas"]] == ["Programa"]
    assert [x["name"] for x in result["matched_investidores"]] == ["Fundo"]
    assert result["meta"]["uses_workspace_chunks"] is False
    assert seen["opps"]["workspace_id"] is None
    assert seen["investors"]["db"] is None


def test_autenticado_encaminha_workspace_e_enfileira_so_misses(monkeypatch):
    db = object()
    seen: dict = {}
    monkeypatch.setattr(radar, "get_workspace_id", lambda got_db, uid: "workspace-1")

    def opportunities(profile, **kwargs):
        seen["opps"] = kwargs
        return [_opportunity("edital", "Edital")]

    def investors(profile, **kwargs):
        seen["investors"] = kwargs
        return [_investor("Fundo")]

    monkeypatch.setattr(radar.match_v3, "find_matching_opportunities", opportunities)
    monkeypatch.setattr(radar.match_v3, "find_matching_investors", investors)
    monkeypatch.setattr(
        radar.match_verdict,
        "attach_cached_verdicts",
        lambda got_db, ws, items, profile: [{"oportunidade_id": "finep:1", "excerpts": []}],
    )
    monkeypatch.setattr(radar, "_enqueue_verdicts", lambda ws, items, profile: seen.setdefault("queued", (ws, items)))

    result = radar.radar_matches(_http_request(), _request(), "user-1", db)

    assert result["meta"]["uses_workspace_chunks"] is True
    assert seen["opps"]["workspace_id"] == "workspace-1"
    assert seen["opps"]["db"] is db
    assert seen["investors"]["workspace_id"] == "workspace-1"
    assert seen["queued"][0] == "workspace-1"
