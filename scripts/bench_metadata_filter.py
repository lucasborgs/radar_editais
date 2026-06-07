#!/usr/bin/env python3
"""
Bake-off Metadata Filtering (#9) — BASE vs BOOST vs FILTER (FINEP+FAPESP).

As flags de tipo de conteúdo (contem_data/valor/elegibilidade/criterios) já são
computadas (core.chunker._detect_metadata) e indexadas (edital_chunks.metadata,
GIN) mas NÃO usadas no retrieval. Aqui medimos se usá-las ajuda.

Isolamento dense-only (como Estágio A do #8): mesmos chunks/embeddings nos 3
braços; muda só como a metadata intervém no ranking.
  • BASE   : top-k por cosseno puro.
  • BOOST  : reordena os candidatos densos (top-N) pondo chunks com flag-que-casa
             a-intenção PRIMEIRO (cosseno como ordem interna). Metadata = desempate,
             parameter-free, sem risco de recall (não exclui nada).
  • FILTER : hard — restringe aos chunks com a flag; se < k, completa com BASE.

query→intenção: keywords determinísticas (sem custo LLM). Queries SEM intenção
ficam idênticas nos 3 braços → o efeito só aparece no subconjunto com intenção,
reportado à parte (o overall dilui).

Métrica: token-recall chunking-invariante sobre gold_text (core.rag_eval).

Uso: python scripts/bench_metadata_filter.py
"""
from __future__ import annotations

import json
import os
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv  # noqa: E402

load_dotenv()

import psycopg  # noqa: E402

from core.embedder import embed_query, embed_texts  # noqa: E402
from core.rag_eval import gold_best_chunk_recall_at_k, gold_recall_at_k  # noqa: E402
from scripts.bench_parsing import _cos  # noqa: E402

GOLDENS = {
    "finep": ROOT / "eval_data" / "golden" / "finep.json",
    "fapesp": ROOT / "eval_data" / "golden" / "fapesp.json",
}
_N_CAND = 20  # pool denso reordenado (espelha _CANDIDATE_LIMIT do retriever)
_ARMS = ["BASE", "BOOST", "FILTER"]
_METRICS = ["recall@3", "recall@5", "best@3", "best@5"]

# query (lowercase) → flag de metadata. Keywords enxutas e de domínio.
_INTENT: dict[str, tuple[str, ...]] = {
    "contem_data": ("prazo", "data", "quando", "cronograma", "submiss", "limite",
                    "calendário", "calendario", "vigência", "vigencia"),
    "contem_valor_financeiro": ("valor", "quanto", "orçament", "orcament", "financ",
                                 "recurso", "custo", "r$", "reais", "teto", "máximo", "maximo"),
    "contem_elegibilidade": ("quem pode", "elegib", "requisito", "participa", "condiç",
                             "condic", "vínculo", "vinculo", "habilitaç", "habilitac", "exig"),
    "contem_criterios": ("critério", "criterio", "avalia", "seleç", "selec", "julga",
                         "pontuaç", "pontuac", "enquadra", "análise", "analise"),
}


def _query_flags(query: str) -> set[str]:
    q = query.lower()
    return {flag for flag, kws in _INTENT.items() if any(kw in q for kw in kws)}


def _chunk_has_flag(chunk: dict, flags: set[str]) -> bool:
    md = chunk.get("metadata") or {}
    return any(md.get(f) in (True, "true") for f in flags)


def _chunks(edital_id: str) -> list[dict]:
    with psycopg.connect(os.environ["DATABASE_URL"]) as c, c.cursor() as cur:
        cur.execute(
            "SELECT text, section, source_file, metadata FROM public.edital_chunks "
            "WHERE edital_id = %s ORDER BY chunk_index",
            (edital_id,),
        )
        return [{"text": t, "section": s, "source_file": sf, "metadata": md}
                for t, s, sf, md in cur.fetchall()]


def _ranked_by_cos(qv: list[float], chunks: list[dict], embs: list[list[float]]) -> list[dict]:
    return [c for _, c in sorted(zip((_cos(qv, e) for e in embs), chunks, strict=False),
                                 key=lambda x: x[0], reverse=True)]


