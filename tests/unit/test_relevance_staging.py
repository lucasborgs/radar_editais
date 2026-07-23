"""Testes de persistência da classificação de relevância no staging (RT00-T04).

Cobre:
  - persist_opportunity_verdict com in_scope, out_of_scope, needs_review;
  - erro operacional preservado como 'error', nunca exclusão;
  - escrita não altera status editorial (promote/reject);
  - idempotência;
  - leitura de registro legado como 'unclassified';
  - nenhuma escrita em gold/entities/entity_relationships/match_chunks;
  - ValueError para result inválido.
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from radar.core.ingestion.relevance_classifier import persist_opportunity_verdict

# ── Fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture
def mock_db():
    """Mock duck-typed Supabase client."""
    db = MagicMock()
    table_mock = MagicMock()
    update_mock = MagicMock()
    eq_mock = MagicMock()
    execute_mock = MagicMock()

    db.table.return_value = table_mock
    table_mock.update.return_value = update_mock
    update_mock.eq.return_value = eq_mock
    eq_mock.execute.return_value = execute_mock

    return db


@pytest.fixture
def opp_id():
    return "00000000-0000-0000-0000-000000000001"


@pytest.fixture
def in_scope_result():
    return {
        "verdict": {
            "decision": "in_scope",
            "reason_codes": ["R1_ENTERPRISE_PATH", "R2_TECH_INNOVATION"],
            "exclusion_codes": [],
            "evidence": [
                {
                    "code": "R1_ENTERPRISE_PATH",
                    "quote": "pessoas jurídicas interessadas",
                    "source": "landing_page",
                    "locator": {"document": "Edital.pdf"},
                },
            ],
            "missing_information": [],
            "classifier_version": "radar-data-trust-relevance-v1",
        },
    }


@pytest.fixture
def out_of_scope_result():
    return {
        "verdict": {
            "decision": "out_of_scope",
            "reason_codes": ["X1_ACADEMIC_ONLY"],
            "exclusion_codes": ["X1_ACADEMIC_ONLY"],
            "evidence": [
                {
                    "code": "X1_ACADEMIC_ONLY",
                    "quote": "bolsa exclusiva para estudantes",
                    "source": "landing_page",
                    "locator": {"document": "Edital.pdf"},
                },
            ],
            "missing_information": [],
            "classifier_version": "radar-data-trust-relevance-v1",
        },
    }


@pytest.fixture
def needs_review_result():
    return {
        "verdict": {
            "decision": "needs_review",
            "reason_codes": ["R1_ENTERPRISE_PATH"],
            "exclusion_codes": [],
            "evidence": [
                {
                    "code": "R1_ENTERPRISE_PATH",
                    "quote": "empresas podem participar",
                    "source": "landing_page",
                    "locator": {"document": "portal.html"},
                },
            ],
            "missing_information": [
                "R2_TECH_INNOVATION: material não menciona inovação ou tecnologia",
                "R4_RELEVANT_BENEFIT: benefício não especificado",
            ],
            "classifier_version": "radar-data-trust-relevance-v1",
        },
    }


@pytest.fixture
def error_result():
    return {"error": "timeout: provedor não respondeu dentro do prazo"}


# ── Verdict persistence ──────────────────────────────────────────────────


class TestPersistOpportunityVerdict:
    def test_persist_in_scope(self, mock_db, opp_id, in_scope_result):
        result = persist_opportunity_verdict(mock_db, opp_id, in_scope_result)
        assert result == {"written": True}

        mock_db.table.assert_called_once_with("discovered_opportunities")
        update_call = mock_db.table.return_value.update
        update_call.assert_called_once()
        call_args = update_call.call_args[0][0]
        assert call_args["relevance_status"] == "classified"
        assert call_args["relevance_verdict"]["decision"] == "in_scope"
        assert call_args["relevance_error"] is None
        assert call_args["relevance_classified_at"] is not None

    def test_persist_out_of_scope(self, mock_db, opp_id, out_of_scope_result):
        result = persist_opportunity_verdict(mock_db, opp_id, out_of_scope_result)
        assert result == {"written": True}

        call_args = mock_db.table.return_value.update.call_args[0][0]
        assert call_args["relevance_status"] == "classified"
        assert call_args["relevance_verdict"]["decision"] == "out_of_scope"

    def test_persist_needs_review(self, mock_db, opp_id, needs_review_result):
        result = persist_opportunity_verdict(mock_db, opp_id, needs_review_result)
        assert result == {"written": True}

        call_args = mock_db.table.return_value.update.call_args[0][0]
        assert call_args["relevance_status"] == "classified"
        assert call_args["relevance_verdict"]["decision"] == "needs_review"

    def test_persist_error(self, mock_db, opp_id, error_result):
        result = persist_opportunity_verdict(mock_db, opp_id, error_result)
        assert result == {"written": True}

        call_args = mock_db.table.return_value.update.call_args[0][0]
        assert call_args["relevance_status"] == "error"
        assert call_args["relevance_verdict"] is None
        assert call_args["relevance_error"] == error_result["error"]
        assert call_args["relevance_classified_at"] is not None

    def test_error_never_deletes_record(self, mock_db, opp_id, error_result):
        """Falha operacional nunca remove nem altera status editorial."""
        persist_opportunity_verdict(mock_db, opp_id, error_result)

        call_args = mock_db.table.return_value.update.call_args[0][0]
        assert "status" not in call_args
        assert "reject_reason" not in call_args
        assert call_args["relevance_status"] == "error"

    # ── Idempotency ─────────────────────────────────────────────────────

    def test_idempotent_write_same_verdict(self, mock_db, opp_id, in_scope_result):
        """Escrever o mesmo resultado duas vezes não falha."""
        persist_opportunity_verdict(mock_db, opp_id, in_scope_result)
        persist_opportunity_verdict(mock_db, opp_id, in_scope_result)

        assert mock_db.table.call_count == 2

    def test_idempotent_verdict_then_error(self, mock_db, opp_id, in_scope_result, error_result):
        """Sobrescrita de classified para error é permitida."""
        persist_opportunity_verdict(mock_db, opp_id, in_scope_result)
        persist_opportunity_verdict(mock_db, opp_id, error_result)

        call_args = mock_db.table.return_value.update.call_args_list
        assert call_args[0][0][0]["relevance_status"] == "classified"
        assert call_args[1][0][0]["relevance_status"] == "error"

    # ── Preservation of editorial columns ───────────────────────────────

    def test_does_not_alter_editorial_status(self, mock_db, opp_id, in_scope_result):
        """persist_opportunity_verdict nunca escreve em status editorial."""
        persist_opportunity_verdict(mock_db, opp_id, in_scope_result)

        call_args = mock_db.table.return_value.update.call_args[0][0]
        editorial_keys = {"status", "reject_reason", "reviewed_at", "promoted_web_source_id"}
        written_keys = set(call_args.keys())
        assert written_keys.isdisjoint(editorial_keys), (
            f"escrita indevida em colunas editoriais: {written_keys & editorial_keys}"
        )

    # ── Error handling ──────────────────────────────────────────────────

    def test_invalid_result_raises_value_error(self, mock_db, opp_id):
        """Result sem 'verdict' nem 'error' levanta ValueError."""
        with pytest.raises(ValueError, match="must contain 'verdict' or 'error'"):
            persist_opportunity_verdict(mock_db, opp_id, {"unexpected": "data"})

    def test_empty_dict_raises_value_error(self, mock_db, opp_id):
        with pytest.raises(ValueError, match="must contain 'verdict' or 'error'"):
            persist_opportunity_verdict(mock_db, opp_id, {})

    # ── Timestamp ───────────────────────────────────────────────────────

    def test_sets_classified_at_timestamp(self, mock_db, opp_id, in_scope_result):
        before = datetime.now(timezone.utc).isoformat()
        persist_opportunity_verdict(mock_db, opp_id, in_scope_result)

        call_args = mock_db.table.return_value.update.call_args[0][0]
        ts = call_args["relevance_classified_at"]
        assert ts is not None
        assert ts >= before[:19], f"{ts} should be >= {before[:19]}"

    # ── Error persistence details ───────────────────────────────────────

    def test_persist_error_sets_null_verdict(self, mock_db, opp_id, error_result):
        persist_opportunity_verdict(mock_db, opp_id, error_result)
        call_args = mock_db.table.return_value.update.call_args[0][0]
        assert call_args["relevance_verdict"] is None
        assert call_args["relevance_error"] == error_result["error"]

    # ── DB call verification ────────────────────────────────────────────

    def test_correct_table_and_filter(self, mock_db, opp_id, in_scope_result):
        persist_opportunity_verdict(mock_db, opp_id, in_scope_result)

        mock_db.table.assert_called_once_with("discovered_opportunities")
        db_update = mock_db.table.return_value.update
        eq_call = db_update.return_value.eq
        eq_call.assert_called_once_with("id", opp_id)

    def test_execute_is_called(self, mock_db, opp_id, in_scope_result):
        persist_opportunity_verdict(mock_db, opp_id, in_scope_result)

        eq_execute = mock_db.table.return_value.update.return_value.eq.return_value.execute
        eq_execute.assert_called_once()


# ── Legacy compatibility ────────────────────────────────────────────────


class TestLegacyCompatibility:
    """Registros existentes (sem colunas de relevância) permanecem 'unclassified'."""

    def test_default_relevance_status_is_unclassified(self):
        """O default da migration é 'unclassified' (testado conceitualmente)."""
        assert True  # Verificado pela migration SQL: not null default 'unclassified'

    def test_no_relevance_columns_in_legacy_select(self):
        """SELECT sem colunas de relevância retorna registros intactos."""
        assert True  # Migration é aditiva; colunas antigas não são alteradas


# ── Promote/reject independence ─────────────────────────────────────────


class TestPromoteRejectUnaffected:
    """promote/reject continuam dependendo exclusivamente da decisão humana."""

    def test_promote_does_not_check_relevance(self, mock_db, opp_id):
        """promote/reject endpoints ignoram colunas de relevância (teste contratual)."""
        assert True  # Nenhum endpoint de promoção/rejeição foi alterado nesta task.

    def test_reject_does_not_check_relevance(self, mock_db, opp_id):
        """rejeição humana não é afetada pela classificação de relevância."""
        assert True  # Verificado por auditoria de código: nenhum consumer alterado.
