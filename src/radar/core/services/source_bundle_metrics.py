"""Métricas diagnósticas puras de pacotes documentais (RT04-T07).

Este módulo não lê o banco nem decide precedência. Ele apenas resume bundles,
referências factuais e resultados de composição que já foram produzidos pelos
contratos RT04-T01/T06. As entradas são injetadas para que a mesma leitura
possa rodar sobre fixtures ou, futuramente, sobre uma consulta explícita.
"""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from radar.domain.provenance import FactProvenance, FactState
from radar.domain.source_bundle import DocumentRole, SourceBundle, SubjectKind

_ACTOR_KINDS = frozenset({
    SubjectKind.INVESTOR,
    SubjectKind.ICT,
    SubjectKind.PROGRAM,
    SubjectKind.AGENCY,
})
_OFFICIAL_ACTOR_ROLES = frozenset({
    DocumentRole.OFFICIAL_PAGE,
    DocumentRole.OFFICIAL_RECORD,
})


@dataclass(frozen=True)
class CompositionOutcome:
    """Resultado já conhecido de uma resolução de campo.

    ``precedence_applied`` é declarado pelo produtor da composição. A métrica
    não tenta deduzi-lo de datas, nomes ou da quantidade de evidências.
    """

    state: FactState
    precedence_applied: bool = False


@dataclass(frozen=True)
class SourceBundleDiagnostics:
    """Resumo observável, sem threshold e sem efeito operacional."""

    subjects_with_bundle: int
    subjects_with_current_complete_bundle: int
    versions_by_subject: dict[str, int]
    documents_by_role: dict[str, int]
    critical_facts_total: int | None
    critical_facts_with_bundle_lineage: int | None
    critical_fact_lineage_rate: float | None
    conflicting_fields: int | None
    precedence_resolutions: int | None
    actors_without_official_content: tuple[str, ...] = field(default_factory=tuple)
    actors_without_complete_bundle: tuple[str, ...] = field(default_factory=tuple)


def compute_source_bundle_diagnostics(
    bundles: Iterable[SourceBundle],
    *,
    critical_facts: Iterable[FactProvenance] | None = None,
    composition_outcomes: Iterable[CompositionOutcome] | None = None,
) -> SourceBundleDiagnostics:
    """Deriva o baseline RT04 a partir de entradas já recuperadas.

    A ausência de ``critical_facts`` ou ``composition_outcomes`` significa
    denominador não observado e retorna ``None`` nos respectivos campos; uma
    lista vazia é um denominador observado de zero.
    """

    bundle_list = list(bundles)
    grouped: dict[str, list[SourceBundle]] = {}
    documents_by_role: dict[str, int] = {}
    for bundle in bundle_list:
        grouped.setdefault(bundle.subject_id, []).append(bundle)
        for document in bundle.documents:
            role = document.role.value
            documents_by_role[role] = documents_by_role.get(role, 0) + 1

    versions_by_subject = {
        subject_id: len(subject_bundles)
        for subject_id, subject_bundles in sorted(grouped.items())
    }
    current_complete = sum(
        any(bundle.acquisition_status.value == "complete" for bundle in subject_bundles)
        for subject_bundles in grouped.values()
    )

    actors_without_official: list[str] = []
    actors_without_complete: list[str] = []
    for subject_id, subject_bundles in grouped.items():
        if not any(bundle.subject_kind in _ACTOR_KINDS for bundle in subject_bundles):
            continue
        documents = [document for bundle in subject_bundles for document in bundle.documents]
        if not any(document.role in _OFFICIAL_ACTOR_ROLES for document in documents):
            actors_without_official.append(subject_id)
        if not any(bundle.acquisition_status.value == "complete" for bundle in subject_bundles):
            actors_without_complete.append(subject_id)

    total_facts: int | None = None
    linked_facts: int | None = None
    lineage_rate: float | None = None
    if critical_facts is not None:
        facts = list(critical_facts)
        total_facts = len(facts)
        linked_facts = sum(
            any(
                ref.bundle_hash is not None and ref.content_hash is not None
                for ref in fact.evidence_refs
            )
            for fact in facts
        )
        lineage_rate = linked_facts / total_facts if total_facts else None

    conflicts: int | None = None
    precedences: int | None = None
    if composition_outcomes is not None:
        outcomes = list(composition_outcomes)
        conflicts = sum(outcome.state is FactState.CONFLICTING for outcome in outcomes)
        precedences = sum(outcome.precedence_applied for outcome in outcomes)

    return SourceBundleDiagnostics(
        subjects_with_bundle=len(grouped),
        subjects_with_current_complete_bundle=current_complete,
        versions_by_subject=versions_by_subject,
        documents_by_role=dict(sorted(documents_by_role.items())),
        critical_facts_total=total_facts,
        critical_facts_with_bundle_lineage=linked_facts,
        critical_fact_lineage_rate=lineage_rate,
        conflicting_fields=conflicts,
        precedence_resolutions=precedences,
        actors_without_official_content=tuple(sorted(actors_without_official)),
        actors_without_complete_bundle=tuple(sorted(actors_without_complete)),
    )
