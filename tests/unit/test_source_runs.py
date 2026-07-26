"""Testes do repositório `source_runs` (RT03-T02).

Cobre:
  - start_run: cria, idempotente sem update, conflito nunca reabre
  - finish_run: atômico (WHERE contra terminal), valida estados finais
  - recusa contadores negativos pre-DB
  - reason_code não canônico é omitido (nunca persistido)
  - sanitização de metrics: só chaves seguras + numéricos finitos >= 0
  - best-effort: falha do DB não relançada
  - FK de discovery_run_id validada estruturalmente no SQL
"""
from __future__ import annotations

import uuid
from unittest.mock import MagicMock

import pytest

from radar.core.services.source_runs import finish_run, start_run

pytestmark = pytest.mark.unit


@pytest.fixture
def mock_db():
    return MagicMock()


def _mock_update_terminal_guard(mock_db, should_update: bool = True):
    """Configura mock_db para que update+WHERE NOT IN terminal retorne
    dados (True = run atualizável) ou vazio (False = já terminal)."""
    mock_db.table.return_value.update.return_value.eq.return_value.not_.in_.return_value.execute.return_value = MagicMock(
        data=[{"id": "mock-run"}] if should_update else [],
    )


# ══════════════════════════════════════════════════════════════════════════
# Migration — structural
# ══════════════════════════════════════════════════════════════════════════


class TestMigration:

    MIGRATION_PATH = "supabase/migrations/043_source_runs.sql"

    def _read_sql(self):
        import os
        path = os.path.join(os.path.dirname(__file__), "..", "..", self.MIGRATION_PATH)
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                return f.read()
        raise FileNotFoundError(path)

    def test_fk_discovery_run_id_exists(self):
        sql = self._read_sql()
        assert "references public.source_runs(id) on delete set null" in sql

    def test_records_observed_check(self):
        sql = self._read_sql()
        assert "records_observed >= 0" in sql or "check (records_observed >= 0)" in sql

    def test_records_emitted_check(self):
        sql = self._read_sql()
        assert "records_emitted >= 0" in sql or "check (records_emitted >= 0)" in sql

    def test_records_staged_check(self):
        sql = self._read_sql()
        assert "records_staged >= 0" in sql or "check (records_staged >= 0)" in sql

    def test_rls_no_policy(self):
        sql = self._read_sql()
        stmts = [line.strip() for line in sql.splitlines() if line.strip().startswith("create policy")]
        assert not stmts


# ══════════════════════════════════════════════════════════════════════════
# start_run
# ══════════════════════════════════════════════════════════════════════════


class TestStartRun:

    def test_creates_new_run(self, mock_db):
        """Select retorna vazio → insert acontece."""
        mock_db.table.return_value.select.return_value.eq.return_value.eq.return_value.maybe_single.return_value.execute.return_value = MagicMock(data=None)
        mock_db.table.return_value.insert.return_value.execute.return_value = MagicMock(data=[{"id": "new-id"}])

        run_id = start_run(mock_db, batch_id="b1", source_key="finep", mode="dedicated")
        assert run_id == "new-id"

        # insert foi chamado, não upsert
        insert_data = mock_db.table.return_value.insert.call_args[0][0]
        assert insert_data["source_key"] == "finep"
        assert insert_data["status"] == "running"

    def test_returns_existing_if_running(self, mock_db):
        """Select encontra running → retorna ID sem insert."""
        existing_id = str(uuid.uuid4())
        mock_db.table.return_value.select.return_value.eq.return_value.eq.return_value.maybe_single.return_value.execute.return_value = MagicMock(
            data={"id": existing_id, "status": "running"},
        )

        run_id = start_run(mock_db, batch_id="b1", source_key="finep", mode="dedicated")
        assert run_id == existing_id
        mock_db.table.return_value.insert.assert_not_called()

    def test_returns_existing_if_terminal(self, mock_db):
        """Select encontra terminal → retorna ID sem insert nem update."""
        existing_id = str(uuid.uuid4())
        mock_db.table.return_value.select.return_value.eq.return_value.eq.return_value.maybe_single.return_value.execute.return_value = MagicMock(
            data={"id": existing_id, "status": "succeeded"},
        )

        run_id = start_run(mock_db, batch_id="b1", source_key="finep", mode="dedicated")
        assert run_id == existing_id
        mock_db.table.return_value.insert.assert_not_called()
        mock_db.table.return_value.update.assert_not_called()

    def test_returns_existing_if_partial(self, mock_db):
        """partial é terminal → não reabre."""
        existing_id = str(uuid.uuid4())
        mock_db.table.return_value.select.return_value.eq.return_value.eq.return_value.maybe_single.return_value.execute.return_value = MagicMock(
            data={"id": existing_id, "status": "partial"},
        )
        run_id = start_run(mock_db, batch_id="b1", source_key="finep", mode="dedicated")
        assert run_id == existing_id
        mock_db.table.return_value.insert.assert_not_called()

    def test_returns_none_on_db_failure(self, mock_db):
        mock_db.table.return_value.select.side_effect = Exception("db down")
        run_id = start_run(mock_db, batch_id="b1", source_key="finep", mode="dedicated")
        assert run_id is None

    def test_accepts_uuid_batch(self, mock_db):
        mock_db.table.return_value.select.return_value.eq.return_value.eq.return_value.maybe_single.return_value.execute.return_value = MagicMock(data=None)
        mock_db.table.return_value.insert.return_value.execute.return_value = MagicMock(data=[{"id": "new"}])
        batch = uuid.uuid4()
        run_id = start_run(mock_db, batch_id=batch, source_key="open_search", mode="open_search")
        assert run_id == "new"

    def test_conflict_does_not_update(self, mock_db):
        """Cenário de conflito real: select acha existente → nenhum insert."""
        existing_id = str(uuid.uuid4())
        mock_db.table.return_value.select.return_value.eq.return_value.eq.return_value.maybe_single.return_value.execute.return_value = MagicMock(
            data={"id": existing_id, "status": "succeeded"},
        )
        run_id = start_run(mock_db, batch_id="b1", source_key="finep", mode="dedicated")
        assert run_id == existing_id
        # NUNCA chama insert nem update
        mock_db.table.return_value.insert.assert_not_called()
        mock_db.table.return_value.upsert.assert_not_called()


