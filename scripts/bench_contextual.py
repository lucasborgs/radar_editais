#!/usr/bin/env python3
"""
Bake-off Contextual Retrieval (Anthropic) vs baseline — FINEP.

Para cada chunk, gera por LLM um contexto curto que o situa no documento e o
PREPENDA antes de embeddar (boundaries inalteradas → sem o viés do gold_text,
que é um chunk do baseline). Mede gold_recall/best_chunk: contextualizado vs cru.

Metodologicamente limpo: ambos os braços usam os MESMOS chunks (baseline); só o
EMBEDDING muda. Score = corpo original; embedding = contexto+corpo.

Uso: python scripts/bench_contextual.py
"""
from __future__ import annotations

import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv  # noqa: E402

load_dotenv()

import psycopg  # noqa: E402

from core.llm_client import make_client  # noqa: E402
from scripts.bench_parsing import _eval_arm  # noqa: E402

EDITAIS = ["768", "762", "743"]
GOLDEN = ROOT / "eval_data" / "golden" / "finep.json"
_MODEL = "gpt-4o-mini"
_CTX_PROMPT = (
    "<documento>\n{doc}\n</documento>\n\n"
    "Aqui está um trecho do documento:\n<trecho>\n{chunk}\n</trecho>\n\n"
    "Dê um contexto curto (1-2 frases) que situe este trecho no documento "
    "(qual seção/assunto, de qual edital), para melhorar a busca. Responda "
    "APENAS com o contexto, sem preâmbulo."
)


def _baseline_chunks(native: str) -> list[dict]:
    with psycopg.connect(os.environ["DATABASE_URL"]) as c, c.cursor() as cur:
        cur.execute(
            "SELECT text, section, source_file FROM public.edital_chunks "
            "WHERE edital_id = %s ORDER BY chunk_index",
            (f"finep:{native}",),
        )
        return [{"text": t, "section": s, "source_file": sf} for t, s, sf in cur.fetchall()]


def _contextualize(chunks: list[dict]) -> list[dict]:
    """Adiciona `embed_text` = contexto(LLM) + corpo a cada chunk. `text` (corpo)
    fica intacto para o score. Doc = concat dos corpos (truncado)."""
    client = make_client(api_key=os.environ["OPENAI_API_KEY"])
    doc = "\n\n".join(c["text"] for c in chunks)[:12000]

    def ctx(chunk: dict) -> dict:
        try:
            r = client.chat.completions.create(
                model=_MODEL, max_tokens=80, temperature=0.0,
                messages=[{"role": "user",
                           "content": _CTX_PROMPT.format(doc=doc, chunk=chunk["text"][:1500])}],
            )
            context = (r.choices[0].message.content or "").strip()
        except Exception:
            context = ""
        return {**chunk, "embed_text": f"{context}\n\n{chunk['text']}" if context else chunk["text"]}

    with ThreadPoolExecutor(max_workers=8) as ex:
        return list(ex.map(ctx, chunks))


def main() -> int:
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    queries = [q for q in golden["queries"] if q["edital_id"].split(":")[-1] in EDITAIS]
    print(f"bake-off Contextual: {len(queries)} queries em {EDITAIS}")

    base = {e: _baseline_chunks(e) for e in EDITAIS}
    print(f"contextualizando {sum(len(v) for v in base.values())} chunks via LLM…", flush=True)
    ctx = {e: _contextualize(ch) for e, ch in base.items()}

    m_base = _eval_arm("BASELINE (embed cru)", base, queries)
    m_ctx = _eval_arm("CONTEXTUAL (embed contexto+corpo)", ctx, queries)

    print("\n=== DELTA (contextual - baseline) ===")
    for m in m_base:
        print(f"  {m:<32} {round(m_ctx[m] - m_base[m], 4):+}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
