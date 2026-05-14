#!/usr/bin/env python3
"""
Probe de qualidade de retrieval sobre `edital_chunks`.

Roda uma bateria de queries contra um edital específico e imprime os top-k
chunks recuperados em 3 modos:
  • dense puro   (fts_weight=0.0) — só pgvector cosine
  • FTS puro     (fts_weight=1.0) — só tsvector português
  • híbrido RRF  (fts_weight=0.5) — fusão Reciprocal Rank Fusion (default)

Uso:
    python scripts/probe_rag.py                              # default: 768 + 5 queries
    python scripts/probe_rag.py --edital 762                 # outro edital
    python scripts/probe_rag.py --edital 768 --k 3           # top-k menor
    python scripts/probe_rag.py --edital 768 --query "..."   # query única
    python scripts/probe_rag.py --edital 768 --queries-file queries.txt

Saída: pra cada query, três blocos lado a lado. Cada chunk recuperado mostra
rank, score RRF, section, source_file e snippet (200 chars) — suficiente
pra eyeball se a recuperação está casando o que faria sentido.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Carrega .env antes do retriever (precisa DATABASE_URL + OPENAI_API_KEY).
from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from core.retriever import retrieve_chunks  # noqa: E402


# Queries-padrão calibradas pra exercitar os 5 flags de metadata + um caso de
# paráfrase pura (testa se o dense ganha sobre o FTS em consulta semântica).
_DEFAULT_QUERIES = [
    "Qual o valor máximo de subvenção por projeto?",
    "Quem pode submeter proposta? Que tipo de instituição é elegível?",
    "Qual o prazo final de submissão da proposta?",
    "Quais são os critérios de avaliação e seus pesos?",
    "Que despesas são apoiáveis? Pessoal e equipamento são cobertos?",
]


def _snippet(text: str, n: int = 200) -> str:
    """Primeiros ~n chars do texto, em uma linha única (sem quebras)."""
    if not text:
        return ""
    flat = " ".join(text.split())
    return flat[:n] + ("…" if len(flat) > n else "")


def _render_results(chunks: list[dict]) -> list[str]:
    """Renderiza uma lista de chunks recuperados como linhas legíveis."""
    if not chunks:
        return ["  (nenhum chunk recuperado)"]
    out: list[str] = []
    for i, c in enumerate(chunks, start=1):
        section = (c.get("section") or "—")[:55]
        source = c.get("source_file") or "?"
        score = c.get("score", 0.0)
        snippet = _snippet(c.get("text") or "", 180)
        out.append(f"  #{i}  score={score:.4f}  [{section}]")
        out.append(f"      📄 {source}")
        out.append(f"      “{snippet}”")
    return out


def _probe_query(edital_id: str, query: str, k: int) -> None:
    """Roda os 3 modos pra uma query e imprime resultados lado a lado."""
    print()
    print("═" * 100)
    print(f"❓ {query}")
    print("═" * 100)

    from core.retriever import DEFAULT_FTS_WEIGHT
    modes = [
        ("DENSE PURO  (fts_weight=0.0)", 0.0),
        ("FTS PURO    (fts_weight=1.0)", 1.0),
        (f"HÍBRIDO RRF (fts_weight={DEFAULT_FTS_WEIGHT}, default)", DEFAULT_FTS_WEIGHT),
    ]

    for label, fts_weight in modes:
        print(f"\n── {label} ──")
        try:
            # db=None porque o retriever ignora o argumento (usa psycopg direto).
            results = retrieve_chunks(db=None, edital_id=edital_id, query=query,
                                      k=k, fts_weight=fts_weight)
        except Exception as e:
            print(f"  ERRO: {e}")
            continue
        for line in _render_results(results):
            print(line)


def _load_queries(args: argparse.Namespace) -> list[str]:
    if args.query:
        return [args.query]
    if args.queries_file:
        path = Path(args.queries_file)
        return [ln.strip() for ln in path.read_text(encoding="utf-8").splitlines()
                if ln.strip() and not ln.startswith("#")]
    return list(_DEFAULT_QUERIES)


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                      formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--edital", default="768",
                        help="ID do edital (default: 768, o maior do corpus)")
    parser.add_argument("--k", type=int, default=5, help="Top-k por modo (default: 5)")
    parser.add_argument("--query", help="Query única (sobrepõe defaults)")
    parser.add_argument("--queries-file", help="Caminho pra arquivo com 1 query por linha")
    args = parser.parse_args(list(argv) if argv is not None else None)

    queries = _load_queries(args)
    print(f"🔍 Probe RAG — edital={args.edital}, top-k={args.k}, queries={len(queries)}")
    for q in queries:
        _probe_query(args.edital, q, args.k)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
