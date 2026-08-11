"""Seam de extração adaptativa e rota textual RT06.

O módulo conhece apenas o documento adquirido e os alvos. Aquisição, autoridade
documental e consumidores ficam fora dele.
"""
from __future__ import annotations

import inspect
import json
import logging
import os
import time
from collections.abc import Callable
from datetime import date, datetime, timezone
from typing import Any

from radar.core.kg.evidence_resolver import resolve_quote
from radar.core.services import document_extractions
from radar.domain.adaptive_extraction import (
    ADAPTIVE_TEXT_PRODUCER_VERSION,
    FAMILY_FIELDS,
    FIELD_VALUE_TYPES,
    CounterpartValue,
    DocumentAsset,
    ExtractedClaim,
    ExtractionArtifact,
    ExtractionRoute,
    ExtractionStatus,
    ExtractionTarget,
    FundingLimits,
    MonetaryRange,
    RouteTrace,
    SubmissionWindow,
    TableReference,
    TextUnit,
    extraction_fingerprint,
)
from radar.domain.data_quality import DataQualityException, IssueCode
from radar.domain.edital_extraction import Extracted, FieldState
from radar.domain.provenance import (
    FactProvenance,
    FactState,
    LocatorQuality,
    ProducerInfo,
    ProducerKind,
    ValidationResult,
)
from radar.domain.source_bundle import SubjectKind

logger = logging.getLogger(__name__)

TEXT_PRODUCER = "adaptive_textual_extractor"
TEXT_PRODUCER_VERSION = ADAPTIVE_TEXT_PRODUCER_VERSION
_TEXTUAL_TARGET_FIELDS = frozenset().union(*FAMILY_FIELDS.values())
_TARGET_VALUE_TYPES = FIELD_VALUE_TYPES
_LIST_EVIDENCE_FIELDS = frozenset({
    "requirements", "exclusions", "eligible_entities", "publico_alvo",
    "table_references",
})


def document_asset_from_blocks(
    *,
    subject_id: str,
    source: str,
    doc_name: str,
    blocks: list[dict],
    source_url: str | None = None,
    bundle_hash: str | None = None,
    asset_hash: str | None = None,
    document_role: str = "opportunity_page",
    authority_state: str = "current",
) -> DocumentAsset:
    """Adapta silver já adquirido ao contrato do seam, sem refetch."""
    units: list[TextUnit] = []
    for index, block in enumerate(blocks):
        units.append(TextUnit(
            text=str(block.get("text") or ""),
            unit_id=str(block.get("idx", index)),
            document=block.get("doc") or doc_name,
            page=block.get("page"),
            section_path=list(block.get("section_path") or []),
            block_idx=block.get("idx", index),
            document_metadata=block.get("document_metadata"),
            kind=block.get("kind"),
            table_structure_lost=block.get("table_structure_lost", False),
        ))
    return DocumentAsset(
        subject_id=subject_id,
        source=source,
        doc_name=doc_name,
        document_role=document_role,
        authority_state=authority_state,
        source_url=source_url,
        media_type="text/plain",
        text_units=units,
        asset_hash=asset_hash,
        bundle_hash=bundle_hash,
    )


def document_assets_from_blocks(
    *,
    subject_id: str,
    source: str,
    blocks: list[dict],
    source_url: str | None = None,
) -> list[DocumentAsset]:
    """Divide silver por documento antes de executar a extração.

    A função não compõe documentos.  Quando RT04 anexou metadata aos blocos,
    seus hashes e papéis são copiados para cada asset; sem metadata, o hash
    canônico local continua identificando apenas aquele documento.
    """
    grouped: dict[str, list[dict]] = {}
    for block in blocks:
        grouped.setdefault(str(block.get("doc") or "document"), []).append(block)
    assets: list[DocumentAsset] = []
    for doc_name, doc_blocks in grouped.items():
        metadata = doc_blocks[0].get("document_metadata") or {}
        explicit_hash = metadata.get("content_hash")
        if not isinstance(explicit_hash, str) or not explicit_hash.startswith("sha256:"):
            explicit_hash = None
        assets.append(document_asset_from_blocks(
            subject_id=subject_id,
            source=source,
            doc_name=doc_name,
            blocks=doc_blocks,
            source_url=source_url or metadata.get("source_url"),
            bundle_hash=metadata.get("bundle_hash"),
            asset_hash=explicit_hash,
            document_role=str(metadata.get("role") or "opportunity_page"),
            authority_state=str(metadata.get("authority_state") or "current"),
        ))
    return assets


def _blocks(document: DocumentAsset) -> list[dict]:
    return [
        {
            "idx": unit.block_idx if unit.block_idx is not None else index,
            "doc": unit.document or document.doc_name,
            "page": unit.page,
            "section_path": list(unit.section_path),
            "text": unit.text,
        }
        | ({"document_metadata": unit.model_extra["document_metadata"]}
           if unit.model_extra and unit.model_extra.get("document_metadata") else {})
        for index, unit in enumerate(document.text_units)
    ]


def _quote_is_section_title_only(quote: str, document: DocumentAsset) -> bool:
    """Reject a quote that contains no proposition beyond a section title.

    This is intentionally structural: it compares the literal quote with the
    titles already present in ``section_path`` and does not attempt semantic
    classification.
    """
    candidate = quote.strip()
    return bool(candidate) and any(
        candidate == title.strip()
        for unit in document.text_units
        for title in unit.section_path
        if title and title.strip()
    )


