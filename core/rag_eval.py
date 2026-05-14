"""
Métricas para avaliação do RAG (retrieval-side).

Módulo *puro* exceto pela função `judge_faithfulness`, que faz chamada à
OpenAI (LLM judge style RAGAS). Todas as outras funções recebem listas e
devolvem números — sem DB, sem rede.

Modelo de dados
---------------
Um "chunk recuperado" é o dict emitido por `core.retriever.retrieve_chunks`:
    {id, chunk_index, text, section, source_file, page_range, score}

Um "chunk esperado" no golden é só `{source_file, section}` — comparação
loose-section opcional (vide `_matches`).

Métricas
--------
- Recall@K:   hit se algum dos top-K chunks recuperados casa com algum
              chunk esperado. Mean over queries.
- MRR:        1/rank do primeiro chunk recuperado que casa. 0 se sem hit. Mean.
- Faithfulness lite: LLM judge 0-5 sobre se os chunks recuperados permitem
              responder à query (sem rodar o writer — só context relevance).
- Latency P50/P95: percentil da latência por query.
- NULL rate:  % de queries onde retrieve_chunks devolveu lista vazia.
"""
from __future__ import annotations

import logging
import statistics
from typing import Iterable

logger = logging.getLogger(__name__)

DEFAULT_KS: tuple[int, ...] = (1, 3, 5)


# =============================================================================
# Matching: chunk recuperado × esperado
# =============================================================================

def _matches(chunk: dict, expected: dict) -> bool:
    """Um chunk recuperado casa com um esperado se:

    - `source_file` é igual (sempre obrigatório), E
    - `expected.section is None`  (any-chunk-from-this-pdf é aceito), OU
    - `expected.section == chunk.section` (igualdade estrita).

    Section comparison é case-sensitive e por igualdade exata — o usuário
    do golden deve copiar o texto exato como aparece em `edital_chunks.section`.
    """
    if chunk.get("source_file") != expected.get("source_file"):
        return False
    expected_section = expected.get("section")
    if expected_section is None:
        return True
    return chunk.get("section") == expected_section


def _any_match(chunk: dict, expected_list: list[dict]) -> bool:
    """True se `chunk` casa com algum item de `expected_list`."""
    return any(_matches(chunk, e) for e in expected_list)


# =============================================================================
# Métricas por query
# =============================================================================

def hit_at_k(retrieved: list[dict], expected: list[dict], k: int) -> bool:
    """True se algum dos `k` primeiros chunks recuperados casa com algum esperado."""
    if k <= 0 or not retrieved or not expected:
        return False
    return any(_any_match(chunk, expected) for chunk in retrieved[:k])


def reciprocal_rank(retrieved: list[dict], expected: list[dict]) -> float:
    """1 / rank do primeiro hit. 0.0 se nenhum chunk casa."""
    if not retrieved or not expected:
        return 0.0
    for rank, chunk in enumerate(retrieved, start=1):
        if _any_match(chunk, expected):
            return 1.0 / rank
    return 0.0


def evaluate_query(
    retrieved: list[dict],
    expected: list[dict],
    ks: Iterable[int] = DEFAULT_KS,
) -> dict:
    """Retorna o dict de métricas retrieval-side pra uma única query.

    NÃO inclui faithfulness nem latency — esses são calculados externamente
    e injetados antes de `aggregate_runs`.
    """
    ks_list = list(ks)
    return {
        "hit_at_k": {str(k): hit_at_k(retrieved, expected, k) for k in ks_list},
        "reciprocal_rank": reciprocal_rank(retrieved, expected),
        "n_retrieved": len(retrieved),
        "null_result": len(retrieved) == 0,
    }


# =============================================================================
# Agregação
# =============================================================================

def _percentile(sorted_values: list[float], pct: float) -> float:
    """Percentil com interpolação linear. `sorted_values` ASCENDENTE."""
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    rank = (pct / 100.0) * (len(sorted_values) - 1)
    lo = int(rank)
    hi = min(lo + 1, len(sorted_values) - 1)
    frac = rank - lo
    return sorted_values[lo] + frac * (sorted_values[hi] - sorted_values[lo])


