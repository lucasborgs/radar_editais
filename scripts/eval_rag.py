#!/usr/bin/env python3
"""
Roda a suite de avaliação do RAG contra um golden dataset.

Pra cada query no golden:
  1. Chama `retrieve_chunks` com a config atual (k, fts_weight, max_per_source).
  2. Mede latência.
  3. Compara recuperados × esperados (`core.rag_eval.evaluate_query`).
  4. Opcionalmente: chama `judge_faithfulness` pra context relevance.

Saída: `eval_results/<YYYYMMDD_HHMMSS>_<source>.json` com config + summary +
detalhe por query.

Uso:
    python scripts/eval_rag.py                                # source=finep default
    python scripts/eval_rag.py --source finep
    python scripts/eval_rag.py --source finep --k 3 --fts-weight 0.5
    python scripts/eval_rag.py --no-faithfulness              # skip LLM judge
    python scripts/eval_rag.py --editais 768                  # subset
    python scripts/eval_rag.py --quiet                        # só o summary

Custo (faithfulness ligado): ~$0.0003 por query × n_queries. ~$0.01 pra 24 queries.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from core.rag_eval import (  # noqa: E402
    DEFAULT_KS,
    aggregate_runs,
    evaluate_query,
    judge_faithfulness,
)
from core.retriever import (  # noqa: E402
    DEFAULT_FTS_WEIGHT,
    DEFAULT_MAX_PER_SOURCE,
    DEFAULT_TOP_K,
    retrieve_chunks,
)

GOLDEN_DIR = ROOT / "eval_data" / "golden"
RESULTS_DIR = ROOT / "eval_results"


def _load_golden(source: str) -> dict:
    path = GOLDEN_DIR / f"{source}.json"
    if not path.exists():
        raise FileNotFoundError(
            f"Golden não encontrado: {path}. Rode generate_golden.py primeiro.")
    return json.loads(path.read_text(encoding="utf-8"))


def _run_eval(args: argparse.Namespace) -> dict:
    golden = _load_golden(args.source)
    queries = golden.get("queries", [])
    if args.editais:
        wanted = set(map(str, args.editais))
        queries = [q for q in queries if str(q["edital_id"]) in wanted]
    if not queries:
        raise SystemExit("Nenhuma query restou após filtro.")

    ks = list(DEFAULT_KS) if max(DEFAULT_KS) >= args.k else list(DEFAULT_KS) + [args.k]
    ks = sorted(set(k for k in ks if k <= args.k))  # cap em --k (não medir Recall@>k)

    if not args.quiet:
        print(f"[eval] source={args.source}  n_queries={len(queries)}  k={args.k}  "
              f"fts_weight={args.fts_weight}  max_per_source={args.max_per_source}  "
              f"faithfulness={'on' if not args.no_faithfulness else 'off'}")
        print()

    per_query: list[dict] = []
    for q in queries:
        edital_id = str(q["edital_id"])
        query = q["query"]
        expected = q.get("expected", [])

        # Mede latência da chamada de retrieval — ignora o tempo de monta-payload.
        t0 = time.perf_counter()
        retrieved = retrieve_chunks(
            db=None, edital_id=edital_id, query=query,
            k=args.k, fts_weight=args.fts_weight,
            max_per_source=args.max_per_source,
        )
        latency_ms = (time.perf_counter() - t0) * 1000.0

        result = evaluate_query(retrieved, expected, ks=ks)
        result["query_id"] = q["id"]
        result["query"] = query
        result["edital_id"] = edital_id
        result["latency_ms"] = round(latency_ms, 2)
        result["expected"] = expected
        result["retrieved"] = [
            {"chunk_index": c.get("chunk_index"),
             "source_file": c.get("source_file"),
             "section": c.get("section"),
             "score": round(c.get("score", 0.0), 6)}
            for c in retrieved
        ]

        # Faithfulness opcional — adicional roundtrip LLM por query.
        if not args.no_faithfulness:
            result["faithfulness"] = judge_faithfulness(query, retrieved)

        per_query.append(result)

        if not args.quiet:
            hits_str = " ".join(
                f"H@{k}={'✓' if result['hit_at_k'][str(k)] else '✗'}" for k in ks
            )
            rr = result["reciprocal_rank"]
            faith = result.get("faithfulness", "—")
            faith_str = f"  F={faith}" if faith != "—" else ""
            print(f"  {q['id']:24s}  {hits_str}  RR={rr:.2f}  "
                  f"lat={latency_ms:.0f}ms{faith_str}")

    summary = aggregate_runs(per_query, ks=ks)
    return {
        "run_id": datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S") + "_" + args.source,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "config": {
            "source": args.source,
            "k": args.k,
            "fts_weight": args.fts_weight,
            "max_per_source": args.max_per_source,
            "faithfulness_enabled": not args.no_faithfulness,
            "golden_path": str(GOLDEN_DIR / f"{args.source}.json"),
            "golden_generated_at": golden.get("generated_at"),
            "n_editais_in_golden": len({q["edital_id"] for q in queries}),
        },
        "summary": summary,
        "per_query": per_query,
    }


def _print_summary(result: dict) -> None:
    s = result["summary"]
    print()
    print("═" * 60)
    print(f"  SUMMARY — run {result['run_id']}")
    print("═" * 60)
    print(f"  n_queries:       {s.get('n_queries', 0)}")
    for k in DEFAULT_KS:
        key = f"recall_at_{k}"
        if key in s:
            print(f"  {key}:      {s[key]:.3f}")
    if "mrr" in s:
        print(f"  mrr:             {s['mrr']:.3f}")
    if "null_rate" in s:
        print(f"  null_rate:       {s['null_rate']:.3f}")
    if "latency_p50_ms" in s:
        print(f"  latency P50:     {s['latency_p50_ms']:.0f} ms")
        print(f"  latency P95:     {s['latency_p95_ms']:.0f} ms")
    if "faithfulness_mean" in s:
        print(f"  faithfulness:    {s['faithfulness_mean']:.2f} / 5  (n={s['faithfulness_n']})")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                      formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", default="finep")
    parser.add_argument("--k", type=int, default=DEFAULT_TOP_K)
    parser.add_argument("--fts-weight", type=float, default=DEFAULT_FTS_WEIGHT)
    parser.add_argument("--max-per-source", type=int, default=DEFAULT_MAX_PER_SOURCE)
    parser.add_argument("--no-faithfulness", action="store_true",
                        help="Pula o LLM judge (mais rápido / mais barato)")
    parser.add_argument("--editais", nargs="*",
                        help="Subset de editais (filtra o golden)")
    parser.add_argument("--quiet", action="store_true",
                        help="Suprime output por query — só imprime o summary")
    args = parser.parse_args()

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    result = _run_eval(args)
    result["finished_at"] = datetime.now(timezone.utc).isoformat()

    out_path = RESULTS_DIR / f"{result['run_id']}.json"
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2),
                         encoding="utf-8")

    _print_summary(result)
    print()
    print(f"  → {out_path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
