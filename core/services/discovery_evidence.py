"""Contrato serializável de evidência para a torneira de Descoberta.

O pacote vive exclusivamente em ``discovered_opportunities.raw`` até a decisão
humana.  Ele é deliberadamente independente de Crawl4AI: qualquer coletor ou
adapter pode produzir a mesma estrutura e a promoção só conhece este contrato.
"""
from __future__ import annotations

import hashlib
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from core.web_identity import normalize_web_url, web_url_hash

TEXT_CAP = 60_000
DOCUMENT_TEXT_CAP = 20_000
EVIDENCE_VERSION = 1

_FIELD_NAMES = (
    "title", "prazo_envio", "publico_alvo", "descricao", "status",
    "tema", "opportunity_type", "agency", "fonte",
)
_PRECEDENCE = {"adapter": 3, "document": 2, "page": 1}


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


def compose_fields(*field_sets: dict[str, dict[str, Any]]) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    """Compõe fatos determinísticamente e conserva conflitos para o operador.

    Adapter validado ganha de documento, que ganha de página. Valores distintos
    nunca são descartados silenciosamente: ficam em ``conflicts``.
    """
    merged: dict[str, dict[str, Any]] = {}
    conflicts: list[dict[str, Any]] = []
    for fields in field_sets:
        for name, candidate in (fields or {}).items():
            value = candidate.get("value")
            if value in (None, "", []):
                continue
            current = merged.get(name)
            if current is None:
                merged[name] = deepcopy(candidate)
                continue
            if current.get("value") == value:
                continue
            current_priority = _PRECEDENCE.get(str(current.get("origin")), 0)
            candidate_priority = _PRECEDENCE.get(str(candidate.get("origin")), 0)
            conflicts.append({"field": name, "kept": deepcopy(current), "candidate": deepcopy(candidate)})
            if candidate_priority > current_priority:
                merged[name] = deepcopy(candidate)
    return merged, conflicts


def apply_composed_fields(record: dict[str, Any], package: dict[str, Any]) -> dict[str, Any]:
    """Preenche somente lacunas do record; nunca sobrescreve extração existente."""
    out = dict(record)
    for name, field in (package.get("fields") or {}).items():
        if name in _FIELD_NAMES and not out.get(name) and field.get("value"):
            out[name] = field["value"]
    return out