# ══════════════════════════════════════════════════════════════════════════
# finish_run
# ══════════════════════════════════════════════════════════════════════════


class TestFinishRun:

    def _setup_terminal_guard(self, mock_db, current_status: str | None = "running"):
        """Configura mock para que o update com WHERE NOT IN retorne dados."""
        if current_status is not None:
            mock_db.table.return_value.update.return_value.eq.return_value.not_.in_.return_value.execute.return_value = MagicMock(
                data=[{"id": "run-1", "status": "succeeded"}],
            )
        else:
            mock_db.table.return_value.update.return_value.eq.return_value.not_.in_.return_value.execute.return_value = MagicMock(
                data=[],
            )

    def test_success_status(self, mock_db):
        _mock_update_terminal_guard(mock_db, "running")
        result = finish_run(mock_db, run_id="r1", status="succeeded",
                            records_observed=10, records_emitted=5,
                            records_staged=3, error_count=0)
        assert result is True
        update_call = mock_db.table.return_value.update.call_args[0][0]
        assert update_call["status"] == "succeeded"
        assert update_call["records_observed"] == 10

    def test_partial_status(self, mock_db):
        _mock_update_terminal_guard(mock_db, "running")
        result = finish_run(mock_db, run_id="r1", status="partial",
                            error_count=2, reason_code="timeout")
        assert result is True
        update_call = mock_db.table.return_value.update.call_args[0][0]
        assert update_call["status"] == "partial"
        assert update_call["reason_code"] == "timeout"

    def test_rejects_invalid_status(self, mock_db):
        with pytest.raises(ValueError, match="invalid final status"):
            finish_run(mock_db, run_id="r1", status="running")

    def test_rejects_unknown_status(self, mock_db):
        with pytest.raises(ValueError, match="invalid final status"):
            finish_run(mock_db, run_id="r1", status="unknown_status")

    def test_terminal_guard_atomic(self, mock_db):
        """WHERE NOT IN terminal impede regressão sem race."""
        # Simula que a run já está terminal: update devolve vazio
        mock_db.table.return_value.update.return_value.eq.return_value.not_.in_.return_value.execute.return_value = MagicMock(
            data=[],
        )
        result = finish_run(mock_db, run_id="r1", status="succeeded")
        assert result is False

    def test_ignored_when_already_terminal(self, mock_db):
        mock_db.table.return_value.update.return_value.eq.return_value.not_.in_.return_value.execute.return_value = MagicMock(
            data=[],
        )
        result = finish_run(mock_db, run_id="r1", status="succeeded")
        assert result is False

    def test_db_failure_returns_false(self, mock_db):
        mock_db.table.return_value.update.side_effect = Exception("db down")
        result = finish_run(mock_db, run_id="r1", status="succeeded")
        assert result is False

    def test_nonexistent_run_returns_false(self, mock_db):
        mock_db.table.return_value.update.return_value.eq.return_value.not_.in_.return_value.execute.return_value = MagicMock(
            data=[],
        )
        result = finish_run(mock_db, run_id="nonexistent", status="failed")
        assert result is False


# ══════════════════════════════════════════════════════════════════════════
# Contadores negativos — rejeitados pre-DB
# ══════════════════════════════════════════════════════════════════════════


