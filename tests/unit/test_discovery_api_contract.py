"""Testes do contrato da API de staging da Descoberta (RT00-T05).

Cobre:
  - _LIST_COLS inclui os 4 campos de relevância;
  - _normalize_row: legado sem campos vira unclassified;
  - _normalize_row: classified preserva verdict;
  - _normalize_row: erro canônico preservado;
  - _normalize_row: erro arbitrário vira contract_violation;
  - _normalize_row: classified sem verdict vira error contract_violation;
  - _normalize_row: classified com verdict inválido vira error;
  - _normalize_row: promotion_run não é removido;
  - list_discovered retorna dados normalizados via mock;
  - auth administrativa permanece inalterada;
  - promote/reject não referenciam colunas de relevância.
"""
from __future__ import annotations

import inspect
from unittest.mock import MagicMock, patch

import pytest

from radar.api.routers.discovered import (
    _LIST_COLS,
    _normalize_row,
    list_discovered,
)
from radar.core.ingestion.relevance_classifier import validate_opportunity_result

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

# Mensagens canónicas derivadas do classificador (API pública, não privada).
CANONICAL_ERROR = validate_opportunity_result(
    {"error": "contract_violation: dummy"}
)["error"]


def _build_legacy_row(overrides: dict | None = None) -> dict:
    """Linha SEM os 4 campos de relevância (registo legado)."""
    row = {c: None for c in LEGACY_COLS}
    row.update({
        "id": "test-legacy",
        "url": "https://exemplo.com/legacy",
        "title": "Edital Legado",
        "status": "pending",
        "created_at": "2026-06-01T00:00:00Z",
    })
    if overrides:
        row.update(overrides)
    return row


def _build_complete_row(overrides: dict | None = None) -> dict:
    """Linha com todos os campos, incluindo relevância."""
    row = _build_legacy_row({"id": "test-complete"})
    row.update({
        "relevance_status": "unclassified",
        "relevance_verdict": None,
        "relevance_error": None,
        "relevance_classified_at": None,
    })
    if overrides:
        row.update(overrides)
    return row


def test_list_cols_includes_relevance_fields():
    cols_str = _LIST_COLS if isinstance(_LIST_COLS, str) else " ".join(_LIST_COLS)
    for field in REQUIRED_FIELDS:
        assert field in cols_str, f"{field} não encontrado em _LIST_COLS"


def test_list_cols_preserves_legacy_fields():
    cols_str = _LIST_COLS if isinstance(_LIST_COLS, str) else " ".join(_LIST_COLS)
    for field in LEGACY_COLS:
        assert field in cols_str, f"{field} não encontrado em _LIST_COLS"


# ── _normalize_row: legado ──────────────────────────────────


def test_legacy_row_missing_all_relevance_fields():
    """Registo legado SEM os 4 campos vira unclassified com nulls."""
    row = _build_legacy_row()
    assert "relevance_status" not in row

    result = _normalize_row(row)

    assert result["relevance_status"] == "unclassified"
    assert result["relevance_verdict"] is None
    assert result["relevance_error"] is None
    assert result["relevance_classified_at"] is None


def test_legacy_row_with_none_status():
    """Registo com relevance_status=None vira unclassified."""
    row = _build_legacy_row({"relevance_status": None})
    result = _normalize_row(row)
    assert result["relevance_status"] == "unclassified"
    assert result["relevance_verdict"] is None


def test_legacy_row_with_unknown_status():
    """Registo com relevance_status inválido vira unclassified."""
    row = _build_legacy_row({"relevance_status": "unknown"})
    result = _normalize_row(row)
    assert result["relevance_status"] == "unclassified"
    assert result["relevance_verdict"] is None


# ── _normalize_row: classified ──────────────────────────────


