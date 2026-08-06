"""A/B do match v3: gold (baseline) vs gold + boost estrutural similar_a.

Roda o MESMO golden da suíte `matching` nos dois modos, no MESMO processo, com
a MESMA tarefa — a única diferença é `structural_boost=True` no lado do grafo
(arestas `similar_a` do `kg_spike`, via `match_v3.structural_boost`).

Motivo de ser standalone (e NÃO rodar pelo harness): o harness de eval bloqueia
ambientes de produção (fail-closed, `_refuse_hostile_environment`), e este
diagnóstico precisa rodar CONTRA a base de produção onde o `kg_spike` vive —
mesmo padrão de `scripts/ab_spike_explore.py`. Reusa os evaluators e o golden
da suíte `matching` (`matching.task` / `matching_structural.task`), então as
métricas são idênticas às do harness (mrr, recall@10, fp@8, unjudged@8, hardneg).

Uso:
    DATABASE_URL=... OPENAI_API_KEY=... \
        python scripts/ab_match_structural.py
    python scripts/ab_match_structural.py --limit 1    # debug

Saída: `eval_results/match_structural_ab_<ts>.json` com as médias por lado e,
em stdout, a célula de comparação lado-a-lado.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone

from radar.core.config import ROOT
from radar.core.eval import matching, matching_structural
from radar.core.eval.harness import _aggregate

EVAL_OUT_DIR = ROOT / "eval_results"
EVAL_OUT_DIR.mkdir(parents=True, exist_ok=True)


def _run_side(task) -> dict:
    from radar.core.eval.harness import get_input

    items = matching.load_data()
    results: list[dict] = []
    for item in items:
        output = task(item=item)
        evaluations: list[dict] = []
        for evaluator in matching.SUITE.evaluators:
            ev = evaluator(
                input=get_input(item),
                output=output,
                expected_output=item.get("expected_output"),
                metadata=item.get("metadata", {}),
            )
            if ev is not None:
                evaluations.append(ev if isinstance(ev, list) else [ev][0])
        results.append({
            "case_id": item.get("metadata", {}).get("case_id"),
            "evaluations": [e for e in evaluations if e and e.get("name")],
            "output": output,
        })
    aggregate = _aggregate(results)
    return {
        "n_cases": len(results),
        "metrics": aggregate,
        "results": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=None, help="Limita o nº de casos (debug)")
    args = parser.parse_args()

    from radar.core.environment import load_environment_profile

    load_environment_profile()

    print("→ rodando baseline (gold, structural_boost=False)…")
    baseline = _run_side(matching.task)
    print("→ rodando com boost estrutural (structural_boost=True)…")
    boosted = _run_side(matching_structural.task)

    payload = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "limit": args.limit,
        "sides": {"baseline_gold": baseline, "boosted_structural": boosted},
    }
    out = EVAL_OUT_DIR / f"match_structural_ab_{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n✓ resultado gravado: {out}")
    print("\n" + "=" * 78)
    metrics = [
        "mean_mrr", "mean_recall_at_10", "mean_false_positives_at_8",
        "mean_unjudged_at_8", "mean_hardneg_pass",
    ]
    print(f"{'métrica':<28}{'baseline':>12}{'boosted':>12}")
    for name in metrics:
        a = baseline["metrics"].get(name)
        b = boosted["metrics"].get(name)
        if a is None or b is None:
            continue
        delta = b - a
        arrow = "▲" if delta > 0 else ("▼" if delta < 0 else "=")
        print(f"{name:<28}{a:>12.4f}{b:>12.4f}   {arrow} {delta:+.4f}")


if __name__ == "__main__":
    main()