def _raw_value(value: Any) -> tuple[Any, Any, FactState]:
    if isinstance(value, Extracted):
        state = {
            FieldState.STATED: FactState.STATED,
            FieldState.INFERRED: FactState.INFERRED,
            FieldState.ABSENT: FactState.ABSENT,
        }[value.state]
        return value.value, value.evidence, state
    if isinstance(value, dict) and "state" in value:
        state = FactState(str(value.get("state")))
        return value.get("value", value), value.get("evidence"), state
    if isinstance(value, list):
        states: list[FactState] = []
        quotes: list[str] = []
        for item in value:
            item_value, item_quote, item_state = _raw_value(item)
            del item_value
            states.append(item_state)
            if item_quote:
                quotes.append(item_quote)
        if any(item_state is FactState.CONFLICTING for item_state in states):
            state = FactState.CONFLICTING
        elif any(item_state is FactState.UNKNOWN for item_state in states):
            state = FactState.UNKNOWN
        elif any(item_state is FactState.INFERRED for item_state in states):
            state = FactState.INFERRED
        elif states and all(item_state is FactState.ABSENT for item_state in states):
            state = FactState.ABSENT
        elif states and all(item_state is FactState.STATED for item_state in states):
            state = FactState.STATED
        else:
            # Mixed stated/absent coverage cannot be promoted as a collection.
            state = FactState.UNKNOWN
        return value, quotes if len(quotes) > 1 else (quotes[0] if quotes else None), state
    if hasattr(value, "model_dump"):
        data = value.model_dump(mode="json")
        return _raw_value(data)
    if value is None or value == [] or value == "":
        return None, None, FactState.ABSENT
    return value, None, FactState.STATED


def _field_value(extraction: Any, field_path: str) -> tuple[Any, Any, FactState]:
    aliases = {
        # These are intentionally exact.  Raw context fields from the legacy
        # EditalExtraction are not claims: they lack state/evidence and must
        # not silently become coverage in the first family.
        "eligible_entities": ("eligible_entities",),
        "publico_alvo": ("publico_alvo",),
        "eligibility_constraints": ("constraints",),
        "requirements": ("requirements",),
        "exclusions": ("exclusions",),
    }
    keys = aliases.get(field_path, (field_path,))
    for key in keys:
        if isinstance(extraction, dict):
            if key in extraction:
                return _raw_value(extraction[key])
        elif hasattr(extraction, key):
            return _raw_value(getattr(extraction, key))
    # An omitted producer key is not evidence that the document lacks the
    # fact. Only an explicit state=absent may resolve a target as absent.
    return None, None, FactState.UNKNOWN


def _compact_whitespace(value: str) -> str:
    return " ".join(value.split())


def _evidence_quotes(field_path: str, value: Any, evidence: Any) -> list[str]:
    """Return literal quotes that substantiate every item of a list claim."""
    if field_path not in _LIST_EVIDENCE_FIELDS:
        return [evidence] if isinstance(evidence, str) and evidence.strip() else []
    if not isinstance(value, list):
        return []
    if isinstance(evidence, str):
        quotes = [evidence]
    elif isinstance(evidence, list) and all(
        isinstance(item, str) and item.strip() for item in evidence
    ):
        quotes = evidence
    else:
        return []
    if not quotes:
        return []
    normalized_quotes = [_compact_whitespace(quote) for quote in quotes]
    for item in value:
        item_text = (
            item.get("title") or item.get("caption")
            if field_path == "table_references" and isinstance(item, dict)
            else item
        )
        if not isinstance(item_text, str) or not item_text.strip():
            return []
        normalized_item = _compact_whitespace(item_text)
        if not any(normalized_item in quote for quote in normalized_quotes):
            return []
    return list(dict.fromkeys(quote.strip() for quote in quotes))


def _raw_evidence_quotes(evidence: Any) -> list[str]:
    """Normalize producer evidence before the structural title guard."""
    if isinstance(evidence, str) and evidence.strip():
        return [evidence]
    if isinstance(evidence, list) and all(
        isinstance(item, str) and item.strip() for item in evidence
    ):
        return list(dict.fromkeys(item.strip() for item in evidence))
    return []


def _clean_value(value: Any) -> Any:
    if isinstance(value, list):
        return [_clean_value(item) for item in value]
    if isinstance(value, tuple):
        return [_clean_value(item) for item in value]
    if hasattr(value, "model_dump"):
        return _clean_value(value.model_dump(mode="json"))
    if isinstance(value, dict):
        return {str(k): _clean_value(v) for k, v in value.items() if k not in {"evidence", "state"}}
    return value


def _constraint_contract() -> tuple[set[str], set[str], dict[str, set[str]], dict[str, set[str]]]:
    """Carrega o contrato de constraints sem fallback executável.

    `constraint_enums` é o subconjunto que o gold avalia.  O bloco v3 fornece
    enums fechados para os tipos categóricos.  Se o documento autoritativo não
    estiver disponível ou estiver incompleto, a família falha fechada.
    """
    from radar.core.kg import schema

    # Keep the legacy subset accessor as a schema availability check; it is
    # not used as the family vocabulary.
    schema.constraint_tipos()
    ops = set(schema.constraint_ops())
    vocab = schema.constraint_vocab_v3()
    types = set(vocab.get("tipos", [])) if isinstance(vocab, dict) else set()
    values = vocab.get("valores") if isinstance(vocab, dict) else None
    if not types or not ops or not isinstance(vocab, dict) or not isinstance(values, dict):
        raise ValueError("authoritative constraint schema is unavailable or incomplete")
    closed = {
        str(key): {str(item).strip().lower() for item in raw}
        for key, raw in values.items()
        if isinstance(raw, list) and raw
    }
    raw_ops_by_type = schema.constraint_ops_by_type()
    if set(raw_ops_by_type) != types:
        raise ValueError("authoritative constraint ops_by_type is incomplete")
    return types, ops, closed, {key: set(value) for key, value in raw_ops_by_type.items()}


