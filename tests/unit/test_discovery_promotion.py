from __future__ import annotations

import pytest

from core.services.discovery_promotion import aggregate_status, initial_stages

pytestmark = pytest.mark.unit


def test_web_run_waits_for_real_fetch():
    assert aggregate_status(initial_stages("web_source"), "web_source") == "awaiting_fetch"


def test_ready_requires_radar_and_rag():
    stages = initial_stages("evidence_package")
    stages.update({
        "bronze_ready": {"status": "ready"}, "silver_ready": {"status": "ready"},
        "radar_ready": {"status": "ready"}, "rag_ready": {"status": "ready"},
    })
    assert aggregate_status(stages, "evidence_package") == "ready"


def test_partial_failure_keeps_successful_surface():
    stages = initial_stages("direct_pdf")
    stages.update({
        "bronze_ready": {"status": "ready"}, "silver_ready": {"status": "ready"},
        "radar_ready": {"status": "ready"}, "rag_ready": {"status": "failed"},
    })
    assert aggregate_status(stages, "direct_pdf") == "partial_failure"
