"""
OpenAI embeddings — text-embedding-3-large (1536 dims).

ADR A1/A3: embeddings are OpenAI-only (Gemini is not used for this path).
The model is hardcoded; callers do NOT choose. If the project ever migrates
to a different embedding model, the dimension in migration 004 must be
updated in lockstep.

Pattern mirrors core.content_library._make_client() but uses the embeddings
endpoint instead of chat. We intentionally replicate the client construction
rather than import from content_library to avoid creating a coupling between
two unrelated subsystems.
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

EMBEDDING_MODEL = "text-embedding-3-large"
EMBEDDING_DIMENSIONS = 1536
_BATCH_LIMIT = 2048  # OpenAI per-call input limit


def _make_openai_client():
    """Return an OpenAI client authenticated for embeddings.

    Replicates the pattern from core.content_library._make_client but does NOT
    consider LLM_BACKEND — embeddings are always OpenAI by policy (A1).
    """
    from openai import OpenAI
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "OPENAI_API_KEY não configurada — embeddings exigem OpenAI (ADR A1)."
        )
    # text-embedding-3-large lives on the canonical OpenAI endpoint.
    return OpenAI(api_key=api_key)


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed a batch of texts. Returns one 1536-dim vector per input.

    - Empty input → empty output, no API call.
    - Strings are truncated input-side by the OpenAI tokenizer (max 8192 tokens
      per item, far above our chunk size).
    - Batches larger than _BATCH_LIMIT (2048) are split transparently.
    - Errors propagate so the caller (e.g. procrastinate task) can retry.
    """
    if not texts:
        return []

    # Defensive: replace empty/None entries with a single space so the API
    # call doesn't reject the whole batch. The caller should ideally filter
    # these out, but we don't want a typo upstream to nuke an indexing run.
    cleaned = [(t if (t and t.strip()) else " ") for t in texts]

    client = _make_openai_client()
    out: list[list[float]] = []
    for start in range(0, len(cleaned), _BATCH_LIMIT):
        batch = cleaned[start:start + _BATCH_LIMIT]
        response = client.embeddings.create(model=EMBEDDING_MODEL, input=batch, dimensions=EMBEDDING_DIMENSIONS)
        # response.data is ordered the same as input — preserve order.
        out.extend([item.embedding for item in response.data])

    return out


def embed_query(text: str) -> list[float]:
    """Embed a single query string. Returns a 1536-dim vector."""
    if not text or not text.strip():
        # Caller error — but rather than raising, return a zero vector so a
        # retrieval call against an empty query degrades to "no signal".
        return [0.0] * EMBEDDING_DIMENSIONS
    vectors = embed_texts([text])
    return vectors[0] if vectors else [0.0] * EMBEDDING_DIMENSIONS
