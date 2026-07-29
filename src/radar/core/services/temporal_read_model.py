"""Read model temporal canônico para consumidores de oportunidades.

Centraliza a avaliação T01 e a projeção de revisão T04. A fila e as revisões
são lidas uma vez por lote; nenhum consumidor deve consultar qualidade factual
por oportunidade.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel

from radar.core.services.data_quality_exceptions import (
    load_current_temporal_reviews,
    load_temporal_exceptions,
)
from radar.core.services.data_quality_reviews import project_loaded_temporal_validity
from radar.core.services.temporal_quality import _build_temporal_fingerprint
from radar.domain.data_quality import TemporalMode, ValidityState

logger = logging.getLogger(__name__)

DecisionSource = Literal["source", "human_review", "legacy"]


@dataclass(frozen=True)
class TemporalSubject:
    """Campos mínimos de uma oportunidade necessários para decidir validade."""

    subject_id: str
    deadline: date | None
    status: str | None
    updated_at: str | None = None


class TemporalReadModel(BaseModel):
    """Payload público seguro; não inclui exceção, revisão ou nota interna."""

    model_config = {"extra": "forbid"}

    temporal_mode: TemporalMode
    validity_state: ValidityState
    temporal_value: str | None
    decision_source: DecisionSource
    last_verified_at: str | None = None

    def public_payload(self) -> dict:
        return self.model_dump(mode="json")


def _today_sao_paulo() -> date:
    return datetime.now(ZoneInfo("America/Sao_Paulo")).date()


def _to_date(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def subjects_from_rows(rows: list[dict]) -> list[TemporalSubject]:
    """Adapta linhas gold sem criar uma cópia temporal em ``entities``."""
    subjects: list[TemporalSubject] = []
    for row in rows:
        subject_id = str(row.get("native_id") or "")
        if not subject_id:
            continue
        subjects.append(TemporalSubject(
            subject_id=subject_id,
            deadline=_to_date(row.get("deadline")),
            status=row.get("status"),
            updated_at=_safe_timestamp(row.get("updated_at")),
        ))
    return subjects


def _safe_timestamp(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).isoformat()
    except ValueError:
        return None


def _fingerprint(subject: TemporalSubject) -> str:
    return _build_temporal_fingerprint(subject.deadline, subject.status)


def _matching_exception(rows: list[dict], fingerprint: str) -> dict | None:
    current = [
        row for row in rows
        if row.get("status") != "superseded"
        and row.get("input_fingerprint") == fingerprint
    ]
    if not current:
        return None
    # Uma fingerprint é idempotente; a ordenação só torna leituras legadas
    # anômalas determinísticas, sem escolher conteúdo de revisão.
    return sorted(current, key=lambda row: str(row.get("last_observed_at") or ""), reverse=True)[0]


def _conservative(subject: TemporalSubject) -> TemporalReadModel:
    value = subject.deadline.isoformat() if subject.deadline else None
    return TemporalReadModel(
        temporal_mode=TemporalMode.UNKNOWN,
        validity_state=ValidityState.NEEDS_REVIEW,
        temporal_value=value,
        decision_source="legacy",
        last_verified_at=subject.updated_at,
    )


def resolve_temporal_read_models(
    subjects: list[TemporalSubject], *, as_of: date | None = None,
) -> dict[str, TemporalReadModel]:
    """Resolve N sujeitos com uma carga de exceções e uma de revisões.

    Qualquer falha na carga é categorizada no log e devolve o estado
    conservador. A avaliação determinística sem exceção ainda pode declarar
    prazo datado como ativo/encerrado; ausência de prazo jamais vira contínuo.
    """
    unique = {subject.subject_id: subject for subject in subjects if subject.subject_id}
    if not unique:
        return {}
    effective_as_of = as_of or _today_sao_paulo()
    ordered = list(unique.values())
    fingerprints = {subject.subject_id: _fingerprint(subject) for subject in ordered}
    try:
        exception_rows = load_temporal_exceptions(list(unique))
        by_subject: dict[str, list[dict]] = {}
        for row in exception_rows:
            by_subject.setdefault(str(row.get("subject_id") or ""), []).append(row)
        selected = {
            subject_id: _matching_exception(rows, fingerprints[subject_id])
            for subject_id, rows in by_subject.items()
            if subject_id in fingerprints
        }
        exception_ids = [row["id"] for row in selected.values() if row and row.get("id")]
        reviews = load_current_temporal_reviews(exception_ids)
    except Exception as exc:  # noqa: BLE001 — fail closed
        logger.warning(
            "temporal_read_model: batch load failure category=%s subjects=%d",
            type(exc).__name__, len(ordered),
        )
        return {subject.subject_id: _conservative(subject) for subject in ordered}

    out: dict[str, TemporalReadModel] = {}
    for subject in ordered:
        row = selected.get(subject.subject_id)
        projection = project_loaded_temporal_validity(
            deadline=subject.deadline,
            status=subject.status,
            input_fingerprint=fingerprints[subject.subject_id],
            exception_row=row,
            expected_exception_id=row["id"] if row and row.get("id") else None,
            review=reviews.get(row["id"]) if row and row.get("id") else None,
            as_of=effective_as_of,
        )
        if projection.review_id:
            source: DecisionSource = "human_review"
            verified_at = _safe_timestamp(
                projection.provenance.review.reviewed_at.isoformat()
                if projection.provenance and projection.provenance.review else None
            )
        elif projection.validity_state is ValidityState.NEEDS_REVIEW:
            source = "legacy"
            verified_at = subject.updated_at
        else:
            source = "source"
            verified_at = subject.updated_at
        out[subject.subject_id] = TemporalReadModel(
            temporal_mode=projection.temporal_mode,
            validity_state=projection.validity_state,
            temporal_value=projection.value,
            decision_source=source,
            last_verified_at=verified_at,
        )
    return out


__all__ = [
    "TemporalReadModel",
    "TemporalSubject",
    "resolve_temporal_read_models",
    "subjects_from_rows",
]
