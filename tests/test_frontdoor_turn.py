"""Testes do front-door (deltas B1-B2) — partes determinísticas, SEM LLM real.

Estilo do repo: chama os handlers/métodos direto, mockando explore/LLM/build_radar.
Cobre:
  (a) /frontdoor/turn com mensagem vazia → 422
  (b) atalho de mensagem curta pula a extração de diff (sem chamar LLM)
  (c) montagem do diff (old/new/label) a partir de um retorno de extração mockado
  (d) /match/radar sem auth não levanta 401 (build_radar mockado)
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fastapi import HTTPException  # noqa: E402

from backend.common import CompanyProfileSchema  # noqa: E402
from backend.rate_limit import limiter  # noqa: E402
from core.profile_extractor import ProfileExtractor  # noqa: E402


@pytest.fixture(autouse=True)
def _disable_rate_limit():
    """Desliga o slowapi na suíte: com `limiter.enabled=False` o decorador
    @limiter.limit no-opa e o handler roda chamado direto (sem ASGI/Request real)."""
    prev = limiter.enabled
    limiter.enabled = False
    yield
    limiter.enabled = prev


def _fake_request() -> MagicMock:
    """Request mockado — só ocupa o parâmetro `request` dos handlers."""
    return MagicMock()


# ============================================================================
# (a) mensagem vazia → 422
# ============================================================================

def test_frontdoor_turn_empty_message_422():
    from backend.routers.frontdoor import FrontdoorTurnRequest, frontdoor_turn

    with pytest.raises(HTTPException) as exc:
        frontdoor_turn(
            _fake_request(),
            FrontdoorTurnRequest(message="   "),
            user_id=None,
            db=None,
        )
    assert exc.value.status_code == 422


# ============================================================================
# (b) atalho de mensagem curta pula a extração
# ============================================================================

def test_short_message_skips_diff_extraction(monkeypatch):
    extractor = ProfileExtractor()
    # Se o LLM for chamado, falha o teste.
    monkeypatch.setattr(
        extractor, "_call_diff_llm",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("não deve chamar LLM")),
    )
    out = extractor.extract_diff_from_message("ok", {})
    assert out == []


def test_frontdoor_turn_short_message_no_diff(monkeypatch):
    """Mensagem curta: explore roda (resposta), mas diff é pulado → profile_diff None."""
    from backend.routers import frontdoor

    monkeypatch.setattr(frontdoor.kg_service, "explore", lambda *a, **k: "resposta curta")
    # garante que o LLM de diff nunca é chamado para mensagem curta
    monkeypatch.setattr(
        ProfileExtractor, "_call_diff_llm",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("não deve chamar LLM")),
    )

    out = frontdoor.frontdoor_turn(
        _fake_request(),
        frontdoor.FrontdoorTurnRequest(message="oi?"),
        user_id=None,
        db=None,
    )
    assert out["answer"] == "resposta curta"
    assert out["profile_diff"] is None
    # Anônimo: nada persiste, response sem session_id/entry_ids.
    assert "session_id" not in out
    assert "entry_ids" not in out


# ============================================================================
# (c) montagem do diff (old/new/label)
# ============================================================================

def test_build_diff_from_mocked_extraction(monkeypatch):
    extractor = ProfileExtractor()
    # mensagem longa o suficiente para passar do atalho determinístico
    msg = "Somos uma healthtech de São Paulo com TRL 6 e fundada em 2019."
    monkeypatch.setattr(
        extractor, "_call_diff_llm",
        lambda message, current: {"trl": 6, "uf": "SP", "ano_fundacao": 2019},
    )
    diff = extractor.extract_diff_from_message(msg, {"uf": "", "trl": None})

    by_field = {d["field"]: d for d in diff}
    assert by_field["trl"] == {"field": "trl", "label": "TRL", "old": None, "new": 6}
    assert by_field["uf"]["label"] == "UF"
    assert by_field["uf"]["old"] is None
    assert by_field["ano_fundacao"]["new"] == 2019


def test_build_diff_drops_unchanged_and_unknown_fields():
    # old == new → fora; campo desconhecido → fora; vazio → fora.
    raw = {
        "trl": 6,                 # muda (old 5) → entra
        "uf": "SP",               # igual ao atual → fora
        "campo_inexistente": "x",  # desconhecido → fora
        "nome": "",               # vazio → fora
    }
    diff = ProfileExtractor._build_diff(raw, {"trl": 5, "uf": "SP"})
    fields = {d["field"] for d in diff}
    assert fields == {"trl"}
    assert diff[0]["old"] == 5
    assert diff[0]["new"] == 6


def test_extract_diff_empty_when_llm_returns_nothing(monkeypatch):
    extractor = ProfileExtractor()
    monkeypatch.setattr(extractor, "_call_diff_llm", lambda message, current: {})
    out = extractor.extract_diff_from_message(
        "Uma mensagem longa o suficiente mas sem fatos novos de perfil.", {},
    )
    assert out == []


# ============================================================================
# (d) /match/radar sem auth não levanta 401
# ============================================================================

def _complete_profile() -> CompanyProfileSchema:
    return CompanyProfileSchema(
        nome="ACME",
        tipo_entidade="startup",
        tamanho_empresa="ME",
        one_liner="Faz X",
        solution_summary="Resolve Y com Z",
        descricao_atividades="Desenvolve X para Y",
        trl=6,
        tipos_financiamento_interesse=["subvencao_nao_reembolsavel"],
    )


def test_match_radar_anonymous_no_401(monkeypatch):
    from backend.routers import matching

    captured = {}

    def fake_build_radar(profile, *, top_k, per_type_cap, workspace_id):
        captured["workspace_id"] = workspace_id
        return {"items": [], "top_k": top_k}

    monkeypatch.setattr(
        "core.services.radar_service.build_radar", fake_build_radar,
    )

    # user_id=None, db=None simulam anônimo (OptionalUserId/OptionalDbClient).
    out = matching.match_radar(
        _fake_request(),
        matching.RadarRequest(profile=_complete_profile()),
        user_id=None,
        db=None,
    )
    assert out == {"items": [], "top_k": 10}
    # sem usuário → pesos globais default (workspace_id None), não resolve workspace.
    assert captured["workspace_id"] is None


def test_match_radar_incomplete_profile_422():
    from backend.routers import matching

    with pytest.raises(HTTPException) as exc:
        matching.match_radar(
            _fake_request(),
            matching.RadarRequest(profile=CompanyProfileSchema(nome="só nome")),
            user_id=None,
            db=None,
        )
    assert exc.value.status_code == 422
