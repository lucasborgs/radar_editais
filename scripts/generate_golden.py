#!/usr/bin/env python3
"""
Gera um golden dataset pra eval do RAG via LLM.

Para cada edital indicado, lê chunks da tabela `edital_chunks`, faz amostragem
diversa (por source_file + section quando possível), e pede pro GPT-4o-mini
gerar uma pergunta natural que aquele chunk responde. Resultado fica em
`eval_data/golden/<source>.json` no formato consumido pela suíte rag
(`python -m core.eval rag`).

Uso:
    python scripts/generate_golden.py --source finep --editais 768 762 743
    python scripts/generate_golden.py --source finep --editais 768 --per-edital 5
    python scripts/generate_golden.py --source finep --append   # acumula sem sobrescrever

Custo (gpt-4o-mini): ~$0.0005 por query gerada. 24 queries ≈ $0.01.

Próximo passo após rodar: ABRA O JSON e EDITE. O LLM pode gerar query trivial
("o que diz o item 1.4?") ou específica demais — revisar a mão é onde o golden
fica realmente útil. A revisão é o passo crítico, não a geração.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

import psycopg  # noqa: E402

from core.llm.llm_client import make_client  # noqa: E402

GOLDEN_DIR = ROOT / "eval_data" / "golden"
DEFAULT_PER_EDITAL = 8
DEFAULT_MODEL = "gpt-4o-mini"


# =============================================================================
# Sampling — escolher chunks diversos pra virar queries
# =============================================================================

def _fetch_chunks(edital_id: str) -> list[dict]:
    """Lê todos os chunks de um edital. Retorna lista de dicts."""
    dsn = os.environ["DATABASE_URL"]
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT chunk_index, text, section, source_file
              FROM public.edital_chunks
             WHERE edital_id = %s
             ORDER BY chunk_index
            """,
            (edital_id,),
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row, strict=True)) for row in cur.fetchall()]


def _sample_diverse(chunks: list[dict], n: int, seed: int = 42) -> list[dict]:
    """Amostra `n` chunks priorizando diversidade de (source_file, section).

    Estratégia:
        1. Embaralha chunks (com seed pra reproducibilidade).
        2. Caminha tomando o primeiro chunk de cada (source_file, section) único.
        3. Se faltar pra completar `n`, completa do restante na ordem embaralhada.
    """
    if n >= len(chunks):
        return list(chunks)

    rng = random.Random(seed)
    shuffled = list(chunks)
    rng.shuffle(shuffled)

    seen_combos: set[tuple] = set()
    primary: list[dict] = []
    backup: list[dict] = []
    for c in shuffled:
        combo = (c.get("source_file"), c.get("section"))
        if combo in seen_combos:
            backup.append(c)
        else:
            seen_combos.add(combo)
            primary.append(c)

    selected = primary[:n]
    if len(selected) < n:
        selected.extend(backup[: n - len(selected)])
    return selected


# =============================================================================
# LLM — geração de query a partir de um chunk
# =============================================================================

_QUERY_GEN_SYSTEM = (
    "Você é um assistente que ajuda a montar perguntas de avaliação para um sistema "
    "de busca em editais públicos brasileiros. Receberá um TRECHO de edital e deve "
    "produzir UMA pergunta em português que um candidato a submeter proposta faria "
    "e que esse trecho específico responde."
)

_QUERY_GEN_PROMPT = """TRECHO:
{text}

Regras:
- Pergunte como o candidato perguntaria em linguagem natural (não copie expressões raras do trecho).
- A pergunta deve ser ESPECÍFICA o suficiente para que esse trecho seja necessário (não genérico tipo "do que se trata o edital?").
- Não use jargão fora do que o candidato usaria.
- Apenas 1 pergunta. Sem preâmbulo, sem aspas, sem "Pergunta:".

Pergunta:"""


def _generate_query(client: Any, model: str, chunk: dict) -> str:
    """Pede UMA pergunta natural que o chunk responde."""
    text = (chunk.get("text") or "").strip()[:3000]
    if not text:
        return ""
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": _QUERY_GEN_SYSTEM},
            {"role": "user", "content": _QUERY_GEN_PROMPT.format(text=text)},
        ],
        max_tokens=80,
        temperature=0.7,  # alguma variabilidade — não queremos 24 perguntas idênticas
    )
    return (resp.choices[0].message.content or "").strip()


# =============================================================================
# Category — chuta a categoria a partir da section (heurística simples)
# =============================================================================

