"""Resumo local de runs comparáveis do harness de avaliação.

Uso:
    python scripts/eval_report.py
    python scripts/eval_report.py matching

Resultados anteriores ao manifesto v1 são listados como legados e nunca entram
em deltas. Runs novas só são comparadas quando suíte, versão, datasets,
configuração declarada e número de casos coincidem.
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).parent.parent
RESULTS_DIR = ROOT / "eval_results"

GREEN = "\033[32m"
RED = "\033[31m"
BOLD = "\033[1m"
RESET = "\033[0m"
DIM = "\033[2m"


def _comparability_key(result: dict[str, Any]) -> str | None:
    manifest = result.get("manifest")
    if not isinstance(manifest, dict) or manifest.get("schema_version") != "1":
        return None
    identity = {
        "suite": manifest.get("suite"),
        "datasets": [
            {"path": item.get("path"), "sha256": item.get("sha256")}
            for item in manifest.get("dataset", {}).get("files", [])
        ],
        "loaded_cases": manifest.get("dataset", {}).get("loaded_cases"),
        "models": manifest.get("models", {}),
        "config": manifest.get("config", {}),
    }
    return json.dumps(identity, sort_keys=True, ensure_ascii=False)


def _load_results(filter_suite: str | None = None) -> tuple[dict[str, list[dict]], dict[str, int]]:
    comparable: dict[str, list[dict]] = defaultdict(list)
    legacy: dict[str, int] = defaultdict(int)
    for path in sorted(RESULTS_DIR.glob("*.json")):
        try:
            result = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(result, dict) or not result.get("aggregate"):
            continue
        suite = result.get("suite")
        if not suite or (filter_suite and suite != filter_suite):
            continue
        key = _comparability_key(result)
        if key is None:
            legacy[suite] += 1
            continue
        result["_comparability_key"] = key
        comparable[suite].append(result)
    return dict(comparable), dict(legacy)


def _fmt(value: float | int | bool) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return f"{value:.3f}"
    return str(value)


def _best(values: list[float], direction: str) -> float:
    return min(values) if direction == "lower_is_better" else max(values)


def _delta(current: float, best: float, direction: str) -> str:
    improvement = best - current if direction == "lower_is_better" else current - best
    if abs(improvement) < 0.001:
        return DIM + "—" + RESET
    color = GREEN if improvement > 0 else RED
    return f"{color}{improvement:+.3f}{RESET}"


def report(filter_suite: str | None = None) -> None:
    by_suite, legacy = _load_results(filter_suite)
    if not by_suite and not legacy:
        print("Nenhum resultado encontrado em", RESULTS_DIR)
        return

    for suite in sorted(set(by_suite) | set(legacy)):
        runs = by_suite.get(suite, [])
        if not runs:
            print(f"\n{BOLD}{suite}{RESET}  {DIM}{legacy[suite]} run(s) legadas; sem comparação{RESET}")
            continue
        latest = runs[-1]
        compatible = [
            run for run in runs
            if run["_comparability_key"] == latest["_comparability_key"]
        ]
        aggregate = latest["aggregate"]
        directions = latest["manifest"].get("metric_directions", {})
        print(
            f"\n{BOLD}{suite}{RESET}  {DIM}({latest.get('n_cases', 0)} casos · "
            f"{len(compatible)} run(s) comparáveis · status={latest.get('status')}){RESET}"
        )
        print("  " + "─" * 68)
        for metric, value in sorted(aggregate.items()):
            if not isinstance(value, (int, float, bool)):
                continue
            numeric = [
                float(run["aggregate"][metric])
                for run in compatible
                if isinstance(run.get("aggregate", {}).get(metric), (int, float, bool))
            ]
            direction = directions.get(metric)
            if not direction or direction == "expected":
                print(f"  {metric:<32} {_fmt(value):<8}  {DIM}sem direção comparativa{RESET}")
                continue
            best = _best(numeric, direction)
            print(
                f"  {metric:<32} {_fmt(value):<8}  best={_fmt(best):<8} "
                f"{_delta(float(value), best, direction)}"
            )
        if legacy.get(suite):
            print(f"  {DIM}{legacy[suite]} run(s) legadas excluídas da comparação{RESET}")


if __name__ == "__main__":
    report(sys.argv[1] if len(sys.argv) > 1 else None)
