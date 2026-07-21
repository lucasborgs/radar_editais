"""Factory central de clientes OpenAI com timeout + retry resilientes.

Todos os módulos devem criar clientes via `make_client` / `make_async_client`
em vez de instanciar `OpenAI()` direto, garantindo timeout e retry consistentes
em todo o request-path.

O SDK OpenAI já aplica backoff exponencial entre tentativas; aqui apenas fixamos
os limites. O timeout default (60s) protege contra requests que penduram
indefinidamente sem cortar gerações longas legítimas (ex.: redação de uma seção
inteira de proposta, que pode levar dezenas de segundos).
"""
import os

from openai import AsyncOpenAI, OpenAI

LLM_TIMEOUT_SECONDS = float(os.getenv("LLM_TIMEOUT_SECONDS", "60"))
LLM_MAX_RETRIES = int(os.getenv("LLM_MAX_RETRIES", "2"))
LLM_BASE_URL = os.getenv("LLM_BASE_URL")


def make_client(**kwargs) -> OpenAI:
    kwargs.setdefault("timeout", LLM_TIMEOUT_SECONDS)
    kwargs.setdefault("max_retries", LLM_MAX_RETRIES)
    kwargs.setdefault("base_url", LLM_BASE_URL)
    return OpenAI(**kwargs)


def make_async_client(**kwargs) -> AsyncOpenAI:
    kwargs.setdefault("timeout", LLM_TIMEOUT_SECONDS)
    kwargs.setdefault("max_retries", LLM_MAX_RETRIES)
    kwargs.setdefault("base_url", LLM_BASE_URL)
    return AsyncOpenAI(**kwargs)