def test_classified_in_scope_preserves_verdict():
    """Linha classified com in_scope preserva verdict."""
    verdict = {
        "decision": "in_scope",
        "reason_codes": [
            "R1_ENTERPRISE_PATH", "R2_TECH_INNOVATION",
            "R3_ACTIONABLE", "R4_RELEVANT_BENEFIT", "R5_BRAZIL_RELEVANCE",
        ],
        "exclusion_codes": [],
        "evidence": [
            {
                "code": "R1_ENTERPRISE_PATH",
                "quote": "Empresas de base tecnológica",
                "source": "landing_page",
                "locator": None,
            },
        ],
        "missing_information": [],
        "classifier_version": "radar-data-trust-relevance-v1",
    }
    row = _build_complete_row({
        "relevance_status": "classified",
        "relevance_verdict": verdict,
        "relevance_classified_at": "2026-07-21T12:00:00Z",
    })
    result = _normalize_row(row)

    assert result["relevance_status"] == "classified"
    assert result["relevance_verdict"] == verdict
    assert result["relevance_error"] is None
    assert result["relevance_classified_at"] == "2026-07-21T12:00:00Z"


def test_classified_out_of_scope_preserves_verdict():
    verdict = {
        "decision": "out_of_scope",
        "reason_codes": ["X1_ACADEMIC_ONLY"],
        "exclusion_codes": ["X1_ACADEMIC_ONLY"],
        "evidence": [],
        "missing_information": [],
        "classifier_version": "radar-data-trust-relevance-v1",
    }
    row = _build_complete_row({
        "relevance_status": "classified",
        "relevance_verdict": verdict,
    })
    result = _normalize_row(row)
    assert result["relevance_status"] == "classified"
    assert result["relevance_verdict"]["decision"] == "out_of_scope"


def test_classified_needs_review_preserves_verdict():
    verdict = {
        "decision": "needs_review",
        "reason_codes": ["R1_ENTERPRISE_PATH"],
        "exclusion_codes": [],
        "evidence": [],
        "missing_information": ["R2_TECH_INNOVATION: informação ausente"],
        "classifier_version": "radar-data-trust-relevance-v1",
    }
    row = _build_complete_row({
        "relevance_status": "classified",
        "relevance_verdict": verdict,
    })
    result = _normalize_row(row)
    assert result["relevance_status"] == "classified"
    assert result["relevance_verdict"]["missing_information"] == [
        "R2_TECH_INNOVATION: informação ausente",
    ]


def test_classified_without_verdict_normalized_to_error():
    """classified mas sem relevance_verdict → error contract_violation."""
    row = _build_complete_row({
        "relevance_status": "classified",
        "relevance_verdict": None,
    })
    result = _normalize_row(row)

    assert result["relevance_status"] == "error"
    assert result["relevance_error"] == CANONICAL_ERROR
    assert result["relevance_verdict"] is None


def test_classified_with_empty_verdict_normalized_to_error():
    """classified com relevance_verdict={} vira error."""
    row = _build_complete_row({
        "relevance_status": "classified",
        "relevance_verdict": {},
    })
    result = _normalize_row(row)

    assert result["relevance_status"] == "error"
    assert result["relevance_error"] == CANONICAL_ERROR
    assert result["relevance_verdict"] is None


def test_classified_with_malformed_verdict_normalized_to_error():
    """classified com verdict inválido (falta decision) vira error."""
    row = _build_complete_row({
        "relevance_status": "classified",
        "relevance_verdict": {"foo": "bar"},
    })
    result = _normalize_row(row)

    assert result["relevance_status"] == "error"
    assert result["relevance_error"] == CANONICAL_ERROR
    assert result["relevance_verdict"] is None


def test_classified_with_invalid_decision_normalized_to_error():
    """classified com decision inválida vira error."""
    row = _build_complete_row({
        "relevance_status": "classified",
        "relevance_verdict": {
            "decision": "not_a_valid_decision",
            "reason_codes": [],
            "exclusion_codes": [],
            "evidence": [],
            "missing_information": [],
            "classifier_version": "v1",
        },
    })
    result = _normalize_row(row)

    assert result["relevance_status"] == "error"
    assert result["relevance_error"] == CANONICAL_ERROR
    assert result["relevance_verdict"] is None