def _retrieve(arm: str, qv, chunks, embs, flags: set[str], k: int) -> list[dict]:
    ranked = _ranked_by_cos(qv, chunks, embs)
    if not flags or arm == "BASE":
        return ranked[:k]
    if arm == "BOOST":
        cand = ranked[:_N_CAND]
        hit = [c for c in cand if _chunk_has_flag(c, flags)]
        miss = [c for c in cand if not _chunk_has_flag(c, flags)]
        return (hit + miss)[:k]
    if arm == "FILTER":
        flagged = [c for c in ranked if _chunk_has_flag(c, flags)]
        if len(flagged) >= k:
            return flagged[:k]
        rest = [c for c in ranked if not _chunk_has_flag(c, flags)]
        return (flagged + rest)[:k]
    raise ValueError(arm)


def _metrics(top: list[dict], gold: str, k: int) -> tuple[float, float]:
    return (gold_recall_at_k(top, gold, k) or 0.0,
            gold_best_chunk_recall_at_k(top, gold, k) or 0.0)


def _eval_golden(name: str, path: Path):
    golden = json.loads(path.read_text(encoding="utf-8"))
    queries = golden["queries"]
    eids = sorted({q["edital_id"] for q in queries})
    chunks_by = {e: _chunks(e) for e in eids}
    embs_by = {e: (embed_texts([c["text"] for c in ch]) if ch else []) for e, ch in chunks_by.items()}

    # rows[i] = {arm: {metric: val}, "has_intent": bool}
    rows: list[dict] = []
    n_intent = 0
    for q in queries:
        eid, gold = q["edital_id"], q.get("gold_text", "")
        chunks, embs = chunks_by.get(eid, []), embs_by.get(eid, [])
        if not chunks or not gold:
            continue
        flags = _query_flags(q["query"])
        n_intent += bool(flags)
        qv = embed_query(q["query"])
        row: dict = {"has_intent": bool(flags)}
        for arm in _ARMS:
            m = {}
            for k in (3, 5):
                r, b = _metrics(_retrieve(arm, qv, chunks, embs, flags, k), gold, k)
                m[f"recall@{k}"], m[f"best@{k}"] = r, b
            row[arm] = m
        rows.append(row)
    print(f"\n### {name.upper()} — {len(rows)} queries ({n_intent} com intenção de metadata), "
          f"{sum(len(c) for c in chunks_by.values())} chunks")
    return rows


def _report(title: str, rows: list[dict]) -> None:
    if not rows:
        print(f"\n[{title}] (vazio)")
        return
    intent = [r for r in rows if r["has_intent"]]
    print(f"\n[{title}] {len(rows)} queries total | {len(intent)} com intenção")
    print("  scope         arm      recall@3   recall@5     best@3     best@5")
    for scope_name, scope in (("OVERALL", rows), ("INTENT-ONLY", intent)):
        if not scope:
            continue
        for arm in _ARMS:
            means = {m: statistics.mean(r[arm][m] for r in scope) for m in _METRICS}
            print(f"  {scope_name:<12}  {arm:<6} " + "".join(f"{means[m]:10.4f}" for m in _METRICS))
        print()
    # pareado no subconjunto INTENT (onde o lever age), vs BASE
    if intent:
        print("  Δ vs BASE no subconjunto INTENT (mean | win/loss/tie) — recall@5 e @3:")
        for arm in ("BOOST", "FILTER"):
            line = f"    {arm:<7}"
            for m in ("recall@5", "recall@3"):
                d = [r[arm][m] - r["BASE"][m] for r in intent]
                win = sum(1 for x in d if x > 1e-9)
                loss = sum(1 for x in d if x < -1e-9)
                line += f"   {m}: {statistics.mean(d):+.4f} | {win}/{loss}/{len(d)-win-loss}"
            print(line)


def main() -> int:
    combined: list[dict] = []
    for name, path in GOLDENS.items():
        rows = _eval_golden(name, path)
        _report(name.upper(), rows)
        combined.extend(rows)
    _report("COMBINADO", combined)
    return 0


if __name__ == "__main__":
    sys.exit(main())
