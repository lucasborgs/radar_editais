"""
Embeddings — parametrizável por env (default: OpenAI text-embedding-3-large, 1536 dims).

Historicamente embeddings eram OpenAI-only e o modelo era hardcoded (ADR A1/A3).
O bake-off de modelos (docs/specs/llm-embedding-bakeoff.md) exige trocar o modelo
por env para avaliar substitutos open-weight (Qwen3-Embedding, BGE-M3) sem editar
código. Os defaults preservam exatamente o comportamento anterior: sem nenhuma env
setada, isto embeda com text-embedding-3-large no endpoint canônico da OpenAI,
dim 1536.

Envs (todas opcionais):
    EMBEDDING_BACKEND     "openai" (default) ou "sentence_transformers" (HF in-process)
    EMBEDDING_MODEL       nome do modelo            (default: text-embedding-3-large)
    EMBEDDING_DIMENSIONS  dimensão do vetor         (default: 1536)
    EMBEDDING_BASE_URL    endpoint OpenAI-compat    (default: canônico OpenAI)
    EMBEDDING_API_KEY     key do provider           (default: OPENAI_API_KEY)
    ST_DEVICE             device para sentence-transformers: "cpu", "mps", etc.
                          MPS causa deadlock com alguns modelos → use "cpu" nesses casos.

Gotchas que importam:
  • O parâmetro `dimensions` da API só existe em modelos text-embedding-3 da OpenAI
    (truncagem Matryoshka). Providers open-weight servidos via OpenAI-compat (Ollama,
    vLLM) tipicamente REJEITAM esse parâmetro — então só o enviamos no endpoint
    canônico (EMBEDDING_BASE_URL não setada). Lá o modelo fixa sua própria dimensão.
  • Re-indexar prod com outro modelo é caro/irreversível e a coluna
    `edital_chunks.embedding` é vector(1536) — trocar de modelo (esp. dims ≠ 1536)
    exige coluna-sombra/migração. Por isso o bake-off avalia OFFLINE em numpy antes
    (ver scripts/eval_embedding_offline.py): nenhuma re-indexação até bater o baseline.
  • `EMBEDDING_MODEL` entra no namespace do cache de match_embeddings — trocar o
    modelo invalida o cache automaticamente (não há reuso cross-modelo).
  • Backend sentence_transformers: modelos assimétricos exigem prefixos diferentes
    por papel (query vs passagem). Prefixos vêm de _ST_REGISTRY; modelo não listado
    → sem prefixo (simétrico). Bake-off lê ST_DEVICE=cpu para evitar deadlock MPS.

Pattern mirrors core.services.content_library._make_client() but uses the embeddings
endpoint instead of chat.
"""
from __future__ import annotations

import logging
import os
from collections.abc import Callable

logger = logging.getLogger(__name__)

# Lidas no import. CLIs de eval chamam load_dotenv() antes dos imports, então as
# envs já estão no ambiente quando este módulo carrega.
EMBEDDING_BACKEND = os.environ.get("EMBEDDING_BACKEND", "openai").lower()
EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "text-embedding-3-large")
EMBEDDING_DIMENSIONS = int(os.environ.get("EMBEDDING_DIMENSIONS", "1536"))
EMBEDDING_BASE_URL = os.environ.get("EMBEDDING_BASE_URL") or None
_BATCH_LIMIT = 2048  # OpenAI per-call input limit

# Prefixos por papel para modelos HF assimétricos. Espelha o _ST_REGISTRY do
# scripts/eval_embedding_offline.py — fonte única de verdade aqui; o script offline
# importa daqui para evitar divergência.
_ST_REGISTRY: dict[str, dict] = {
    "intfloat/multilingual-e5-large": {
        "query_prefix": "query: ",
        "passage_prefix": "passage: ",
    },
    "intfloat/multilingual-e5-large-instruct": {
        "query_prefix": (
            "Instruct: Given a web search query, retrieve relevant passages "
            "that answer the query\nQuery: "
        ),
        "passage_prefix": "",
    },
    "jinaai/jina-embeddings-v2-base-pt": {
        "query_prefix": "",
        "passage_prefix": "",
        "trust_remote_code": True,
    },
    "tardellirs/embeddinggemma-pt-br": {
        "query_prefix": "task: search result | query: ",
        "passage_prefix": "title: none | text: ",
    },
}


# =============================================================================
# Backend: sentence_transformers (HF in-process)
# =============================================================================

_st_embed_texts: Callable[[list[str]], list[list[float]]] | None = None
_st_embed_query: Callable[[str], list[float]] | None = None