# ── _normalize_row: error ───────────────────────────────────


_KNOWN_PREFIXES = [
    "parse_failure:",
    "timeout:",
    "provider_error:",
    "contract_violation:",
    "grounding_error:",
]

def test_error_with_raw_suffix_returns_only_canonical():
    """Erro com sufixo bruto após prefixo conhecido retorna só a mensagem
    canónica — nunca o conteúdo arbitrário."""
    for prefix in _KNOWN_PREFIXES:
        raw = f"{prefix} SEGREDO_BRUTO traceback ou conteúdo sensível qualquer"
        row = _build_complete_row({
            "relevance_status": "error",
            "relevance_verdict": None,
            "relevance_error": raw,
            "relevance_classified_at": None,
        })
        result = _normalize_row(row)

        assert result["relevance_status"] == "error"
        assert result["relevance_verdict"] is None
        # A mensagem canónica nunca contém o sufixo bruto
        assert result["relevance_error"] is not None
        assert "SEGREDO_BRUTO" not in result["relevance_error"]
        # O prefixo "prefixo:" pode ou não estar na saída — depende do formato
        # da mensagem canónica. O que importa: nenhum texto bruto vaza.


def test_provider_error_raw_suffix_stripped():
    """provider_error: SEGREDO_BRUTO → só mensagem canónica, sem o segredo."""
    row = _build_complete_row({
        "relevance_status": "error",
        "relevance_verdict": None,
        "relevance_error": "provider_error: SEGREDO_BRUTO conteúdo_sensível",
        "relevance_classified_at": None,
    })
    result = _normalize_row(row)

    assert result["relevance_status"] == "error"
    assert "SEGREDO_BRUTO" not in result["relevance_error"]
    assert "conteúdo_sensível" not in result["relevance_error"]


def test_timeout_with_traceback_returns_only_canonical():
    """timeout: traceback ou conteúdo arbitrário → só mensagem canónica."""
    row = _build_complete_row({
        "relevance_status": "error",
        "relevance_verdict": None,
        "relevance_error": "timeout: traceback (most recent call last):\n  File ...",
        "relevance_classified_at": None,
    })
    result = _normalize_row(row)

    assert result["relevance_status"] == "error"
    assert "traceback" not in result["relevance_error"]
    assert "File" not in result["relevance_error"]


def test_error_without_prefix_returns_contract_violation():
    """Erro sem prefixo conhecido → contract_violation."""
    row = _build_complete_row({
        "relevance_status": "error",
        "relevance_verdict": None,
        "relevance_error": (
            "mensagem de erro bruta e arbitrária que não se parece com "
            "nenhum prefixo canónico do classificador"
        ),
        "relevance_classified_at": None,
    })
    result = _normalize_row(row)

    assert result["relevance_status"] == "error"
    assert result["relevance_error"] == CANONICAL_ERROR


def test_error_empty_string_returns_contract_violation():
    """Erro com string vazia → contract_violation (None sanitizado)."""
    row = _build_complete_row({
        "relevance_status": "error",
        "relevance_verdict": None,
        "relevance_error": "",
        "relevance_classified_at": None,
    })
    result = _normalize_row(row)

    assert result["relevance_status"] == "error"
    assert result["relevance_error"] == CANONICAL_ERROR


def test_error_none_preserved():
    """Erro com None permanece error e relevance_error=None."""
    row = _build_complete_row({
        "relevance_status": "error",
        "relevance_verdict": None,
        "relevance_error": None,
    })
    result = _normalize_row(row)

    assert result["relevance_status"] == "error"
    assert result["relevance_error"] is None
    assert result["relevance_verdict"] is None


# ── _normalize_row: promotion_run ───────────────────────────


