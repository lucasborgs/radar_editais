"""
Embeddings — parametrizável por env (default: OpenAI text-embedding-3-large, 1536 dims).

Historicamente embeddings eram OpenAI-only e o modelo era hardcoded (ADR A1/A3).
O bake-off de modelos (docs/specs/llm-embedding-bakeoff.md) exige trocar o modelo
por env para avaliar substitutos open-weight (Qwen3-Embedding, BGE-M3) sem editar
código. Os defaults preservam exatamente o comportamento anterior: sem nenhuma env
setada, isto embeda com text-embedding-3-large no endpoint canônico da OpenAI,
dim 1536.

Envs (todas opcionais):
    EMBEDDING_MODEL       nome do modelo            (default: text-embedding-3-large)
    EMBEDDING_DIMENSIONS  dimensão do vetor         (default: 1536)
    EMBEDDING_BASE_URL    endpoint OpenAI-compat    (default: canônico OpenAI)
    EMBEDDING_API_KEY     key do provider           (default: OPENAI_API_KEY)

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

Pattern mirrors core.services.content_library._make_client() but uses the embeddings
endpoint instead of chat.
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

# Lidas no import. CLIs de eval chamam load_dotenv() antes dos imports, então as
# envs já estão no ambiente quando este módulo carrega.
EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "text-embedding-3-large")
EMBEDDING_DIMENSIONS = int(os.environ.get("EMBEDDING_DIMENSIONS", "1536"))
EMBEDDING_BASE_URL = os.environ.get("EMBEDDING_BASE_URL") or None
_BATCH_LIMIT = 2048  # OpenAI per-call input limit


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
        # Servidores locais (Ollama/vLLM) aceitam qualquer key não-vazia.
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


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed a batch of texts. Returns one EMBEDDING_DIMENSIONS-dim vector per input.

    - Empty input → empty output, no API call.
    - Strings are truncated input-side by the provider tokenizer.
    - Batches larger than _BATCH_LIMIT (2048) are split transparently.
    - Errors propagate so the caller (e.g. procrastinate task) can retry.
    """
    if not texts:
        return []

    # Defensive: replace empty/None entries with a single space so the API
    # call doesn't reject the whole batch. The caller should ideally filter
    # these out, but we don't want a typo upstream to nuke an indexing run.
    cleaned = [(t if (t and t.strip()) else " ") for t in texts]

    client = _make_embeddings_client()
    out: list[list[float]] = []
    for start in range(0, len(cleaned), _BATCH_LIMIT):
        batch = cleaned[start:start + _BATCH_LIMIT]
        response = client.embeddings.create(**_create_kwargs(batch))
        # response.data is ordered the same as input — preserve order.
        out.extend([item.embedding for item in response.data])

    return out


def embed_query(text: str) -> list[float]:
    """Embed a single query string. Returns an EMBEDDING_DIMENSIONS-dim vector."""
    if not text or not text.strip():
        # Caller error — but rather than raising, return a zero vector so a
        # retrieval call against an empty query degrades to "no signal".
        return [0.0] * EMBEDDING_DIMENSIONS
    vectors = embed_texts([text])
    return vectors[0] if vectors else [0.0] * EMBEDDING_DIMENSIONS
