"""Agent runtime — facade do harness de tool-calling multi-step.

A lógica do loop ReAct vive em `core.llm.agent_graph` (LangGraph StateGraph —
migração Etapas 1-2). Este módulo mantém:
  • o contrato `AgentResult`/`TraceStep` que todos os call sites consomem;
  • a resolução de provider por API key disponível (`resolve_agent_provider`);
  • os shims `run_agent` (sync) / `run_agent_async` (async) que delegam ao grafo;
  • `run_subagent` (subagente-como-tool com degradação graciosa);
  • helpers compartilhados (`_cap`, constantes de reflexão) que o grafo importa.

As tools são tools NATIVAS do LangChain (`from langchain_core.tools import tool`)
desde a Etapa 2 — construídas pelas factories em `core/llm/agent_tools/`.

Padrão de uso:

    from langchain_core.tools import tool
    from core.llm.agent_runtime import run_agent

    @tool
    def search_edital(query: str, k: int = 5) -> str:
        '''Busca trechos relevantes do edital atual.'''
        ...  # retorna string que o modelo vê

    result = run_agent(
        system="Você é um redator de propostas...",
        initial_messages=[{"role": "user", "content": user_message}],
        tools=[search_edital, save_draft, ...],
        model="gpt-4o-mini", provider="openai", max_steps=8,
    )
    result.final_text  # resposta do modelo ao usuário
    result.steps       # lista de TraceStep (llm + tool intercalados)

Princípios (preservados pelo grafo):
  • Tools NUNCA quebram o loop. Erro vira ToolMessage-string; o modelo decide.
  • Tools com estado usam closures (factory `build_<agente>_tools(state)`).
  • Tool-results acima do orçamento são capadas (`_cap` / `TOOL_RESULT_CHAR_CAP`).
"""
from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from langchain_core.tools import BaseTool

logger = logging.getLogger(__name__)

Provider = Literal["openai", "anthropic"]
StopReason = Literal["end_turn", "max_steps", "max_tokens", "error", "other"]

# Defaults compartilhados com core.llm.llm_client (vide LLM_TIMEOUT_SECONDS / MAX_RETRIES).
# Usados pela factory de ChatModel do grafo (agent_graph._build_chat_model).
_TIMEOUT = float(os.getenv("LLM_TIMEOUT_SECONDS", "60"))
_MAX_RETRIES = int(os.getenv("LLM_MAX_RETRIES", "2"))

# Orçamento de contexto (spec 02): tool-results são appendadas ao histórico sem
# truncamento → em sessões longas o contexto cresce sem teto. Cap central por
# chars, aplicado no nó `tools` do grafo. Default folgado (calibrar com o log de
# disparo antes de apertar). Caps por-tool (writing_tools) são complementares.
TOOL_RESULT_CHAR_CAP = int(os.getenv("TOOL_RESULT_CHAR_CAP", "8000"))


def _cap(text: str, limit: int, *, tool_name: str | None = None) -> str:
    """Trunca `text` a `limit` chars, anexando marcador de truncamento.

    Cap simples por chars (a spec recomenda medir antes de ir token-aware).
    Quando dispara, loga qual tool e quanto cortou — observabilidade leve para
    calibrar o limite. Retorna o texto intacto se já couber.
    """
    if text is None:
        return text
    if limit <= 0 or len(text) <= limit:
        return text
    omitted = len(text) - limit
    logger.info(
        "tool-result cap disparou (tool=%s): %d chars → %d (cortou %d)",
        tool_name or "?", len(text), limit, omitted,
    )
    return text[:limit] + f"\n…[truncado: {omitted} chars omitidos]"


# =============================================================================
# Trace + resultado do agente — contrato público consumido pelos call sites
# =============================================================================

