"""Executa a validação local T07-A com o produtor textual real.

O script é deliberadamente um runner de validação, não uma nova suíte de eval:
reutiliza ``AdaptiveDocumentExtraction`` e deixa a aprovação humana fora do
processo. Os artifacts duráveis são escritos no Supabase local configurado pelo
ambiente chamador; o pacote de revisão contém claims/evidências, nunca prompts
ou respostas brutas do provedor.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from radar.core.eval.extraction import SUITE as EXTRACTION_SUITE
from radar.core.ingestion.adaptive_extraction import (
    AdaptiveDocumentExtraction,
    document_asset_from_blocks,
)
from radar.core.kg import source_bundles
from radar.core.llm.llm_client import make_client
from radar.core.services import document_extractions
from radar.domain.adaptive_extraction import (
    ADAPTIVE_EXTRACTION_SCHEMA_VERSION,
    ADAPTIVE_PRODUCER_SCHEMA,
    ADAPTIVE_TEXT_PRODUCER_VERSION,
    FAMILY_FIELDS,
    FIELD_VALUE_TYPES,
    ExtractionArtifact,
    ExtractionStatus,
    ExtractionTarget,
    extraction_fingerprint,
)
from radar.domain.provenance import FactState
from radar.domain.source_bundle import (
    AcquisitionStatus,
    AuthorityState,
    DocumentRole,
    SourceBundle,
    SubjectKind,
    compute_content_hash,
)

ROOT = Path(__file__).resolve().parents[1]
SILVER = ROOT / "data" / "silver" / "structured_docs"
OUT = (
    ROOT
    / "docs"
    / "execution"
    / "radar-data-trust"
    / "reports"
    / "06-adaptive-extraction"
    / "artifacts"
    / "t07-a-v2"
)

PRODUCER_VERSIONS = {
    "adaptive_text": ADAPTIVE_TEXT_PRODUCER_VERSION,
    "edital_extraction_schema": ADAPTIVE_PRODUCER_SCHEMA,
}

TARGETS = [
    (
        field,
        FIELD_VALUE_TYPES[field],
        "writing" if family == "table_evidence" else "eligibility",
        "advisory" if family in {"financial", "table_evidence"} else "decision",
    )
    for family, fields in FAMILY_FIELDS.items()
    for field in sorted(fields)
]

CASES = [
    {
        "subject_id": "finep:602",
        "source": "finep",
        "legacy_status": "active (legacy: aberta)",
        "reason": "PDF textual; prazo explícito e contrapartida mencionada.",
    },
    {
        "subject_id": "finep:769",
        "source": "finep",
        "legacy_status": "active (legacy: aberta)",
        "reason": "Oportunidade aberta com múltiplos PDFs, rerratificações, valores, contrapartida e referências de tabela.",
    },
    {
        "subject_id": "fapesp:16466",
        "source": "fapesp",
        "legacy_status": "needs_review (legacy: status não confirmado)",
        "reason": "Página HTML adquirida; submissão a qualquer momento, mas prazo/continuidade não são presumidos para o campo temporal.",
    },
    {
        "subject_id": "web:3b554a9fcafc",
        "source": "web",
        "legacy_status": "needs_review (staging web sem status produtivo)",
        "reason": "Documento HTML adquirido com declaração literal de fluxo contínuo; não é candidato a promoção nesta execução.",
    },
]


def _targets() -> list[ExtractionTarget]:
    return [
        ExtractionTarget(
            field_path=field,
            value_type=value_type,
            required_for=required_for,
            criticality=criticality,
        )
        for field, value_type, required_for, criticality in TARGETS
    ]


def _safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("_")[:180]


def _load_documents(case: dict[str, str]) -> list[tuple[str, list[dict[str, Any]]]]:
    source, native_id = case["subject_id"].split(":", 1)
    path = SILVER / source / f"{native_id}.jsonl"
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            block = json.loads(line)
            grouped[str(block.get("doc") or "document")].append(block)
    return sorted(grouped.items())


def _document_role(doc_name: str, source: str) -> DocumentRole:
    lowered = doc_name.lower()
    if "anexo" in lowered:
        return DocumentRole.ANNEX
    if "rerrat" in lowered or "retif" in lowered:
        return DocumentRole.AMENDMENT
    if source in {"fapesp", "web"} or "pagina" in lowered:
        return DocumentRole.OPPORTUNITY_PAGE
    return DocumentRole.BASE_NOTICE


def _build_source_bundle(
    case: dict[str, str],
    documents: list[tuple[str, list[dict[str, Any]]]],
) -> tuple[SourceBundle, dict[str, str]]:
    metadata: list[dict[str, Any]] = []
    content_hashes: dict[str, str] = {}
    for order, (doc_name, blocks) in enumerate(documents):
        units = [str(block.get("text") or "") for block in blocks if str(block.get("text") or "").strip()]
        if not units:
            raise ValueError(f"documento sem texto para SourceBundle: {doc_name}")
        content_hash = compute_content_hash(units)
        content_hashes[doc_name] = content_hash
        document_metadata = blocks[0].get("document_metadata") or {}
        revision = document_metadata.get("revision")
        composition_order = revision if isinstance(revision, int) else order
        metadata.append({
            "doc_name": doc_name,
            "units": units,
            "role": _document_role(doc_name, case["source"]).value,
            "content_hash": content_hash,
            "authority_state": AuthorityState.ACTIVE.value,
            "composition_order": composition_order,
        })
    bundle = SourceBundle.model_validate({
        "subject_kind": SubjectKind.OPPORTUNITY.value,
        "subject_id": case["subject_id"],
        "source": case["source"],
        "collected_at": datetime.now(timezone.utc),
        "producer_version": "t07-a-v2-silver-bundle",
        "acquisition_status": AcquisitionStatus.COMPLETE.value,
        "documents": metadata,
    })
    return bundle, content_hashes


class _CountingClient:
    """Proxy sanitizado: conta chamadas/tokens sem guardar payloads."""

    def __init__(self, client: Any) -> None:
        self._client = client
        self.calls = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self.response_states: Counter[str] = Counter()
        self.response_non_null_claims = 0
        self.invalid_response_envelopes = 0
        owner = self

        class _Completions:
            def __init__(self) -> None:
                self.create = owner._create

        class _Chat:
            def __init__(self) -> None:
                self.completions = _Completions()

        self.chat = _Chat()

    def _create(self, **kwargs: Any) -> Any:
        self.calls += 1
        response = self._client.chat.completions.create(**kwargs)
        usage = getattr(response, "usage", None)
        self.input_tokens += int(getattr(usage, "prompt_tokens", 0) or 0)
        self.output_tokens += int(getattr(usage, "completion_tokens", 0) or 0)
        content = getattr(getattr(response, "choices", [None])[0], "message", None)
        content = getattr(content, "content", None)
        try:
            payload = json.loads(content) if isinstance(content, str) else content
            claims = payload.get("claims") if isinstance(payload, dict) else None
            if not isinstance(claims, dict):
                self.invalid_response_envelopes += 1
            else:
                for item in claims.values():
                    if not isinstance(item, dict):
                        continue
                    state = item.get("state")
                    if state:
                        self.response_states[str(state)] += 1
                    if item.get("value") is not None:
                        self.response_non_null_claims += 1
        except (TypeError, ValueError, IndexError, AttributeError):
            self.invalid_response_envelopes += 1
        return response


def _claim_json(claim: Any) -> dict[str, Any]:
    return claim.model_dump(mode="json")


def _family(field: str) -> str:
    for family, fields in FAMILY_FIELDS.items():
        if field in fields:
            return family
    raise ValueError(f"unsupported adaptive field: {field}")


def _resolved_evidence(claim: Any) -> list[Any]:
    return [
        ref
        for ref in getattr(getattr(claim, "provenance", None), "evidence_refs", []) or []
        if (
            ref.locator_quality.value in {"exact", "document_only"}
            and (ref.canonical_content_hash or ref.silver_source_hash)
        )
    ]


def _gate_row(
    *,
    subject_id: str,
    document: str,
    family: str,
    target: ExtractionTarget,
    artifact: ExtractionArtifact,
    claim: Any | None,
    divergence: Any = None,
) -> dict[str, Any]:
    """Registra somente diagnóstico factual do shadow, sem decisão de promoção."""
    state = claim.provenance.state if claim is not None else FactState.UNKNOWN
    state_value = state.value
    evidence_refs = _resolved_evidence(claim) if claim is not None else []
    evidence_literal = (
        [ref.quote for ref in evidence_refs]
        if len(evidence_refs) > 1
        else evidence_refs[0].quote if evidence_refs else None
    )
    schema_valid = (
        artifact.schema_version == ADAPTIVE_EXTRACTION_SCHEMA_VERSION
        and FIELD_VALUE_TYPES.get(target.field_path) == target.value_type
    )
    material_conflict = state is FactState.CONFLICTING
    errors: list[str] = []
    if not schema_valid:
        errors.append("schema_invalid")
    if state is FactState.STATED and not evidence_refs:
        errors.append("evidence_unresolved")
    if claim is not None and any(
        validation.name == "evidence_substantive"
        and validation.status == "failed"
        for validation in getattr(claim.provenance, "validations", [])
    ):
        errors.append("evidence_not_substantive")
    if claim is not None:
        errors.extend(
            validation.name
            for validation in getattr(claim.provenance, "validations", [])
            if validation.status == "failed"
            and validation.name not in {"evidence_substantive", "quote_resolved"}
        )
    if material_conflict:
        errors.append("material_conflict")
    review_required = material_conflict or bool(errors)
    value = claim.value if claim is not None else None
    return {
        "subject": subject_id,
        "document": document,
        "family": family,
        "field": target.field_path,
        "value_new": value,
        "state": state_value,
        "schema_valid": schema_valid,
        "evidence_resolved": bool(evidence_refs),
        "evidence_literal": evidence_literal,
        "page": evidence_refs[0].page if evidence_refs else None,
        "section": evidence_refs[0].section_path if evidence_refs else [],
        "material_conflict": material_conflict,
        "error_codes": sorted(set(errors)),
        "review_required": review_required,
        "divergence": divergence,
    }


def _diagnostic_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Agrega estado, schema, evidência, conflito e revisão necessária."""
    states = Counter(row["state"] for row in rows)
    errors = Counter(
        error for row in rows for error in row.get("error_codes") or []
    )
    by_family: dict[str, dict[str, Any]] = {}
    for family in sorted({row["family"] for row in rows}):
        family_rows = [row for row in rows if row["family"] == family]
        by_family[family] = {
            "rows": len(family_rows),
            "states": dict(sorted(Counter(row["state"] for row in family_rows).items())),
            "schema_valid": sum(bool(row["schema_valid"]) for row in family_rows),
            "schema_invalid": sum(not row["schema_valid"] for row in family_rows),
            "evidence_resolved": sum(bool(row["evidence_literal"]) for row in family_rows),
            "evidence_unresolved": sum(not bool(row["evidence_literal"]) for row in family_rows),
            "material_conflicts": sum(bool(row["material_conflict"]) for row in family_rows),
            "review_required": sum(bool(row["review_required"]) for row in family_rows),
        }
    return {
        "rows": len(rows),
        "states": dict(sorted(states.items())),
        "schema_valid": sum(bool(row["schema_valid"]) for row in rows),
        "schema_invalid": sum(not row["schema_valid"] for row in rows),
        "evidence_resolved": sum(bool(row["evidence_literal"]) for row in rows),
        "evidence_unresolved": sum(not bool(row["evidence_literal"]) for row in rows),
        "material_conflicts": sum(bool(row["material_conflict"]) for row in rows),
        "error_codes": dict(sorted(errors.items())),
        "review_required": sum(bool(row["review_required"]) for row in rows),
        "by_family": by_family,
    }


