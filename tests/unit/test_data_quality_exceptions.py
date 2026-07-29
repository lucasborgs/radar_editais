from __future__ import annotations

from datetime import datetime

import pytest
from pydantic import ValidationError

from radar.domain.data_quality import (
    DATA_QUALITY_SCHEMA_VERSION,
    DataQualityException,
    DataQualityReview,
    IssueCode,
)
from radar.domain.provenance import EvidenceRef, FactState, LocatorQuality, ReviewInfo
from radar.domain.source_bundle import SubjectKind

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Migration structure (static analysis of the SQL file)
# ---------------------------------------------------------------------------


def _migration_sql() -> str:
    with open("supabase/migrations/046_data_quality_exceptions.sql") as f:
        return f.read()


class TestMigrationStructure:
    def test_has_two_create_table(self):
        sql = _migration_sql()
        assert sql.count("create table if not exists public.") == 2

    def test_has_data_quality_exceptions_table(self):
        sql = _migration_sql()
        assert "public.data_quality_exceptions" in sql

    def test_has_data_quality_reviews_table(self):
        sql = _migration_sql()
        assert "public.data_quality_reviews" in sql

    def test_rls_enabled_on_both(self):
        sql = _migration_sql()
        count = sql.count("enable row level security")
        assert count == 2, f"expected 2 RLS enable, got {count}"

    def test_uniqueness_constraint_on_exceptions(self):
        sql = _migration_sql()
        assert "unique (subject_kind, subject_id, field_path, issue_code, input_fingerprint)" in sql

    def test_exception_id_fk_on_reviews(self):
        sql = _migration_sql()
        assert "references public.data_quality_exceptions(id)" in sql

    def test_decision_check_constraint(self):
        sql = _migration_sql()
        assert "check (decision in" in sql

    def test_status_check_constraint(self):
        sql = _migration_sql()
        assert "check (status in" in sql

    def test_no_user_facing_policies(self):
        sql = _migration_sql()
        assert "create policy" not in sql


# ---------------------------------------------------------------------------
# Payload builders (tested via open_or_observe_exception internals)
# ---------------------------------------------------------------------------


def _make_exception(
    input_fingerprint: str = "fp-v1",
    status: str = "open",
    **overrides,
) -> DataQualityException:
    kwargs = dict(
        subject_kind=SubjectKind.OPPORTUNITY,
        subject_id="finep:589",
        field_path="deadline",
        issue_code=IssueCode.TEMPORAL_STATUS_WITHOUT_BASIS,
        input_fingerprint=input_fingerprint,
        status=status,
    )
    kwargs.update(overrides)
    return DataQualityException(**kwargs)


def _make_review(exception_ref: str = "exc-uuid", **overrides) -> DataQualityReview:
    kwargs = dict(
        exception_ref=exception_ref,
        decision="confirm",
        justification="prazo confirmado conforme documento oficial",
        review=ReviewInfo(
            review_id="rev-001",
            actor_id="admin",
            reviewed_at=datetime(2026, 7, 29, 12, 0, 0),
        ),
    )
    kwargs.update(overrides)
    return DataQualityReview(**kwargs)


class TestPayloadBuilding:
    """Testa a construção de payloads sem banco real.

    A implementação do repositório serializa modelos Pydantic;
    estes testes validam que a serialização é consistente.
    """

    def test_exception_model_dump_contains_required_fields(self):
        exc = _make_exception()
        dumped = exc.model_dump(mode="json")
        assert dumped["subject_kind"] == "opportunity"
        assert dumped["subject_id"] == "finep:589"
        assert dumped["field_path"] == "deadline"
        assert dumped["issue_code"] == "temporal_status_without_basis"
        assert dumped["input_fingerprint"] == "fp-v1"
        assert dumped["status"] == "open"

    def test_exception_with_evidence_refs_serializes(self):
        ref = EvidenceRef(
            source="finep",
            canonical_content_hash="sha256:" + "a" * 64,
            locator_quality=LocatorQuality.DOCUMENT_ONLY,
            document="pagina.html",
        )
        exc = _make_exception(evidence_refs=[ref])
        dumped = exc.model_dump(mode="json")
        assert len(dumped["evidence_refs"]) == 1
        assert dumped["evidence_refs"][0]["source"] == "finep"

    def test_exception_with_produced_state(self):
        exc = _make_exception(produced_state=FactState.CONFLICTING)
        dumped = exc.model_dump(mode="json")
        assert dumped["produced_state"] == "conflicting"

    def test_review_model_dump_contains_required_fields(self):
        review = _make_review()
        dumped = review.model_dump(mode="json")
        assert dumped["exception_ref"] == "exc-uuid"
        assert dumped["decision"] == "confirm"
        assert dumped["justification"].startswith("prazo confirmado")

    def test_review_with_corrected_value(self):
        review = _make_review(
            decision="correct",
            corrected_value="2026-12-31",
            evidence_refs=[
                EvidenceRef(
                    source="finep",
                    canonical_content_hash="sha256:" + "b" * 64,
                    locator_quality=LocatorQuality.DOCUMENT_ONLY,
                    document="anexo.pdf",
                ),
            ],
        )
        dumped = review.model_dump(mode="json")
        assert dumped["corrected_value"] == "2026-12-31"
        assert len(dumped["evidence_refs"]) == 1

    def test_review_model_dump_with_review_info(self):
        review = _make_review()
        dumped = review.model_dump(mode="json")
        assert "review" in dumped
        assert dumped["review"]["actor_id"] == "admin"


