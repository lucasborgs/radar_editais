"""
FAPESC Source Adapter — L1 (docs/domain/schema.md §12, docs/domain/sources/fapesc.md).

O texto autoritativo do edital FAPESC vive num PDF anexo (não no HTML do post —
ver docs/domain/sources/fapesc.md §8). O scraper (pipeline/extractors/fapesc.py) já baixa esse
PDF e extrai o texto inline, gravando-o em `texto_cru`. Este adapter lê o bronze,
encontra a chamada pelo `native_id` (gravado pelo scraper) e retorna 1 entrada de
Documento Canônico (§12.3) com o corpo fatiado em units — agnóstico de onde o
texto veio (PDF ou, em fallback, o resumo HTML).

Estratégia §12.4: `pdf` (extração no scraper). O adapter só fatia `texto_cru`.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from radar.core.config import BRONZE_DIR
from radar.domain.source_bundle import (
    AcquisitionStatus,
    AuthorityState,
    DocumentRole,
    SourceBundle,
    SubjectKind,
    compute_content_hash,
)

from .base import CanonicalDoc, SourceAdapter, coletado_em, split_into_units

logger = logging.getLogger(__name__)

_BRONZE_DIR = BRONZE_DIR / "fapesc_raw"


def _load_latest_bronze() -> list[dict]:
    """Lê o JSON bronze FAPESC mais recente. [] se diretório/arquivos ausentes."""
    if not _BRONZE_DIR.exists():
        logger.warning("fapesc adapter: bronze dir ausente: %s", _BRONZE_DIR)
        return []
    files = sorted(_BRONZE_DIR.glob("*.json"))
    if not files:
        logger.warning("fapesc adapter: nenhum bronze em %s", _BRONZE_DIR)
        return []
    try:
        return json.loads(files[-1].read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("fapesc adapter: erro lendo %s: %s", files[-1].name, e)
        return []


def _find_bronze_record(bronze: list[dict], edital_id: str) -> dict | None:
    """Localiza a primeira ocorrência do edital, preservando a dedup do adapter."""
    seen: set[str] = set()
    for ch in bronze:
        url = (ch.get("url") or "").replace("http://", "https://").rstrip("/")
        if url and url in seen:
            continue
        if url:
            seen.add(url)
        if ch.get("native_id") == edital_id:
            return ch
    return None


def _collected_at(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        collected_at = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if collected_at.tzinfo is None:
        collected_at = collected_at.replace(tzinfo=timezone.utc)
    return collected_at.astimezone(timezone.utc)


def build_source_bundle(edital_id: str) -> SourceBundle | None:
    """Constrói somente o bundle normativo já presente no bronze FAPESC."""
    match = _find_bronze_record(_load_latest_bronze(), edital_id)
    if not match or not (match.get("documentos_normativos") or []):
        return None
    collected_at = _collected_at(match.get("data_extracao"))
    if collected_at is None:
        logger.warning("fapesc bundle: coleta ausente ou inválida para %s", edital_id)
        return None

    documents: list[dict] = []
    has_base = False
    for item in match["documentos_normativos"]:
        family = item.get("family")
        role = {
            "edital-base": DocumentRole.BASE_NOTICE,
            "emenda": DocumentRole.AMENDMENT,
        }.get(family)
        text = (item.get("text") or "").strip()
        source_url = (item.get("url") or "").strip()
        if role is None or not text or not source_url:
            continue
        units = split_into_units(text)
        legacy_state = item.get("authority_state")
        authority = (
            AuthorityState(legacy_state)
            if legacy_state in {state.value for state in AuthorityState}
            and legacy_state != "vigente"
            else AuthorityState.CONTEXTUAL
        )
        documents.append({
            "doc_name": item.get("doc_name") or source_url.rsplit("/", 1)[-1],
            "units": units,
            "role": role.value,
            "source_url": source_url,
            "content_hash": compute_content_hash(units),
            "authority_state": authority.value,
        })
        has_base |= role is DocumentRole.BASE_NOTICE
    if not documents:
        return None
    return SourceBundle.model_validate({
        "subject_kind": SubjectKind.OPPORTUNITY.value,
        "subject_id": f"fapesc:{edital_id}",
        "source": "fapesc",
        "collected_at": collected_at,
        "producer_version": "fapesc-adapter-v1",
        "acquisition_status": (
            AcquisitionStatus.COMPLETE.value if has_base
            else AcquisitionStatus.PARTIAL.value
        ),
        "documents": documents,
    })


class Adapter(SourceAdapter):
    """L1 FAPESC — bronze JSON → Documento Canônico (1 doc, units do texto_cru)."""

    def to_documents(self, edital_id: str) -> CanonicalDoc:
        """Recebe o native_id (ex.: '37-2026') e retorna o conteúdo da chamada
        como Documento Canônico fatiado em units (o `texto_cru` do bronze)."""
        bronze = _load_latest_bronze()
        if not bronze:
            return []

        match = _find_bronze_record(bronze, edital_id)

        if match is None:
            logger.info("fapesc adapter: chamada %s não encontrada no bronze", edital_id)
            return []

        normative = match.get("documentos_normativos") or []
        if normative:
            documents: CanonicalDoc = []
            for index, item in enumerate(normative):
                text = (item.get("text") or "").strip()
                if not text:
                    continue
                documents.append({
                    "doc_name": item.get("doc_name") or f"documento-{index + 1}",
                    "units": split_into_units(text),
                    "metadata": {
                        "family": item.get("family") or "edital-base",
                        "revision": index,
                        "source_url": item.get("url") or "",
                        "authority_state": "vigente",
                        "composition_order": index,
                    },
                })
            if documents:
                return documents

        texto = (match.get("texto_cru") or "").strip()
        if not texto:
            logger.info("fapesc adapter: chamada %s sem texto_cru — sem documento", edital_id)
            return []

        return [{
            "doc_name": "pagina-chamada",
            "units": split_into_units(texto),
            "metadata": {
                "family": "edital-base", "revision": 0,
                "source_url": match.get("edital_pdf_url") or match.get("url") or "",
                "authority_state": "vigente", "composition_order": 0,
            },
        }]

    def provenance(self, edital_id: str) -> dict:
        """URL oficial (`url`) + PDF anexo (`edital_pdf_url`) + data de coleta do
        bronze FAPESC, casando por `native_id`."""
        for ch in _load_latest_bronze():
            if ch.get("native_id") != edital_id:
                continue
            url = (ch.get("url") or "").replace("http://", "https://").rstrip("/")
            prov: dict = {"fonte": "fapesc"}
            if url:
                prov["url"] = url
            urls = [
                d.get("url") for d in (ch.get("documentos_normativos") or [])
                if d.get("url")
            ]
            if not urls and ch.get("edital_pdf_url"):
                urls = [ch["edital_pdf_url"]]
            if urls:
                prov["urls_documentos"] = urls
            if coletado_em(ch):
                prov["coletado_em"] = coletado_em(ch)
            return prov
        return {}