@dataclass
class TraceStep:
    """Um passo do loop do agente — chamada LLM OU execução de tool.

    kind == "llm":   modelo gerou texto e/ou pediu tools. `text` tem o texto,
                     `tool_uses` lista o que ele pediu, `usage` traz tokens.
    kind == "tool":  uma tool foi executada em resposta a um tool_use. `name`
                     tem o nome da tool, `input` o dict que veio do modelo,
                     `output` a string devolvida pela tool.
    """
    kind: Literal["llm", "tool"]
    text: str = ""
    tool_uses: list[dict[str, Any]] = field(default_factory=list)
    name: str = ""
    input: dict[str, Any] = field(default_factory=dict)
    output: str = ""
    usage: dict[str, int] = field(default_factory=dict)


@dataclass
class AgentResult:
    """Resultado final de run_agent.

    final_text:  o texto da última mensagem do assistant (resposta ao usuário)
    steps:       trace completo do loop (LLM + tools intercalados)
    stop_reason: por que o loop terminou (end_turn = ok; resto = degradado)
    usage:       soma de input_tokens + output_tokens em todas as chamadas LLM
    """
    final_text: str
    steps: list[TraceStep]
    stop_reason: StopReason
    usage: dict[str, int] = field(default_factory=dict)


# =============================================================================
# Reflexão dinâmica — constantes compartilhadas com o grafo (agent_graph)
# =============================================================================

_REFLECT_PROMPT = (
    "[Reflexão interna — não é mensagem do usuário] "
    "Antes de continuar: em 2 frases, o que você já aprendeu com as buscas feitas? "
    "O que ainda precisa para responder bem ao pedido do usuário?"
)

# Reflexão dinâmica (spec 08): sinais leves (sem LLM) que antecipam a reflexão
# antes do teto reflect_every. `reflect_every` vira piso de frequência; entre
# tetos, só rodadas com sinal disparam — rodadas triviais não geram reflexão à toa.
_REFLECT_CHAR_THRESHOLD = int(os.getenv("REFLECT_CHAR_THRESHOLD", "12000"))
_PLAN_TOOL_NAMES = {"write_todos"}  # mudança de plano → vale sintetizar


# =============================================================================
# Resolução de provider por API key disponível
# =============================================================================

def resolve_agent_provider(
    preferred: Provider,
    model: str,
    *,
    openai_model: str | None = None,
    anthropic_model: str | None = None,
) -> tuple[Provider, str]:
    """Escolhe (provider, model) conforme as API keys disponíveis no ambiente.

    Resiliência multi-modelo: o sistema não deve depender de um único
    fornecedor. Quando o provider preferido não tem credencial, degrada de
    forma graciosa para o outro (logando em WARNING). O grafo é
    provider-agnóstico, então o fallback é transparente.

    Args:
        preferred: provider desejado pelo call site.
        model: modelo desejado para o provider preferido.
        openai_model: modelo a usar se cair para OpenAI. Default: env
            OPENAI_MODEL_AGENT → OPENAI_MODEL_PRO → "gpt-4o-mini" (tool-loops
            pedem um modelo capaz, não o mini).
        anthropic_model: modelo a usar se cair para Anthropic. Default: env
            ANTHROPIC_MODEL_AGENT → "claude-sonnet-4-6".

    Raises:
        RuntimeError: se nenhuma API key de LLM estiver disponível.
    """
    # "openai" cobre tanto o endpoint canônico (OPENAI_API_KEY) quanto um
    # endpoint OpenAI-compat custom do tier agêntico (AGENT_OPENAI_BASE_URL, com
    # key opcional). Sem nenhuma env nova, isto é exatamente `bool(OPENAI_API_KEY)`.
    have = {
        "anthropic": bool(os.environ.get("ANTHROPIC_API_KEY")),
        "openai": bool(
            os.environ.get("OPENAI_API_KEY")
            or os.environ.get("AGENT_OPENAI_API_KEY")
            or os.environ.get("AGENT_OPENAI_BASE_URL")
        ),
    }
    if have[preferred]:
        return preferred, model

    fallback: Provider = "openai" if preferred == "anthropic" else "anthropic"
    if have[fallback]:
        if fallback == "openai":
            fb_model = (
                openai_model
                or os.getenv("OPENAI_MODEL_AGENT")
                or os.getenv("OPENAI_MODEL_PRO")
                or "gpt-4o-mini"
            )
        else:
            fb_model = (
                anthropic_model
                or os.getenv("ANTHROPIC_MODEL_AGENT")
                or "claude-sonnet-4-6"
            )
        logger.warning(
            "resolve_agent_provider: '%s' indisponível (sem API key) — "
            "fallback para '%s' (%s)", preferred, fallback, fb_model,
        )
        return fallback, fb_model

    raise RuntimeError(
        f"Nenhuma API key de LLM disponível (preferido={preferred}, "
        f"fallback={fallback}). Configure ANTHROPIC_API_KEY ou OPENAI_API_KEY."
    )


