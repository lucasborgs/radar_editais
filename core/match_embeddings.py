"""Stage 2a por embeddings — cosseno(perfil × edital), determinístico.

Caminho ALTERNATIVO ao Stage 2a generativo-LLM de `core/hybrid_match_service.py`,
ligado pela flag `MATCH_STAGE2A_BACKEND=embeddings` (default `llm` — NÃO promovido;
gated por experimento na suíte `matching`, ver docs/BACKLOG.md "Matching Stage 2a").

Princípios:
  • Summary-level (ADR M9): embeda `title`+`objective`+`themes` do edital × o pitch
    do perfil. NÃO usa chunks nem importa `core/retriever.py` (acoplamento proibido).
  • Determinístico, zero token/request após cache. Cache file-based em
    `.match_embedding_cache.json` (espelha `.enrichment_cache.json`), chaveado por
    hash(modelo+texto) — invalida sozinho se o texto ou o modelo mudar.
  • Cosseno em Python puro (sem numpy — não é dependência do projeto).
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import threading

from config import ROOT
from core.retrieval.embedder import EMBEDDING_MODEL, embed_texts

logger = logging.getLogger(__name__)

_CACHE_PATH = ROOT / ".match_embedding_cache.json"
_cache_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Textos a embeddar (o "botão sensível" — ver BACKLOG)
# ---------------------------------------------------------------------------

def edital_embedding_text(card: dict) -> str:
    """Texto summary-level do edital: título + objetivo + temas (cru)."""
    themes = card.get("themes") or card.get("eligible_sectors") or []
    parts = [
        card.get("title") or "",
        card.get("objective") or "",
        ", ".join(t for t in themes if t),
    ]
    return "\n".join(p for p in parts if p).strip()


def profile_embedding_text(profile) -> str:
    """Pitch do perfil: one_liner + solução + atividades (simétrico ao edital)."""
    parts = [
        getattr(profile, "one_liner", "") or "",
        getattr(profile, "solution_summary", "") or "",
        getattr(profile, "descricao_atividades", "") or "",
    ]
    return "\n".join(p for p in parts if p).strip()


# ---------------------------------------------------------------------------
# Cache + embed
# ---------------------------------------------------------------------------

def _key(text: str) -> str:
    """Chave de cache: hash do (modelo + texto). Modelo no namespace evita reuso
    de vetores de outro modelo se EMBEDDING_MODEL mudar."""
    h = hashlib.sha256(f"{EMBEDDING_MODEL}\x00{text}".encode())
    return h.hexdigest()


def _load_cache() -> dict[str, list[float]]:
    try:
        return json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_cache(cache: dict[str, list[float]]) -> None:
    try:
        _CACHE_PATH.write_text(json.dumps(cache), encoding="utf-8")
    except Exception as e:
        logger.warning("match_embeddings: falha ao gravar cache: %s", e)


def embed_with_cache(texts: list[str]) -> list[list[float]]:
    """Embeda `texts` preservando a ordem, reusando o cache file-based.

    Só os textos ausentes do cache vão para a API (em um único batch via
    `embed_texts`). Atualiza o cache atomicamente sob lock.
    """
    if not texts:
        return []
    with _cache_lock:
        cache = _load_cache()
        missing = [t for t in dict.fromkeys(texts) if _key(t) not in cache]
        if missing:
            vecs = embed_texts(missing)
            for t, v in zip(missing, vecs, strict=True):
                cache[_key(t)] = v
            _save_cache(cache)
        return [cache[_key(t)] for t in texts]


# ---------------------------------------------------------------------------
# Cosseno + scoring
# ---------------------------------------------------------------------------

def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def score_stage2a_embeddings(eligible, profile) -> dict[str, float]:
    """Pontuação temática `{id: score 0..10}` por cosseno(perfil × edital).

    Mesma assinatura/contrato de `_call_stage2_scores`: em falha total devolve {}
    (logado alto), e o ranking degrada para o Stage 1 — nunca silencioso.

    `eligible` é uma lista de `Stage1Result` (cada um com `.edital_id` e `.card`).
    """
    try:
        profile_text = profile_embedding_text(profile)
        if not profile_text:
            logger.error("Stage 2a (embeddings): perfil sem texto para embeddar — {}")
            return {}

        edital_texts = [edital_embedding_text(r.card) for r in eligible]
        # Perfil + editais em um único batch (perfil na posição 0).
        vectors = embed_with_cache([profile_text] + edital_texts)
        profile_vec, edital_vecs = vectors[0], vectors[1:]

        scores: dict[str, float] = {}
        for r, vec in zip(eligible, edital_vecs, strict=True):
            cos = _cosine(profile_vec, vec)
            # cos ∈ [-1, 1]; clampa em [0, 1] e escala para a faixa 0..10 do LLM.
            scores[r.edital_id] = round(max(0.0, min(1.0, cos)) * 10.0, 1)
        return scores
    except Exception as e:
        logger.error("Stage 2a (embeddings) falhou: %s", e)
        return {}


__all__ = [
    "edital_embedding_text",
    "profile_embedding_text",
    "embed_with_cache",
    "score_stage2a_embeddings",
]