def _normalize_constraints(value: Any) -> tuple[Any, str]:
    if not isinstance(value, list):
        return None, "constraints must be a list"
    types, ops, closed, ops_by_type = _constraint_contract()
    normalized: list[dict[str, Any]] = []
    for item in value:
        # Exact shape prevents the legacy {type, description} objects from
        # leaking into entities.constraints.
        if not isinstance(item, dict) or set(item) != {"tipo", "op", "valor"}:
            return None, "constraint must have exactly {tipo, op, valor}"
        tipo, op, valor = item["tipo"], item["op"], item["valor"]
        if tipo not in types or op not in ops or op not in ops_by_type.get(tipo, set()):
            return None, "constraint outside the authoritative vocabulary"
        if valor in (None, "", []):
            return None, "constraint value is empty"
        if tipo in {"porte", "forma_juridica", "sede_uf"}:
            vals = valor if isinstance(valor, list) else [valor]
            if not vals or any(not isinstance(val, str) or not val.strip() for val in vals):
                return None, "constraint enum is empty"
            normalized_vals = [val.strip().lower() for val in vals]
            enum = closed.get(tipo)
            if not enum or any(val not in enum for val in normalized_vals):
                return None, "constraint enum value is outside the schema"
            if tipo == "sede_uf":
                normalized_vals = [val.upper() for val in normalized_vals]
            valor = normalized_vals
        elif tipo in {"faturamento", "idade_empresa_meses", "trl"}:
            values = valor if isinstance(valor, list) else [valor]
            if tipo != "trl" and len(values) != 1:
                return None, "constraint numeric value is invalid"
            numbers: list[int | float] = []
            for raw_number in values:
                if isinstance(raw_number, bool) or not isinstance(raw_number, (int, float)):
                    return None, "constraint numeric value is invalid"
                number = float(raw_number)
                if not number.is_integer() and tipo in {"idade_empresa_meses", "trl"}:
                    return None, "constraint numeric value must be an integer"
                normalized_number = int(number) if number.is_integer() else number
                if tipo == "idade_empresa_meses" and normalized_number < 0:
                    return None, "company age is negative"
                if tipo == "trl" and not 1 <= normalized_number <= 9:
                    return None, "trl is outside 1..9"
                numbers.append(normalized_number)
            valor = numbers if isinstance(valor, list) else numbers[0]
        elif tipo == "cnae":
            vals = valor if isinstance(valor, list) else [valor]
            if not vals or any(not isinstance(item, str) or not item.strip() for item in vals):
                return None, "cnae value is invalid"
            valor = [item.strip() for item in vals]
        elif tipo in {"parceria", "vinculo_incubacao", "investidor_privado"}:
            if not isinstance(valor, str):
                return None, "constraint actor is invalid"
            valor = valor.strip().lower()
            if not valor:
                return None, "constraint actor is empty"
        normalized.append({"tipo": tipo, "op": op, "valor": valor})
    return normalized, "shape"


def _normalize_target_value(field_path: str, value: Any) -> tuple[Any, str]:
    if value is None:
        return None, "absent"
    if field_path == "eligibility_constraints":
        return _normalize_constraints(value)
    if field_path in {"requirements", "exclusions", "eligible_entities", "publico_alvo"}:
        if not isinstance(value, list) or any(
            not isinstance(entry, str) or not entry.strip() for entry in value
        ):
            return None, "expected a non-empty string list"
        return [entry.strip() for entry in value], "shape"
    if field_path == "deadline":
        if not isinstance(value, str):
            return None, "deadline must be an ISO date"
        try:
            return date.fromisoformat(value).isoformat(), "shape"
        except ValueError:
            return None, "deadline must be an ISO date"
    if field_path == "submission_window":
        try:
            return SubmissionWindow.model_validate(value).model_dump(mode="json"), "shape"
        except Exception:  # noqa: BLE001 — typed claim validation fails closed
            return None, "invalid submission window"
    if field_path == "continuous_flow":
        if not isinstance(value, bool):
            return None, "continuous_flow must be boolean"
        return value, "shape"
    if field_path == "funding_amount":
        if not _strict_money_shape(value, {"min", "max"}):
            return None, "invalid funding amount"
        try:
            parsed = MonetaryRange.model_validate(value)
        except Exception:  # noqa: BLE001 — typed claim validation fails closed
            return None, "invalid funding amount"
        if parsed.min is None and parsed.max is None:
            return None, "funding amount has no numeric bound"
        return parsed.model_dump(mode="json"), "shape"
    if field_path == "funding_limits":
        if not _strict_money_shape(value, {"min", "max", "per_project"}):
            return None, "invalid funding limits"
        try:
            parsed = FundingLimits.model_validate(value)
        except Exception:  # noqa: BLE001 — typed claim validation fails closed
            return None, "invalid funding limits"
        if parsed.min is None and parsed.max is None and parsed.per_project is None:
            return None, "funding limits have no numeric bound"
        return parsed.model_dump(mode="json"), "shape"
    if field_path == "counterpart":
        if not _strict_counterpart_shape(value):
            return None, "invalid counterpart"
        try:
            return CounterpartValue.model_validate(value).model_dump(mode="json"), "shape"
        except Exception:  # noqa: BLE001 — typed claim validation fails closed
            return None, "invalid counterpart"
    if field_path == "table_references":
        if not isinstance(value, list) or not value:
            return None, "table references must be a non-empty list"
        if not _strict_table_reference_shape(value):
            return None, "invalid table reference"
        try:
            parsed = [TableReference.model_validate(item) for item in value]
        except Exception:  # noqa: BLE001 — typed claim validation fails closed
            return None, "invalid table reference"
        return [item.model_dump(mode="json") for item in parsed], "shape"
    return None, "unsupported textual target"


def _strict_money_shape(value: Any, numeric_fields: set[str]) -> bool:
    if not isinstance(value, dict) or not isinstance(value.get("currency"), str):
        return False
    return all(
        field not in value
        or (
            isinstance(value[field], (int, float))
            and not isinstance(value[field], bool)
        )
        for field in numeric_fields
    )


