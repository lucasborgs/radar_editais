from __future__ import annotations

import json

from core import edital_extractor


def test_fapesp_raw_index_composes_historical_snapshots(tmp_path, monkeypatch):
    raw_dir = tmp_path / "fapesp_raw"
    raw_dir.mkdir()
    (raw_dir / "scan_1.json").write_text(json.dumps([
        {"url": "https://fapesp.br/old", "titulo": "Antigo", "texto_cru": "v1"},
        {"url": "https://fapesp.br/same", "titulo": "Mesmo", "texto_cru": "v1"},
    ]))
    (raw_dir / "scan_2.json").write_text(json.dumps([
        {"url": "https://fapesp.br/same", "titulo": "Mesmo", "texto_cru": "v2"},
    ]))
    monkeypatch.setattr(edital_extractor, "BRONZE_DIR", tmp_path)

    index = edital_extractor.raw_by_native_id("fapesp")

    assert set(index) == {"old", "same"}
    assert index["old"]["raw"].endswith("v1")
    assert index["same"]["raw"].endswith("v2")
