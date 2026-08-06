from __future__ import annotations

from datetime import date, datetime

import pytest

from radar.core.services import temporal_read_model as temporal
from radar.core.services.temporal_quality import _build_temporal_fingerprint
from radar.domain.data_quality import DataQualityReview, TemporalMode, ValidityState
from radar.domain.provenance import EvidenceRef, LocatorQuality, ReviewInfo

pytestmark = pytest.mark.unit


def _evidence() -> EvidenceRef:
    return EvidenceRef(
        source="finep",
        silver_source_hash="md5:" + "a" * 32,
        document="finep.json",
        quote="Inscrições em fluxo contínuo.",
        locator_quality=LocatorQuality.DOCUMENT_ONLY,
    )


def test_batch_read_model_uses_one_load_per_data_type_and_is_conservative(monkeypatch):
    """N sujeitos não produzem N+1; Finep/Eureka só passa com revisão válida."""
    as_of = date(2026, 7, 29)
    eureka = temporal.TemporalSubject("finep:eureka", None, "ABERTA", "2026-07-15T10:00:00+00:00")
    today = temporal.TemporalSubject("finep:today", as_of, "ABERTA", "2026-07-15T10:00:00+00:00")
    closed = temporal.TemporalSubject("finep:closed", date(2026, 7, 1), "ENCERRADA", None)
    ref = _evidence()
    exception = {
        "id": "exc-eureka",
        "subject_id": eureka.subject_id,
        "field_path": "deadline",
        "issue_code": "temporal_status_without_basis",
        "produced_value": "ABERTA",
        "evidence_refs": [ref.model_dump(mode="json")],
        "input_fingerprint": _build_temporal_fingerprint(None, "ABERTA"),
        "status": "resolved",
        "last_observed_at": "2026-07-20T10:00:00+00:00",
    }
    review = DataQualityReview(
        exception_ref="exc-eureka",
        decision="confirm_continuous",
        justification="Evidência documental preservada.",
        evidence_refs=[ref],
        review=ReviewInfo(
            review_id="review-eureka", actor_id="admin", overridden=False,
            reviewed_at=datetime.fromisoformat("2026-07-21T10:00:00-03:00"),
        ),
    )
    calls = {"exceptions": 0, "reviews": 0}

    def load_exceptions(ids):
        calls["exceptions"] += 1
        assert set(ids) == {eureka.subject_id, today.subject_id, closed.subject_id}
        return [exception]

    def load_reviews(ids):
        calls["reviews"] += 1
        assert ids == ["exc-eureka"]
        return {"exc-eureka": review}

    monkeypatch.setattr(temporal, "load_temporal_exceptions", load_exceptions)
    monkeypatch.setattr(temporal, "load_current_temporal_reviews", load_reviews)

    models = temporal.resolve_temporal_read_models([eureka, today, closed], as_of=as_of)

    assert calls == {"exceptions": 1, "reviews": 1}
    assert models[eureka.subject_id].temporal_mode is TemporalMode.CONTINUOUS
    assert models[eureka.subject_id].validity_state is ValidityState.ACTIVE
    assert models[eureka.subject_id].decision_source == "human_review"
    assert models[today.subject_id].validity_state is ValidityState.ACTIVE
    assert models[closed.subject_id].validity_state is ValidityState.CLOSED
    assert "exception_id" not in models[eureka.subject_id].public_payload()
    assert "justification" not in models[eureka.subject_id].public_payload()


def test_batch_failure_never_grants_active(monkeypatch, caplog):
    subject = temporal.TemporalSubject("finep:future", date(2026, 12, 31), "ABERTA")

    def fail(_):
        raise RuntimeError("secret provider payload")

    monkeypatch.setattr(temporal, "load_temporal_exceptions", fail)
    model = temporal.resolve_temporal_read_models([subject], as_of=date(2026, 7, 29))[subject.subject_id]

    assert model.validity_state is ValidityState.NEEDS_REVIEW
    assert "secret provider payload" not in caplog.text
    assert "RuntimeError" in caplog.text


def test_open_without_deadline_is_needs_review_without_exception(monkeypatch):
    monkeypatch.setattr(temporal, "load_temporal_exceptions", lambda _: [])
    monkeypatch.setattr(temporal, "load_current_temporal_reviews", lambda _: {})
    subject = temporal.TemporalSubject("finep:eureka", None, "ABERTA")

    model = temporal.resolve_temporal_read_models([subject], as_of=date(2026, 7, 29))[subject.subject_id]

    assert model.temporal_mode is TemporalMode.UNKNOWN
    assert model.validity_state is ValidityState.NEEDS_REVIEW
    assert model.decision_source == "legacy"


def test_programa_row_is_continuous_and_active(monkeypatch):
    """Programa (kind=programa) sem deadline vira ACTIVE(CONTINUOUS) pela
    evidência contínua do catálogo — desbloqueia o Stage 0 do match."""
    monkeypatch.setattr(temporal, "load_temporal_exceptions", lambda _: [])
    monkeypatch.setattr(temporal, "load_current_temporal_reviews", lambda _: {})

    subject = temporal.subjects_from_rows([{
        "kind": "programa",
        "native_id": "finep:startup",
        "name": "Finep Startup",
        "status": "ativa",
        "deadline": None,
        "updated_at": "2026-08-06T10:00:00+00:00",
    }])[0]

    assert subject.continuous_evidence is not None
    model = temporal.resolve_temporal_read_models([subject], as_of=date(2026, 8, 6))[subject.subject_id]

    assert model.temporal_mode is TemporalMode.CONTINUOUS
    assert model.validity_state is ValidityState.ACTIVE
    assert model.decision_source == "source"


def test_programa_closed_is_needs_review(monkeypatch):
    """Programa com status fechado + evidência contínua conflita → fail closed."""
    monkeypatch.setattr(temporal, "load_temporal_exceptions", lambda _: [])
    monkeypatch.setattr(temporal, "load_current_temporal_reviews", lambda _: {})

    subject = temporal.subjects_from_rows([{
        "kind": "programa",
        "native_id": "finep:startup",
        "status": "encerrada",
        "deadline": None,
    }])[0]

    model = temporal.resolve_temporal_read_models([subject], as_of=date(2026, 8, 6))[subject.subject_id]

    assert model.validity_state is ValidityState.NEEDS_REVIEW


def test_edital_row_keeps_strict_model(monkeypatch):
    """Edital sem deadline NÃO ganha evidência contínua — continua NEEDS_REVIEW."""
    monkeypatch.setattr(temporal, "load_temporal_exceptions", lambda _: [])
    monkeypatch.setattr(temporal, "load_current_temporal_reviews", lambda _: {})

    subject = temporal.subjects_from_rows([{
        "kind": "edital",
        "native_id": "finep:774",
        "status": "aberta",
        "deadline": None,
    }])[0]

    assert subject.continuous_evidence is None
    model = temporal.resolve_temporal_read_models([subject], as_of=date(2026, 8, 6))[subject.subject_id]

    assert model.validity_state is ValidityState.NEEDS_REVIEW