def _init_st_backend() -> None:
    """Carrega o modelo ST na primeira chamada (lazy). Thread-unsafe mas o eval
    é single-threaded, então não há risco de double-init."""
    global _st_embed_texts, _st_embed_query
    if _st_embed_texts is not None:
        return
    from sentence_transformers import SentenceTransformer
    cfg = _ST_REGISTRY.get(EMBEDDING_MODEL, {})
    qp: str = cfg.get("query_prefix", "")
    pp: str = cfg.get("passage_prefix", "")
    trust: bool = cfg.get("trust_remote_code", False)
    device = os.environ.get("ST_DEVICE") or None
    model = SentenceTransformer(EMBEDDING_MODEL, trust_remote_code=trust, device=device)

    def _embed_texts(texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        vecs = model.encode(
            [pp + t for t in texts],
            batch_size=32,
            show_progress_bar=False,
            normalize_embeddings=False,
        )
        return vecs.tolist()

    def _embed_query(text: str) -> list[float]:
        vec = model.encode(qp + text, normalize_embeddings=False)
        return vec.tolist()

    _st_embed_texts = _embed_texts
    _st_embed_query = _embed_query


# =============================================================================
# Backend: OpenAI-compat API
# =============================================================================

def _make_embeddings_client():
    """Return an OpenAI-compatible client for the embeddings endpoint.

    - Endpoint canônico OpenAI (sem EMBEDDING_BASE_URL): exige uma key
      (EMBEDDING_API_KEY ou OPENAI_API_KEY) — preserva a política A1.
    - Endpoint custom (EMBEDDING_BASE_URL setada, ex.: Ollama/vLLM local): a key
      é opcional; usamos um placeholder se nenhuma for fornecida, pois servidores
      OpenAI-compat locais ignoram a key.
    """
    from core.llm.llm_client import make_client
    api_key = os.environ.get("EMBEDDING_API_KEY") or os.environ.get("OPENAI_API_KEY")
    kwargs: dict = {}
    if EMBEDDING_BASE_URL:
        kwargs["base_url"] = EMBEDDING_BASE_URL
        api_key = api_key or "not-needed"
    elif not api_key:
        raise RuntimeError(
            "OPENAI_API_KEY/EMBEDDING_API_KEY não configurada — embeddings no "
            "endpoint canônico OpenAI exigem uma key (ADR A1)."
        )
    return make_client(api_key=api_key, **kwargs)


def _create_kwargs(batch: list[str]) -> dict:
    """Monta os kwargs da chamada embeddings.create.

    Só envia `dimensions` no endpoint canônico OpenAI (Matryoshka). Providers
    open-weight servidos via OpenAI-compat rejeitam esse parâmetro.
    """
    kwargs: dict = {"model": EMBEDDING_MODEL, "input": batch}
    if not EMBEDDING_BASE_URL:
        kwargs["dimensions"] = EMBEDDING_DIMENSIONS
    return kwargs


# =============================================================================
# Public API
# =============================================================================

def embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed a batch of texts. Returns one EMBEDDING_DIMENSIONS-dim vector per input.

    - Empty input → empty output, no API call.
    - Strings are truncated input-side by the provider tokenizer.
    - Batches larger than _BATCH_LIMIT (2048) are split transparently (OpenAI only).
    - Errors propagate so the caller (e.g. procrastinate task) can retry.
    """
    if not texts:
        return []

    if EMBEDDING_BACKEND == "sentence_transformers":
        _init_st_backend()
        assert _st_embed_texts is not None
        return _st_embed_texts(texts)

    # OpenAI-compat path (default).
    cleaned = [(t if (t and t.strip()) else " ") for t in texts]
    client = _make_embeddings_client()
    out: list[list[float]] = []
    for start in range(0, len(cleaned), _BATCH_LIMIT):
        batch = cleaned[start:start + _BATCH_LIMIT]
        response = client.embeddings.create(**_create_kwargs(batch))
        out.extend([item.embedding for item in response.data])
    return out


def embed_query(text: str) -> list[float]:
    """Embed a single query string. Returns an EMBEDDING_DIMENSIONS-dim vector."""
    if not text or not text.strip():
        return [0.0] * EMBEDDING_DIMENSIONS

    if EMBEDDING_BACKEND == "sentence_transformers":
        _init_st_backend()
        assert _st_embed_query is not None
        return _st_embed_query(text)

    vectors = embed_texts([text])
    return vectors[0] if vectors else [0.0] * EMBEDDING_DIMENSIONS
