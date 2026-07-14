from __future__ import annotations

import json

from core.services import discovery_materializer as materializer
from core.services.discovery_evidence import build_evidence_package, compose_fields


def test_field_composition_keeps_adapter_and_reports_conflict():
    fields, conflicts = compose_fields(
        {"prazo_envio": {"value": "01/08/2026", "origin": "page"}},
        {"prazo_envio": {"value": "15/08/2026", "origin": "adapter"}},
    )
    assert fields["prazo_envio"]["value"] == "15/08/2026"
    assert fields["prazo_envio"]["origin"] == "adapter"
    assert conflicts[0]["field"] == "prazo_envio"


def test_materialize_evidence_writes_web_contract_and_source_document(monkeypatch, tmp_path):
    monkeypatch.setattr(materializer, "BRONZE_DIR", tmp_path)
    saved: list[tuple] = []
    monkeypatch.setattr("core.kg.source_docs.save", lambda *args: saved.append(args) or True)
    opp = {"url": "https://example.gov.br/chamada", "title": "Chamada", "fonte": "Web"}
    evidence = build_evidence_package({**opp, "texto_cru": "texto da página"})
    evidence["documents"] = [{"status": "loaded", "label": "Edital", "text": "texto do regulamento", "url": "https://example.gov.br/e.pdf"}]

    edital_id = materializer.materialize_approved_evidence(opp, evidence)

    bronze = next((tmp_path / "web_raw").glob("*.json"))
    entry = json.loads(bronze.read_text())[0]
    assert edital_id == f"web:{entry['url_hash']}"
    assert entry["texto_cru"] == "texto da página"
    assert entry["source_document_refs"][0]["status"] == "loaded"
    assert saved[0][0] == edital_id
    assert len(saved[0][2]) == 2
