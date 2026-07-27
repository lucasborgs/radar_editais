"""Materializa evidência aprovada no contrato já aceito pela fonte `web`."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from radar.core.config import BRONZE_DIR
from radar.core.kg import source_bundles
from radar.core.kg.source_bundles import BundleStorageError
from radar.core.web_identity import normalize_web_url, web_url_hash
from radar.domain.source_bundle import (
    AcquisitionStatus,
    AuthorityState,
    DocumentRole,
    SourceBundle,
    SubjectKind,
    compute_content_hash,
)
from radar.pipeline.adapters.base import CanonicalDoc, split_into_units

logger = logging.getLogger(__name__)


def _loaded_text(document: dict) -> str:
    if document.get("status") != "loaded":
        return ""
    return str(document.get("text") or "").strip()


def _canonical_document(document: dict, *, role: DocumentRole, authority: AuthorityState) -> dict:
    text = _loaded_text(document)
    units = split_into_units(text)
    return {
        "doc_name": document.get("label") or document.get("doc_name") or role.value,
        "units": units,
        "role": role.value,
        "source_url": document.get("url") or document.get("source_url"),
        "content_hash": compute_content_hash(units),
        "authority_state": authority.value,
    }


def _bundle_from_evidence(opportunity: dict, evidence: dict) -> SourceBundle | None:
    """Constrói o bundle Web sem buscar ou inferir documentos ausentes."""
    page = evidence.get("page") or {}
    page_text = _loaded_text(page)
    identity = evidence.get("identity") or {}
    canonical_url = normalize_web_url(
        identity.get("canonical_url") or evidence.get("canonical_url") or opportunity["url"],
    )
    documents: list[dict] = []
    if page_text:
        documents.append(_canonical_document(
            {**page, "text": page_text, "url": canonical_url,
             "label": opportunity.get("title") or "pagina-do-desafio"},
            role=DocumentRole.OPPORTUNITY_PAGE,
            authority=AuthorityState.ACTIVE,
        ))
    for related_page in evidence.get("related_pages") or []:
        if _loaded_text(related_page):
            documents.append(_canonical_document(
                related_page,
                role=DocumentRole.PROGRAM_PAGE,
                authority=AuthorityState.CONTEXTUAL,
            ))
    if not documents:
        return None
    collected_at_raw = identity.get("collected_at")
    try:
        collected_at = datetime.fromisoformat(str(collected_at_raw).replace("Z", "+00:00"))
        if collected_at.tzinfo is None:
            collected_at = collected_at.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        collected_at = datetime.now(timezone.utc)
    return SourceBundle.model_validate({
        "subject_kind": SubjectKind.OPPORTUNITY.value,
        "subject_id": f"web:{web_url_hash(canonical_url)}",
        "source": "web",
        "collected_at": collected_at,
        "producer_version": "discovery-evidence-v1",
        "acquisition_status": (
            AcquisitionStatus.COMPLETE.value if page_text
            else AcquisitionStatus.PARTIAL.value
        ),
        "documents": documents,
    })


def canonical_documents_from_evidence(opportunity: dict, evidence: dict) -> CanonicalDoc:
    """Gera o Documento Canônico com a página e PDFs já aprovados.

    O adapter web continua sendo o fallback a partir do bronze. Quando houver
    banco configurado, salvar este documento em ``edital_source_docs`` faz com
    que ambos os jobs nativos usem exatamente os mesmos artefatos aprovados.
    """
    page = evidence.get("page") or {}
    page_text = (page.get("text") or evidence.get("texto_cru") or opportunity.get("descricao") or "").strip()
    docs: CanonicalDoc = []
    if page_text:
        docs.append({
            "doc_name": opportunity.get("title") or "pagina-oficial",
            "units": split_into_units(page_text),
        })
    for index, document in enumerate(evidence.get("documents") or [], start=1):
        if document.get("status") != "loaded" or not (document.get("text") or "").strip():
            continue
        docs.append({
            "doc_name": document.get("label") or f"documento-{index}",
            "units": split_into_units(document["text"]),
        })
    for index, related_page in enumerate(evidence.get("related_pages") or [], start=1):
        related_text = _loaded_text(related_page)
        if related_text:
            docs.append({
                "doc_name": related_page.get("label") or f"pagina-relacionada-{index}",
                "units": split_into_units(related_text),
            })
    return docs


def materialize_approved_evidence(opportunity: dict, evidence: dict | None = None) -> str:
    """Grava bronze web compatível e retorna o `edital_id` nativo.

    Não chama crawler, adapter, gold ou RAG; a promoção enfileira os jobs já
    existentes usando o identificador retornado.
    """
    evidence = evidence or opportunity.get("raw") or {}
    page = evidence.get("page") or {}
    identity = evidence.get("identity") or {}
    url = normalize_web_url(identity.get("canonical_url") or evidence.get("canonical_url") or opportunity["url"])
    url_hash = web_url_hash(url)
    entry = {
        "url": url, "url_hash": url_hash,
        "title": opportunity.get("title") or evidence.get("title") or "Descoberta promovida",
        "html": page.get("html") or "",
        "texto_cru": page.get("text") or evidence.get("texto_cru") or opportunity.get("descricao") or "",
        "descricao": opportunity.get("descricao") or "", "agency": opportunity.get("agency") or "",
        "fonte": opportunity.get("fonte") or "Web (promoção)", "status": "ABERTA",
        "tema": opportunity.get("tema") or "", "opportunity_type": opportunity.get("opportunity_type") or "edital",
        "prazo_envio": opportunity.get("prazo_envio") or "", "publico_alvo": opportunity.get("publico_alvo") or "",
        "verificacao": "promovido", "data_extracao": datetime.now(timezone.utc).date().isoformat(),
        "source_document_refs": [
            {key: document.get(key) for key in ("url", "label", "kind", "status", "pages", "bytes")}
            for document in evidence.get("documents") or []
        ],
    }
    target = BRONZE_DIR / "web_raw"
    target.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    (target / f"web_promoted_{stamp}.json").write_text(json.dumps([entry], ensure_ascii=False), encoding="utf-8")
    edital_id = f"web:{url_hash}"
    # Persistência durável é best-effort e não muda o contrato do adapter web:
    # sem Supabase, o job continua lendo o bronze local como já fazia.
    from radar.core.kg import source_docs
    source_docs.save(edital_id, "web", canonical_documents_from_evidence(opportunity, evidence))
    try:
        bundle = _bundle_from_evidence(opportunity, evidence)
        if bundle is not None:
            source_bundles.save(bundle)
    except BundleStorageError as exc:
        logger.warning(
            "source_bundles: falha best-effort para %s: %s",
            edital_id, exc,
        )
    return edital_id