def _markdown_value(value: Any) -> str:
    if value is None:
        return "—"
    rendered = json.dumps(value, ensure_ascii=False, sort_keys=True)
    return rendered.replace("|", "\\|").replace("\n", " ")


def _write_human_review(rows: list[dict[str, Any]]) -> None:
    lines = [
        "# T07-A v2 — Revisão humana inicial",
        "**Status:** Aguardando revisão humana dos goldens",
        "**Escopo:** claims `stated`, conflitos materiais e erros detectados; `unknown` isolado não exige revisão humana.",
        "A revisão deve confirmar valor, estado, evidência literal e localização. `inferred` não é fato decisório.",
        f"**Total para revisão inicial:** {len(rows)} linhas. O diagnóstico factual completo permanece em `review_rows.jsonl`.",
        "",
    ]
    subjects = list(dict.fromkeys(row["subject"] for row in rows))
    for subject in subjects:
        lines.extend([
            f"## {subject}",
            "| Documento | Família | Campo | Valor novo | Estado | Schema | Evidência | Página | Seção | Conflito material | Erros | Decisão humana | Comentário humano |",
            "|---|---|---|---|---|---|---|---:|---|---|---|---|---|",
        ])
        for row in [item for item in rows if item["subject"] == subject]:
            lines.append("| " + " | ".join([
                _markdown_value(row["document"]),
                _markdown_value(row["family"]),
                _markdown_value(row["field"]),
                _markdown_value(row["value_new"]),
                _markdown_value(row["state"]),
                _markdown_value(row["schema_valid"]),
                _markdown_value(row["evidence_literal"]),
                _markdown_value(row["page"]),
                _markdown_value(row["section"]),
                _markdown_value(row["material_conflict"]),
                _markdown_value(row["error_codes"]),
                "pending",
                "",
            ]) + " |")
        lines.append("")
    lines.extend([
        "## Exemplos de revisão",
        "",
        "Veja [review_examples.json](review_examples.json) para exemplos de valor correto, ausência legítima, `unknown`, possível fabricação, divergência, retificação e tabela. Todos continuam pendentes.",
        "",
    ])
    (OUT / "T07-A-human-review.md").write_text("\n".join(lines), encoding="utf-8")


