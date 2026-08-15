"""Smoke autenticado mínimo da jornada ConsultantGraph (SCV1 T04--T07).

Executa contra backend, Supabase Auth e frontend locais reais. Não usa DEMO_MODE,
JWT forjado, mocks ou um framework de browser. A suíte cria apenas um usuário,
um achado aberto e uma conversa legada temporários; a autenticação das chamadas
do produto usa o access token emitido pelo Supabase Auth.

Uso local:
    INTEGRATION_TARGET=local ENVIRONMENT=test \
      CONSULTANT_SMOKE_API_URL=http://127.0.0.1:8001 \
      CONSULTANT_SMOKE_FRONTEND_URL=http://127.0.0.1:3000 \
      pytest -m integration tests/integration/test_consultant_journey_smoke.py -v

O teste é deliberadamente opt-in por ambiente: sem Supabase local ou uma chave
LLM real, ele é pulado com a causa explícita; com os servidores locais parados,
a execução falha claramente como bloqueio operacional.
"""
from __future__ import annotations

import os
import uuid
from collections.abc import Iterator

import pytest
import requests

from supabase import Client, create_client

_API_URL = os.getenv("CONSULTANT_SMOKE_API_URL", "http://127.0.0.1:8001").rstrip("/")
_FRONTEND_URL = os.getenv(
    "CONSULTANT_SMOKE_FRONTEND_URL", "http://127.0.0.1:3000"
).rstrip("/")


def _local_target(value: str) -> bool:
    return value.startswith(("http://127.0.0.1:", "http://localhost:", "http://backend:"))


def _skip_reason() -> str | None:
    if os.getenv("INTEGRATION_TARGET", "").strip().lower() != "local":
        return "exige INTEGRATION_TARGET=local"
    if os.getenv("ENVIRONMENT", "").strip().lower() not in {"test", "staging"}:
        return "exige ENVIRONMENT=test ou staging"
    if not _local_target(_API_URL) or not _local_target(_FRONTEND_URL):
        return "somente endpoints locais são permitidos"
    if not _local_target(os.getenv("SUPABASE_URL", "")):
        return "SUPABASE_URL local é obrigatório"
    required = (
        "SUPABASE_URL",
        "SUPABASE_ANON_KEY",
        "SUPABASE_SERVICE_KEY",
        "OPENAI_API_KEY",
    )
    missing = [name for name in required if not os.getenv(name)]
    if missing:
        return f"faltam pré-requisitos: {', '.join(missing)}"
    if os.getenv("DEMO_MODE", "").strip().lower() in {"1", "true", "yes", "on"}:
        return "DEMO_MODE é proibido neste smoke"
    return None


pytestmark = [pytest.mark.integration, pytest.mark.skipif(
    _skip_reason() is not None, reason=_skip_reason() or "pré-requisitos ausentes"
)]


def _response_json(response: requests.Response) -> dict:
    try:
        payload = response.json()
    except ValueError:
        payload = {"body": response.text}
    assert response.ok, f"HTTP {response.status_code}: {payload}"
    assert isinstance(payload, dict), payload
    return payload


def _delete_auth_user(service: Client, user_id: str) -> None:
    service.auth.admin.delete_user(user_id)


@pytest.fixture
def authenticated_smoke() -> Iterator[tuple[requests.Session, Client, str, str]]:
    """Cria identidade real e dados temporários para um único smoke."""
    service = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])
    suffix = uuid.uuid4().hex
    email = f"consultant-smoke-{suffix}@example.test"
    password = f"Smoke-{suffix}-9!"
    created = service.auth.admin.create_user({
        "email": email,
        "password": password,
        "email_confirm": True,
    })
    user_id = created.user.id
    opportunity_id: str | None = None
    gold_native_id = f"smoke-gold:{suffix}"

    try:
        auth = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_ANON_KEY"])
        signed_in = auth.auth.sign_in_with_password({"email": email, "password": password})
        token = signed_in.session.access_token

        client = requests.Session()
        client.headers.update({
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        })
        me = _response_json(client.get(f"{_API_URL}/me", timeout=30))
        workspace_id = str(me["workspace_id"])

        opportunity = service.table("discovered_opportunities").insert({
            "url": f"https://example.test/consultant-smoke/{suffix}",
            "url_hash": f"consultant-smoke-{suffix}",
            "title": "Desafio corporativo de manutenção preditiva",
            "agency": "Empresa Smoke",
            "fonte": "web",
            "descricao": "Piloto de inovação aberta para manutenção preditiva industrial.",
            "opportunity_type": "desafio",
            "status": "pending",
            "discovery_channel": "web_curated",
            "raw": {
                "evidence_package": {
                    "identity": {
                        "canonical_url": f"https://example.test/consultant-smoke/{suffix}",
                        "collected_at": "2026-08-08T10:00:00Z",
                    },
                    "page": {"content_hash": f"sha256:{suffix}"},
                }
            },
        }).execute()
        opportunity_id = opportunity.data[0]["id"]

        service.table("entities").insert({
            "kind": "edital",
            "source": "smoke",
            "native_id": gold_native_id,
            "name": "Chamada smoke de subvenção industrial",
            "description": "Apoio público não reembolsável para inovação industrial.",
            "mecanismo": "subvencao",
            "formato": "edital_periodico",
            "setores": ["Indústria"],
            "status": "aberta",
            "deadline": "2099-12-31",
            "requisitos_texto": ["Descreva o problema, a solução e o plano de inovação."],
            "metadata": {"url": f"https://example.test/consultant-smoke/gold/{suffix}"},
        }).execute()

        legacy = service.table("writing_sessions").insert({
            "workspace_id": workspace_id,
            "kind": "frontdoor",
            "title": "Conversa legada do smoke",
            "status": "active",
            "edital_id": None,
            "summary": None,
            "proposal_outline": [],
            "section_drafts": {},
        }).execute()
        legacy_id = legacy.data[0]["id"]
        yield client, service, str(opportunity_id), str(legacy_id)
    finally:
        if opportunity_id:
            service.table("discovered_opportunities").delete().eq("id", opportunity_id).execute()
        service.table("entities").delete().eq("source", "smoke").eq(
            "native_id", gold_native_id,
        ).execute()
        _delete_auth_user(service, user_id)


