from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.services.crawl4ai_discovery import _document_links

pytestmark = pytest.mark.unit


def test_document_links_prefers_canonical_pdf_embedded_in_download_url():
    result = SimpleNamespace(links={
        "internal": [{
            "href": "https://portal.example/download?url=https%3A%2F%2Fcdn.example%2Fedital.pdf",
            "text": "Edital PDF",
        }],
        "external": [],
    })

    documents = _document_links(result)

    assert documents == [{
        "url": "https://cdn.example/edital.pdf", "label": "Edital PDF", "score": 100,
    }]