def _strict_counterpart_shape(value: Any) -> bool:
    if not isinstance(value, dict) or not isinstance(value.get("required"), bool):
        return False
    return (
        ("percentage" not in value or value["percentage"] is None or (
            isinstance(value["percentage"], (int, float))
            and not isinstance(value["percentage"], bool)
        ))
        and ("base" not in value or value["base"] is None or isinstance(value["base"], str))
    )


def _strict_table_reference_shape(value: Any) -> bool:
    if not isinstance(value, list):
        return False
    for item in value:
        if not isinstance(item, dict):
            return False
        for field in ("document", "purpose"):
            if not isinstance(item.get(field), str):
                return False
        for field in ("title", "caption", "section"):
            if field in item and item[field] is not None and not isinstance(item[field], str):
                return False
        if "page" in item and item["page"] is not None and (
            isinstance(item["page"], bool) or not isinstance(item["page"], int)
        ):
            return False
    return True


def _validate_family(field_path: str, value: Any) -> tuple[bool, str]:
    try:
        _, reason = _normalize_target_value(field_path, value)
    except Exception:  # noqa: BLE001 — typed target schema fails closed
        return False, "typed target schema unavailable"
    return reason == "shape", reason


def _text_batches(document: DocumentAsset) -> list[list[TextUnit]]:
    """Entrega todo o documento em uma única passagem do produtor textual."""
    units = [unit for unit in document.text_units if unit.text]
    return [units] if units else [[]]


def _canonicalize_text_response(
    data: Any,
    requested: list[str],
) -> dict[str, dict[str, Any]]:
    if not isinstance(data, dict) or not isinstance(data.get("claims"), dict):
        raise ValueError("canonical textual producer returned an invalid claims envelope")
    raw_claims = data["claims"]
    normalized: dict[str, dict[str, Any]] = {}
    for field in requested:
        item = raw_claims.get(field)
        if item is None:
            normalized[field] = {"value": None, "state": "unknown", "evidence": None}
            continue
        if not isinstance(item, dict) or set(item) != {"value", "state", "evidence"}:
            normalized[field] = {"value": None, "state": "unknown", "evidence": None}
            continue
        state = str(item["state"])
        evidence = item["evidence"]
        if state not in {
            FactState.STATED.value,
            FactState.ABSENT.value,
            FactState.UNKNOWN.value,
            FactState.INFERRED.value,
        }:
            state = FactState.UNKNOWN.value
        evidence_valid = (
            isinstance(evidence, str) and bool(evidence.strip())
        ) or (
            field in _LIST_EVIDENCE_FIELDS
            and isinstance(evidence, list)
            and all(isinstance(item, str) and item.strip() for item in evidence)
        )
        if evidence is not None and not evidence_valid:
            state, evidence = FactState.UNKNOWN.value, None
        value = item["value"]
        if state == FactState.ABSENT.value:
            if value is not None:
                state = FactState.UNKNOWN.value
            value, evidence = None, None
        elif state == FactState.STATED.value:
            if not evidence:
                state, value = FactState.UNKNOWN.value, None
            else:
                value, reason = _normalize_target_value(field, value)
                if reason != "shape":
                    state, value, evidence = FactState.UNKNOWN.value, None, None
        elif state == FactState.UNKNOWN.value:
            value = None
        elif value is not None:
            value, reason = _normalize_target_value(field, value)
            if reason != "shape":
                value = None
        normalized[field] = {"value": value, "state": state, "evidence": evidence}
    return normalized


