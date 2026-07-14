"""Enriquecimento opcional Crawl4AI para a Descoberta.

Este módulo não é importado pelo backend nem por adapters no caminho comum. A
torneira o chama somente com ``DISCOVERY_CRAWL4AI_ENABLED=1`` dentro do worker.
Assim a instalação extra do Crawl4AI é uma capacidade do worker, não uma nova
dependência obrigatória do sistema.
"""
from __future__ import annotations

import asyncio
import io
import os
import time
from typing import Any

from core.net_guard import safe_get
from core.services.discovery_evidence import (
    DOCUMENT_TEXT_CAP,
    build_evidence_package,
    sanitized_error,
)

MAX_DOCUMENTS = 3
MAX_DOCUMENT_BYTES = 10 * 1024 * 1024
MAX_DOCUMENT_PAGES = 100
PAGE_TIMEOUT_SECONDS = float(os.getenv("DISCOVERY_CRAWL4AI_PAGE_TIMEOUT_SECONDS", "45"))


def is_enabled() -> bool:
    return os.getenv("DISCOVERY_CRAWL4AI_ENABLED", "0") == "1"


def _markdown(result: Any) -> str:
    markdown = getattr(result, "markdown", "") or ""
    return getattr(markdown, "raw_markdown", None) or str(markdown)


def _document_links(result: Any) -> list[dict[str, str]]:
    links = getattr(result, "links", {}) or {}
    candidates = links.get("internal", []) + links.get("external", []) if isinstance(links, dict) else []
    output: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in candidates:
        url = str((item or {}).get("href") or (item or {}).get("url") or "").strip()
        label = str((item or {}).get("text") or "").strip()
        if not url or url in seen or ".pdf" not in f"{url} {label}".lower():
            continue
        seen.add(url)
        score = 100 if any(w in f"{url} {label}".lower() for w in ("edital", "regulamento", "chamada")) else 0
        output.append({"url": url, "label": label, "score": score})
    return sorted(output, key=lambda x: (-int(x["score"]), x["url"]))[:MAX_DOCUMENTS]


def _download_document(document: dict[str, str]) -> dict[str, Any]:
    """Baixa somente PDFs selecionados, com os mesmos budgets do bake-off."""
    result: dict[str, Any] = {
        **document, "kind": "pdf", "selection_reason": "official_pdf_policy", "status": "pending",
    }
    try:
        response = safe_get(document["url"], timeout=30, headers={"User-Agent": "RadarEditais/1.0"})
        response.raise_for_status()
        body = response.content
        if len(body) > MAX_DOCUMENT_BYTES:
            result.update({"status": "too_large", "bytes": len(body)})
            return result
        import pdfplumber  # instalado no stack de PDFs; import tardio no worker

        pages: list[str] = []
        pages_loaded = 0
        with pdfplumber.open(io.BytesIO(body)) as pdf:
            for number, page in enumerate(pdf.pages, start=1):
                if number > MAX_DOCUMENT_PAGES:
                    break
                pages_loaded = number
                text = page.extract_text()
                if text:
                    pages.append(text)
        text = "\n\n".join(pages)
        result.update({
            "status": "loaded" if text else "empty", "bytes": len(body),
            "pages": pages_loaded, "text": text[:DOCUMENT_TEXT_CAP],
        })
    except Exception as exc:  # uma falha de documento não invalida a página
        result.update({"status": "failed", "error": sanitized_error(exc)})
    return result


async def _crawl(record: dict[str, Any]) -> dict[str, Any]:
    # Import tardio: ambientes sem extra continuam funcionando normalmente.
    from crawl4ai import AsyncWebCrawler, CacheMode, CrawlerRunConfig

    started = time.perf_counter()
    package = build_evidence_package(record, collector="crawl4ai")
    try:
        async with AsyncWebCrawler() as crawler:
            result = await asyncio.wait_for(
                crawler.arun(url=record["url"], config=CrawlerRunConfig(cache_mode=CacheMode.BYPASS)),
                timeout=PAGE_TIMEOUT_SECONDS,
            )
        text = _markdown(result)[:60_000]
        if text:
            package["page"].update({"text": text, "status": "loaded"})
        docs = await asyncio.gather(*(asyncio.to_thread(_download_document, d) for d in _document_links(result)))
        package["documents"] = list(docs)
        package["operation"].update({"status": "ready", "duration_ms": round((time.perf_counter() - started) * 1000)})
    except Exception as exc:
        # O fallback legado permanece no pacote e pode ser aprovado normalmente.
        package["operation"].update({
            "status": "partial_failure", "duration_ms": round((time.perf_counter() - started) * 1000),
            "errors": [sanitized_error(exc)],
        })
    return package


def enrich_record(record: dict[str, Any]) -> dict[str, Any]:
    """Retorna pacote pronto para staging; só deve ser chamado pelo worker."""
    return asyncio.run(_crawl(record))