# =============================================================================
# Entradas do agente — delegam ao grafo LangGraph (core.llm.agent_graph)
# =============================================================================

async def run_agent_async(
    *,
    system: str,
    initial_messages: list[dict[str, Any]],
    tools: list[BaseTool],
    model: str,
    provider: Provider = "anthropic",
    max_steps: int = 8,
    on_step: Callable[[TraceStep], None] | None = None,
    reflect_every: int | None = None,
    span_name: str | None = None,
    temperature: float | None = None,
    openai_base_url: str | None = None,
    openai_api_key: str | None = None,
    trace_context: dict | None = None,
) -> AgentResult:
    """Loop ReAct do agente sobre LangGraph. Delega para o grafo compilado em
    `core.llm.agent_graph.run_agent_graph_async`.

    Mantém a assinatura/contrato histórico — todos os call sites (run_agent sync,
    run_subagent, writing/explore/profile) continuam idênticos. A implementação
    do grafo (nós agent/tools/reflect, cap, reflexão dinâmica, telemetria) vive
    em `agent_graph`; aqui só delegamos.

    Args:
        system: instrução de sistema.
        initial_messages: histórico inicial (user/assistant), sem system.
        tools: tools nativas do LangChain (factories em core/llm/agent_tools/).
        model / provider: modelo e provider ("openai" | "anthropic").
        max_steps: teto de chamadas LLM (evita loop infinito).
        on_step: callback opcional por TraceStep.
        reflect_every: teto de cadência da reflexão dinâmica (None/0 desliga).
        span_name: rótulo do span raiz (run_subagent passa f"subagent.{name}").
        temperature: repassada à factory do ChatModel.
        openai_base_url / openai_api_key: overrides do endpoint OpenAI-compat
            (só relevantes quando provider == "openai"; tier agêntico = dado de
            cliente → ZDR/pago, nunca free-tier-com-treino).

    Returns:
        AgentResult com texto final, trace, stop_reason e usage total.
    """
    from core.llm.agent_graph import run_agent_graph_async

    return await run_agent_graph_async(
        system=system,
        initial_messages=initial_messages,
        tools=tools,
        model=model,
        provider=provider,
        max_steps=max_steps,
        on_step=on_step,
        reflect_every=reflect_every,
        span_name=span_name,
        temperature=temperature,
        openai_base_url=openai_base_url,
        openai_api_key=openai_api_key,
        trace_context=trace_context,
    )


def run_agent(
    *,
    system: str,
    initial_messages: list[dict[str, Any]],
    tools: list[BaseTool],
    model: str,
    provider: Provider = "anthropic",
    max_steps: int = 8,
    on_step: Callable[[TraceStep], None] | None = None,
    reflect_every: int | None = None,
    span_name: str | None = None,
    temperature: float | None = None,
    openai_base_url: str | None = None,
    openai_api_key: str | None = None,
    trace_context: dict | None = None,
) -> AgentResult:
    """Shim síncrono sobre `run_agent_async`.

    A lógica vive no grafo; aqui só decidimos como rodá-la:
    - Sem event loop ativo na thread (handler FastAPI sync em threadpool, task
      procrastinate, tool sync em `asyncio.to_thread`) → `asyncio.run` direto.
    - Com loop ativo na thread (sync chamado de dentro de corrotina) → roda num
      worker thread com loop próprio, evitando "asyncio.run() cannot be called
      from a running event loop".
    """
    def _make_coro():
        return run_agent_async(
            system=system,
            initial_messages=initial_messages,
            tools=tools,
            model=model,
            provider=provider,
            max_steps=max_steps,
            on_step=on_step,
            reflect_every=reflect_every,
            span_name=span_name,
            temperature=temperature,
            openai_base_url=openai_base_url,
            openai_api_key=openai_api_key,
            trace_context=trace_context,
        )

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(_make_coro())

    # Loop ativo nesta thread → isola a execução num worker dedicado.
    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        return ex.submit(lambda: asyncio.run(_make_coro())).result()