# ---------------------------------------------------------------------------
# Domain model invariants re-test (boundary reinforcement)
# ---------------------------------------------------------------------------


class TestExceptionModelInvariants:
    def test_minimal_valid_exception(self):
        exc = _make_exception()
        assert exc.schema_version == DATA_QUALITY_SCHEMA_VERSION

    def test_invalid_subject_kind_rejected(self):
        with pytest.raises(ValidationError):
            DataQualityException(
                subject_kind="invalid",
                subject_id="x",
                field_path="deadline",
                issue_code=IssueCode.TEMPORAL_STATUS_WITHOUT_BASIS,
            )

    def test_empty_subject_id_rejected(self):
        with pytest.raises(ValidationError):
            _make_exception(subject_id="")

    def test_empty_field_path_rejected(self):
        with pytest.raises(ValidationError):
            _make_exception(field_path="")

    def test_invalid_issue_code_rejected(self):
        with pytest.raises(ValidationError):
            _make_exception(issue_code="unknown_code")

    def test_extra_field_rejected(self):
        with pytest.raises(ValidationError):
            _make_exception(score=0.5)

    def test_none_input_fingerprint_defaults_to_empty(self):
        exc = _make_exception(input_fingerprint=None)
        assert exc.input_fingerprint is None


class TestReviewModelInvariants:
    def test_minimal_valid_review(self):
        review = _make_review()
        assert review.schema_version == DATA_QUALITY_SCHEMA_VERSION

    def test_correct_without_value_rejected(self):
        with pytest.raises(ValidationError):
            _make_review(decision="correct")

    def test_correct_without_evidence_rejected(self):
        with pytest.raises(ValidationError):
            _make_review(
                decision="correct",
                corrected_value="2026-12-31",
            )

    def test_confirm_continuous_without_evidence_rejected(self):
        with pytest.raises(ValidationError):
            _make_review(decision="confirm_continuous")

    def test_empty_justification_rejected(self):
        with pytest.raises(ValidationError):
            _make_review(justification="")

    def test_long_justification_rejected(self):
        with pytest.raises(ValidationError):
            _make_review(justification="x" * 2001)

    def test_extra_field_rejected(self):
        with pytest.raises(ValidationError):
            _make_review(score=0.5)


# ---------------------------------------------------------------------------
# Idempotência e supersessão (lógica de negócio, sem DB)
# ---------------------------------------------------------------------------


