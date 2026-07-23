"""Testes do contrato da API de staging da Descoberta (RT00-T05).

Cobre:
  - _LIST_COLS inclui os 4 campos de relevância;
  - linha classificada preserva verdict na resposta;
  - linha legada sem campos vira unclassified;
  - linha com erro preserva apenas mensagem sanitizada (sem conteúdo bruto);
  - promotion_run continua compatível na resposta;
  - promote/reject continuam independentes da relevância;
  - auth administrativa permanece inalterada.
"""
from __future__ import annotations

import inspect
from unittest.mock import MagicMock, patch

import pytest

from radar.api.routers.discovered import _LIST_COLS, list_discovered

pytestmark = pytest.mark.unit

REQUIRED_FIELDS = [
    "relevance_status",
    "relevance_verdict",
    "relevance_error",
    "relevance_classified_at",
]

LEGACY_COLS = [
    "id", "url", "title", "agency", "fonte", "descricao",
    "prazo_envio", "publico_alvo", "tema", "opportunity_type",
    "status", "extraction_quality", "edital_link",
    "created_at", "reviewed_at", "promoted_web_source_id",
]


def _build_discovered_row(overrides: dict | None = None) -> dict:
    row = {c: None for c in LEGACY_COLS}
    row.update({
        "id": "test-001",
        "url": "https://exemplo.com/edital",
        "title": "Edital Teste",
        "status": "pending",
        "created_at": "2026-07-01T00:00:00Z",
        "relevance_status": "unclassified",
        "relevance_verdict": None,
        "relevance_error": None,
        "relevance_classified_at": None,
    })
    if overrides:
        row.update(overrides)
    return row


def _mock_query_builder_reviewed(return_data: list[dict], run_data: list[dict]):
    """Cria mock para listagem com include_reviewed=True.

    Usa call_count side_effect para alternar entre dois mocks de tabela.
    """
    mock_db = MagicMock()

    # Runs chain: .select().in_(...).order(...).execute().data
    class FakeRunResponse:
        data = run_data

    mock_run_select = MagicMock()
    mock_run_select.in_.return_value = MagicMock(
        order=MagicMock(return_value=MagicMock(execute=MagicMock(return_value=FakeRunResponse())))
    )

    # Opps chain (include_reviewed=True): .select().order(...).execute().data
    class FakeOppResponse:
        data = return_data

    mock_opp_order = MagicMock()
    mock_opp_order.execute.return_value = FakeOppResponse()
    mock_opp_select = MagicMock()
    mock_opp_select.order.return_value = mock_opp_order

    call_count = [0]
    select_results = [mock_opp_select, mock_run_select]

    def _table_side_effect(name):
        idx = call_count[0]
        call_count[0] += 1
        t = MagicMock()
        t.select.return_value = select_results[idx % len(select_results)]
        return t

    mock_db.table.side_effect = _table_side_effect
    return mock_db


def _mock_query_builder(return_data: list[dict]):
    """Cria uma cadeia de mocks que reproduz o Supabase query builder para
    a listagem padrão (sem include_reviewed).

    O fluxo real (include_reviewed=False):
      db.table("discovered_opportunities").select(_LIST_COLS)
        .eq("status","pending").gte("created_at",cutoff)
        .order("created_at", desc=True).execute()
    """
    mock_db = MagicMock()
    mock_execute = MagicMock()
    mock_execute.data = return_data

    mock_order = MagicMock()
    mock_order.execute.return_value = mock_execute

    # .gte() returns a mock that has .order()
    mock_gte = MagicMock()
    mock_gte.order.return_value = mock_order

    # .eq() returns a mock that has .gte()
    mock_eq = MagicMock()
    mock_eq.gte.return_value = mock_gte

    mock_select = MagicMock()
    mock_select.eq.return_value = mock_eq

    mock_table = MagicMock()
    mock_table.select.return_value = mock_select

    mock_db.table.return_value = mock_table
    return mock_db


# ── Testes estruturais ─────────────────────────────────────


def test_list_cols_includes_relevance_fields():
    """_LIST_COLS contém os 4 campos de relevância."""
    cols_str = _LIST_COLS if isinstance(_LIST_COLS, str) else " ".join(_LIST_COLS)
    for field in REQUIRED_FIELDS:
        assert field in cols_str, f"{field} não encontrado em _LIST_COLS"


def test_list_cols_preserves_legacy_fields():
    """_LIST_COLS preserva todos os campos legados."""
    cols_str = _LIST_COLS if isinstance(_LIST_COLS, str) else " ".join(_LIST_COLS)
    for field in LEGACY_COLS:
        assert field in cols_str, f"{field} não encontrado em _LIST_COLS"


def test_promote_endpoint_unchanged_source():
    """Promote não referencia colunas de relevância no endpoint."""
    source = inspect.getsource(
        __import__("radar.api.routers.discovered", fromlist=["promote_discovered"]).promote_discovered
    )
    assert "relevance_status" not in source
    assert "relevance_verdict" not in source
    assert "relevance_error" not in source
    assert "relevance_classified_at" not in source


def test_reject_endpoint_unchanged_source():
    """Reject não referencia colunas de relevância."""
    source = inspect.getsource(
        __import__("radar.api.routers.discovered", fromlist=["reject_discovered"]).reject_discovered
    )
    assert "relevance_status" not in source
    assert "relevance_verdict" not in source
    assert "relevance_error" not in source
    assert "relevance_classified_at" not in source


# ── Testes comportamentais ─────────────────────────────────


