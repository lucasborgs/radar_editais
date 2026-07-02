"""
Contextual Retrieval (Anthropic 2024) — contexto-no-chunk antes do embed.

Para cada chunk, gera por LLM um contexto curto que o situa no documento e o
prepende ANTES de embeddar. O texto ARMAZENADO (coluna `text`) continua o
original; só o vetor muda. Bake-off (scripts/bench_contextual.py): +1-2pp
consistente em gold_recall/best_chunk no FINEP — único lever com ganho de
retrieval medido (parser estrutura-aware não rendeu).

Custo: 1 chamada LLM barata por chunk no INGEST (offline). Gateado pelo
`content_hash` do chunk_edital_task → só editais que mudaram pagam. Desligável
por env. Falha por-chunk degrada para o texto cru (nunca quebra o ingest).
"""
from __future__ import annotations

import logging
import os
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger(__name__)

_MODEL = os.getenv("CONTEXTUAL_RETRIEVAL_MODEL", "gpt-4o-mini")
# Endpoint/key parametrizáveis (bake-off): permite trocar o contextualizador por
# um modelo free/open (ex.: Gemini Flash) sem editar código. Defaults preservam
# o gpt-4o-mini no endpoint canônico OpenAI.
_BASE_URL = os.getenv("CONTEXTUAL_RETRIEVAL_BASE_URL") or None
_DOC_CHARS = 12000      # doc-contexto truncado (custo/latência por chamada)
_CHUNK_CHARS = 1500
_MAX_WORKERS = 8
_PROMPT = (
    "<documento>\n{doc}\n</documento>\n\n"
    "Aqui está um trecho do documento:\n<trecho>\n{chunk}\n</trecho>\n\n"
    "Dê um contexto curto (1-2 frases) que situe este trecho no documento "
    "(qual seção/assunto, de qual edital), para melhorar a busca. Responda "
    "APENAS com o contexto, sem preâmbulo."
)


def _api_key() -> str | None:
    """Resolve a key em CALL-TIME (não no import) — senão monkeypatch.setenv nos
    testes e mudanças de env pós-import seriam ignoradas (era raiz de flakes)."""
    return os.getenv("CONTEXTUAL_RETRIEVAL_API_KEY") or os.getenv("OPENAI_API_KEY")


def is_enabled() -> bool:
    """Liga por default; desliga com CONTEXTUAL_RETRIEVAL=false. Sem key
    (CONTEXTUAL_RETRIEVAL_API_KEY/OPENAI_API_KEY) também desliga (degrada para
    embed cru) — salvo endpoint custom, que aceita key placeholder."""
    if os.getenv("CONTEXTUAL_RETRIEVAL", "true").lower() in ("false", "0", "no"):
        return False
    return bool(_api_key() or _BASE_URL)


def contextualize_chunks(chunks: list[dict]) -> list[str]:
    """Retorna o texto A EMBEDDAR por chunk: `contexto + corpo` quando habilitado,
    senão o corpo cru. Ordem preservada (1:1 com `chunks`). Nunca lança —
    falha por-chunk cai para o corpo cru.
    """
    texts = [c.get("text", "") for c in chunks]
    if not is_enabled() or not chunks:
        return texts

    try:
        from core.llm.llm_client import make_client
        kwargs: dict = {"api_key": _api_key() or "not-needed"}
        if _BASE_URL:
            kwargs["base_url"] = _BASE_URL
        client = make_client(**kwargs)
    except Exception as e:  # noqa: BLE001
        logger.warning("contextual_retrieval: cliente LLM indisponível (%s) — embed cru", e)
        return texts

    doc = "\n\n".join(texts)[:_DOC_CHARS]
    usages: list = []  # usage por chunk (append thread-safe sob o GIL)

    def _ctx(text: str) -> str:
        body = text.strip()
        if not body:
            return text
        try:
            r = client.chat.completions.create(
                model=_MODEL, max_tokens=80, temperature=0.0,
                messages=[{"role": "user",
                           "content": _PROMPT.format(doc=doc, chunk=body[:_CHUNK_CHARS])}],
            )
            if getattr(r, "usage", None) is not None:
                usages.append(r.usage)
            ctx = (r.choices[0].message.content or "").strip()
            return f"{ctx}\n\n{body}" if ctx else text
        except Exception as e:  # noqa: BLE001
            logger.debug("contextual_retrieval: chunk falhou (%s) — embed cru", e)
            return text

    from core import telemetry

    # 1 span por BATCH (não por chunk — spec PR5): usage agregado do lote.
    with telemetry.llm_span(
        "rag.contextual_retrieval_batch",
        model=_MODEL,
        metadata={"n_chunks": len(texts)},
    ) as span:
        with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as ex:
            out = list(ex.map(_ctx, texts))
        if span is not None:
            try:
                span.update(usage_details={
                    "input": sum(getattr(u, "prompt_tokens", 0) or 0 for u in usages),
                    "output": sum(getattr(u, "completion_tokens", 0) or 0 for u in usages),
                })
            except Exception as e:  # noqa: BLE001 — telemetria nunca quebra o ingest
                logger.debug("contextual_retrieval: usage span falhou (%s)", e)
    return out
