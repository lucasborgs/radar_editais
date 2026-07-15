from __future__ import annotations

import json

from scripts import eval_report


def _result(dataset_hash: str, value: float) -> dict:
    return {
        "suite": "matching",
        "run_name": f"run-{dataset_hash}",
        "status": "diagnostic",
        "n_cases": 2,
        "aggregate": {"mean_noise": value},
        "manifest": {
            "schema_version": "1",
            "suite": {"name": "matching", "version": "1"},
            "dataset": {
                "loaded_cases": 2,
                "files": [{"path": "golden.json", "sha256": dataset_hash}],
            },
            "config": {"EMBEDDING_MODEL": "model-a"},
            "metric_directions": {"mean_noise": "lower_is_better"},
        },
    }


def test_report_only_compares_compatible_runs(tmp_path, monkeypatch, capsys):
    (tmp_path / "01_matching.json").write_text(json.dumps(_result("a", 2.0)))
    (tmp_path / "02_matching.json").write_text(json.dumps(_result("a", 1.0)))
    (tmp_path / "03_matching.json").write_text(json.dumps(_result("b", 9.0)))
    (tmp_path / "legacy_matching.json").write_text(json.dumps({
        "suite": "matching", "aggregate": {"mean_noise": 0.0},
    }))
    monkeypatch.setattr(eval_report, "RESULTS_DIR", tmp_path)

    eval_report.report("matching")

    output = capsys.readouterr().out
    assert "1 run(s) comparáveis" in output
    assert "best=9.000" in output
    assert "1 run(s) legadas excluídas" in output
