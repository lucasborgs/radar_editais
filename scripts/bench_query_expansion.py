#!/usr/bin/env python3
"""
Bake-off Query Expansion (#8) — query crua vs QR vs HyDE vs HyDE+RAW (FINEP+FAPESP).

Mede se transformar a QUERY melhora o retrieval. Diferente do bench_contextual:
lá mudava o embedding do CHUNK e a query era crua; aqui os chunks e seus
embeddings são IDÊNTICOS entre braços — muda só o vetor da query. Logo o viés
gold_text=chunk-do-baseline é simétrico e se cancela no delta.

Estágio A (isolamento, dense-only): este script. Mede o efeito puro no espaço
de embedding via cosine top-k sobre os chunks de produção (corpo cru). Estágio B
(ecológico, pelo retrieve_chunks real) só roda se A der sinal.

Braços (todos contra A0):
  • A0 RAW       : query crua → embed
  • A1 QR        : LLM reescreve a query (siglas/sinônimos, sem floreio) → embed
  • A2 HyDE      : LLM gera pseudo-trecho de edital → embed o pseudo-trecho
  • A3 HyDE+RAW  : média dos vetores HyDE e RAW (hedge contra pseudo-doc ruim)

Métrica: token-recall chunking-invariante sobre gold_text (core.rag_eval),
@{3,5}, union e best_chunk. LLM em temp=0 p/ reprodutibilidade.

Uso: python scripts/bench_query_expansion.py
"""
from __future__ import annotations

import json
import os
import statistics
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv  # noqa: E402

load_dotenv()

import psycopg  # noqa: E402

from core.embedder import embed_texts  # noqa: E402
from core.llm_client import make_client  # noqa: E402
from core.rag_eval import gold_best_chunk_recall_at_k, gold_recall_at_k  # noqa: E402
from scripts.bench_parsing import _retrieve  # noqa: E402  (cosine top-k dense-only)

GOLDENS = {
    "finep": ROOT / "eval_data" / "golden" / "finep.json",
    "fapesp": ROOT / "eval_data" / "golden" / "fapesp.json",
}
_MODEL = "gpt-4o-mini"
_ARMS = ["RAW", "QR", "HyDE", "HyDE+RAW"]
_METRICS = ["recall@3", "recall@5", "best@3", "best@5"]

_QR_PROMPT = (
    "Reescreva a pergunta abaixo para maximizar a recuperação semântica em um "
    "edital de fomento público brasileiro. Expanda siglas, use o vocabulário "
    "formal do regulamento e remova floreio conversacional (ex.: 'qual o…?', "
    "'eu preciso…'). Mantenha o sentido. Responda APENAS com a query reescrita.\n\n"
    "PERGUNTA: {query}"
)
_HYDE_PROMPT = (
    "Escreva um trecho curto (2 a 4 frases), no registro formal de um edital de "
    "fomento público brasileiro, que responda à pergunta abaixo COMO SE fosse "
    "copiado do regulamento. NÃO invente números/datas específicos — foque em "
    "vocabulário e estrutura típicos de edital. Responda APENAS com o trecho.\n\n"
    "PERGUNTA: {query}"
)


def _chunks(edital_id: str) -> list[dict]:
    """Chunks de produção (corpo cru) de um edital, em ordem."""
    with psycopg.connect(os.environ["DATABASE_URL"]) as c, c.cursor() as cur:
        cur.execute(
            "SELECT text, section, source_file FROM public.edital_chunks "
            "WHERE edital_id = %s ORDER BY chunk_index",
            (edital_id,),
        )
        return [{"text": t, "section": s, "source_file": sf} for t, s, sf in cur.fetchall()]


def _llm_transform(client, prompt_tpl: str, query: str, max_tokens: int) -> str:
    try:
        r = client.chat.completions.create(
            model=_MODEL, max_tokens=max_tokens, temperature=0.0,
            messages=[{"role": "user", "content": prompt_tpl.format(query=query)}],
        )
        return (r.choices[0].message.content or "").strip() or query
    except Exception:
        return query  # degrada para a query crua


def _mean_vec(a: list[float], b: list[float]) -> list[float]:
    return [(x + y) / 2.0 for x, y in zip(a, b, strict=True)]


