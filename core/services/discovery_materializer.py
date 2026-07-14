"""Materializa evidência aprovada no contrato já aceito pela fonte `web`."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from config import BRONZE_DIR
from core.web_identity import normalize_web_url, web_url_hash
from pipeline.adapters.base import CanonicalDoc, split_into_units


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
    from core.kg import source_docs
    source_docs.save(edital_id, "web", canonical_documents_from_evidence(opportunity, evidence))
    return edital_id