def _example_from_row(row: dict[str, Any], example_type: str) -> dict[str, Any]:
    """Render a candidate example without turning it into a golden."""
    return {
        "example_type": example_type,
        "subject": row["subject"],
        "document": row["document"],
        "family": row["family"],
        "field": row["field"],
        "candidate_value": row["value_new"],
        "state": row["state"],
        "schema_valid": row["schema_valid"],
        "evidence_resolved": row["evidence_resolved"],
        "material_conflict": row["material_conflict"],
        "error_codes": row["error_codes"],
        "evidence_literal": row["evidence_literal"],
        "page": row["page"],
        "human_decision": "pending",
        "human_comment": "",
    }


def _write_review_examples(rows: list[dict[str, Any]]) -> None:
    """Write a fixed, compact review vocabulary for initial calibration."""
    examples: list[dict[str, Any]] = []

    def first(predicate: Any) -> dict[str, Any] | None:
        return next((row for row in rows if predicate(row)), None)

    choices = [
        ("valor_correto", lambda row: row["state"] == "stated" and row["schema_valid"] and row["evidence_resolved"]),
        ("ausencia_legitima", lambda row: row["state"] == "absent"),
        ("unknown_conservador", lambda row: row["state"] == "unknown"),
        ("possivel_fabricacao", lambda row: row["state"] == "inferred"),
        ("divergencia_entre_documentos", lambda row: row["material_conflict"] and bool(row.get("divergence"))),
        ("retificacao", lambda row: "rerrat" in row["document"].lower() or "retif" in row["document"].lower()),
        ("tabela_compreensivel", lambda row: row["field"] == "table_references"),
    ]
    for example_type, predicate in choices:
        row = first(predicate)
        if row is not None:
            examples.append(_example_from_row(row, example_type))

    lost = first(lambda row: row["field"] == "table_references" and "table_structure" in row.get("error_codes", []))
    examples.append({
        "example_type": "estrutura_tabela_perdida",
        "subject": lost["subject"] if lost else "corpus:t07-a-v2",
        "document": lost["document"] if lost else "nenhum documento observado",
        "family": "table_evidence",
        "field": "table_references",
        "candidate_value": lost["value_new"] if lost else None,
        "state": lost["state"] if lost else "unknown",
        "schema_valid": lost["schema_valid"] if lost else True,
        "evidence_resolved": lost["evidence_resolved"] if lost else False,
        "material_conflict": lost["material_conflict"] if lost else False,
        "error_codes": lost["error_codes"] if lost else ["table_structure_lost"],
        "evidence_literal": lost["evidence_literal"] if lost else None,
        "page": lost["page"] if lost else None,
        "human_decision": "pending",
        "human_comment": "Confirmar se a estrutura da tabela foi preservada; não ativar OCR/layout/visão sem perda medida.",
    })
    (OUT / "review_examples.json").write_text(
        json.dumps({
            "package_version": "t07-a-v2",
            "approved_golden": False,
            "human_decision_default": "pending",
            "human_comment_default": "",
            "examples": examples,
        }, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _divergences(artifacts: list[ExtractionArtifact]) -> dict[str, list[dict[str, Any]]]:
    by_field: dict[str, list[tuple[str, Any]]] = defaultdict(list)
    for artifact in artifacts:
        for claim in artifact.claims:
            if claim.provenance.state.value == "stated":
                by_field[claim.field_path].append((artifact.document or "", claim.value))
    result: dict[str, list[dict[str, Any]]] = {}
    for field, values in by_field.items():
        distinct = {json.dumps(value, ensure_ascii=False, sort_keys=True) for _, value in values}
        if len(distinct) > 1:
            result[field] = [{"document": document, "value": value} for document, value in values]
    return result


def main() -> None:
    if os.getenv("RADAR_ADAPTIVE_EXTRACTION_SHADOW") != "1":
        raise SystemExit("RADAR_ADAPTIVE_EXTRACTION_SHADOW=1 é obrigatório")
    if not os.getenv("SUPABASE_URL", "").startswith("http://127.0.0.1:"):
        raise SystemExit("SUPABASE_URL não comprova Supabase local")
    if not os.getenv("SUPABASE_SERVICE_KEY"):
        raise SystemExit("SUPABASE_SERVICE_KEY ausente")

    OUT.mkdir(parents=True, exist_ok=True)
    artifacts_dir = OUT / "artifacts"
    if artifacts_dir.exists():
        shutil.rmtree(artifacts_dir)
    artifacts_dir.mkdir(exist_ok=True)
    for stale_name in ("gate_decisions.jsonl", "hold_queue.jsonl"):
        stale_path = OUT / stale_name
        if stale_path.exists():
            stale_path.unlink()
    requested_subjects = {
        value.strip()
        for value in os.getenv("T07A_SUBJECTS", "").split(",")
        if value.strip()
    }
    cases = [
        case for case in CASES
        if not requested_subjects or case["subject_id"] in requested_subjects
    ]
    if requested_subjects and len(cases) != len(requested_subjects):
        unknown = requested_subjects - {case["subject_id"] for case in cases}
        raise SystemExit(f"sujeitos T07-A desconhecidos: {sorted(unknown)}")
    client = _CountingClient(make_client())
    targets = _targets()
    started_at = datetime.now(timezone.utc)
    all_artifacts: list[ExtractionArtifact] = []
    rows: list[dict[str, Any]] = []
    corpus_manifest: list[dict[str, Any]] = []
    document_runs: list[dict[str, Any]] = []

    read_model_smokes: list[dict[str, Any]] = []
    for case in cases:
        documents = _load_documents(case)
        bundle, content_hashes = _build_source_bundle(case, documents)
        if source_bundles.save(bundle) is not True:
            raise SystemExit(f"SourceBundle não persistido localmente: {case['subject_id']}")
        loaded_bundle = source_bundles.load("opportunity", case["subject_id"])
        if loaded_bundle is None or loaded_bundle.compute_bundle_hash() != bundle.compute_bundle_hash():
            raise SystemExit(f"SourceBundle não carregado localmente: {case['subject_id']}")
        bundle_hash = loaded_bundle.compute_bundle_hash()
        case_artifacts: list[ExtractionArtifact] = []
        document_manifest: list[dict[str, Any]] = []
        for doc_name, blocks in documents:
            media_type = "text/html" if case["source"] in {"web", "fapesp"} else "application/pdf"
            document = document_asset_from_blocks(
                subject_id=case["subject_id"],
                source=case["source"],
                doc_name=doc_name,
                blocks=blocks,
                bundle_hash=bundle_hash,
                asset_hash=content_hashes[doc_name],
                document_role=_document_role(doc_name, case["source"]).value,
            ).model_copy(update={"media_type": media_type})
            fingerprint = extraction_fingerprint(
                document, targets, producer_versions=PRODUCER_VERSIONS,
            )
            cached = document_extractions.load(fingerprint)
            cache_hit = cached is not None and cached.status in {
                ExtractionStatus.COMPLETE, ExtractionStatus.PARTIAL,
            }
            before = client.calls
            before_input_tokens = client.input_tokens
            before_output_tokens = client.output_tokens
            started = time.perf_counter()
            artifact = AdaptiveDocumentExtraction(
                llm_client=client,
                producer_versions=PRODUCER_VERSIONS,
            ).extract(document, targets)
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            case_artifacts.append(artifact)
            all_artifacts.append(artifact)
            artifact_path = OUT / "artifacts" / _safe_name(case["subject_id"])
            artifact_path.mkdir(exist_ok=True)
            (artifact_path / f"{_safe_name(doc_name)}.json").write_text(
                json.dumps(artifact.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            claim_by_field = {claim.field_path: claim for claim in artifact.claims}
            case_rows: list[dict[str, Any]] = []
            for target in targets:
                case_rows.append(_gate_row(
                    subject_id=case["subject_id"],
                    document=doc_name,
                    family=_family(target.field_path),
                    target=target,
                    artifact=artifact,
                    claim=claim_by_field.get(target.field_path),
                ))
            rows.extend(case_rows)
            document_run = {
                "document": doc_name,
                "media_type": media_type,
                "asset_hash": document.asset_hash,
                "blocks": len(blocks),
                "table_structure_lost": any(bool(block.get("table_structure_lost")) for block in blocks),
                "status": artifact.status.value,
                "fingerprint": artifact.fingerprint,
                "attempt_id": artifact.attempt_id,
                "unresolved_targets": artifact.unresolved_targets,
                "resolved_evidence": sum(
                    1 for claim in artifact.claims
                    if any(
                        ref.locator_quality.value in {"exact", "document_only"}
                        and (ref.canonical_content_hash or ref.silver_source_hash)
                        for ref in claim.provenance.evidence_refs
                    )
                ),
                "calls": client.calls - before,
                "input_tokens": client.input_tokens - before_input_tokens,
                "output_tokens": client.output_tokens - before_output_tokens,
                "latency_ms": elapsed_ms,
                "cost": None,
                "cache_hit": cache_hit,
                "cache_status": "hit" if cache_hit else "miss",
            }
            document_manifest.append(document_run)
            document_runs.append({"subject": case["subject_id"], **document_run})
        raw_divergences = _divergences(case_artifacts)
        previous_active_families = os.environ.get("RADAR_ADAPTIVE_ACTIVE_FAMILIES")
        os.environ["RADAR_ADAPTIVE_ACTIVE_FAMILIES"] = ",".join(FAMILY_FIELDS)
        try:
            from radar.core.kg.adaptive_read_model import resolve

            family_smokes: dict[str, dict[str, Any]] = {}
            for family in FAMILY_FIELDS:
                projection = resolve(
                    case["subject_id"],
                    artifacts=case_artifacts,
                    bundle=loaded_bundle,
                    review_overrides={},
                    family=family,
                )
                family_smokes[family] = {
                    "source_state": projection.source_state,
                    "needs_review": projection.needs_review,
                    "gaps": projection.gaps,
                    "temporal_state": projection.temporal_state,
                    "artifact_fingerprint": projection.artifact_fingerprint,
                }
                for row in rows:
                    if row["subject"] != case["subject_id"] or row["family"] != family:
                        continue
                    claim = projection.claim(row["field"])
                    material_conflict = bool(
                        claim
                        and (claim.get("provenance") or {}).get("state") == FactState.CONFLICTING.value
                    )
                    row["material_conflict"] = material_conflict
                    row["review_required"] = row["review_required"] or material_conflict
                    if material_conflict:
                        row["error_codes"] = sorted(set(row["error_codes"] + ["material_conflict"]))
        finally:
            if previous_active_families is None:
                os.environ.pop("RADAR_ADAPTIVE_ACTIVE_FAMILIES", None)
            else:
                os.environ["RADAR_ADAPTIVE_ACTIVE_FAMILIES"] = previous_active_families
        read_model_smokes.extend({
            "subject_id": case["subject_id"],
            "source_bundle_persisted": True,
            "source_bundle_loaded": True,
            "source_bundle_hash": bundle_hash,
            "family": family,
            "bundle_gap_absent_with_bundle": "SourceBundle corrente indisponível" not in details["gaps"],
            **details,
        } for family, details in family_smokes.items())
        corpus_manifest.append({
            **case,
            "documents": document_manifest,
            "document_count": len(document_manifest),
            "raw_divergences_by_field": raw_divergences,
            "source_bundle": {
                "persisted": True,
                "loaded": True,
                "hash": bundle_hash,
            },
            "read_model_smoke": family_smokes,
        })

    diagnostic_metrics = _diagnostic_metrics(rows)
    (OUT / "review_rows.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )
    human_rows = [
        row for row in rows
        if row["state"] in {"stated", "inferred", "conflicting"}
        or row["error_codes"]
    ]
    _write_human_review(human_rows)
    _write_review_examples(rows)
    states = Counter(row["state"] for row in rows)
    family_coverage: dict[str, dict[str, Any]] = {}
    for family in sorted({_family(field) for field, *_ in TARGETS}):
        family_rows = [row for row in rows if row["family"] == family]
        family_coverage[family] = {
            "rows": len(family_rows),
            "fields": sorted({row["field"] for row in family_rows}),
            "states": dict(sorted(Counter(row["state"] for row in family_rows).items())),
            "evidence_resolved": sum(bool(row["evidence_resolved"]) for row in family_rows),
            "evidence_unresolved": sum(not bool(row["evidence_resolved"]) for row in family_rows),
        }

    llm_usage = {
        "calls": client.calls,
        "input_tokens": client.input_tokens,
        "output_tokens": client.output_tokens,
        "response_states": dict(sorted(client.response_states.items())),
        "response_non_null_claims": client.response_non_null_claims,
        "invalid_response_envelopes": client.invalid_response_envelopes,
        "cost": None,
        "cost_note": "custo monetário não calculado: tabela de preço não é parte do harness; chamadas e tokens foram registrados.",
    }
    naive_field_calls = len(document_runs) * len(targets)
    operational_metrics = {
        "documents": len(document_runs),
        "artifacts_by_status": dict(sorted(Counter(run["status"] for run in document_runs).items())),
        "reused_artifacts": sum(bool(run["cache_hit"]) for run in document_runs),
        "cache_misses": sum(not bool(run["cache_hit"]) for run in document_runs),
        "failed": sum(run["status"] == ExtractionStatus.FAILED.value for run in document_runs),
        "partial": sum(run["status"] == ExtractionStatus.PARTIAL.value for run in document_runs),
        "unavailable": sum(run["status"] == ExtractionStatus.UNAVAILABLE.value for run in document_runs),
        "calls": client.calls,
        "input_tokens": client.input_tokens,
        "output_tokens": client.output_tokens,
        "latency_ms_total": sum(run["latency_ms"] for run in document_runs),
        "per_document": [
            {
                "subject": run["subject"],
                "document": run["document"],
                "calls": run["calls"],
                "input_tokens": run["input_tokens"],
                "output_tokens": run["output_tokens"],
                "latency_ms": run["latency_ms"],
                "cost": run["cost"],
            }
            for run in document_runs
        ],
        "estimated_naive_field_calls": naive_field_calls,
        "calls_avoided": max(0, naive_field_calls - client.calls),
        "cost": None,
        "cost_note": "não calculado; o pacote registra chamadas e tokens, sem tabela de preços.",
        "consumer_consistency": {
            "status": "not_applicable_in_shadow",
            "reason": "promoção é proibida nesta execução; não há projeção efetiva para exercitar",
            "common_projection_contract": "validated_by_rt06_t07_v2_ticket_04",
            "consumers": ["KG", "Knowledge", "consultoria", "Writing"],
        },
    }
    corpus_payload = json.dumps(corpus_manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    manifest = {
        "package_version": "t07-a-v2",
        "corpus_hash": "sha256:" + hashlib.sha256(corpus_payload).hexdigest(),
        "corpus": corpus_manifest,
        "schema_version": ADAPTIVE_EXTRACTION_SCHEMA_VERSION,
        "producer_versions": PRODUCER_VERSIONS,
        "targets": [target.model_dump(mode="json") for target in targets],
        "runtime": {
            "model": os.getenv("OPENAI_MODEL_PRO", os.getenv("OPENAI_MODEL", "gpt-4o-mini (code default)")),
            "ocr": False,
            "vision": False,
            "deep_research": False,
            "web_discovery": False,
        },
        "evaluation": {
            "harness": "src/radar/core/eval",
            "suite": EXTRACTION_SUITE.name,
            "suite_version": EXTRACTION_SUITE.version,
            "evaluator_names": [getattr(evaluator, "__name__", "unknown") for evaluator in EXTRACTION_SUITE.evaluators],
            "mode": "diagnostic-only; evaluators held until human goldens exist",
            "reuse_status": "existing suite contract referenced; no parallel evaluator registered",
            "golden_status": "not_approved; human review pending",
            "legacy_comparison": "removed",
        },
        "artifacts": {
            "raw_prompts": False,
            "raw_responses": False,
            "credentials": False,
        },
    }
    (OUT / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    summary = {
        "package_version": "t07-a-v2",
        "started_at": started_at.isoformat(),
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "shadow": {"enabled": True, "flag": "RADAR_ADAPTIVE_EXTRACTION_SHADOW"},
        "runtime": {
            "producer": "adaptive_textual_extractor",
            "producer_version": PRODUCER_VERSIONS["adaptive_text"],
            "model": os.getenv("OPENAI_MODEL_PRO", os.getenv("OPENAI_MODEL", "gpt-4o-mini (code default)")),
            "provider_base_url_configured": bool(os.getenv("LLM_BASE_URL")),
            "ocr": False,
            "vision": False,
            "deep_research": False,
            "web_discovery": False,
        },
        "corpus": corpus_manifest,
        "coverage_by_family": family_coverage,
        "states_total": dict(sorted(states.items())),
        "evidence": {
            "resolved": sum(bool(row["evidence_literal"]) for row in rows),
            "unresolved": sum(not bool(row["evidence_literal"]) for row in rows),
        },
        "diagnostics": diagnostic_metrics,
        "operational_metrics": operational_metrics,
        "evaluation": manifest["evaluation"],
        "llm_usage": llm_usage,
        "read_model_smoke": read_model_smokes,
        "human_review": {
            "status": "Aguardando revisão humana dos goldens",
            "decision_default": "pending",
            "comment_default": "",
            "approval_prohibited": True,
        },
        "authority": {
            "promotion": "not performed",
            "legacy_removal": "not performed",
            "rt04_composition": "not changed",
            "rt05_projection": "not changed",
            "channel": "pending",
            "temporal_read_model": "continues to calculate active|closed|needs_review; extraction supplies facts only",
            "table_evidence": "structured evidence for Knowledge/Writing; not necessarily new gold columns",
        },
    }
    (OUT / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({
        "package": str(OUT.relative_to(ROOT)),
        "documents": len(all_artifacts),
        "artifacts": len(all_artifacts),
        "calls": client.calls,
        "states": dict(sorted(states.items())),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
