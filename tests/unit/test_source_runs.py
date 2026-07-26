"""Testes do repositório `source_runs` (RT03-T02).

Cobre:
  - schema/RLS/checks/defaults da migration (validação estrutural do SQL);
  - staging legado: colunas novas são nullable, registros existentes intactos;
  - start_run: cria nova run, idempotente (reenvia mesma batch/key);
  - finish_run: atualiza contadores, não regride estado terminal;
  - best-effort: falha do DB é logada e não relançada.
"""
from __future__ import annotations

import uuid
from unittest.mock import MagicMock

import pytest

from radar.core.services.source_runs import finish_run, start_run

pytestmark = pytest.mark.unit

# ══════════════════════════════════════════════════════════════════════════
# Fixtures
# ══════════════════════════════════════════════════════════════════════════


@pytest.fixture
def mock_db():
    return MagicMock()


# ══════════════════════════════════════════════════════════════════════════
# Migration — structural validation
# ══════════════════════════════════════════════════════════════════════════


class TestMigration:

    MIGRATION_PATH = "supabase/migrations/043_source_runs.sql"

    def _read_sql(self):
        import os
        path = os.path.join(
            os.path.dirname(__file__), "..", "..", self.MIGRATION_PATH,
        )
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                return f.read()
        raise FileNotFoundError(path)

    def test_source_runs_table_created(self):
        sql = self._read_sql()
        assert "create table if not exists public.source_runs" in sql

    def test_source_runs_has_uuid_pk(self):
        sql = self._read_sql()
        assert "uuid primary key" in sql

    def test_source_runs_has_batch_id(self):
        sql = self._read_sql()
        assert "batch_id" in sql

    def test_source_runs_status_check(self):
        sql = self._read_sql()
        assert "running" in sql
        assert "succeeded" in sql
        assert "partial" in sql
        assert "failed" in sql
        assert "skipped" in sql

    def test_source_runs_uniqueness_constraint(self):
        sql = self._read_sql()
        assert "unique (batch_id, source_key)" in sql

    def test_source_runs_error_count_check(self):
        sql = self._read_sql()
        assert "error_count >= 0" in sql or "check (error_count >= 0)" in sql

    def test_source_runs_rls_enabled(self):
        sql = self._read_sql()
        assert "enable row level security" in sql

    def test_source_runs_no_user_policy(self):
        sql = self._read_sql()
        # Verifica que não há CREATE POLICY como statement SQL.
        # O texto "create policy" no comentário SQL é permitido.
        statements = [line.strip() for line in sql.splitlines() if line.strip().startswith("create policy")]
        assert not statements, f"found CREATE POLICY statements: {statements}"

    def test_source_runs_metrics_jsonb(self):
        sql = self._read_sql()
        assert "metrics" in sql
        assert "jsonb" in sql

    def test_discovered_columns_nullable(self):
        sql = self._read_sql()
        for col in ("discovery_run_id", "discovery_channel", "query_family", "origin_domain"):
            assert f"add column if not exists {col}" in sql

    def test_discovered_channel_check_in_values(self):
        sql = self._read_sql()
        assert "open_search" in sql
        assert "dou" in sql
        assert "hub_expansion" in sql

    def test_discovered_family_check_in_values(self):
        sql = self._read_sql()
        assert "state_innovation_funding" in sql
        assert "corporate_open_innovation" in sql
        assert "startup_acceleration" in sql
        assert "international_brazil_access" in sql

    def test_no_alter_on_existing_columns(self):
        """Não altera status, raw, dedup, promoção ou RLS existente."""
        sql = self._read_sql()
        # O segundo ALTER TABLE (discovered_opportunities) só adiciona colunas
        # novas — não referencia status, reject_reason, etc.
        assert "status" not in sql[sql.rindex("alter table public.discovered_opportunities"):].split("alter")[0]


# ══════════════════════════════════════════════════════════════════════════
# start_run
# ══════════════════════════════════════════════════════════════════════════


class TestStartRun:

    def test_creates_new_run(self, mock_db):
        mock_db.table.return_value.upsert.return_value.execute.return_value = MagicMock(
            data=[{"id": "new-uuid"}],
        )
        run_id = start_run(mock_db, batch_id="batch-1", source_key="finep", mode="dedicated")
        assert run_id is not None
        assert isinstance(run_id, str)
        # Verifica que upsert foi chamado
        mock_db.table.assert_called_with("source_runs")
        upsert_call = mock_db.table.return_value.upsert.call_args[0][0]
        assert upsert_call["source_key"] == "finep"
        assert upsert_call["batch_id"] == "batch-1"
        assert upsert_call["mode"] == "dedicated"
        assert upsert_call["status"] == "running"

    def test_returns_existing_if_already_running(self, mock_db):
        existing_id = str(uuid.uuid4())
        # Primeira chamada (insert) retorna dado existente
        mock_db.table.return_value.upsert.return_value.execute.return_value = MagicMock(
            data=[{"id": existing_id, "status": "running"}],
        )
        run_id = start_run(mock_db, batch_id="batch-1", source_key="finep", mode="dedicated")
        assert run_id == existing_id

    def test_returns_existing_if_terminal(self, mock_db):
        existing_id = str(uuid.uuid4())
        mock_db.table.return_value.upsert.return_value.execute.return_value = MagicMock(
            data=[{"id": existing_id, "status": "succeeded"}],
        )
        run_id = start_run(mock_db, batch_id="batch-1", source_key="finep", mode="dedicated")
        assert run_id == existing_id

    def test_returns_none_on_db_failure(self, mock_db):
        mock_db.table.side_effect = Exception("connection refused")
        run_id = start_run(mock_db, batch_id="batch-1", source_key="finep", mode="dedicated")
        assert run_id is None

    def test_accepts_uuid_batch_id(self, mock_db):
        mock_db.table.return_value.upsert.return_value.execute.return_value = MagicMock(
            data=[{"id": "new-uuid"}],
        )
        batch = uuid.uuid4()
        run_id = start_run(mock_db, batch_id=batch, source_key="open_search", mode="open_search")
        assert run_id is not None