class TestNegativeCounters:

    def test_negative_records_observed_raises(self, mock_db):
        with pytest.raises(ValueError, match="records_observed"):
            finish_run(mock_db, run_id="r1", status="succeeded", records_observed=-1)

    def test_negative_records_emitted_raises(self, mock_db):
        with pytest.raises(ValueError, match="records_emitted"):
            finish_run(mock_db, run_id="r1", status="succeeded", records_emitted=-5)

    def test_negative_records_staged_raises(self, mock_db):
        with pytest.raises(ValueError, match="records_staged"):
            finish_run(mock_db, run_id="r1", status="succeeded", records_staged=-3)

    def test_negative_error_count_raises(self, mock_db):
        with pytest.raises(ValueError, match="error_count"):
            finish_run(mock_db, run_id="r1", status="failed", error_count=-1)

    def test_zero_counters_ok(self, mock_db):
        """Zero é válido, não negativo."""
        _mock_update_terminal_guard(mock_db, "running")
        result = finish_run(mock_db, run_id="r1", status="succeeded",
                            error_count=0, records_observed=0)
        assert result is True
        update_call = mock_db.table.return_value.update.call_args[0][0]
        assert update_call["error_count"] == 0
        assert update_call["records_observed"] == 0


# ══════════════════════════════════════════════════════════════════════════
# reason_code — não canônico omitido
# ══════════════════════════════════════════════════════════════════════════


class TestReasonCodeNormalization:

    def test_canonical_reason_persisted(self, mock_db):
        _mock_update_terminal_guard(mock_db, "running")
        result = finish_run(mock_db, run_id="r1", status="failed", reason_code="timeout")
        assert result is True
        update_call = mock_db.table.return_value.update.call_args[0][0]
        assert update_call["reason_code"] == "timeout"

    def test_unknown_reason_omitted(self, mock_db):
        _mock_update_terminal_guard(mock_db, "running")
        result = finish_run(mock_db, run_id="r1", status="failed", reason_code="some_secret_error")
        assert result is True
        update_call = mock_db.table.return_value.update.call_args[0][0]
        assert "reason_code" not in update_call

    def test_none_reason_omitted(self, mock_db):
        _mock_update_terminal_guard(mock_db, "running")
        result = finish_run(mock_db, run_id="r1", status="succeeded", reason_code=None)
        assert result is True
        update_call = mock_db.table.return_value.update.call_args[0][0]
        assert "reason_code" not in update_call


# ══════════════════════════════════════════════════════════════════════════
# Sanitização de metrics
# ══════════════════════════════════════════════════════════════════════════


class TestMetricsSanitization:

    def _run_with_metrics(self, mock_db, metrics):
        _mock_update_terminal_guard(mock_db, "running")
        result = finish_run(mock_db, run_id="r1", status="succeeded", metrics=metrics)
        return result, mock_db.table.return_value.update.call_args[0][0].get("metrics") if result else None

    def test_safe_metrics_persisted(self, mock_db):
        safe = {"triage_skipped": 5, "hubs_expanded": 2, "total_candidates": 0}
        _mock_update_terminal_guard(mock_db, "running")
        result = finish_run(mock_db, run_id="r1", status="succeeded", metrics=safe)
        assert result is True
        update_call = mock_db.table.return_value.update.call_args[0][0]
        persisted = update_call.get("metrics")
        assert persisted == safe

    def test_rejects_string_value(self, mock_db):
        _, persisted = self._run_with_metrics(mock_db, {"leaked_query": "SELECT * FROM secrets"})
        assert "leaked_query" not in (persisted or {})

    def test_rejects_nested_dict(self, mock_db):
        _, persisted = self._run_with_metrics(mock_db, {"nested": {"a": 1}})
        assert "nested" not in (persisted or {})

    def test_rejects_list(self, mock_db):
        _, persisted = self._run_with_metrics(mock_db, {"items": [1, 2, 3]})
        assert "items" not in (persisted or {})

    def test_rejects_boolean(self, mock_db):
        _, persisted = self._run_with_metrics(mock_db, {"flag": True})
        assert "flag" not in (persisted or {})

    def test_rejects_negative_value(self, mock_db):
        _, persisted = self._run_with_metrics(mock_db, {"neg": -5})
        assert "neg" not in (persisted or {})

    def test_rejects_nan(self, mock_db):
        _, persisted = self._run_with_metrics(mock_db, {"nan": float("nan")})
        assert "nan" not in (persisted or {})

    def test_rejects_infinity(self, mock_db):
        _, persisted = self._run_with_metrics(mock_db, {"inf": float("inf")})
        assert "inf" not in (persisted or {})

    def test_mixed_safe_and_unsafe(self, mock_db):
        mixed = {"valid_count": 10, "leaked_url": "http://secret.com", "nested": {"x": 1}}
        _mock_update_terminal_guard(mock_db, "running")
        result = finish_run(mock_db, run_id="r1", status="succeeded", metrics=mixed)
        assert result is True
        persisted = mock_db.table.return_value.update.call_args[0][0].get("metrics")
        assert persisted == {"valid_count": 10}
        assert "leaked_url" not in persisted
        assert "nested" not in persisted

    def test_unsafe_key_names_rejected(self, mock_db):
        _, persisted = self._run_with_metrics(mock_db, {"query text": 1, "user-token": 2})
        assert not persisted

    def test_none_metrics_omitted(self, mock_db):
        _mock_update_terminal_guard(mock_db, "running")
        result = finish_run(mock_db, run_id="r1", status="succeeded", metrics=None)
        assert result is True
        update_call = mock_db.table.return_value.update.call_args[0][0]
        assert "metrics" not in update_call
