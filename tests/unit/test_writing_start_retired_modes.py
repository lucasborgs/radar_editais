"""A criação de pitch/investidor foi retirada, sem apagar sessões históricas."""
from __future__ import annotations

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from radar.api.common import CompanyProfileSchema
from radar.api.routers import writing

pytestmark = pytest.mark.unit


def _http_request() -> Request:
    return Request({
        "type": "http", "method": "POST", "path": "/writing/start",
        "headers": [], "client": ("127.0.0.1", 12345),
    })


def _start_request(edital_id: str, mode: str | None = None) -> writing.WritingStartRequest:
    return writing.WritingStartRequest(
        edital_id=edital_id,
        profile=CompanyProfileSchema(
            nome="Acme",
            descricao_atividades="Tecnologia para indústria.",
        ),
        mode=mode,
    )


@pytest.mark.parametrize(
    ("edital_id", "mode"),
    [
        ("investidor:acme-capital", None),
        ("finep:123", "pitch"),
        ("programa:centelha", " PITCH "),
    ],
)
def test_writing_start_rejeita_criacao_de_pitch_ou_investidor(edital_id, mode):
    with pytest.raises(HTTPException) as exc:
        writing.writing_start(_http_request(), _start_request(edital_id, mode), "user-1", object())

    assert exc.value.status_code == 410
    assert exc.value.detail["error"] == "writing_mode_retired"


class _ApplicationQuery:
    def __init__(self, db):
        self.db = db

    def select(self, *_args):
        return self

    def eq(self, *_args):
        return self

    def maybe_single(self):
        return self

    def insert(self, payload):
        self.db.inserted.append(payload)
        return self

    def execute(self):
        return type("Result", (), {"data": None})()


class _ApplicationDb:
    def __init__(self):
        self.inserted = []

    def table(self, name):
        assert name == "application_log"
        return _ApplicationQuery(self)


def test_writing_direta_cria_acompanhamento_quando_nao_ha_brief_previo():
    from radar.core.services.writing_session import WritingSession

    session = WritingSession.__new__(WritingSession)
    session._db = _ApplicationDb()

    session._link_application_log(
        "workspace-1", "programa:centelha", "session-1", create_if_missing=True
    )

    assert session._db.inserted == [{
        "workspace_id": "workspace-1",
        "edital_id": "programa:centelha",
        "session_id": "session-1",
        "status": "proposta_iniciada",
    }]