_CATEGORY_HINTS = [
    ("eligibility", ("elegibili", "proponente", "executora")),
    ("financial", ("recurso", "valor", "financeir", "subvenc", "rnr")),
    ("expenses", ("despesa", "apoiáveis", "apoiav")),
    ("criteria", ("critério", "criterio", "avaliação", "avaliacao", "mérito")),
    ("timeline", ("cronograma", "prazo", "submissão", "submissao")),
    ("objective", ("objetivo", "finalidade")),
    ("definitions", ("definiç", "definic", "conceito")),
]


def _infer_category(section: str | None, text: str) -> str:
    haystack = ((section or "") + " " + (text or "")).lower()
    for cat, keywords in _CATEGORY_HINTS:
        if any(k in haystack for k in keywords):
            return cat
    return "other"


# =============================================================================
# Pipeline principal
# =============================================================================

def _build_entry(source: str, edital_id: str, idx: int, chunk: dict, query: str) -> dict:
    """Monta um item do golden no formato consumido pela suíte rag."""
    return {
        "id": f"{source}_{edital_id}_q{idx}",
        "edital_id": edital_id,
        "query": query,
        "expected": [{
            "source_file": chunk.get("source_file"),
            "section": chunk.get("section"),
        }],
        # gold_text: passagem-fonte (texto do chunk que responde a query). Serve
        # de gabarito CHUNKING-INVARIANTE — token-recall/IoU (estilo Chroma) mede
        # cobertura desta passagem por QUALQUER estratégia de chunk, sem depender
        # da label `section` (que muda a cada re-chunk). Capado p/ arquivo enxuto.
        "gold_text": (chunk.get("text") or "").strip()[:1500],
        "category": _infer_category(chunk.get("section"), chunk.get("text") or ""),
        "_source_chunk_index": chunk.get("chunk_index"),  # rastreio pra revisão
    }


def _load_existing(path: Path) -> dict | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                      formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", required=True,
                        help="Nome da fonte (ex.: finep, fapesp)")
    parser.add_argument("--editais", nargs="+", required=True,
                        help="IDs dos editais (ex.: 768 762 743)")
    parser.add_argument("--per-edital", type=int, default=DEFAULT_PER_EDITAL,
                        help=f"Quantas queries gerar por edital (default: {DEFAULT_PER_EDITAL})")
    parser.add_argument("--model", default=DEFAULT_MODEL,
                        help=f"Modelo OpenAI (default: {DEFAULT_MODEL})")
    parser.add_argument("--append", action="store_true",
                        help="Concatena no golden existente; default é sobrescrever")
    parser.add_argument("--seed", type=int, default=42,
                        help="Seed pra amostragem (reproducibilidade)")
    args = parser.parse_args()

    if not os.environ.get("OPENAI_API_KEY"):
        print("ERRO: OPENAI_API_KEY ausente", file=sys.stderr)
        return 2

    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    out_path = GOLDEN_DIR / f"{args.source}.json"

    client = make_client()
    existing = _load_existing(out_path) if args.append else None
    existing_queries = list(existing["queries"]) if existing else []
    start_idx = len(existing_queries) + 1

    new_queries: list[dict] = []
    idx = start_idx
    for edital_id in args.editais:
        chunks = _fetch_chunks(edital_id)
        if not chunks:
            print(f"  ! edital {edital_id} sem chunks — pulando", file=sys.stderr)
            continue
        sample = _sample_diverse(chunks, args.per_edital, seed=args.seed)
        print(f"[gen] edital={edital_id}: {len(sample)} chunks selecionados de {len(chunks)}")

        for chunk in sample:
            try:
                q = _generate_query(client, args.model, chunk)
            except Exception as e:
                print(f"  ! falha LLM no chunk {chunk['chunk_index']}: {e}", file=sys.stderr)
                continue
            if not q:
                continue
            entry = _build_entry(args.source, edital_id, idx, chunk, q)
            new_queries.append(entry)
            print(f"  + {entry['id']} ({entry['category']}): {q[:100]}")
            idx += 1

    if not new_queries and not existing_queries:
        print("Nenhuma query gerada.", file=sys.stderr)
        return 1

    out = {
        "source": args.source,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generator": args.model,
        "queries": existing_queries + new_queries,
    }
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print()
    print(f"[gen] gravado em {out_path}")
    print(f"[gen] total no golden: {len(out['queries'])} ({len(new_queries)} novas)")
    print("[gen] REVISE o JSON antes de rodar `python -m core.eval rag` — algumas queries podem estar")
    print("[gen] triviais ou específicas demais. Editar/deletar a mão é parte do processo.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
