"""
core/reranker.py — Estágio de rerank do RAG de escrita (Front 4).

Entre a fusão RRF e o corte top-k, reordenamos um pool maior de candidatos por
relevância à query. O RRF é bom em recall mas fraco em queries sutis: dois
chunks com o mesmo conjunto de termos recebem rank parecido mesmo que só um
responda à pergunta. Um reranker (par query↔passagem) corrige isso.

Backends (env `RERANK_BACKEND`):
  • "cross-encoder" (default) — modelo local de reranking multilíngue. Sem
    custo de API, baixa latência (CPU), mas dep pesada (sentence-transformers
    + torch). Lazy-load: só importa quando efetivamente chamado.
  • "llm" — gpt-4o-mini com prompt de scoring. Sem infra nova, mais caro/lento;
    fallback configurável.
  • "off" — desliga; o caller mantém a ordem RRF.

Contrato: `rerank_scores(query, texts)` devolve uma lista de scores ∈ [0,1]
(um por texto, na MESMA ordem) OU `None` quando o rerank está indisponível
(dep ausente, modelo não carrega, API falha). `None` é o sinal de degradação
graciosa — o caller volta para a ordem RRF pura sem quebrar o turno.

Decisão do modelo cross-encoder (questão aberta #3 da spec): default
`cross-encoder/mmarco-mMiniLMv2-L12-H384-v1` — MiniLM multilíngue treinado no
mMARCO (inclui português), ~118M params, leve o suficiente para rodar em CPU
por turno. Sobrescrevível via `RERANK_MODEL`.
"""
from __future__ import annotations

import logging
import math
import os

logger = logging.getLogger(__name__)

DEFAULT_BACKEND = os.getenv("RERANK_BACKEND", "cross-encoder")
CROSS_ENCODER_MODEL = os.getenv(
    "RERANK_MODEL", "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1",
)

# Cache de processo do modelo carregado (carga é cara: pesos + torch).
_model_cache: dict[str, object] = {}


def _sigmoid(x: float) -> float:
    # Clamp para evitar overflow em logits extremos.
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


def _get_cross_encoder():
    """Lazy-load do CrossEncoder, cacheado por processo.

    Levanta ImportError se sentence-transformers não estiver instalado — o
    caller (`rerank_scores`) captura e degrada para None.
    """
    cached = _model_cache.get("cross_encoder")
    if cached is not None:
        return cached
    from sentence_transformers import CrossEncoder  # lazy: dep pesada
    logger.info("reranker: carregando cross-encoder %s", CROSS_ENCODER_MODEL)
    model = CrossEncoder(CROSS_ENCODER_MODEL)
    _model_cache["cross_encoder"] = model
    return model


def _rerank_cross_encoder(query: str, texts: list[str]) -> list[float]:
    model = _get_cross_encoder()
    pairs = [[query, t] for t in texts]
    # predict devolve logits (1 por par); sigmoid normaliza para [0,1].
    raw = model.predict(pairs)
    return [_sigmoid(float(s)) for s in raw]


_LLM_RERANK_SYSTEM = (
    "Você é um reranker de passagens. Dada uma pergunta e uma lista numerada de "
    "passagens, atribua a cada passagem um score de relevância de 0 a 10 (10 = "
    "responde diretamente à pergunta; 0 = irrelevante). Responda APENAS com JSON "
    "válido no formato {\"scores\": [{\"i\": <índice>, \"s\": <0-10>}, ...]}."
)


def _rerank_llm(query: str, texts: list[str]) -> list[float]:
    import json
    import re

    from core.llm.llm_client import make_client

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY ausente para RERANK_BACKEND=llm")
    client = make_client(api_key=api_key)
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

    numbered = "\n\n".join(
        f"[{i}] {t[:1000]}" for i, t in enumerate(texts)
    )
    user = f"PERGUNTA:\n{query}\n\nPASSAGENS:\n{numbered}"
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": _LLM_RERANK_SYSTEM},
            {"role": "user", "content": user},
        ],
        temperature=0.0,
        max_tokens=600,
    )
    raw = response.choices[0].message.content.strip()
    if "```" in raw:
        raw = re.sub(r"```(?:json)?", "", raw).strip().rstrip("`").strip()
    data = json.loads(raw)
    scores = [0.0] * len(texts)
    for item in data.get("scores", []):
        idx = int(item.get("i", -1))
        if 0 <= idx < len(texts):
            scores[idx] = max(0.0, min(10.0, float(item.get("s", 0)))) / 10.0
    return scores


def rerank_scores(
    query: str,
    texts: list[str],
    backend: str | None = None,
) -> list[float] | None:
    """Score de relevância ∈ [0,1] por texto, na ordem de entrada.

    Retorna None quando o rerank está indisponível ou desligado — sinal para
    o caller manter a ordenação RRF (degradação graciosa, nunca quebra o turno).
    """
    if not query or not texts:
        return None
    backend = (backend or DEFAULT_BACKEND).lower()
    if backend in ("off", "none", "disabled", ""):
        return None
    try:
        if backend in ("cross-encoder", "cross_encoder", "ce"):
            return _rerank_cross_encoder(query, texts)
        if backend == "llm":
            return _rerank_llm(query, texts)
        logger.warning("reranker: RERANK_BACKEND desconhecido %r — desligando", backend)
        return None
    except ImportError as e:
        logger.warning(
            "reranker: dep do cross-encoder ausente (%s) — instale o extra "
            "`rerank` ou use RERANK_BACKEND=llm/off. Caindo para RRF puro.", e,
        )
        return None
    except Exception as e:
        logger.warning("reranker: backend %s falhou (%s) — caindo para RRF puro.", backend, e)
        return None


__all__ = ["rerank_scores"]