def aggregate_runs(
    per_query: list[dict],
    ks: Iterable[int] = DEFAULT_KS,
) -> dict:
    """Agrega métricas across queries.

    Cada item de `per_query` deve conter as chaves de `evaluate_query`
    (`hit_at_k`, `reciprocal_rank`, `null_result`) e opcionalmente
    `latency_ms` e `faithfulness`. Campos faltantes são ignorados —
    se nenhuma query tem `faithfulness`, o agregado omite o campo.
    """
    if not per_query:
        return {}

    n = len(per_query)
    out: dict = {"n_queries": n}

    for k in ks:
        kstr = str(k)
        hits = sum(1 for q in per_query if q.get("hit_at_k", {}).get(kstr))
        out[f"recall_at_{k}"] = hits / n

    rrs = [q.get("reciprocal_rank", 0.0) for q in per_query]
    out["mrr"] = sum(rrs) / n

    nulls = sum(1 for q in per_query if q.get("null_result"))
    out["null_rate"] = nulls / n

    latencies = sorted(q["latency_ms"] for q in per_query if "latency_ms" in q)
    if latencies:
        out["latency_p50_ms"] = _percentile(latencies, 50)
        out["latency_p95_ms"] = _percentile(latencies, 95)
        out["latency_mean_ms"] = statistics.mean(latencies)

    faiths = [q["faithfulness"] for q in per_query if "faithfulness" in q]
    if faiths:
        out["faithfulness_mean"] = statistics.mean(faiths)
        out["faithfulness_n"] = len(faiths)

    return out


# =============================================================================
# Faithfulness — LLM judge (Context Relevance, estilo RAGAS)
# =============================================================================
# Modelo deliberadamente fixo (gpt-4o-mini) — eval só é comparável entre runs
# se o juiz não mudar. NÃO consulta LLM_BACKEND. Se mudar pra outro modelo,
# isole numa nova "rodada de calibração" com tag explícita no resultado.

_FAITHFULNESS_MODEL = "gpt-4o-mini"
_FAITHFULNESS_SYSTEM = (
    "Você é um avaliador imparcial de sistemas de busca em editais públicos. "
    "Sua tarefa é decidir se trechos recuperados (retrieved) permitem responder "
    "à pergunta — não se a resposta está certa, e sim se os trechos a contêm."
)
_FAITHFULNESS_PROMPT = """PERGUNTA: {query}

TRECHOS RECUPERADOS:
{chunks}

Avalie de 0 a 5 se os trechos contêm informação suficiente para responder à pergunta:
- 0: trechos não tratam do tema.
- 1-2: tangenciam mas não respondem.
- 3: permitem resposta parcial.
- 4: permitem responder com pequena lacuna.
- 5: contêm resposta completa e direta.

Responda APENAS com o número, sem texto adicional."""


def _format_chunks_for_judge(chunks: list[dict], max_chars: int = 1500) -> str:
    """Renderiza chunks pro prompt do juiz. Trunca cada texto pra max_chars."""
    blocks = []
    for i, c in enumerate(chunks, start=1):
        section = c.get("section") or "—"
        src = c.get("source_file") or "?"
        text = (c.get("text") or "").strip()[:max_chars]
        blocks.append(f"[Trecho {i} | {src} | {section}]\n{text}")
    return "\n\n---\n\n".join(blocks)


def _parse_faithfulness_score(raw: str) -> float:
    """Extrai o primeiro dígito 0-5 da resposta do juiz. 0.0 se não encontrar."""
    for char in raw:
        if char in "012345":
            return float(char)
    return 0.0


def judge_faithfulness(query: str, retrieved_chunks: list[dict]) -> float:
    """LLM-judge (gpt-4o-mini) sobre context relevance dos chunks recuperados.

    Retorna 0-5. Se a chamada falhar OU se chunks=[], retorna 0.0 — eval
    nunca quebra por causa do juiz. Falhas são logadas em WARNING.

    Custo aproximado: ~$0.0003 por chamada (gpt-4o-mini, ~2k tokens in,
    1 token out). Pra um golden de 30 queries: ~$0.01/run.
    """
    if not retrieved_chunks:
        return 0.0
    if not query or not query.strip():
        return 0.0

    try:
        # Import lazy — eval pode rodar sem openai instalado se faithfulness
        # for desativado.
        import os

        from openai import OpenAI
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            logger.warning("judge_faithfulness: OPENAI_API_KEY ausente — retornando 0.0")
            return 0.0
        client = OpenAI(api_key=api_key)
        prompt = _FAITHFULNESS_PROMPT.format(
            query=query, chunks=_format_chunks_for_judge(retrieved_chunks),
        )
        resp = client.chat.completions.create(
            model=_FAITHFULNESS_MODEL,
            messages=[
                {"role": "system", "content": _FAITHFULNESS_SYSTEM},
                {"role": "user", "content": prompt},
            ],
            max_tokens=4,
            temperature=0.0,
        )
        raw = (resp.choices[0].message.content or "").strip()
        return _parse_faithfulness_score(raw)
    except Exception as e:
        logger.warning("judge_faithfulness falhou: %s", e)
        return 0.0


__all__ = [
    "DEFAULT_KS",
    "aggregate_runs",
    "evaluate_query",
    "hit_at_k",
    "judge_faithfulness",
    "reciprocal_rank",
]
