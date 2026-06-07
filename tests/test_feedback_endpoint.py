"""Testes do endpoint de feedback do tester (POST /feedback).

Estilo do repo: chama o handler direto com db mockado (sem TestClient). Os
parâmetros CurrentUserId/DbClient são markers de Depends — passamos posicional.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.auth_routes import FeedbackPayload, submit_feedback  # noqa: E402
from fastapi import HTTPException  # noqa: E402


def _db_returning(rows):
    db = MagicMock()
    db.table.return_value.insert.return_value.execute.return_value = SimpleNamespace(data=rows)
    return db


def _insert_payload(db) -> dict:
    return db.table.return_value.insert.call_args.args[0]


def test_feedback_happy_path():
    db = _db_returning([{"id": 42}])
    out = submit_feedback(
        FeedbackPayload(message="  o match não trouxe nada  ", context={"page": "/matching"}),
        user_id="user_1", db=db,
    )
    assert out == {"success": True, "id": 42}
    payload = _insert_payload(db)
    assert payload["user_id"] == "user_1"          # vem do JWT, não do corpo
    assert payload["message"] == "o match não trouxe nada"  # strip aplicado
    assert payload["context"] == {"page": "/matching"}


def test_feedback_empty_message_rejected():
    db = _db_returning([{"id": 1}])
    with pytest.raises(HTTPException) as exc:
        submit_feedback(FeedbackPayload(message="   "), user_id="u", db=db)
    assert exc.value.status_code == 400
    db.table.return_value.insert.assert_not_called()


def test_feedback_truncates_long_message():
    db = _db_returning([{"id": 1}])
    submit_feedback(FeedbackPayload(message="x" * 9000), user_id="u", db=db)
    assert len(_insert_payload(db)["message"]) == 5000


def test_feedback_db_failure_raises_500():
    db = _db_returning([])  # insert sem retorno → falha
    with pytest.raises(HTTPException) as exc:
        submit_feedback(FeedbackPayload(message="oi"), user_id="u", db=db)
    assert exc.value.status_code == 500