def _request_text_batch(
    document: DocumentAsset,
    requested: list[str],
    units: list[TextUnit],
    *,
    client: Any,
    model: str | None,
    ops_by_type: dict[str, set[str]],
    target_specs: dict[str, str],
) -> dict[str, dict[str, Any]]:
    raw_text = "\n".join(unit.text for unit in units if unit.text)
    prompt = {
        "subject_id": document.subject_id,
        "document": document.doc_name,
        "targets": requested,
        "target_specs": target_specs,
        "field_semantics": {
            "continuous_flow": (
                "true apenas para declaração de submissão contínua, permanente ou "
                "a qualquer momento; false apenas para declaração explícita de que "
                "o fluxo/submissão não é contínuo. Prazo, janela, duração do projeto, "
                "duração da execução, parcelas ou limites não autorizam false. "
                "A frase 'A FAPESP receberá propostas associadas a propostas do "
                "Horizon Europe a qualquer momento.' é true. A frase 'A duração máxima "
                "de cada projeto será de 2 anos.' é unknown."
            ),
            "funding_amount": (
                "somente recursos disponibilizados pela oportunidade ou orçamento "
                "total de apoio; unknown para preço de produto/pacote, custo estimado, "
                "valor de mercado, contrapartida e qualquer valor por proposta/projeto. "
                "'O valor solicitado à Finep/FNDCT em cada proposta deverá ... entre "
                "o mínimo e o máximo' é funding_limits, não funding_amount. 'Preço alvo "
                "do pacote tecnológico: R$ 80.000,00' é unknown."
            ),
            "funding_limits": (
                "piso, teto ou limite de uma única proposta/projeto/faixa compatível. "
                "Não combinar limite global com limite por projeto nem escopos distintos; "
                "sem um único escopo compatível, unknown."
            ),
            "list_evidence": (
                "Para requirements, exclusions, eligible_entities e publico_alvo, "
                "evidence deve ser uma lista de quotes literais (uma ou mais), e cada "
                "item do value deve aparecer literalmente em pelo menos uma quote. "
                "Não resuma nem invente itens que não aparecem nas quotes. Título, "
                "cabeçalho ou frase introdutória isolada não sustenta a lista."
            ),
            "requirements": (
                "Inclua somente exigências ao proponente para elegibilidade, participação, "
                "submissão ou contratação. A exigência deve ser uma regra do proponente, "
                "não uma exigência técnica da proposta ou do desafio. Não extraia de "
                "anexo de especificações técnicas itens sobre implementos, potência, "
                "motor, peso, tração, dimensões ou outras características da solução, "
                "produto ou equipamento."
            ),
            "table_references": (
                "Inclua somente tabela, quadro ou estrutura tabular que sustente regra, "
                "prazo ou valor. Lista de anexos, índice ou sumário não é table_references."
            ),
        },
        "constraint_ops_by_type": {
            tipo: sorted(ops) for tipo, ops in ops_by_type.items()
        },
        "text": raw_text,
        "blocks": [
            {
                "document": unit.document or document.doc_name,
                "page": unit.page,
                "section": list(unit.section_path),
                "block_idx": unit.block_idx,
                "text": unit.text,
            }
            for unit in units
        ],
        "final_decision_check": {
            "continuous_flow": (
                "Só true com 'contínua', 'permanente' ou 'a qualquer momento'; só false "
                "com declaração explícita de fluxo/submissão não contínuo. Para prazo, "
                "janela, duração, parcelas ou limites, use unknown. Neste documento, "
                "se houver 'A FAPESP receberá propostas ... a qualquer momento.', use true."
            ),
            "funding_amount": (
                "Só recursos da oportunidade/orçamento total de apoio. Se o texto disser "
                "'em cada proposta', 'por projeto', preço de pacote ou preço alvo, use "
                "unknown em funding_amount e considere funding_limits apenas se houver "
                "um escopo único compatível."
            ),
            "funding_limits": (
                "Nunca combine escopos. Limite global + limite por projeto, ou múltiplos "
                "valores sem escopo único, é unknown."
            ),
            "evidence": "A quote precisa conter a proposição factual, não apenas título de seção.",
        },
    }
    response = client.chat.completions.create(
        model=model or os.getenv("OPENAI_MODEL_PRO", os.getenv("OPENAI_MODEL", "gpt-4o-mini")),
        messages=[
            {
                "role": "system",
                "content": (
                    "Extraia todos os fatos solicitados nesta passagem textual. "
                    "Retorne JSON no formato {claims: {campo: {value, state, evidence}}}. "
                    "Respeite exatamente o value_type de cada target: list[str] é uma "
                    "lista de strings; date é YYYY-MM-DD; submission_window é "
                    "{start: YYYY-MM-DD|null, end: YYYY-MM-DD|null}; bool é true/false; "
                    "monetary_range é {currency, min, max}; funding_limits é "
                    "{currency, min, max, per_project}; counterpart é "
                    "{required, percentage, base}; list[table_reference] é uma lista "
                    "de {document, title ou caption, page, section, purpose}. "
                    "States válidos: stated, inferred, unknown, absent. Para "
                    "requirements, exclusions, eligible_entities e publico_alvo, "
                    "evidence deve ser uma lista de quotes literais; cada item do valor "
                    "deve aparecer em pelo menos uma quote. Para valores escalares, "
                    "evidence é uma string literal. "
                    "Use stated somente para valor explícito com quote literal copiado "
                    "de um único bloco; o quote deve ser substring daquele bloco e "
                    "conter a proposição factual que sustenta o valor. Um título de "
                    "seção isolado, como 'Critérios de elegibilidade', não é evidência. "
                    "Use absent somente quando os blocos permitirem concluir que o "
                    "fato não consta; omissão é unknown. Não retorne conflicting: "
                    "conflitos entre documentos são resolvidos pela composição RT04. "
                    "Para continuous_flow, use true somente se o documento declarar "
                    "submissão contínua, permanente ou que propostas podem ser enviadas "
                    "a qualquer momento; use false somente se declarar explicitamente "
                    "que o fluxo não é contínuo. Prazo, janela fixa, duração do projeto "
                    "ou duração da execução, isoladamente, não autorizam false. Sem "
                    "declaração suficiente sobre o fluxo de submissão, use unknown. "
                    "Para funding_amount, extraia recursos disponibilizados pela "
                    "oportunidade ou orçamento total de apoio. Não trate preço de produto "
                    "ou pacote tecnológico, custo estimado, valor de mercado, contrapartida "
                    "ou limite individual por projeto como funding_amount. "
                    "Para funding_limits, extraia piso, teto ou limite por "
                    "proposta/projeto/faixa. Nunca combine valores de escopos diferentes "
                    "para fabricar min|max; se não houver um único escopo compatível, "
                    "retorne unknown. "
                    "Requirements incluem somente regras exigidas do proponente para "
                    "elegibilidade, participação, submissão ou contratação; não incluem "
                    "exigências técnicas da proposta/desafio nem conteúdo de anexo de "
                    "especificações técnicas (implementos, potência, motor, peso, "
                    "tração ou dimensões). Uma frase como 'Cada proposta deverá apresentar "
                    "os implementos agrícolas obrigatórios' continua sendo conteúdo técnico "
                    "do desafio e deve ser unknown. Table_references não incluem lista de anexos, índice ou "
                    "sumário; exigem tabela/quadro/estrutura tabular que sustente regra, "
                    "prazo ou valor. "
                    "Exemplos obrigatórios: 'A FAPESP receberá propostas associadas a "
                    "propostas do Horizon Europe a qualquer momento.' significa "
                    "continuous_flow=true; 'A duração máxima de cada projeto será de 2 "
                    "anos.' significa continuous_flow=unknown; 'O valor solicitado à "
                    "Finep/FNDCT em cada proposta deverá ... entre o mínimo e o máximo' "
                    "é funding_limits, não funding_amount; 'Preço alvo do pacote "
                    "tecnológico: R$ 80.000,00' é unknown para funding_amount. "
                    "Constraints usam objetos {tipo, op, valor}."
                ),
            },
            {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
        ],
        temperature=0,
        response_format={"type": "json_object"},
    )
    content = response.choices[0].message.content
    data = json.loads(content) if isinstance(content, str) else content
    return _canonicalize_text_response(data, requested)


def _consolidate_text_claims(
    responses: list[dict[str, dict[str, Any]]],
    requested: list[str],
) -> dict[str, dict[str, Any]]:
    consolidated: dict[str, dict[str, Any]] = {}
    for field in requested:
        items = [response[field] for response in responses]
        stated = [item for item in items if item["state"] == FactState.STATED.value]
        if stated:
            first = stated[0]
            if any(
                json.dumps(item["value"], sort_keys=True, ensure_ascii=False)
                != json.dumps(first["value"], sort_keys=True, ensure_ascii=False)
                for item in stated[1:]
            ):
                consolidated[field] = {"value": None, "state": "unknown", "evidence": None}
            else:
                consolidated[field] = dict(first)
        elif items and all(item["state"] == FactState.ABSENT.value for item in items):
            consolidated[field] = {"value": None, "state": "absent", "evidence": None}
        elif any(item["state"] == FactState.UNKNOWN.value for item in items):
            consolidated[field] = {"value": None, "state": "unknown", "evidence": None}
        elif any(item["state"] == FactState.INFERRED.value for item in items):
            inferred = [
                item for item in items
                if item["state"] == FactState.INFERRED.value
            ]
            first = inferred[0]
            if all(
                json.dumps(item["value"], sort_keys=True, ensure_ascii=False)
                == json.dumps(first["value"], sort_keys=True, ensure_ascii=False)
                for item in inferred[1:]
            ):
                # Keep a typed candidate visible to human review.  The state
                # remains inferred, so downstream gates must still ignore it.
                consolidated[field] = dict(first)
            else:
                consolidated[field] = {"value": None, "state": "unknown", "evidence": None}
        else:
            consolidated[field] = {"value": None, "state": "unknown", "evidence": None}
    return consolidated


def _text_model() -> str:
    backend = os.getenv("LLM_BACKEND", "openai").lower()
    if backend == "gemini":
        return os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
    if backend == "ollama":
        return os.getenv("OLLAMA_MODEL", "llama3.2")
    return os.getenv("OPENAI_MODEL_PRO", "gpt-4o-mini")


def _make_text_client() -> tuple[Any, str]:
    from radar.core.llm.llm_client import make_client

    backend = os.getenv("LLM_BACKEND", "openai").lower()
    if backend == "gemini":
        api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        if not api_key:
            raise ValueError("GEMINI_API_KEY não definida")
        return make_client(
            api_key=api_key,
            max_retries=6,
            base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        ), _text_model()
    if backend == "ollama":
        return make_client(
            api_key="ollama",
            max_retries=6,
            base_url="http://localhost:11434/v1",
        ), _text_model()
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("OPENAI_API_KEY não definida")
    return make_client(api_key=api_key, max_retries=6), _text_model()


def extract_initial_family(
    document: DocumentAsset,
    targets: list[ExtractionTarget],
    *,
    client: Any = None,
    model: str | None = None,
) -> dict[str, dict[str, Any]]:
    """Produz a saída textual canônica da família inicial.

    Esta rota tem contrato próprio e não reutiliza ``EditalExtraction``. A
    resposta é normalizada para um claim por alvo, inclusive quando o texto
    não contém cobertura (``absent``), sem transformar ausência em valor.
    """
    if client is None:
        client, configured_model = _make_text_client()
        model = model or configured_model
    requested = [target.field_path for target in targets]
    if any(field not in _TEXTUAL_TARGET_FIELDS for field in requested):
        raise ValueError("textual producer received an unsupported target")
    ops_by_type: dict[str, set[str]] = {}
    if "eligibility_constraints" in requested:
        _, _, _, ops_by_type = _constraint_contract()
    target_specs = {target.field_path: target.value_type for target in targets}
    responses = [
        _request_text_batch(
            document,
            requested,
            units,
            client=client,
            model=model,
            ops_by_type=ops_by_type,
            target_specs=target_specs,
        )
        for units in _text_batches(document)
    ]
    return _consolidate_text_claims(responses, requested)


def _producer() -> ProducerInfo:
    return ProducerInfo(
        kind=ProducerKind.LLM,
        name=TEXT_PRODUCER,
        version=TEXT_PRODUCER_VERSION,
        model=_text_model(),
        prompt_version="adaptive_textual_extraction:v9",
    )


class AdaptiveDocumentExtraction:
    """Implementação inicial: texto nativo, com seam para rotas futuras."""

    def __init__(
        self,
        *,
        repository: Any = None,
        text_extractor: Callable[..., Any] | None = None,
        llm_client: Any = None,
        exception_sink: Callable[[DataQualityException], bool] | None = None,
        producer_versions: dict[str, str] | None = None,
    ) -> None:
        self._repository_injected = repository is not None
        self.repository = repository or document_extractions
        self.text_extractor = text_extractor
        self.llm_client = llm_client
        self.exception_sink = exception_sink
        self.producer_versions = producer_versions or {
            "adaptive_text": TEXT_PRODUCER_VERSION,
            "edital_extraction_schema": "v3",
        }

    def extract(
        self,
        document: DocumentAsset,
        targets: list[ExtractionTarget],
    ) -> ExtractionArtifact:
        if not self._repository_injected and not document_extractions.is_configured():
            raise document_extractions.ExtractionStorageError(
                "durable persistence is required before textual extraction"
            )
        fingerprint = extraction_fingerprint(
            document, targets, producer_versions=self.producer_versions,
        )
        cached = self.repository.load(fingerprint)
        if cached is not None and cached.status in {
            ExtractionStatus.COMPLETE, ExtractionStatus.PARTIAL,
        }:
            return cached

        started = time.perf_counter()
        if not document.text_units:
            artifact = self._artifact(
                document, targets, fingerprint, claims=[], unresolved=[t.field_path for t in targets],
                traces=[RouteTrace(
                    route=ExtractionRoute.TEXT, reason="texto nativo indisponível",
                    targets_before=[t.field_path for t in targets], duration_ms=0,
                    status="skipped",
                )], status=ExtractionStatus.UNAVAILABLE,
            )
            return self._persist(artifact)

        try:
            extraction = self._run_text(document, targets)
            claims, unresolved = self._claims(document, targets, extraction)
            status = ExtractionStatus.COMPLETE if not unresolved else ExtractionStatus.PARTIAL
            trace_status = "complete" if status is ExtractionStatus.COMPLETE else "partial"
        except Exception as exc:  # noqa: BLE001 — um documento não derruba o lote
            logger.warning(
                "adaptive_extraction: subject=%s category=%s",
                document.subject_id, type(exc).__name__,
            )
            claims = []
            unresolved = [target.field_path for target in targets]
            status = ExtractionStatus.FAILED
            trace_status = "failed"
            self._observe_failure(document, targets, IssueCode.CRITICAL_FACT_MISSING)

        trace = RouteTrace(
            route=ExtractionRoute.TEXT,
            reason="rota inicial; parada por suficiência dos alvos decision",
            pages_or_units=[unit.unit_id or index for index, unit in enumerate(document.text_units)],
            targets_before=[target.field_path for target in targets],
            targets_resolved=[
                claim.field_path for claim in claims
                if claim.provenance.state in {FactState.STATED, FactState.ABSENT}
            ],
            duration_ms=max(0, int((time.perf_counter() - started) * 1000)),
            status=trace_status,
        )
        artifact = self._artifact(
            document, targets, fingerprint, claims=claims, unresolved=unresolved,
            traces=[trace], status=status,
        )
        return self._persist(artifact)

    def _run_text(self, document: DocumentAsset, targets: list[ExtractionTarget]) -> Any:
        raw = "\n".join(unit.text for unit in document.text_units if unit.text)
        extractor = self.text_extractor
        if extractor is None:
            del raw
            return extract_initial_family(document, targets, client=self.llm_client)
        positional = [
            parameter for parameter in inspect.signature(extractor).parameters.values()
            if parameter.kind in (parameter.POSITIONAL_ONLY, parameter.POSITIONAL_OR_KEYWORD)
        ]
        if len(positional) >= 3:
            return extractor(document.source, document.subject_id, raw)
        return extractor(document, targets)

    def _claims(
        self,
        document: DocumentAsset,
        targets: list[ExtractionTarget],
        extraction: Any,
    ) -> tuple[list[ExtractedClaim], list[str]]:
        claims: list[ExtractedClaim] = []
        unresolved: list[str] = []
        blocks = _blocks(document)
        for target in targets:
            refs = []
            validations = []
            try:
                value, quote, state = _field_value(extraction, target.field_path)
                clean = _clean_value(value)
            except (TypeError, ValueError):
                value, quote, clean, state = None, None, None, FactState.UNKNOWN
                validations.append(ValidationResult(
                    name="state_schema", status="failed",
                ))
                self._observe_failure(document, [target], IssueCode.VALIDATION_FAILED)
            schema_valid = _TARGET_VALUE_TYPES.get(target.field_path) == target.value_type
            if not schema_valid:
                state = FactState.UNKNOWN
                clean = None
                validations.append(ValidationResult(
                    name="field_schema", status="failed",
                ))
                self._observe_failure(document, [target], IssueCode.VALIDATION_FAILED)
            elif state is FactState.CONFLICTING:
                clean = None
                validations.append(ValidationResult(
                    name="document_conflict", status="failed",
                ))
                self._observe_failure(document, [target], IssueCode.FACT_CONFLICT)
            if schema_valid and state in {FactState.STATED, FactState.INFERRED}:
                raw_evidence_quotes = _raw_evidence_quotes(quote)
                evidence_quotes = _evidence_quotes(target.field_path, clean, quote)
                quote_is_title = any(
                    _quote_is_section_title_only(evidence_quote, document)
                    for evidence_quote in raw_evidence_quotes
                )
                if quote_is_title:
                    state = FactState.UNKNOWN
                    clean = None
                    validations.append(ValidationResult(
                        name="evidence_substantive", status="failed",
                    ))
                    self._observe_failure(document, [target], IssueCode.EVIDENCE_UNRESOLVED)
                table_structure_lost = (
                    target.field_path == "table_references"
                    and any(
                        bool(unit.model_extra.get("table_structure_lost"))
                        for unit in document.text_units
                        if unit.model_extra
                    )
                )
                if quote_is_title:
                    pass
                elif table_structure_lost:
                    state = FactState.UNKNOWN
                    validations.append(ValidationResult(
                        name="table_structure", status="failed",
                    ))
                    self._observe_failure(document, [target], IssueCode.VALIDATION_FAILED)
                elif not evidence_quotes:
                    state = FactState.UNKNOWN
                    clean = None
                    validations.append(ValidationResult(
                        name="list_item_evidence" if target.field_path in _LIST_EVIDENCE_FIELDS else "quote_resolved",
                        status="failed",
                    ))
                    self._observe_failure(document, [target], IssueCode.EVIDENCE_UNRESOLVED)
                else:
                    unresolved_quote = False
                    for evidence_quote in evidence_quotes:
                        try:
                            resolved = resolve_quote(
                                evidence_quote, blocks, source=document.source,
                                native_id=document.subject_id, edital_id=document.subject_id,
                                source_url=document.source_url, silver_source_hash=document.asset_hash,
                            )
                            if resolved and resolved.evidence_ref and resolved.evidence_ref.locator_quality in {
                                LocatorQuality.EXACT, LocatorQuality.DOCUMENT_ONLY,
                            }:
                                if not any(
                                    ref.model_dump(mode="json") == resolved.evidence_ref.model_dump(mode="json")
                                    for ref in refs
                                ):
                                    refs.append(resolved.evidence_ref)
                            else:
                                unresolved_quote = True
                        except Exception:  # noqa: BLE001 — evidence failure is a claim gap
                            unresolved_quote = True
                    if unresolved_quote or not refs:
                        if state is FactState.STATED:
                            state = FactState.UNKNOWN
                            clean = None
                        validations.append(ValidationResult(name="quote_resolved", status="failed"))
                        self._observe_failure(document, [target], IssueCode.EVIDENCE_UNRESOLVED)
                    else:
                        validations.append(ValidationResult(name="quote_resolved", status="passed"))
            if state is FactState.ABSENT:
                clean = None
                valid = True
            else:
                try:
                    clean, validation_name = _normalize_target_value(target.field_path, clean)
                    valid = validation_name in {"shape", "absent"}
                except Exception:  # noqa: BLE001 — typed target validation fails closed
                    clean, validation_name, valid = None, "typed target schema unavailable", False
            validations.append(ValidationResult(
                name="field_shape", status="passed" if valid else "failed",
            ))
            if not valid:
                state = FactState.UNKNOWN
                clean = None
                self._observe_failure(document, [target], IssueCode.VALIDATION_FAILED)
            elif state is FactState.UNKNOWN:
                # Unknown is a gap, not a typed candidate.  Inferred values
                # remain available for exploration, but unknown values must
                # never be mistaken for a publishable claim.
                clean = None
            if state in {
                FactState.UNKNOWN,
                FactState.INFERRED,
                FactState.CONFLICTING,
            }:
                unresolved.append(target.field_path)
            provenance = FactProvenance(
                state=state,
                evidence_refs=refs,
                producer=_producer(),
                validations=validations,
                updated_at=datetime.now(timezone.utc),
            )
            claims.append(ExtractedClaim(
                subject_id=document.subject_id,
                field_path=target.field_path,
                value=clean,
                provenance=provenance,
            ))
        return claims, unresolved

    def _artifact(
        self,
        document: DocumentAsset,
        targets: list[ExtractionTarget],
        fingerprint: str,
        *,
        claims: list[ExtractedClaim],
        unresolved: list[str],
        traces: list[RouteTrace],
        status: ExtractionStatus,
    ) -> ExtractionArtifact:
        return ExtractionArtifact(
            subject_id=document.subject_id,
            document=document.doc_name,
            document_role=document.document_role,
            asset_hash=document.asset_hash or "",
            bundle_hash=document.bundle_hash,
            targets_requested=targets,
            claims=claims,
            unresolved_targets=sorted(set(unresolved)),
            # O texto completo permanece somente no silver/documento adquirido;
            # o artifact guarda coordenadas sanitizadas e claims com quotes.
            structured_blocks=[
                unit.model_dump(mode="json", exclude_none=True, exclude={"text"})
                for unit in document.text_units
            ],
            route_trace=traces,
            status=status,
            producer_versions=self.producer_versions,
            fingerprint=fingerprint,
            created_at=datetime.now(timezone.utc),
        )

    def _persist(self, artifact: ExtractionArtifact) -> ExtractionArtifact:
        try:
            saved = self.repository.save(artifact)
            if not saved:
                raise document_extractions.ExtractionStorageError(
                    "artifact was not durably persisted"
                )
            load_attempt = getattr(self.repository, "load_attempt", None)
            if callable(load_attempt):
                own_attempt = load_attempt(artifact.fingerprint, artifact.attempt_id)
            else:
                candidate = self.repository.load(artifact.fingerprint)
                own_attempt = (
                    candidate
                    if candidate is not None and candidate.attempt_id == artifact.attempt_id
                    else None
                )
            if own_attempt is not None:
                return own_attempt

            # A healthy attempt may lose a race on the partial unique index.
            # In that case the durable healthy artifact is the canonical
            # result, not a persistence failure for the losing worker.
            if artifact.status in {
                ExtractionStatus.COMPLETE,
                ExtractionStatus.PARTIAL,
            }:
                canonical = self.repository.load(artifact.fingerprint)
                if canonical is not None and canonical.status in {
                    ExtractionStatus.COMPLETE,
                    ExtractionStatus.PARTIAL,
                }:
                    return canonical
            raise document_extractions.ExtractionStorageError(
                "artifact persistence could not be confirmed"
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "adaptive_extraction: persistence category=%s subject=%s",
                type(exc).__name__, artifact.subject_id,
            )
            raise

    def _observe_failure(
        self,
        document: DocumentAsset,
        targets: list[ExtractionTarget],
        issue_code: IssueCode,
    ) -> None:
        for target in targets:
            exception = DataQualityException(
                subject_kind=SubjectKind.OPPORTUNITY,
                subject_id=document.subject_id,
                field_path=target.field_path,
                issue_code=issue_code,
                produced_state=FactState.UNKNOWN,
                bundle_hash=document.bundle_hash,
                producer_version=TEXT_PRODUCER_VERSION,
                input_fingerprint=extraction_fingerprint(
                    document, [target], producer_versions=self.producer_versions,
                ),
            )
            try:
                if self.exception_sink is not None:
                    self.exception_sink(exception)
                else:
                    from radar.core.services.data_quality_exceptions import (
                        open_or_observe_exception,
                    )

                    open_or_observe_exception(exception)
            except Exception as exc:  # noqa: BLE001 — exceção é best effort
                logger.warning(
                    "adaptive_extraction: exception sink category=%s subject=%s",
                    type(exc).__name__, document.subject_id,
                )


__all__ = [
    "AdaptiveDocumentExtraction",
    "document_asset_from_blocks",
    "document_assets_from_blocks",
]
