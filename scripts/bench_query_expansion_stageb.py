#!/usr/bin/env python3
"""
Estágio B (ecológico) do bake-off Query Expansion (#8) — QR vs RAW pelo
retrieve_chunks REAL (RRF híbrido + rerank), + controle negativo.

Responde o que o Estágio A (dense-only isolado) não respondeu:
  1. O ganho do QR sobrevive à fusão RRF + rerank query-aware? (o rerank já
     reordena por relevância à query — pode se sobrepor ao ganho do QR e lavar.)
  2. Latência: QR custa +1 chamada LLM síncrona por retrieval. Quanto?
  3. CONTROLE NEGATIVO (anti-overfitting): query trocada por texto não-relacionado.
     Se a métrica NÃO desabar, o benchmark é cego e TODAS as conclusões (Contextual/
     Docling/QR) são suspeitas. Se desabar, a métrica tem range → veredictos críveis.

Rerank: o default de prod é cross-encoder (dep pesada, ausente no .venv). Usamos
RERANK_BACKEND=llm (query-aware, sem torch) como proxy ecológico, e reportamos
TAMBÉM rerank=off (RRF puro) como referência — bracketa o cross-encoder de prod.

Uso: python scripts/bench_query_expansion_stageb.py
"""
from __future__ import annotations

import os
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv  # noqa: E402

load_dotenv()
# Backend de rerank query-aware sem dep pesada (proxy do cross-encoder de prod).
os.environ.setdefault("RERANK_BACKEND", "llm")

import json  # noqa: E402

from core.llm_client import make_client  # noqa: E402
from core.rag_eval import gold_best_chunk_recall_at_k, gold_recall_at_k  # noqa: E402
from core.retriever import retrieve_chunks  # noqa: E402
from scripts.bench_query_expansion import GOLDENS, _MODEL, _QR_PROMPT  # noqa: E402

_CONTROL_QUERY = "Receita de bolo de fubá cremoso com goiabada e queijo coalho."
_K = 5


def _qr_rewrite(client, query: str) -> str:
    try:
        r = client.chat.completions.create(
            model=_MODEL, max_tokens=200, temperature=0.0,
            messages=[{"role": "user", "content": _QR_PROMPT.format(query=query)}],
        )
        return (r.choices[0].message.content or "").strip() or query
    except Exception:
        return query


def _load_queries() -> list[dict]:
    out = []
    for path in GOLDENS.values():
        g = json.loads(path.read_text(encoding="utf-8"))
        for q in g["queries"]:
            if q.get("gold_text"):
                out.append(q)
    return out


def _timed_retrieve(eid: str, query: str, rerank: bool) -> tuple[list[dict], float]:
    t0 = time.perf_counter()
    chunks = retrieve_chunks(db=None, edital_ids=[eid], query=query, k=_K, rerank=rerank)
    return chunks, (time.perf_counter() - t0) * 1000.0


def _run(rerank: bool, queries: list[dict], qr_by_id: dict[str, str]) -> None:
    label = "rerank=llm (ecológico)" if rerank else "rerank=off (RRF puro)"
    arms = {a: {"r5": [], "b5": [], "r3": [], "lat": []} for a in ("RAW", "QR", "CONTROL")}

    # warmup (carrega conexões; rerank-llm não tem peso de modelo, mas aquece DNS/TLS)
    _timed_retrieve(queries[0]["edital_id"], queries[0]["query"], rerank)

    for q in queries:
        eid, gold = q["edital_id"], q["gold_text"]
        plans = {"RAW": q["query"], "QR": qr_by_id[q["id"]], "CONTROL": _CONTROL_QUERY}
        for arm, qtext in plans.items():
            chunks, lat = _timed_retrieve(eid, qtext, rerank)
            arms[arm]["r5"].append(gold_recall_at_k(chunks, gold, 5) or 0.0)
            arms[arm]["b5"].append(gold_best_chunk_recall_at_k(chunks, gold, 5) or 0.0)
            arms[arm]["r3"].append(gold_recall_at_k(chunks, gold, 3) or 0.0)
            arms[arm]["lat"].append(lat)

    n = len(queries)
    print(f"\n### Estágio B — {label} — {n} queries (FINEP+FAPESP)")
    print("  arm        recall@5   recall@3     best@5    lat_p50    lat_p95")
    for a in ("RAW", "QR", "CONTROL"):
        d = arms[a]
        lat_sorted = sorted(d["lat"])
        p50 = statistics.median(lat_sorted)
        p95 = lat_sorted[min(int(0.95 * n), n - 1)]
        print(f"  {a.ljust(9)} {statistics.mean(d['r5']):8.4f} {statistics.mean(d['r3']):10.4f}"
              f" {statistics.mean(d['b5']):10.4f} {p50:8.0f}ms {p95:8.0f}ms")

    # pareado QR vs RAW
    raw, qr = arms["RAW"], arms["QR"]
    for m in ("r5", "r3"):
        deltas = [qr[m][i] - raw[m][i] for i in range(n)]
        win = sum(1 for x in deltas if x > 1e-9)
        loss = sum(1 for x in deltas if x < -1e-9)
        md = statistics.mean(deltas)
        sd = statistics.pstdev(deltas)
        print(f"  Δ QR-RAW {m}: {md:+.4f} | win/loss/tie {win}/{loss}/{n-win-loss} | σ={sd:.4f}")


def main() -> int:
    queries = _load_queries()
    print(f"carregadas {len(queries)} queries; reescrevendo (QR) via {_MODEL}…", flush=True)
    client = make_client(api_key=os.environ["OPENAI_API_KEY"])

    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=8) as ex:
        qr_texts = list(ex.map(lambda q: _qr_rewrite(client, q["query"]), queries))
    qr_gen_ms = (time.perf_counter() - t0) * 1000.0 / len(queries)
    qr_by_id = {q["id"]: t for q, t in zip(queries, qr_texts, strict=True)}
    print(f"QR pronto. Custo de latência do rewrite (paralelo, amortizado): ~{qr_gen_ms:.0f}ms/query "
          f"(em prod é SÍNCRONO no turno → ~500-1000ms reais por retrieval)")

    _run(rerank=False, queries=queries, qr_by_id=qr_by_id)
    _run(rerank=True, queries=queries, qr_by_id=qr_by_id)
    return 0


if __name__ == "__main__":
    sys.exit(main())