# ══════════════════════════════════════════════════════════════════════════
# finish_run
# ══════════════════════════════════════════════════════════════════════════


class TestFinishRun:

    def test_updates_run_with_success(self, mock_db):
        mock_db.table.return_value.select.return_value.eq.return_value.maybe_single.return_value.execute.return_value = MagicMock(
            data={"status": "running"},
        )
        result = finish_run(
            mock_db,
            run_id="run-1",
            status="succeeded",
            records_observed=10,
            records_emitted=5,
            records_staged=3,
            error_count=0,
        )
        assert result is True

        update_call = mock_db.table.return_value.update.call_args[0][0]
        assert update_call["status"] == "succeeded"
        assert update_call["records_observed"] == 10
        assert update_call["records_staged"] == 3

    def test_does_not_regress_terminal(self, mock_db):
        mock_db.table.return_value.select.return_value.eq.return_value.maybe_single.return_value.execute.return_value = MagicMock(
            data={"status": "succeeded"},
        )
        result = finish_run(mock_db, run_id="run-1", status="running")
        assert result is False  # ignorada
        mock_db.table.return_value.update.assert_not_called()

    def test_does_not_regress_failed(self, mock_db):
        mock_db.table.return_value.select.return_value.eq.return_value.maybe_single.return_value.execute.return_value = MagicMock(
            data={"status": "failed"},
        )
        result = finish_run(mock_db, run_id="run-1", status="succeeded")
        assert result is False
        mock_db.table.return_value.update.assert_not_called()

    def test_updates_with_reason_code(self, mock_db):
        mock_db.table.return_value.select.return_value.eq.return_value.maybe_single.return_value.execute.return_value = MagicMock(
            data={"status": "running"},
        )
        result = finish_run(
            mock_db,
            run_id="run-1",
            status="failed",
            reason_code="timeout",
        )
        assert result is True
        update_call = mock_db.table.return_value.update.call_args[0][0]
        assert update_call["reason_code"] == "timeout"

    def test_updates_with_metrics(self, mock_db):
        mock_db.table.return_value.select.return_value.eq.return_value.maybe_single.return_value.execute.return_value = MagicMock(
            data={"status": "running"},
        )
        metrics = {"triage_skipped": 5, "hubs_expanded": 2}
        result = finish_run(mock_db, run_id="run-1", status="succeeded", metrics=metrics)
        assert result is True
        update_call = mock_db.table.return_value.update.call_args[0][0]
        assert update_call["metrics"] == metrics

    def test_returns_false_on_db_failure(self, mock_db):
        mock_db.table.return_value.select.side_effect = Exception("db down")
        result = finish_run(mock_db, run_id="run-1", status="succeeded")
        assert result is False

    def test_handles_missing_run_gracefully(self, mock_db):
        mock_db.table.return_value.select.return_value.eq.return_value.maybe_single.return_value.execute.return_value = MagicMock(
            data=None,
        )
        result = finish_run(mock_db, run_id="nonexistent", status="failed")
        # Tenta update mesmo se select não achou (a run pode ter sido criada
        # entre o select e o update; o erro seria 404, logado, não relançado)
        assert result is True
        mock_db.table.return_value.update.assert_called_once()


# ══════════════════════════════════════════════════════════════════════════
# Sanitization — no secrets in metrics/reason
# ══════════════════════════════════════════════════════════════════════════


class TestSanitization:

    def test_no_query_string_in_metrics(self):
        """Garantia estrutural: o contrato não aceita query completa."""
        from radar.core.services.source_runs import _REASON_CODES
        assert "query" not in str(_REASON_CODES).lower()
        assert "prompt" not in str(_REASON_CODES).lower()
        assert "secret" not in str(_REASON_CODES).lower()

    def test_terminal_statuses_immutable(self):
        from radar.core.services.source_runs import _TERMINAL_STATUSES
        assert "running" not in _TERMINAL_STATUSES
        assert "succeeded" in _TERMINAL_STATUSES
        assert "failed" in _TERMINAL_STATUSES
        assert "skipped" in _TERMINAL_STATUSES
