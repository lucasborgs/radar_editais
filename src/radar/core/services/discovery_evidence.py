"""Contrato serializável de evidência para a torneira de Descoberta.

O pacote vive exclusivamente em ``discovered_opportunities.raw`` até a decisão
humana.  Ele é deliberadamente independente de Crawl4AI: qualquer coletor ou
adapter pode produzir a mesma estrutura e a promoção só conhece este contrato.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

from radar.core.web_identity import normalize_web_url, web_url_hash

TEXT_CAP = 60_000
DOCUMENT_TEXT_CAP = 20_000
EVIDENCE_VERSION = 1

_FIELD_NAMES = (
    "title", "prazo_envio", "publico_alvo", "descricao", "status",
    "tema", "opportunity_type", "agency", "fonte",
)


def _text(value: object, cap: int = TEXT_CAP) -> str:
    return str(value or "").strip()[:cap]


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", "ignore")).hexdigest()


def sanitized_error(error: Exception | str) -> str:
    """Erro curto, apropriado para staging/auditoria; nunca stack trace."""
    return " ".join(str(error).split())[:300]


def build_evidence_package(record: dict[str, Any], *, collector: str = "legacy_fetch") -> dict[str, Any]:
    """Converte a extração atual em pacote canônico sem mudar seus campos.

    Isto dá retrocompatibilidade ao coletor já em produção e assegura que toda
    promoção nova usa uma versão congelada, mesmo sem o extra opcional Crawl4AI.
    """
    url = normalize_web_url(str(record["url"]))
    page_text = _text(record.get("texto_cru"))
    fields = {
        name: {
            "value": record.get(name, ""),
            "origin": "page",
            "confidence": "extracted" if record.get(name) else "missing",
        }
        for name in _FIELD_NAMES
    }
    return {
        "version": EVIDENCE_VERSION,
        "identity": {
            "original_url": record["url"], "canonical_url": url,
            "url_hash": web_url_hash(url), "source": record.get("fonte") or "Web (descoberta)",
            "collected_at": datetime.now(timezone.utc).isoformat(), "collector": collector,
        },
        "canonical_url": url,  # compatibilidade explícita para consumidores simples
        "page": {
            "text": page_text, "html": _text(record.get("html")),
            "content_hash": _digest(page_text), "status": "loaded" if page_text else "empty",
        },
        "documents": [],
        "fields": fields,
        "operation": {"collector": collector, "status": "ready", "errors": []},
    }