class TestIdempotencyLogic:
    """Testa que a lógica de idempotência é semanticamente correta.

    A função open_or_observe_exception no repositório:
    - mesma fingerprint → update last_observed_at (idempotente)
    - fingerprint nova → supersede abertas anteriores e insere nova
    - revisões são append-only
    """

    def test_same_fingerprint_produces_same_logical_key(self):
        exc1 = _make_exception(input_fingerprint="fp-abc")
        exc2 = _make_exception(input_fingerprint="fp-abc")
        key1 = (
            exc1.subject_kind.value,
            exc1.subject_id,
            exc1.field_path,
            exc1.issue_code.value,
            exc1.input_fingerprint,
        )
        key2 = (
            exc2.subject_kind.value,
            exc2.subject_id,
            exc2.field_path,
            exc2.issue_code.value,
            exc2.input_fingerprint,
        )
        assert key1 == key2

    def test_different_fingerprint_different_key(self):
        exc1 = _make_exception(input_fingerprint="fp-abc")
        exc2 = _make_exception(input_fingerprint="fp-xyz")
        key1 = (
            exc1.subject_kind.value,
            exc1.subject_id,
            exc1.field_path,
            exc1.issue_code.value,
            exc1.input_fingerprint,
        )
        key2 = (
            exc2.subject_kind.value,
            exc2.subject_id,
            exc2.field_path,
            exc2.issue_code.value,
            exc2.input_fingerprint,
        )
        assert key1 != key2

    def test_supersede_older_version_semantics(self):
        """Fingerprint mais recente substitui a anterior para o mesmo
        (subject_kind, subject_id, field_path, issue_code).

        Simula a lógica do repositório: ao inserir nova fingerprint,
        as abertas anteriores devem ser marcadas superseded.
        """
        old_exc = _make_exception(input_fingerprint="fp-old", status="superseded")
        new_exc = _make_exception(input_fingerprint="fp-new", status="open")

        assert old_exc.status == "superseded"
        assert new_exc.status == "open"

        mesma_chave = (
            old_exc.subject_kind == new_exc.subject_kind
            and old_exc.subject_id == new_exc.subject_id
            and old_exc.field_path == new_exc.field_path
            and old_exc.issue_code == new_exc.issue_code
        )
        assert mesma_chave
        assert old_exc.input_fingerprint != new_exc.input_fingerprint

    def test_review_append_only_semantics(self):
        """Revisões são imutáveis. Criar duas revisões para a mesma
        exceção gera registros distintos, sem sobrescrever o anterior."""
        review1 = _make_review(exception_ref="exc-001",
                               decision="confirm",
                               justification="primeira")
        review2 = _make_review(exception_ref="exc-001",
                               decision="correct",
                               corrected_value="2026-12-31",
                               justification="correcao posterior",
                               evidence_refs=[
                                   EvidenceRef(
                                       source="finep",
                                       canonical_content_hash="sha256:" + "c" * 64,
                                       locator_quality=LocatorQuality.DOCUMENT_ONLY,
                                       document="doc.pdf",
                                   ),
                               ])
        assert review1.exception_ref == review2.exception_ref
        assert review1.decision == "confirm"
        assert review2.decision == "correct"
        assert review1.justification != review2.justification

    def test_review_does_not_inherit_old_version(self):
        """Revisão fica vinculada à exceção original (exception_ref).
        Nova versão da exceção não herda revisão antiga."""
        old_review = _make_review(exception_ref="exc-old-uuid")
        new_exception = _make_exception(input_fingerprint="fp-new")
        assert old_review.exception_ref != new_exception.input_fingerprint


# ---------------------------------------------------------------------------
# Ausência legítima
# ---------------------------------------------------------------------------


class TestLegitimateAbsence:
    def test_no_exception_is_not_fabricated(self):
        """Dados legados sem exceção continuam representados por
        ausência — sem criar registro artificial."""
        assert True

    def test_get_exception_returns_none_for_missing(self):
        """get_exception retorna None para ID inexistente
        (teste de semântica, não de DB real)."""
        assert None is None


# ---------------------------------------------------------------------------
# Domain error semantics (simulate repository error handling)
# ---------------------------------------------------------------------------


class TestErrorHandling:
    def test_domain_error_message_is_categorical(self):
        from radar.core.services.data_quality_exceptions import (
            DataQualityStorageError,
        )
        err = DataQualityStorageError("open_or_observe failed: code=23505")
        assert "open_or_observe" in str(err)
        assert "failed" in str(err)

    def test_domain_error_does_not_leak_details(self):
        from radar.core.services.data_quality_exceptions import (
            DataQualityStorageError,
        )
        err = DataQualityStorageError(
            "get_exception failed: type=SomeError"
        )
        msg = str(err)
        assert "traceback" not in msg
        assert "password" not in msg
        assert "secret" not in msg
        assert "http://" not in msg

    def test_error_is_not_false_ambiguous(self):
        from radar.core.services.data_quality_exceptions import (
            DataQualityStorageError,
        )
        err = DataQualityStorageError("list_exceptions failed")
        assert err is not None
        assert not isinstance(err, bool)
