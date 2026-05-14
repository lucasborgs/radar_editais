#!/usr/bin/env python3
"""
Compara duas runs de eval do RAG e imprime o delta por métrica.

Uso:
    python scripts/compare_runs.py                          # últimas 2 do --source default
    python scripts/compare_runs.py --source finep
    python scripts/compare_runs.py --baseline <id> --new <id>
    python scripts/compare_runs.py --new <id>               # baseline = run anterior

`<id>` pode ser o `run_id` completo ou o nome do arquivo (com ou sem .json).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESULTS_DIR = ROOT / "eval_results"


def _resolve_run(identifier: str | None, source: str, offset: int = 0) -> Path:
    """Resolve um identificador (run_id, arquivo, ou None+offset) num path.

    `offset=0` = run mais recente; `offset=1` = penúltima; etc.
    """
    if identifier:
        candidate = RESULTS_DIR / identifier
        if not candidate.suffix:
            candidate = candidate.with_suffix(".json")
        if not candidate.exists():
            raise SystemExit(f"Run não encontrado: {candidate}")
        return candidate

    # Pega as runs do source ordenadas por timestamp (nome do arquivo).
    pattern = f"*_{source}.json"
    runs = sorted(RESULTS_DIR.glob(pattern), reverse=True)
    if len(runs) <= offset:
        raise SystemExit(
            f"Só {len(runs)} run(s) encontrados para source={source}; "
            f"preciso pelo menos {offset + 1}.")
    return runs[offset]


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _fmt_delta(old: float | None, new: float | None, precision: int = 3) -> str:
    """Formata como 'old → new (Δ+0.123)' com setinha de melhora/piora."""
    if old is None and new is None:
        return "— → —"
    if old is None:
        return f"— → {new:.{precision}f}  (novo)"
    if new is None:
        return f"{old:.{precision}f} → —  (removido)"
    delta = new - old
    arrow = "→"
    sign = "+" if delta >= 0 else ""
    return f"{old:.{precision}f} {arrow} {new:.{precision}f}  (Δ {sign}{delta:.{precision}f})"


_METRICS = [
    ("recall_at_1", 3),
    ("recall_at_3", 3),
    ("recall_at_5", 3),
    ("mrr", 3),
    ("null_rate", 3),
    ("faithfulness_mean", 2),
    ("latency_p50_ms", 0),
    ("latency_p95_ms", 0),
    ("latency_mean_ms", 0),
]


def _diff_summaries(old: dict, new: dict) -> None:
    print("═" * 72)
    print(f"  BASELINE: {old.get('run_id', '?')}")
    print(f"  NEW:      {new.get('run_id', '?')}")
    print("═" * 72)

    # Diff de config (mostrar só o que mudou)
    old_cfg, new_cfg = old.get("config", {}), new.get("config", {})
    config_changes = {k: (old_cfg.get(k), new_cfg.get(k))
                       for k in set(old_cfg) | set(new_cfg)
                       if old_cfg.get(k) != new_cfg.get(k)}
    if config_changes:
        print("  CONFIG diff:")
        for k, (o, n) in config_changes.items():
            print(f"    {k}: {o} → {n}")
        print()

    print("  MÉTRICAS:")
    so, sn = old.get("summary", {}), new.get("summary", {})
    for key, precision in _METRICS:
        if key not in so and key not in sn:
            continue
        line = _fmt_delta(so.get(key), sn.get(key), precision)
        print(f"    {key:24s} {line}")
    print()


def _diff_per_query(old: dict, new: dict, top_changes: int = 5) -> None:
    """Lista queries que mudaram de status (hit→miss ou miss→hit)."""
    old_q = {q["query_id"]: q for q in old.get("per_query", [])}
    new_q = {q["query_id"]: q for q in new.get("per_query", [])}
    common = set(old_q) & set(new_q)

    flipped: list[tuple[str, str, float, float]] = []
    for qid in common:
        old_rr = old_q[qid].get("reciprocal_rank", 0.0)
        new_rr = new_q[qid].get("reciprocal_rank", 0.0)
        if (old_rr == 0) != (new_rr == 0):
            direction = "miss→hit" if new_rr > 0 else "hit→miss"
            flipped.append((qid, direction, old_rr, new_rr))

    if not flipped:
        print("  Nenhuma query mudou de status (hit↔miss).")
        return

    print(f"  QUERIES QUE MUDARAM DE STATUS ({len(flipped)}):")
    for qid, direction, old_rr, new_rr in flipped[:top_changes]:
        arrow = "📈" if direction == "miss→hit" else "📉"
        print(f"    {arrow} {qid}: {direction}  RR {old_rr:.2f} → {new_rr:.2f}")
        # Mostra a query também pra contexto
        q_text = new_q[qid].get("query", "")
        if q_text:
            print(f"        “{q_text[:90]}…”" if len(q_text) > 90 else f"        “{q_text}”")
    if len(flipped) > top_changes:
        print(f"    ... e mais {len(flipped) - top_changes} queries")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                      formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", default="finep")
    parser.add_argument("--baseline", help="run anterior (default: penúltima run)")
    parser.add_argument("--new", help="run nova (default: última run)")
    args = parser.parse_args()

    new_path = _resolve_run(args.new, args.source, offset=0)
    baseline_path = _resolve_run(args.baseline, args.source, offset=1 if not args.baseline else 0)

    if baseline_path == new_path:
        raise SystemExit(f"baseline e new são a mesma run: {new_path.name}")

    old = _load(baseline_path)
    new = _load(new_path)

    _diff_summaries(old, new)
    _diff_per_query(old, new)
    return 0


if __name__ == "__main__":
    sys.exit(main())