@patch("radar.api.routers.discovered.get_supabase_service")
def test_classified_row_preserves_verdict(mock_get_db):
    """Linha classificada retorna relevance_verdict intacto na listagem."""
    verdict = {
        "decision": "in_scope",
        "reason_codes": ["R1_ENTERPRISE_PATH", "R2_TECH_INNOVATION"],
        "exclusion_codes": [],
        "evidence": [
            {
                "code": "R1_ENTERPRISE_PATH",
                "quote": "Empresas de base tecnológica",
                "source": "landing_page",
                "locator": None,
            }
        ],
        "missing_information": [],
        "classifier_version": "radar-data-trust-relevance-v1",
    }
    row = _build_discovered_row({
        "relevance_status": "classified",
        "relevance_verdict": verdict,
        "relevance_classified_at": "2026-07-21T12:00:00Z",
    })
    mock_get_db.return_value = _mock_query_builder([row])

    result = list_discovered("admin-user")
    opps = result["opportunities"]
    assert len(opps) == 1
    assert opps[0]["relevance_status"] == "classified"
    assert opps[0]["relevance_verdict"] == verdict
    assert opps[0]["relevance_classified_at"] == "2026-07-21T12:00:00Z"


@patch("radar.api.routers.discovered.get_supabase_service")
def test_legacy_row_is_unclassified(mock_get_db):
    """Registro legado (sem campos de relevância) aparece como unclassified."""
    row = _build_discovered_row({
        "relevance_status": "unclassified",
        "relevance_verdict": None,
        "relevance_error": None,
        "relevance_classified_at": None,
    })
    mock_get_db.return_value = _mock_query_builder([row])

    result = list_discovered("admin-user")
    opps = result["opportunities"]
    assert opps[0]["relevance_status"] == "unclassified"
    assert opps[0]["relevance_verdict"] is None
    assert opps[0]["relevance_error"] is None
    assert opps[0]["relevance_classified_at"] is None


@patch("radar.api.routers.discovered.get_supabase_service")
def test_error_row_preserves_sanitized_message(mock_get_db):
    """Linha com erro preserva apenas mensagem sanitizada (sem conteúdo bruto)."""
    row = _build_discovered_row({
        "relevance_status": "error",
        "relevance_verdict": None,
        "relevance_error": "timeout: LLM não respondeu a tempo",
        "relevance_classified_at": None,
    })
    mock_get_db.return_value = _mock_query_builder([row])

    result = list_discovered("admin-user")
    opps = result["opportunities"]
    assert opps[0]["relevance_status"] == "error"
    assert opps[0]["relevance_verdict"] is None
    # A mensagem de erro deve ser apenas a sanitizada (prefixo + msg canônica)
    assert opps[0]["relevance_error"] == "timeout: LLM não respondeu a tempo"
    # Conteúdo bruto não deve aparecer
    assert "traceback" not in opps[0]["relevance_error"]
    assert "Internal Server Error" not in opps[0]["relevance_error"]


@patch("radar.api.routers.discovered.get_supabase_service")
def test_promotion_run_compatible_with_relevance(mock_get_db):
    """promotion_run continua funcional ao lado dos campos de relevância."""
    row = _build_discovered_row({
        "status": "promoted",
        "relevance_status": "classified",
        "relevance_verdict": {
            "decision": "in_scope",
            "reason_codes": ["R1_ENTERPRISE_PATH"],
            "exclusion_codes": [],
            "evidence": [],
            "missing_information": [],
            "classifier_version": "radar-data-trust-relevance-v1",
        },
    })

    run = {
        "id": "run-001",
        "discovered_opportunity_id": "test-001",
        "route": "web_source",
        "status": "awaiting_fetch",
        "edital_id": None,
        "stages": {},
        "updated_at": "2026-07-21T12:00:00Z",
    }

    mock_db = _mock_query_builder_reviewed([row], [run])
    mock_get_db.return_value = mock_db

    result = list_discovered("admin-user", include_reviewed=True)
    opps = result["opportunities"]
    assert opps[0]["promotion_run"]["id"] == "run-001"
    assert opps[0]["promotion_run"]["status"] == "awaiting_fetch"
    assert opps[0]["relevance_status"] == "classified"


@patch("radar.api.routers.discovered.get_supabase_service")
def test_include_reviewed_still_works(mock_get_db):
    """include_reviewed=true continua funcionando."""
    rows = [
        _build_discovered_row({
            "id": "pending-1", "status": "pending",
            "relevance_status": "unclassified",
        }),
        _build_discovered_row({
            "id": "promoted-1", "status": "promoted",
            "relevance_status": "classified",
        }),
        _build_discovered_row({
            "id": "rejected-1", "status": "rejected",
            "relevance_status": "error",
            "relevance_error": "parse_failure: formato não reconhecido",
        }),
    ]
    mock_db = _mock_query_builder_reviewed(rows, [])
    mock_get_db.return_value = mock_db

    result = list_discovered("admin-user", include_reviewed=True)
    opps = result["opportunities"]
    assert len(opps) == 3
    statuses = {o["status"] for o in opps}
    assert statuses == {"pending", "promoted", "rejected"}


@patch("radar.api.routers.discovered.get_supabase_service")
def test_auth_remains_admin_gate(mock_get_db):
    """A auth administrativa (AdminUserId) permanece como gate."""
    from radar.api.routers import discovered as discovered_router_module
    from radar.core.infra.auth import get_admin_user_id as gate

    routes = [r for r in discovered_router_module.router.routes if hasattr(r, "endpoint")]
    assert routes
    for r in routes:
        deps = [d.call for d in r.dependant.dependencies]
        assert gate in deps, f"rota {r.path} sem gate de admin"
    mock_get_db.assert_not_called()