def _build_query_vecs(queries: list[dict]) -> dict[str, list[list[float]]]:
    """Para cada braço, um vetor por query (alinhado por índice)."""
    client = make_client(api_key=os.environ["OPENAI_API_KEY"])
    raw_texts = [q["query"] for q in queries]

    with ThreadPoolExecutor(max_workers=8) as ex:
        qr_texts = list(ex.map(lambda q: _llm_transform(client, _QR_PROMPT, q, 200), raw_texts))
        hyde_texts = list(ex.map(lambda q: _llm_transform(client, _HYDE_PROMPT, q, 250), raw_texts))

    raw_vecs = embed_texts(raw_texts)
    qr_vecs = embed_texts(qr_texts)
    hyde_vecs = embed_texts(hyde_texts)
    hybrid_vecs = [_mean_vec(h, r) for h, r in zip(hyde_vecs, raw_vecs, strict=True)]
    return {"RAW": raw_vecs, "QR": qr_vecs, "HyDE": hyde_vecs, "HyDE+RAW": hybrid_vecs}


def _eval_golden(name: str, path: Path) -> dict[str, list[dict[str, float]]]:
    """Roda todos os braços num golden. Retorna arm -> lista de dicts de métrica
    (um por query VÁLIDA: tem chunks e gold_text). Pareado por índice entre braços."""
    golden = json.loads(path.read_text(encoding="utf-8"))
    queries = golden["queries"]

    # chunks + embeddings (corpo cru) por edital — UMA vez, compartilhado por todos os braços.
    eids = sorted({q["edital_id"] for q in queries})
    chunks_by = {e: _chunks(e) for e in eids}
    embs_by = {e: (embed_texts([c["text"] for c in ch]) if ch else []) for e, ch in chunks_by.items()}

    qvecs = _build_query_vecs(queries)

    per_arm: dict[str, list[dict[str, float]]] = {a: [] for a in _ARMS}
    n_skipped = 0
    for i, q in enumerate(queries):
        eid = q["edital_id"]
        chunks, embs = chunks_by.get(eid, []), embs_by.get(eid, [])
        gold = q.get("gold_text", "")
        if not chunks or not gold:
            n_skipped += 1
            continue
        for arm in _ARMS:
            qv = qvecs[arm][i]
            m: dict[str, float] = {}
            for k in (3, 5):
                top = _retrieve(qv, chunks, embs, k)
                m[f"recall@{k}"] = gold_recall_at_k(top, gold, k) or 0.0
                m[f"best@{k}"] = gold_best_chunk_recall_at_k(top, gold, k) or 0.0
            per_arm[arm].append(m)

    n_valid = len(per_arm["RAW"])
    print(f"\n### {name.upper()} — {n_valid} queries válidas ({n_skipped} puladas), "
          f"{sum(len(c) for c in chunks_by.values())} chunks em {len(eids)} editais")
    _report(per_arm)
    return per_arm


def _report(per_arm: dict[str, list[dict[str, float]]]) -> None:
    raw = per_arm["RAW"]
    n = len(raw)
    if not n:
        print("  (sem queries válidas)")
        return
    # média por braço
    means = {a: {m: statistics.mean(q[m] for q in per_arm[a]) for m in _METRICS} for a in _ARMS}
    hdr = "  arm".ljust(12) + "".join(m.rjust(12) for m in _METRICS)
    print(hdr)
    for a in _ARMS:
        print("  " + a.ljust(10) + "".join(f"{means[a][m]:.4f}".rjust(12) for m in _METRICS))

    # delta vs RAW + pareado por-query (win/loss/tie) + desvio dos deltas
    print("\n  delta vs RAW (mean Δ | win/loss/tie | std Δ)  — foco em recall@5:")
    for a in _ARMS:
        if a == "RAW":
            continue
        line = f"  {a.ljust(10)}"
        for m in ("recall@5", "recall@3"):
            deltas = [per_arm[a][i][m] - raw[i][m] for i in range(n)]
            win = sum(1 for d in deltas if d > 1e-9)
            loss = sum(1 for d in deltas if d < -1e-9)
            tie = n - win - loss
            md = statistics.mean(deltas)
            sd = statistics.pstdev(deltas) if n > 1 else 0.0
            line += f"   {m}: {md:+.4f} | {win}/{loss}/{tie} | σ={sd:.4f}"
        print(line)


def main() -> int:
    combined: dict[str, list[dict[str, float]]] = {a: [] for a in _ARMS}
    for name, path in GOLDENS.items():
        per_arm = _eval_golden(name, path)
        for a in _ARMS:
            combined[a].extend(per_arm[a])

    print("\n### COMBINADO (FINEP + FAPESP)")
    _report(combined)
    return 0


if __name__ == "__main__":
    sys.exit(main())
