"""scripts/eval_report.py — resumo local dos resultados de eval.

Lê todos os eval_results/*.json e imprime uma tabela comparativa por suíte:
última run, melhor run histórica e delta.

Uso:
    python scripts/eval_report.py           # todas as suítes
    python scripts/eval_report.py matching  # suíte específica
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).parent.parent
RESULTS_DIR = ROOT / "eval_results"

GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
BOLD = "\033[1m"
RESET = "\033[0m"
DIM = "\033[2m"


def _load_results(filter_suite: str | None = None) -> dict[str, list[dict]]:
    """Agrupa runs por suíte, ordem cronológica."""
    by_suite: dict[str, list[dict]] = defaultdict(list)
    for f in sorted(RESULTS_DIR.glob("*.json")):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(d, dict):
            continue
        suite = d.get("suite")
        if not suite or d.get("skipped"):
            continue
        if filter_suite and suite != filter_suite:
            continue
        agg = d.get("aggregate", {})
        if not agg:
            continue
        by_suite[suite].append({
            "run_name": d.get("run_name", f.stem),
            "n_cases": d.get("n_cases", 0),
            "aggregate": agg,
            "backend": d.get("backend", "local"),
            "langfuse_url": d.get("langfuse_url"),
        })
    return dict(by_suite)


def _delta_str(current: float, best: float) -> str:
    diff = current - best
    if abs(diff) < 0.001:
        return DIM + "  —" + RESET
    sign = "+" if diff > 0 else ""
    color = GREEN if diff > 0 else RED
    return f"{color}{sign}{diff:+.3f}{RESET}"


def _fmt(v: float | int | bool) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float):
        return f"{v:.3f}"
    return str(v)


def report(filter_suite: str | None = None) -> None:
    by_suite = _load_results(filter_suite)
    if not by_suite:
        print("Nenhum resultado encontrado em", RESULTS_DIR)
        return

    for suite, runs in sorted(by_suite.items()):
        latest = runs[-1]
        metrics = {
            k: v for k, v in latest["aggregate"].items()
            if isinstance(v, (int, float, bool))
        }
        if not metrics:
            continue

        lf_url = latest.get("langfuse_url")
        lf_badge = f"  {DIM}[langfuse]{RESET}" if lf_url else ""
        print(f"\n{BOLD}{suite}{RESET}  {DIM}({latest['n_cases']} casos · {latest['run_name']}){RESET}{lf_badge}")
        print("  " + "─" * 58)

        for metric, val in sorted(metrics.items()):
            name = metric.replace("mean_", "")
            all_vals = [
                r["aggregate"].get(metric)
                for r in runs
                if isinstance(r["aggregate"].get(metric), (int, float))
            ]
            best = max(all_vals) if all_vals else val
            delta = _delta_str(float(val), float(best)) if isinstance(val, (int, float)) else ""
            n_runs = f"{DIM}({len(all_vals)} runs){RESET}"
            # latency_ms: menor é melhor — inverte o sinal do delta
            _lower_is_better = {"latency_ms", "n_factual_errors", "missing_fields"}
            if name in _lower_is_better and isinstance(val, (int, float)):
                best = min(all_vals) if all_vals else val
                delta = _delta_str(-float(val), -float(best))
            print(f"  {name:<28} {_fmt(val):<8}  best={_fmt(best)}  {delta}  {n_runs}")


if __name__ == "__main__":
    suite_filter = sys.argv[1] if len(sys.argv) > 1 else None
    report(suite_filter)