# =============================================================================
# Subagente-como-tool — wrapper de degradação graciosa em volta de run_agent
# =============================================================================

def run_subagent(
    *,
    name: str,
    system: str,
    user_message: str,
    tools: list[BaseTool],
    provider: Provider = "anthropic",
    model: str | None = None,
    max_steps: int = 5,
    temperature: float | None = None,
    openai_base_url: str | None = None,
    openai_api_key: str | None = None,
    trace_context: dict | None = None,
) -> AgentResult:
    """Roda um subagente-como-tool: resolve provider, executa `run_agent` e
    degrada graciosamente em caso de erro.

    Resolve o provider via API key disponível, encapsula o `run_agent` em
    try/except, e — se algo explodir — devolve um `AgentResult` vazio com
    `stop_reason="error"` em vez de propagar a exceção. O chamador (que é uma
    tool no agente pai) nunca quebra o loop por causa de um subagente.

    Args:
        name: identificador do subagente (ex.: "deep_research") — vira
            `span_name=f"subagent.{name}"` na telemetria e rótulo de log.
        system: system prompt do subagente.
        user_message: a pergunta/tarefa única que dispara o subagente.
        tools: tools internas do subagente (LangChain).
        provider: provider preferido (resolve_agent_provider faz fallback por key).
        model: modelo desejado; None → default do provider via resolve.
        max_steps: limite de iterações do loop interno (subagentes são curtos).
        temperature: repassada ao grafo/factory.
        openai_base_url / openai_api_key: overrides do endpoint OpenAI-compat
            (só usados quando o provider resolvido é "openai"). O critic os passa
            para mirar seu próprio endpoint ZDR/pago (CRITIC_OPENAI_*).
        trace_context: contexto Langfuse do turno pai ({trace_id, parent_span_id}).
            Prefira capturar no call site (antes de entrar no thread pool do
            LangGraph) e passar aqui — o contextvar OTel não cruza fronteiras de
            thread. Se None, tenta capturar via current_trace_context() como fallback.

    Returns:
        AgentResult do loop interno; ou, em falha de resolução/execução,
        AgentResult(final_text="", steps=[], stop_reason="error", usage={}).
    """
    try:
        from core import telemetry
        # Usa o trace_context fornecido pelo chamador (capturado antes de entrar no
        # thread pool do LangGraph, onde o contextvar OTel ainda está disponível).
        # Fallback para current_trace_context() quando não fornecido (preserva
        # comportamento para call sites sem contexto agêntico — ex: scripts CLI).
        parent_ctx = trace_context if trace_context is not None else telemetry.current_trace_context()

        prov, mdl = resolve_agent_provider(
            provider,
            model or os.getenv("ANTHROPIC_MODEL_AGENT", "claude-sonnet-4-6"),
        )
        return run_agent(
            system=system,
            initial_messages=[{"role": "user", "content": user_message}],
            tools=tools,
            model=mdl,
            provider=prov,
            max_steps=max_steps,
            span_name=f"subagent.{name}",
            temperature=temperature,
            openai_base_url=openai_base_url,
            openai_api_key=openai_api_key,
            trace_context=parent_ctx,
        )
    except Exception as e:
        logger.error("run_subagent '%s' falhou: %s", name, e)
        return AgentResult(
            final_text="", steps=[], stop_reason="error", usage={},
        )
