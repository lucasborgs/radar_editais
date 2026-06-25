"""
HyDE — Hypothetical Document Embeddings (Gao et al., 2022).

Gera um trecho hipotético no estilo de edital de fomento brasileiro que CONTERIA
a resposta à query, e embeda esse trecho no lugar da query crua. Motivo: o
vocabulário de quem pergunta ("qual o prazo?") é semanticamente distante do
vocabulário do edital ("Art. 8º — o período de execução não excederá..."); a
resposta hipotética fica mais próxima do corpus do que a pergunta.

Configuração por env (todas opcionais):
    HYDE_MODEL     modelo de chat        (default: gpt-4o-mini)
    HYDE_BASE_URL  endpoint OpenAI-compat (ex.: http://localhost:11434/v1 p/ Ollama)
    HYDE_API_KEY   key do provider       (default: OPENAI_API_KEY)

Falha graciosa: qualquer erro (timeout, modelo indisponível, key ausente) →
retorna "". O caller detecta a string vazia e cai pra query original. HyDE está
no caminho crítico de um turno de escrita, então o timeout aqui é curto (~10s),
não o default de 60s do llm_client.
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

HYDE_MODEL = os.environ.get("HYDE_MODEL", "gpt-4o-mini")
HYDE_BASE_URL = os.environ.get("HYDE_BASE_URL") or None
HYDE_TIMEOUT_SECONDS = float(os.environ.get("HYDE_TIMEOUT_SECONDS", "10"))

_SYSTEM_PROMPT = (
    "Você é um assistente que gera trechos de editais públicos brasileiros de "
    "fomento. Dada uma pergunta, escreva um trecho de 2 a 3 frases no estilo "
    "formal e jurídico de edital (linguagem de Art./parágrafo, termos como "
    "'proponente', 'proposta', 'recursos', 'vigência') que conteria a resposta. "
    "Invente detalhes plausíveis (datas, valores, prazos) se necessário — o "
    "trecho serve só para busca semântica, não precisa ser factual. Responda "
    "apenas com o trecho, sem preâmbulo."
)


def generate_hyde_doc(query: str) -> str:
    """Trecho hipotético de edital que responderia `query`. "" em qualquer falha.

    Retorno vazio é o contrato de fallback: o caller (retriever/eval) embeda a
    query original quando isto devolve "".
    """
    if not query or not query.strip():
        return ""
    try:
        from core.llm.llm_client import make_client

        api_key = os.environ.get("HYDE_API_KEY") or os.environ.get("OPENAI_API_KEY")
        kwargs: dict = {"timeout": HYDE_TIMEOUT_SECONDS}
        if HYDE_BASE_URL:
            kwargs["base_url"] = HYDE_BASE_URL
            api_key = api_key or "not-needed"
        elif not api_key:
            return ""
        client = make_client(api_key=api_key, **kwargs)

        response = client.chat.completions.create(
            model=HYDE_MODEL,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": query},
            ],
            temperature=0.3,
        )
        return (response.choices[0].message.content or "").strip()
    except Exception as e:
        logger.warning("generate_hyde_doc falhou para query=%r: %s", query, e)
        return ""


__all__ = ["generate_hyde_doc"]