def test_consultant_graph_t04_to_t07_smoke(authenticated_smoke):
    client, _service, _opportunity_id, legacy_id = authenticated_smoke

    # T07: deep-links antigos continuam apontando para a entrada única da jornada.
    # `/radar` é destino primário (não redireciona); apenas as rotas legadas de
    # escrita retornam à entrada.
    for route in ("/chat", "/workspace/planning"):
        response = requests.get(f"{_FRONTEND_URL}{route}", allow_redirects=False, timeout=30)
        assert response.status_code in {307, 308}, (route, response.status_code, response.text)
        assert response.headers.get("location", "").rstrip("/") in {"", "/"}

    # T07: conversa frontdoor antiga é legível, mas suas mutações estão encerradas.
    entry_payload = {"entry_kind": "radar", "payload": {"status": "pending"}}
    response = client.post(
        f"{_API_URL}/conversations/{legacy_id}/entries", json=entry_payload, timeout=30
    )
    assert response.status_code == 410, response.text
    response = client.patch(
        f"{_API_URL}/conversations/{legacy_id}/entries/1",
        json={"payload": {"status": "dismissed"}},
        timeout=30,
    )
    assert response.status_code == 410, response.text

    # T04: a brief/projeto real encontra um canal aberto em staging, sem edital
    # formal ou elegibilidade implícita.
    response = client.post(
        f"{_API_URL}/consultant/turn",
        json={
            "message": "Quero reduzir perdas industriais com sensores e IA.",
            "idempotency_key": f"smoke-brief-{uuid.uuid4().hex}",
        },
        timeout=120,
    )
    first = _response_json(response)
    conversation_id = first["conversation_id"]
    state = first["state"]
    assert state["pending_confirmation"] is True

    response = client.post(
        f"{_API_URL}/consultant/{conversation_id}/project/confirm",
        json={"expected_revision": state["revision"]},
        timeout=120,
    )
    confirmed = _response_json(response)["state"]
    assert confirmed["project"]["id"] == confirmed["project_id"]

    response = client.post(
        f"{_API_URL}/consultant/turn",
        json={
            "conversation_id": conversation_id,
            "message": "Quero um desafio corporativo de inovação aberta para esse piloto.",
            "idempotency_key": f"smoke-open-{uuid.uuid4().hex}",
            "expected_revision": confirmed["revision"],
        },
        timeout=120,
    )
    open_state = _response_json(response)["state"]
    open_path = next(path for path in open_state["paths"] if path["kind"] == "open_innovation")
    assert open_path["formal_instrument"] is False
    assert open_path["temporal_state"] == "unknown"
    assert any("não é elegibilidade" in risk.lower() for risk in open_path["risks"])

    # T05: seleção única e persistida.
    response = client.post(
        f"{_API_URL}/consultant/{conversation_id}/paths/{open_path['id']}/select",
        json={
            "expected_revision": open_state["revision"],
            "reason": "O piloto é o próximo passo comercial mais viável.",
        },
        timeout=60,
    )
    selected = _response_json(response)["state"]
    persisted = _response_json(
        client.get(f"{_API_URL}/consultant/{conversation_id}", timeout=30)
    )["state"]
    assert selected["selected_path_id"] == open_path["id"]
    assert persisted["selected_path_id"] == open_path["id"]

    # T06: abertura grounded, resolução do plano pendente, turno e revisão.
    response = client.post(
        f"{_API_URL}/writing/grounded/open",
        json={
            "conversation_id": conversation_id,
            "path_id": open_path["id"],
            "artifact_type": "abordagem_mercado",
        },
        timeout=60,
    )
    opened = _response_json(response)
    session_id = opened["writing_session_id"]
    assert opened["context"]["path_id"] == open_path["id"]
    assert opened["artifact_type"] == "abordagem_mercado"

    document = _response_json(
        client.get(f"{_API_URL}/writing/{session_id}/document", timeout=30)
    )
    assert document["plan_pending"] is True
    first_section = document["sections"][0]["title"]

    response = client.post(
        f"{_API_URL}/writing/{session_id}/generate",
        json={"sections": [first_section]},
        timeout=300,
    )
    generated = _response_json(response)
    assert first_section in generated["sections_done"]
    document = _response_json(
        client.get(f"{_API_URL}/writing/{session_id}/document", timeout=30)
    )
    assert document["plan_pending"] is False

    response = client.post(
        f"{_API_URL}/writing/grounded/turn",
        json={
            "session_id": session_id,
            "instruction": "Aprimore a abordagem de mercado para o próximo contato com o promotor.",
            "idempotency_key": f"smoke-writing-{uuid.uuid4().hex}",
        },
        timeout=300,
    )
    turn = _response_json(response)
    assert turn["writing_session_id"] == session_id
    assert turn["context"]["path_id"] == open_path["id"]

    response = client.post(
        f"{_API_URL}/writing/grounded/{session_id}/review", timeout=300
    )
    reviewed = _response_json(response)
    assert reviewed["writing_session_id"] == session_id
    assert isinstance(reviewed["review"], dict)
    assert reviewed["context"]["path_id"] == open_path["id"]
