from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

from radar.domain.adaptive_extraction import (
    ADAPTIVE_EXTRACTION_SCHEMA_VERSION,
    ExtractionStatus,
    ExtractionTarget,
)
from radar.domain.provenance import FactState, LocatorQuality

pytestmark = pytest.mark.unit


_SCRIPT = Path(__file__).parents[2] / "scripts" / "run_rt06_t07a.py"
_SPEC = importlib.util.spec_from_file_location("run_rt06_t07a", _SCRIPT)
assert _SPEC and _SPEC.loader
t07a = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(t07a)


def _target(field: str = "deadline") -> ExtractionTarget:
    return ExtractionTarget(
        field_path=field,
        value_type="date",
        required_for="eligibility",
        criticality="decision",
    )


def _artifact(status: ExtractionStatus = ExtractionStatus.COMPLETE) -> SimpleNamespace:
    return SimpleNamespace(status=status, schema_version=ADAPTIVE_EXTRACTION_SCHEMA_VERSION)


def _claim(state: FactState, *, evidence: bool = False) -> SimpleNamespace:
    refs = []
    if evidence:
        refs = [SimpleNamespace(
            locator_quality=LocatorQuality.EXACT,
            canonical_content_hash="sha256:" + "a" * 64,
            silver_source_hash=None,
            quote="Prazo final: 2026-12-31",
            page=1,
            section_path=["Cronograma"],
        )]
    return SimpleNamespace(
        value="2026-12-31" if state is not FactState.ABSENT else None,
        provenance=SimpleNamespace(state=state, evidence_refs=refs),
    )


def _row(state: FactState, *, evidence: bool = False) -> dict:
    return t07a._gate_row(
        subject_id="finep:1",
        document="edital.pdf",
        family="temporal",
        target=_target(),
        artifact=_artifact(),
        claim=_claim(state, evidence=evidence),
    )


def test_gate_publica_stated_apenas_com_evidencia_resolvida():
    row = _row(FactState.STATED, evidence=True)

    assert row["schema_valid"] is True
    assert row["evidence_resolved"] is True
    assert row["material_conflict"] is False
    assert row["review_required"] is False


def test_gate_envia_conflito_material_para_hold_e_revisao():
    state = FactState.CONFLICTING
    row = _row(state)

    assert row["material_conflict"] is True
    assert row["review_required"] is True


def test_gate_mantem_unknown_como_lacuna_sem_fila_humana_automatica():
    row = _row(FactState.UNKNOWN)

    assert row["state"] == "unknown"
    assert row["review_required"] is False


def test_gate_rebaixa_stated_sem_evidencia():
    row = _row(FactState.STATED)

    assert row["review_required"] is True
    assert "evidence_unresolved" in row["error_codes"]


def test_absent_e_publicavel_sem_aprovacao_humana():
    row = _row(FactState.ABSENT)

    assert row["schema_valid"] is True
    assert row["review_required"] is False


def test_metricas_nao_calculam_precisao_sem_goldens_e_nao_usam_legado():
    rows = [_row(FactState.STATED, evidence=True), _row(FactState.INFERRED)]
    metrics = t07a._diagnostic_metrics(rows)

    assert metrics["states"] == {"inferred": 1, "stated": 1}
    assert metrics["schema_invalid"] == 0


def test_fila_humana_e_apenas_conflito_material(tmp_path, monkeypatch):
    monkeypatch.setattr(t07a, "OUT", tmp_path)
    t07a.OUT.mkdir(exist_ok=True)
    rows = [_row(FactState.STATED, evidence=True), _row(FactState.CONFLICTING)]

    t07a._write_human_review([row for row in rows if row["review_required"]])
    review = (tmp_path / "T07-A-human-review.md").read_text(encoding="utf-8")

    assert "**Total para revisão inicial:** 1 linhas." in review
    assert "edital.pdf" in review
