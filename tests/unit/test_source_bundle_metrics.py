"""Testes proporcionais das métricas diagnósticas RT04-T07."""
from __future__ import annotations

from radar.core.services.source_bundle_metrics import (
    CompositionOutcome,
    compute_source_bundle_diagnostics,
)
from radar.domain.provenance import EvidenceRef, FactState, LocatorQuality
from radar.domain.source_bundle import SourceBundle
from tests.fixtures.source_bundles.fixtures import (
    actor_insufficient,
    fapesc_base_amendment,
    web_portal_challenge,
)


def _bundle(data: dict) -> SourceBundle:
    return SourceBundle.model_validate(data)


def _ref(*, with_lineage: bool) -> EvidenceRef:
    hashes = {
        "canonical_content_hash": "sha256:" + "a" * 64,
        "bundle_hash": "sha256:" + "b" * 64 if with_lineage else None,
        "content_hash": "sha256:" + "c" * 64 if with_lineage else None,
    }
    return EvidenceRef(
        source="fixture",
        document="fixture.txt",
        locator_quality=LocatorQuality.DOCUMENT_ONLY,
        **hashes,
    )


def test_fixture_baseline_measures_versions_roles_and_actor_gaps():
    web = _bundle(web_portal_challenge())
    fapesc = _bundle(fapesc_base_amendment())
    actor = _bundle(actor_insufficient())
    fapesc_partial = fapesc.model_copy(
        update={"acquisition_status": "partial", "collected_at": "2026-07-28T12:00:00Z"}
    )

    result = compute_source_bundle_diagnostics([web, fapesc, actor, fapesc_partial])

    assert result.subjects_with_bundle == 3
    assert result.subjects_with_current_complete_bundle == 2
    assert result.versions_by_subject == {
        "fapesc:37-2026": 2,
        "ict:exemplo:lab-inovacao": 1,
        "web:a1b2c3d4e5f6": 1,
    }
    assert result.documents_by_role == {
        "amendment": 2,
        "base_notice": 2,
        "official_page": 1,
        "opportunity_page": 1,
        "program_page": 1,
    }
    assert result.actors_without_official_content == ()
    assert result.actors_without_complete_bundle == ("ict:exemplo:lab-inovacao",)


def test_partial_posterior_does_not_remove_current_complete_subject():
    complete = _bundle(fapesc_base_amendment())
    partial = complete.model_copy(
        update={"acquisition_status": "partial", "collected_at": "2026-07-28T12:00:00Z"}
    )

    result = compute_source_bundle_diagnostics([complete, partial])

    assert result.versions_by_subject == {"fapesc:37-2026": 2}
    assert result.subjects_with_current_complete_bundle == 1


def test_actor_with_only_curated_material_is_an_explicit_official_content_gap():
    data = actor_insufficient()
    data.update({
        "subject_kind": "investor",
        "subject_id": "investidor:teste",
        "source": "curadoria",
        "acquisition_status": "complete",
    })
    data["documents"][0]["role"] = "curated_record"
    bundle = _bundle(data)

    result = compute_source_bundle_diagnostics([bundle])

    assert result.actors_without_official_content == ("investidor:teste",)
    assert result.actors_without_complete_bundle == ()


def test_fact_and_composition_denominators_are_null_when_not_observed():
    result = compute_source_bundle_diagnostics([_bundle(web_portal_challenge())])

    assert result.critical_facts_total is None
    assert result.critical_facts_with_bundle_lineage is None
    assert result.critical_fact_lineage_rate is None
    assert result.conflicting_fields is None
    assert result.precedence_resolutions is None


def test_fact_lineage_and_explicit_composition_outcomes_are_counted_without_inference():
    result = compute_source_bundle_diagnostics(
        [_bundle(web_portal_challenge())],
        critical_fact_refs=[_ref(with_lineage=True), _ref(with_lineage=False)],
        composition_outcomes=[
            CompositionOutcome(state=FactState.STATED, precedence_applied=True),
            CompositionOutcome(state=FactState.CONFLICTING),
            CompositionOutcome(state=FactState.STATED),
        ],
    )

    assert result.critical_facts_total == 2
    assert result.critical_facts_with_bundle_lineage == 1
    assert result.critical_fact_lineage_rate == 0.5
    assert result.conflicting_fields == 1
    assert result.precedence_resolutions == 1


def test_observed_empty_denominators_remain_explicitly_empty():
    result = compute_source_bundle_diagnostics(
        [], critical_fact_refs=[], composition_outcomes=[]
    )

    assert result.subjects_with_bundle == 0
    assert result.critical_facts_total == 0
    assert result.critical_facts_with_bundle_lineage == 0
    assert result.critical_fact_lineage_rate is None
    assert result.conflicting_fields == 0
    assert result.precedence_resolutions == 0
