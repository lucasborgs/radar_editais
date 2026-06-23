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


def make_client(**kwargs) -> OpenAI:
    kwargs.setdefault("timeout", LLM_TIMEOUT_SECONDS)
    kwargs.setdefault("max_retries", LLM_MAX_RETRIES)
    return OpenAI(**kwargs)


def make_async_client(**kwargs) -> AsyncOpenAI:
    kwargs.setdefault("timeout", LLM_TIMEOUT_SECONDS)
    kwargs.setdefault("max_retries", LLM_MAX_RETRIES)
    return AsyncOpenAI(**kwargs)


def make_chat_client(
    *,
    openai_model: str | None = None,
    ollama_default: str = "llama3.2",
) -> tuple[OpenAI, str]:
    """Resolve (client, model) do tier de chat por `LLM_BACKEND`.

    Seam único para os módulos que fazem chamadas de chat fora do tier agêntico
    (LangGraph tem o seu próprio em core/llm/agent_graph.py). Espelha o padrão
    historicamente duplicado em core.structurer._make_client. Sem nenhuma env nova
    o comportamento é idêntico ao anterior: OpenAI canônico, OPENAI_MODEL
    (default gpt-4o-mini).

        LLM_BACKEND=ollama  → endpoint OpenAI-compat local (OLLAMA_BASE_URL,
                              default http://localhost:11434/v1), modelo OLLAMA_MODEL
        LLM_BACKEND=gemini  → endpoint OpenAI-compat do Gemini, modelo GEMINI_MODEL
        (default/openai)    → OpenAI canônico, openai_model ou OPENAI_MODEL

    `openai_model` força o modelo do branch OpenAI (resolução de tier do caller);
    ignorado nos demais backends, que têm modelo próprio por env.
    """
    backend = os.getenv("LLM_BACKEND", "openai").lower()
    if backend == "gemini":
        api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        if not api_key:
            raise ValueError("GEMINI_API_KEY não definida")
        return make_client(
            api_key=api_key,
            base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        ), os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
    if backend == "ollama":
        return make_client(
            api_key="ollama",
            base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1"),
        ), os.getenv("OLLAMA_MODEL", ollama_default)
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("OPENAI_API_KEY não definida")
    return make_client(api_key=api_key), openai_model or os.getenv("OPENAI_MODEL", "gpt-4o-mini")


def make_eval_client() -> OpenAI | None:
    """Cliente do JUIZ de eval (LLM-as-judge das suítes em core/*_eval.py).

    Default = OpenAI no endpoint canônico (juiz deliberadamente pinado, para que
    os scores sejam comparáveis entre runs/commits). Para rodar o juiz local ou
    gratuito (ex.: Ollama via OpenAI-compat) durante a construção, sem queimar API
    paga, basta env — o código não muda:

        EVAL_LLM_BASE_URL   endpoint OpenAI-compat (ex.: http://localhost:11434/v1)
        EVAL_LLM_API_KEY    key do provider (default: OPENAI_API_KEY;
                            com base_url custom, "not-needed" é aceito)

    Retorna None quando não há juiz disponível (sem base_url custom E sem
    OPENAI_API_KEY) — o caller degrada graciosamente (score 0.0 + warning) em vez
    de quebrar a suíte. NÃO consulta LLM_BACKEND: o juiz é um knob próprio.
    """
    base_url = os.getenv("EVAL_LLM_BASE_URL") or None
    api_key = os.getenv("EVAL_LLM_API_KEY") or os.getenv("OPENAI_API_KEY")
    if base_url:
        return make_client(api_key=api_key or "not-needed", base_url=base_url)
    if not api_key:
        return None
    return make_client(api_key=api_key)
