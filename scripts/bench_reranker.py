"""Benchmark de qualidade + latência do reranker (auditoria item 6).

Compara o cross-encoder atual com um candidato, sobre o MESMO golden da suíte
de eval (`eval_data/golden/reranker.json`), e mede latência por chamada em CPU
com o modelo já carregado (exclui o tempo de load).

Uso:
    .venv/bin/python scripts/bench_reranker.py \
        --models cross-encoder/mmarco-mMiniLMv2-L12-H384-v1 BAAI/bge-reranker-v2-m3

Saída: por modelo, mean_top1_accuracy, mean_ndcg_3 (mesmas métricas da suíte) e
latência mediana ms/chamada (1 query × 20 passagens de ~800 tokens, modelo
carregado). Não altera nada no repo; só imprime.
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
import time

from config import ROOT

GOLDEN = ROOT / "eval_data" / "golden" / "reranker.json"


def _sigmoid(x: float) -> float:
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-x))
    z = math.exp(x)
    return z / (1.0 + z)


def _ndcg3(pred_ranking: list[int], exp_ranking: list[int]) -> float:
    n = len(exp_ranking)
    relevance = {idx: n - pos for pos, idx in enumerate(exp_ranking)}
    k = min(3, n)
    dcg = sum(
        relevance.get(pred_ranking[i], 0) / math.log2(i + 2)
        for i in range(min(k, len(pred_ranking)))
    )
    ideal = sorted(relevance.values(), reverse=True)[:k]
    idcg = sum(v / math.log2(i + 2) for i, v in enumerate(ideal))
    return round(dcg / idcg, 4) if idcg > 0 else 0.0


def _load_golden() -> list[dict]:
    return json.loads(GOLDEN.read_text(encoding="utf-8"))


def _scores(model, query: str, texts: list[str]) -> list[float]:
    raw = model.predict([[query, t] for t in texts])
    return [_sigmoid(float(s)) for s in raw]


def _quality(model, cases: list[dict]) -> tuple[float, float]:
    top1, ndcgs = [], []
    for c in cases:
        inp = c["input"]
        scores = _scores(model, inp["query"], inp["chunks"])
        ranking = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
        exp = c["expected_output"]["ranking"]
        top1.append(1.0 if ranking[0] == exp[0] else 0.0)
        ndcgs.append(_ndcg3(ranking, exp))
    return statistics.mean(top1), statistics.mean(ndcgs)


def _latency_ms(model, n_runs: int = 7) -> float:
    """Mediana de ms para 1 query × 20 passagens de ~800 tokens (modelo carregado)."""
    # ~800 tokens ≈ ~5300 chars de PT. Texto sintético realista de edital.
    passage = (
        "São elegíveis empresas com receita operacional bruta anual entre R$ 360 mil e "
        "R$ 300 milhões, conforme classificação de porte vigente, devendo apresentar "
        "documentação de habilitação jurídica, fiscal e econômico-financeira no ato da "
        "submissão da proposta pelo sistema eletrônico da agência de fomento. "
    ) * 14  # ~800 tokens
    query = "Quais empresas podem participar e qual a documentação exigida?"
    texts = [passage + f" Variante {i}." for i in range(20)]
    # warmup
    _scores(model, query, texts)
    samples = []
    for _ in range(n_runs):
        t0 = time.perf_counter()
        _scores(model, query, texts)
        samples.append((time.perf_counter() - t0) * 1000.0)
    return statistics.median(samples)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", nargs="+", required=True)
    ap.add_argument("--max-length", type=int, default=None,
                    help="se setado, passa max_length ao CrossEncoder")
    args = ap.parse_args()

    from sentence_transformers import CrossEncoder

    cases = _load_golden()
    print(f"golden: {len(cases)} casos\n")
    for name in args.models:
        t_load = time.perf_counter()
        kw = {"max_length": args.max_length} if args.max_length else {}
        model = CrossEncoder(name, **kw)
        load_s = time.perf_counter() - t_load
        top1, ndcg = _quality(model, cases)
        lat = _latency_ms(model)
        print(f"== {name}  (max_length={args.max_length or 'default'})")
        print(f"   load:            {load_s:6.1f} s")
        print(f"   mean_top1_acc:   {top1:.4f}")
        print(f"   mean_ndcg_3:     {ndcg:.4f}")
        print(f"   latency (CPU):   {lat:.1f} ms/chamada (20 passagens ~800 tok, mediana)")
        print()


if __name__ == "__main__":
    main()
