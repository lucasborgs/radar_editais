from __future__ import annotations

import hashlib
from unittest.mock import MagicMock

import pytest

from radar.core.ingestion import opportunity_discovery as discovery
from radar.core.services import crawl4ai_discovery
from radar.core.services.discovery_evidence import build_evidence_package
from radar.core.web_search import SearchHit

pytestmark = pytest.mark.unit


def _hub_hit() -> SearchHit:
    return SearchHit(
        title="Portal de inovação",
        url="https://portal.example/desafios/?utm_source=test",
        snippet="desafios",
        content="",
    )


def test_expand_hub_shares_capped_snapshot_and_fetches_once(monkeypatch):
    calls: list[str] = []
    hub = _hub_hit()
    portal_text = "portal " + ("x" * discovery._HUB_SNAPSHOT_TEXT_CAP)

    def fetch(url):
        calls.append(url)
        return {
            "text": portal_text,
            "links": [
                {"href": "/desafio-a", "text": "Desafio A"},
                {"href": "/desafio-b", "text": "Desafio B"},
            ],
        }

    monkeypatch.setattr(
        "radar.core.llm.agent_tools.profile_tools._fetch_and_parse", fetch,
    )

    children = discovery._expand_hub(hub, set(), max_children=8)

    assert calls == [hub.url]
    assert len(children) == 2
    snapshots = [entry["hub_snapshot"] for entry in children]
    assert snapshots[0] == snapshots[1]
    snapshot = snapshots[0]
    assert snapshot["canonical_url"] == "https://portal.example/desafios"
    assert len(snapshot["text"]) == discovery._HUB_SNAPSHOT_TEXT_CAP
    assert snapshot["status"] == "loaded"
    assert snapshot["content_hash"] == hashlib.sha256(
        snapshot["text"].encode("utf-8"),
    ).hexdigest()


def test_empty_hub_snapshot_does_not_fabricate_program_page(monkeypatch):
    hub = _hub_hit()
    monkeypatch.setattr(
        "radar.core.llm.agent_tools.profile_tools._fetch_and_parse",
        lambda url: {"text": "", "links": [{"href": "/desafio", "text": "Desafio"}]},
    )

    child = discovery._expand_hub(hub, set(), max_children=8)[0]
    assert child["hub_snapshot"]["status"] == "empty"
    package = build_evidence_package({
        "url": "https://portal.example/desafio",
        "title": "Desafio",
        "hub_snapshot": child["hub_snapshot"],
    })
    assert package["documents"] == []


def test_isolated_challenge_keeps_legacy_evidence_shape():
    package = build_evidence_package({
        "url": "https://example.org/desafio",
        "title": "Desafio",
        "texto_cru": "texto do desafio",
    })

    assert package["page"]["status"] == "loaded"
    assert package["documents"] == []


def test_crawl4ai_path_preserves_hub_context(monkeypatch):
    record = {
        "url": "https://portal.example/desafio",
        "title": "Desafio",
        "texto_cru": "texto do desafio",
        "hub_snapshot": {
            "canonical_url": "https://portal.example/desafios",
            "text": "regras gerais",
            "content_hash": hashlib.sha256(b"regras gerais").hexdigest(),
            "status": "loaded",
        },
    }

    async def fake_crawl(value):
        return build_evidence_package(value, collector="crawl4ai")

    monkeypatch.setattr(crawl4ai_discovery, "_crawl", fake_crawl)
    package = crawl4ai_discovery.enrich_record(record)

    assert package["identity"]["collector"] == "crawl4ai"
    assert package["documents"][0]["role"] == "program_page"
    assert package["documents"][0]["authority_state"] == "contextual"
    assert package["documents"][0]["text"] == "regras gerais"


def test_staging_raw_keeps_evidence_package(monkeypatch):
    record = {
        "url": "https://example.org/desafio",
        "url_hash": "abc123",
        "title": "Desafio",
        "texto_cru": "texto do desafio",
        "fonte": "Web (descoberta)",
        "hub_snapshot": {
            "canonical_url": "https://portal.example/desafios",
            "text": "regras gerais",
            "content_hash": hashlib.sha256(b"regras gerais").hexdigest(),
            "status": "loaded",
        },
    }
    record["evidence_package"] = build_evidence_package(record)
    db = MagicMock()
    monkeypatch.setattr(
        "radar.core.infra.db.get_supabase_service", lambda: db,
    )
    monkeypatch.setattr(
        "radar.core.ingestion.relevance_classifier.classify_opportunity",
        lambda material: {"verdict": {"decision": "needs_review"}},
    )
    monkeypatch.setattr(
        "radar.core.ingestion.relevance_classifier.validate_opportunity_result",
        lambda result: result,
    )

    discovery._stage_records([record])

    row = db.table.return_value.upsert.call_args[0][0][0]
    assert row["raw"]["evidence_package"] == record["evidence_package"]
    assert row["raw"]["evidence_package"]["documents"][0]["role"] == "program_page"