def test_promotion_run_preserved():
    """promotion_run não é alterado pela normalização."""
    run = {
        "id": "run-001",
        "route": "web_source",
        "status": "awaiting_fetch",
        "edital_id": None,
        "stages": {},
        "updated_at": "2026-07-21T12:00:00Z",
    }
    row = _build_complete_row({
        "relevance_status": "classified",
        "relevance_verdict": {
            "decision": "needs_review",
            "reason_codes": [],
            "exclusion_codes": [],
            "evidence": [],
            "missing_information": ["R2_TECH_INNOVATION: ausente"],
            "classifier_version": "radar-data-trust-relevance-v1",
        },
        "promotion_run": run,
    })
    result = _normalize_row(row)

    assert result["promotion_run"]["id"] == "run-001"
    assert result["promotion_run"]["status"] == "awaiting_fetch"
    assert result["relevance_status"] == "classified"


def test_promotion_run_preserved_even_with_error_normalization():
    """promotion_run mantém-se mesmo quando a relevância é normalizada."""
    run = {
        "id": "run-002",
        "route": "direct_pdf",
        "status": "ready",
        "edital_id": "web:abc",
        "stages": {"radar_ready": {"status": "ready"}},
        "updated_at": "2026-07-21T12:00:00Z",
    }
    row = _build_complete_row({
        "relevance_status": "classified",
        "relevance_verdict": None,
        "promotion_run": run,
    })
    result = _normalize_row(row)

    assert result["relevance_status"] == "error"
    assert result["promotion_run"]["id"] == "run-002"
    assert result["promotion_run"]["status"] == "ready"


# ── list_discovered (mock) ──────────────────────────────────


def _mock_query_builder(return_data: list[dict]):
    mock_db = MagicMock()
    mock_execute = MagicMock()
    mock_execute.data = return_data
    mock_order = MagicMock()
    mock_order.execute.return_value = mock_execute
    mock_gte = MagicMock()
    mock_gte.order.return_value = mock_order
    mock_eq = MagicMock()
    mock_eq.gte.return_value = mock_gte
    mock_select = MagicMock()
    mock_select.eq.return_value = mock_eq
    mock_table = MagicMock()
    mock_table.select.return_value = mock_select
    mock_db.table.return_value = mock_table
    return mock_db


def _mock_query_builder_reviewed(return_data: list[dict], run_data: list[dict]):
    mock_db = MagicMock()

    class FakeRunResponse:
        data = run_data

    mock_run_select = MagicMock()
    mock_run_select.in_.return_value = MagicMock(
        order=MagicMock(return_value=MagicMock(execute=MagicMock(return_value=FakeRunResponse())))
    )

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


@patch("radar.api.routers.discovered.get_supabase_service")
def test_list_discovered_legacy_row_normalized(mock_get_db):
    """list_discovered normaliza linha legada como unclassified."""
    row = _build_legacy_row()
    mock_get_db.return_value = _mock_query_builder([row])

    result = list_discovered("admin-user")
    opps = result["opportunities"]
    assert len(opps) == 1
    assert opps[0]["relevance_status"] == "unclassified"
    assert opps[0]["relevance_verdict"] is None
    assert opps[0]["relevance_error"] is None
    assert opps[0]["relevance_classified_at"] is None


@patch("radar.api.routers.discovered.get_supabase_service")
def test_list_discovered_classified_row(mock_get_db):
    """list_discovered retorna linha classificada com verdict intacto."""
    verdict = {
        "decision": "in_scope",
        "reason_codes": ["R1_ENTERPRISE_PATH", "R2_TECH_INNOVATION",
                         "R3_ACTIONABLE", "R4_RELEVANT_BENEFIT",
                         "R5_BRAZIL_RELEVANCE"],
        "exclusion_codes": [],
        "evidence": [],
        "missing_information": [],
        "classifier_version": "radar-data-trust-relevance-v1",
    }
    row = _build_complete_row({
        "relevance_status": "classified",
        "relevance_verdict": verdict,
        "relevance_classified_at": "2026-07-21T12:00:00Z",
    })
    mock_get_db.return_value = _mock_query_builder([row])

    result = list_discovered("admin-user")
    opps = result["opportunities"]
    assert opps[0]["relevance_status"] == "classified"
    assert opps[0]["relevance_verdict"]["decision"] == "in_scope"
    assert opps[0]["relevance_classified_at"] == "2026-07-21T12:00:00Z"


@patch("radar.api.routers.discovered.get_supabase_service")
def test_list_discovered_malformed_row_normalized(mock_get_db):
    """list_discovered normaliza linha inválida para error contract_violation."""
    row = _build_complete_row({
        "relevance_status": "classified",
        "relevance_verdict": None,
    })
    mock_get_db.return_value = _mock_query_builder([row])

    result = list_discovered("admin-user")
    opps = result["opportunities"]
    assert opps[0]["relevance_status"] == "error"
    assert opps[0]["relevance_error"] == CANONICAL_ERROR
    assert opps[0]["relevance_verdict"] is None


@patch("radar.api.routers.discovered.get_supabase_service")
def test_list_discovered_error_arbitrary_normalized(mock_get_db):
    """list_discovered normaliza erro arbitrário."""
    row = _build_complete_row({
        "relevance_status": "error",
        "relevance_verdict": None,
        "relevance_error": "mensagem bruta arbitrária sem prefixo",
    })
    mock_get_db.return_value = _mock_query_builder([row])

    result = list_discovered("admin-user")
    opps = result["opportunities"]
    assert opps[0]["relevance_status"] == "error"
    assert opps[0]["relevance_error"] == CANONICAL_ERROR


@patch("radar.api.routers.discovered.get_supabase_service")
def test_list_discovered_preserves_editorial_fields(mock_get_db):
    """Campos editoriais (status, reviewed_at, etc.) não são alterados."""
    row = _build_legacy_row({
        "status": "promoted",
        "reviewed_at": "2026-07-20T10:00:00Z",
    })
    mock_get_db.return_value = _mock_query_builder([row])

    result = list_discovered("admin-user")
    opps = result["opportunities"]
    assert opps[0]["status"] == "promoted"
    assert opps[0]["reviewed_at"] == "2026-07-20T10:00:00Z"


@patch("radar.api.routers.discovered.get_supabase_service")
def test_list_discovered_promotion_run_compatible(mock_get_db):
    """promotion_run convive com campos de relevância na listagem."""
    row = _build_complete_row({
        "id": "test-001", "status": "promoted",
        "relevance_status": "classified",
        "relevance_verdict": {
            "decision": "needs_review",
            "reason_codes": [],
            "exclusion_codes": [],
            "evidence": [],
            "missing_information": ["R2_TECH_INNOVATION: ausente"],
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


# ── Promote / reject independence ───────────────────────────


def test_promote_not_called_by_staging():
    """_stage_records nunca chama promote (inspeção do source de T04)."""
    from radar.core.ingestion.opportunity_discovery import _row_with_relevance, _stage_records
    src = inspect.getsource(_stage_records)
    assert "promote" not in src
    src2 = inspect.getsource(_row_with_relevance)
    assert "promote" not in src2


def test_reject_not_called_by_staging():
    from radar.core.ingestion.opportunity_discovery import _row_with_relevance, _stage_records
    src = inspect.getsource(_stage_records)
    assert "reject" not in src
    src2 = inspect.getsource(_row_with_relevance)
    assert "reject" not in src2


# ── Auth ────────────────────────────────────────────────────


def test_auth_remains_admin_gate():
    """Todos os endpoints têm AdminUserId como dependência."""
    from radar.api.routers import discovered as discovered_router_module
    from radar.core.infra.auth import get_admin_user_id as gate

    routes = [r for r in discovered_router_module.router.routes if hasattr(r, "endpoint")]
    assert routes
    for r in routes:
        deps = [d.call for d in r.dependant.dependencies]
        assert gate in deps, f"rota {r.path} sem gate de admin"
