"""Runtime ReAct sobre LangGraph (StateGraph) — runtime único em produção.

`run_agent_async` delega inteiramente a este grafo (agent / tools / finalize),
traduzindo o estado final de volta para o contrato `AgentResult` sem tocar em
nenhum call site. O loop hand-rolled legado foi removido (migração concluída —
ver docs/historical/langgraph-migration.md).

Decisões fechadas:
  • Cap de iterações = contador `llm_calls` em state (paridade exata com
    `for ... range(max_steps)`), NÃO `recursion_limit`. Este vira só backstop.
  • Sem poda intra-turno nem nó de reflexão: o contexto por turno é limitado
    por `max_steps × caps por tool` (TOOL_RESULT_CHAR_CAP), então cabe sem poda.
    A classe do bug de vazamento da reflexão morre por construção — não há mais
    HumanMessage interna injetada no meio do turno (só o `finalize` em max_steps).

Em produção: telemetria via `langfuse.langchain.CallbackHandler`, checkpointer
`AsyncPostgresSaver` durável (interrupt/resume), e tools LangChain nativas.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import threading
import weakref
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Annotated, Any, Literal

from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    AnyMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.tools import BaseTool
from langgraph.errors import GraphRecursionError
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode
from typing_extensions import TypedDict

from core.llm.agent_runtime import (
    _FINALIZE_PROMPT,
    _LAST_STEP_PROMPT,
    _MAX_RETRIES,
    _TIMEOUT,
    TOOL_RESULT_CHAR_CAP,
    AgentResult,
    Provider,
    StopReason,
    TraceStep,
    _cap,
)

logger = logging.getLogger(__name__)


# =============================================================================
# State
# =============================================================================

class AgentState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]
    llm_calls: int
    tool_rounds: int
    documents: dict[str, str]


# =============================================================================
# Model factory (seam de teste: monkeypatch _build_chat_model)
# =============================================================================

def _build_chat_model(
    provider: Provider,
    model: str,
    *,
    temperature: float | None = None,
    openai_base_url: str | None = None,
    openai_api_key: str | None = None,
):
    """Constrói o ChatModel LangChain. Espelha a resolução de endpoint de
    `_openai_agent_client` (ZDR/custom OpenAI-compat)."""
    # Paridade de resiliência com o tier legado (make_client / _call_anthropic):
    # timeout + retries explícitos. Sem isto, um 429 transitório (TPM) derruba o
    # turno inteiro em vez de re-tentar com backoff.
    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic

        kw: dict[str, Any] = {
            "model": model, "max_tokens": 4096,
            "timeout": _TIMEOUT, "max_retries": _MAX_RETRIES,
        }
        if temperature is not None:
            kw["temperature"] = temperature
        return ChatAnthropic(**kw)

    from langchain_openai import ChatOpenAI

    base = openai_base_url or os.environ.get("AGENT_OPENAI_BASE_URL") or None
    key = (
        openai_api_key
        or os.environ.get("AGENT_OPENAI_API_KEY")
        or os.environ.get("OPENAI_API_KEY")
    )
    kw = {
        "model": model, "timeout": _TIMEOUT, "max_retries": _MAX_RETRIES,
        # Defensivo (Item 1, TASK 2): protege o usage em streaming caso a lib
        # regrida — langchain-openai 1.3.3 já popula usage_metadata em stream
        # por padrão (achado do spike, spikes/lever1_streaming/FINDINGS.md),
        # então isto é inócuo no path não-streaming e no streaming de hoje.
        "stream_usage": True,
    }
    if temperature is not None:
        kw["temperature"] = temperature
    if base:
        kw["base_url"] = base
        key = key or "not-needed"
    if key:
        kw["api_key"] = key
    return ChatOpenAI(**kw)


# =============================================================================
# Graph builder
# =============================================================================

def _tool_error_to_str(e: Exception) -> str:
    """Degradação graciosa (Etapa 2): tool que levanta vira ToolMessage-string em
    vez de quebrar o grafo. Mantém o prefixo "Erro ao executar" — o sinal que o
    nó `tools` usa para antecipar a reflexão (espelha o loop legado)."""
    return f"Erro ao executar a tool: {e}"


def _build_graph(
    model,
    lc_tools: list[BaseTool],
    *,
    max_steps: int,
    checkpointer=None,
):
    bound = model.bind_tools(lc_tools) if lc_tools else model
    tool_node = ToolNode(lc_tools, handle_tool_errors=_tool_error_to_str)

    async def agent(state: AgentState) -> dict:
        resp = await bound.ainvoke(state["messages"])
        return {
            "messages": [resp],
            "llm_calls": state["llm_calls"] + 1,
            "documents": state.get("documents", {}),
        }

    async def agent_final(state: AgentState) -> dict:
        # SEM tools vinculadas (`model`, não `bound`) — garante que a resposta não
        # tenha tool_calls, então essa rodada sempre encerra o turno de verdade.
        resp = await model.ainvoke(state["messages"])
        return {
            "messages": [resp],
            "llm_calls": state["llm_calls"] + 1,
            "documents": state.get("documents", {}),
        }

    def should_continue(state: AgentState) -> str:
        last = state["messages"][-1]
        if not getattr(last, "tool_calls", None):
            return END  # fim natural do turno
        return "tools"

    async def tools(state: AgentState) -> dict:
        out = await tool_node.ainvoke(state)
        tmsgs = out["messages"]
        # Cap central (movido do bridge na Etapa 2): trunca cada tool-result acima
        # do orçamento antes de ir ao histórico. Caps por-tool (writing_tools) já
        # podem ter agido antes; este é o teto de segurança final. Sem poda
        # intra-turno: o histórico cresce no máximo max_steps × este cap.
        for m in tmsgs:
            m.content = _cap(
                str(m.content), TOOL_RESULT_CHAR_CAP, tool_name=getattr(m, "name", None),
            )
        return {
            "messages": tmsgs,
            "tool_rounds": state["tool_rounds"] + 1,
            "documents": state.get("documents", {}),
        }

    def after_tools(state: AgentState) -> str:
        # Cap pós-tools (paridade com o legado: as tools da última rodada SÃO
        # executadas — já rodaram no nó `tools` — e só então o teto corta).
        # Vai para `finalize` (não END direto): sem isso o turno termina no
        # ToolMessage da última tool, sem o modelo nunca escrever uma resposta —
        # texto vazio pro usuário (bug real, achado ao validar a rodada final).
        if state["llm_calls"] >= max_steps:
            return "finalize"
        # Item 6 (Task 2): aviso de penúltimo passo — o modelo ainda tem uma
        # rodada com tools, mas sabe que é a última antes do backstop cego.
        # ADENDO DE GOVERNANÇA: só dispara com max_steps >= 3 — com max_steps=2
        # (batch de geração) o passo 1 já é o penúltimo, e o aviso viraria um
        # fixture constante no caminho calibrado do batch (redundante, o design
        # já é "busca uma vez e escreve").
        if max_steps >= 3 and state["llm_calls"] == max_steps - 1:
            return "budget_notice"
        return "agent"

    def finalize(state: AgentState) -> dict:
        return {
            "messages": [HumanMessage(content=_FINALIZE_PROMPT)],
            "documents": state.get("documents", {}),
        }

    def budget_notice(state: AgentState) -> dict:
        return {
            "messages": [HumanMessage(content=_LAST_STEP_PROMPT)],
            "documents": state.get("documents", {}),
        }

    g = StateGraph(AgentState)
    g.add_node("agent", agent)
    g.add_node("agent_final", agent_final)
    g.add_node("tools", tools)
    g.add_node("finalize", finalize)
    g.add_node("budget_notice", budget_notice)
    g.add_edge(START, "agent")
    g.add_conditional_edges(
        "agent", should_continue,
        {END: END, "tools": "tools"},
    )
    g.add_conditional_edges(
        "tools", after_tools,
        {"finalize": "finalize", "agent": "agent", "budget_notice": "budget_notice"},
    )
    g.add_edge("budget_notice", "agent")
    g.add_edge("finalize", "agent_final")
    g.add_edge("agent_final", END)
    return g.compile(checkpointer=checkpointer)


# =============================================================================
# Tradutor de contrato: estado final → AgentResult
# =============================================================================

def _msg_text(m: AIMessage) -> str:
    t = getattr(m, "text", None)
    if isinstance(t, str):  # langchain-core >=1: `.text` é property (str)
        return t
    if callable(t):  # compat <1: `.text()` método
        return t() or ""
    return m.content if isinstance(m.content, str) else str(m.content or "")


def _messages_to_agent_result(messages: list[AnyMessage], stop_reason: StopReason) -> AgentResult:
    steps: list[TraceStep] = []
    call_inputs: dict[str, dict] = {}
    total_in = total_out = 0
    last_text = ""

    for m in messages:
        if isinstance(m, AIMessage):
            tool_uses = [
                {"id": tc["id"], "name": tc["name"], "input": tc["args"]}
                for tc in (m.tool_calls or [])
            ]
            for tu in tool_uses:
                call_inputs[tu["id"]] = tu["input"]
            um = m.usage_metadata or {}
            usage = {
                "input_tokens": um.get("input_tokens", 0),
                "output_tokens": um.get("output_tokens", 0),
            }
            total_in += usage["input_tokens"]
            total_out += usage["output_tokens"]
            text = _msg_text(m)
            steps.append(TraceStep(kind="llm", text=text, tool_uses=tool_uses, usage=usage))
            last_text = text
        elif isinstance(m, ToolMessage):
            steps.append(
                TraceStep(
                    kind="tool",
                    name=m.name or "",
                    input=call_inputs.get(m.tool_call_id, {}),
                    output=str(m.content),
                )
            )

    return AgentResult(
        final_text=last_text,
        steps=steps,
        stop_reason=stop_reason,
        usage={"input_tokens": total_in, "output_tokens": total_out},
    )


def _derive_stop_reason(final_state: dict, max_steps: int) -> StopReason:
    """max_steps se o loop foi cortado — via tool_calls pendentes (backstop antigo)
    OU via a rodada forçada de `finalize` (llm_calls > max_steps: só acontece
    quando `after_tools` desviou pro finalize por ter estourado o teto)."""
    msgs = final_state["messages"]
    last_ai = next((m for m in reversed(msgs) if isinstance(m, AIMessage)), None)
    if last_ai and last_ai.tool_calls:
        return "max_steps"
    if final_state.get("llm_calls", 0) > max_steps:
        return "max_steps"
    return "end_turn"


# =============================================================================
# Entry point (delegado pela facade run_agent_async)
# =============================================================================

def _as_cached_content(content: Any) -> list[dict[str, Any]]:
    """Converte texto simples no formato de blocos da Anthropic com um breakpoint
    de cache efêmero: `[{"type": "text", "text": ..., "cache_control": {...}}]`.

    O TTL padrão do ephemeral (5 min) cobre um turno ReAct inteiro com folga; as
    iterações 2..N do mesmo turno leem o prefixo do cache em vez de reenviá-lo a
    preço cheio. langchain-anthropic aceita content em blocos com cache_control e
    o repassa verbatim à API. O texto permanece byte-a-byte idêntico."""
    return [{"type": "text", "text": str(content),
             "cache_control": {"type": "ephemeral"}}]


def _to_lc_messages(
    initial: list[dict[str, Any]], *, provider: Provider | None = None,
) -> list[AnyMessage]:
    """Converte os dicts do produtor (writing_session/explore) em mensagens LangChain.

    A flag `"cache_hint"` (marcada pelo produtor nos breakpoints de cache) é SEMPRE
    consumida aqui e NUNCA vaza para a API. Só quando `provider == "anthropic"` ela
    vira um `cache_control` ephemeral no content da mensagem — nos demais providers
    (OpenAI, que já faz prefix caching automático) o comportamento é byte-a-byte
    idêntico ao anterior: a flag é ignorada e o content vai como string."""
    out: list[AnyMessage] = []
    for m in initial:
        role, content = m.get("role"), m.get("content", "")
        cache_hint = bool(m.pop("cache_hint", False))
        if provider == "anthropic" and cache_hint:
            content = _as_cached_content(content)
        # `id` opcional (Item 3): quando o produtor marca uma mensagem com id
        # determinístico (ex.: prefixo estável da escrita numa thread-por-sessão),
        # o reducer `add_messages` a SUBSTITUI em posição em vez de acumular cópias
        # a cada turno. Sem id (default), comportamento byte-idêntico ao anterior
        # (o LangGraph gera um uuid por mensagem).
        msg_id = m.get("id")
        if role == "assistant":
            out.append(AIMessage(content=content, id=msg_id))
        elif role == "system":
            out.append(SystemMessage(content=content, id=msg_id))
        else:
            out.append(HumanMessage(content=content, id=msg_id))
    return out


def _build_system_message(
    system: str, provider: Provider | None, *, msg_id: str | None = None,
) -> SystemMessage:
    """SystemMessage top-level. No caminho Anthropic recebe um breakpoint de cache
    (o system é estável por sessão — 1º dos 3 breakpoints do turno). Nos demais
    providers vai como string simples (idêntico ao anterior).

    `msg_id` (Item 3): id determinístico para que `add_messages` substitua o system
    em posição numa thread-por-sessão (writing). Sem ele (default), comportamento
    byte-idêntico ao anterior."""
    if provider == "anthropic":
        return SystemMessage(content=_as_cached_content(system), id=msg_id)
    return SystemMessage(content=system, id=msg_id)


async def run_agent_graph_async(
    *,
    system: str,
    initial_messages: list[dict[str, Any]],
    tools: list[BaseTool],
    model: str,
    provider: Provider = "anthropic",
    max_steps: int = 8,
    on_step: Callable[[TraceStep], None] | None = None,
    span_name: str | None = None,
    temperature: float | None = None,
    openai_base_url: str | None = None,
    openai_api_key: str | None = None,
    trace_context: dict | None = None,
    mode: str | None = None,
) -> AgentResult:
    """Equivalente LangGraph de `run_agent_async` (mesma assinatura/contrato).

    `tools` são tools nativas do LangChain (Etapa 2): consumidas direto pelo
    ToolNode, sem bridge.

    `trace_context` (Etapa 6): parent remoto p/ aninhar quando este run é um subagente
    rodando noutra thread (ver run_subagent → agent_run(trace_context=...)).

    `mode` (Item 6, Task 1): rótulo do produtor ("explore"/"writing"/"profile"/...)
    — dimensão só para observabilidade (metadata do span + log `turn_end`); não
    afeta o comportamento do grafo."""
    chat = _build_chat_model(
        provider, model,
        temperature=temperature,
        openai_base_url=openai_base_url,
        openai_api_key=openai_api_key,
    )
    # checkpointer=False (NÃO None): este é o caminho stateless (kg_match/profile/
    # subagentes). `None` faria o LangGraph HERDAR o checkpointer do pai quando o
    # grafo roda como subgrafo — e o critic (subagente dentro de save_draft) herdaria
    # o AsyncPostgresSaver do turno de escrita via contextvar do config, tentando
    # usar o lock dele (preso ao bg-loop) a partir do loop do subagente → "Lock is
    # bound to a different event loop". `False` corta a herança: subagente nunca
    # persiste nem toca o checkpointer do pai.
    graph = _build_graph(
        chat, tools, max_steps=max_steps, checkpointer=False,
    )

    init: AgentState = {
        "messages": [
            _build_system_message(system, provider),
            *_to_lc_messages(initial_messages, provider=provider),
        ],
        "llm_calls": 0,
        "tool_rounds": 0,
        "documents": {},
    }

    # Import lazy (evita custo de telemetria em testes que não a exercem).
    from core import telemetry

    result: AgentResult
    with telemetry.agent_run(
        name=span_name or f"agent.{provider}.{model}",
        input={"system": system, "initial_messages": initial_messages},
        metadata={
            "provider": provider, "model": model,
            "max_steps": max_steps, "runtime": "langgraph",
            "tools": [t.name for t in tools], "mode": mode,
        },
        trace_context=trace_context,
    ) as agent_span:
        # Spans nativos (chain/llm/tool) com timing real + usage automático, aninhados
        # sob o agent_span corrente (Etapa 6). None se Langfuse off → grafo sem overhead.
        config: dict[str, Any] = {"recursion_limit": 3 * max_steps + 5}
        handler = telemetry.make_callback_handler()
        if handler is not None:
            config["callbacks"] = [handler]
        try:
            final = await graph.ainvoke(init, config=config)
        except GraphRecursionError:
            # Backstop — não deveria disparar (teto real é llm_calls). Trata como max_steps.
            logger.warning("agent_graph: recursion_limit atingido (backstop) — max_steps")
            logger.info(
                "turn_end mode=%s stop_reason=%s llm_calls=%d max_steps=%d",
                mode, "max_steps", 0, max_steps,
            )
            return AgentResult(final_text="", steps=[], stop_reason="max_steps", usage={})
        except Exception as e:
            logger.error("agent_graph: grafo falhou: %s", e)
            if agent_span is not None:
                agent_span.update(level="ERROR", status_message=str(e))
            return AgentResult(final_text="", steps=[], stop_reason="error", usage={})

        msgs = final["messages"]
        stop = _derive_stop_reason(final, max_steps)
        result = _messages_to_agent_result(msgs, stop)
        logger.info(
            "turn_end mode=%s stop_reason=%s llm_calls=%d max_steps=%d",
            mode, stop, final.get("llm_calls", 0), max_steps,
        )

        if agent_span is not None:
            agent_span.update(
                output={"final_text": result.final_text, "stop_reason": result.stop_reason},
                metadata={
                    "stop_reason": result.stop_reason,
                    "n_steps": len(result.steps),
                    "usage": result.usage,
                },
            )

    if on_step:
        for s in result.steps:
            on_step(s)
    return result


# =============================================================================
# Item 1 (streaming) — entry point streaming aditivo, ao lado de ainvoke
# =============================================================================

@dataclass
class StreamDelta:
    """Evento do canal lateral de streaming (`run_agent_graph_streaming`).

    kind == "token":     delta de texto do assistente (`text`).
    kind == "tool_end":  uma tool terminou de rodar (`name`) — sinal leve para
                         indicador de "pensando"; sem `tool_start` neste
                         transporte (astream multimode não expõe início de
                         tool de forma barata — ver FINDINGS da TASK 1).
    kind == "done":      fim do turno — `result` é o AgentResult AUTORITATIVO,
                         reconstruído do estado final pelo MESMO tradutor do
                         `ainvoke` (nunca dos chunks acumulados).
    kind == "error":     erro antes do turno terminar (sempre seguido de um
                         "done" com AgentResult degradado — nunca propaga
                         exceção crua pelo generator).
    """
    kind: Literal["token", "tool_end", "done", "error"]
    text: str = ""
    name: str = ""
    result: AgentResult | None = None


async def run_agent_graph_streaming(
    *,
    system: str,
    initial_messages: list[dict[str, Any]],
    tools: list[BaseTool],
    model: str,
    provider: Provider = "anthropic",
    max_steps: int = 8,
    on_step: Callable[[TraceStep], None] | None = None,
    span_name: str | None = None,
    temperature: float | None = None,
    openai_base_url: str | None = None,
    openai_api_key: str | None = None,
    trace_context: dict | None = None,
    mode: str | None = None,
    thread_id: str | None = None,
    checkpointer: Any = None,
    prior_n_msgs: int = 0,
    system_msg_id: str | None = None,
) -> AsyncIterator[StreamDelta]:
    """Variação streaming de `run_agent_graph_async` — MESMA assinatura, canal
    lateral de tokens ao vivo. Aditivo: não toca `run_agent_graph_async` nem
    nenhum call site existente.

    Item 3 (TASK 3) — thread-por-sessão do explore (aditivo, opt-in por `thread_id`):
      • `thread_id is None` (default) → comportamento BYTE-IDÊNTICO de hoje:
        `checkpointer=False` (corta herança de subagente), delta = estado inteiro.
      • `thread_id` setado → compila com o `checkpointer` LOOP-LOCAL passado, roda
        `astream` com `config.configurable.thread_id`, o `system` entra com
        `system_msg_id` determinístico (descoberta A: `add_messages` substitui em
        posição, sem system stale), e o `AgentResult` do `done` traduz só
        `msgs[prior_n_msgs:]` (descoberta B: usage/trace/called_* do TURNO, não da
        conversa toda). O produtor lê `prior_n_msgs` do checkpointer antes do turno.

    Transporte decidido na TASK 1 do item 1 (checkpoint GO, ver
    `spikes/lever1_streaming/FINDINGS.md`): `graph.astream(stream_mode=
    ["messages", "values"])`. `astream_events(version="v3")` não está
    disponível como async iterator direto na versão pinada do langgraph
    (retorna um `AsyncGraphRunStream` experimental); `values` garante o
    estado terminal pela própria API, sem heurística de "isto é a raiz".

    Wrinkle crítico (achado da TASK 1, critério de aceite formal): o modo
    `"messages"` emite tanto `AIMessageChunk` (delta real de token) quanto
    `ToolMessage` completo (resultado da tool). Sem filtrar por
    `isinstance(msg, AIMessageChunk)`, o texto bruto da tool vaza como se
    fosse resposta do assistente.

    O `AgentResult` do evento `done` vem do estado final (última mensagem do
    modo `"values"`) passado pelos MESMOS tradutores de `run_agent_graph_async`
    (`_derive_stop_reason` + `_messages_to_agent_result`) — o streaming é só
    um canal lateral, nunca a fonte do contrato (decisão de arquitetura #1 do
    plano do item 1).
    """
    chat = _build_chat_model(
        provider, model,
        temperature=temperature,
        openai_base_url=openai_base_url,
        openai_api_key=openai_api_key,
    )
    # thread_id None → checkpointer=False (path stateless de hoje; corta herança de
    # subagente rodando noutro loop). thread_id setado → o saver loop-local passado
    # (nunca None aqui: o produtor só seta thread_id quando tem saver).
    graph = _build_graph(
        chat, tools, max_steps=max_steps,
        checkpointer=(checkpointer if thread_id is not None else False),
    )

    init: AgentState = {
        "messages": [
            _build_system_message(system, provider, msg_id=system_msg_id),
            *_to_lc_messages(initial_messages, provider=provider),
        ],
        "llm_calls": 0,
        "tool_rounds": 0,
        "documents": {},
    }

    from core import telemetry

    with telemetry.agent_run(
        name=span_name or f"agent.{provider}.{model}",
        input={"system": system, "initial_messages": initial_messages},
        metadata={
            "provider": provider, "model": model,
            "max_steps": max_steps, "runtime": "langgraph-streaming",
            "tools": [t.name for t in tools], "mode": mode,
        },
        trace_context=trace_context,
    ) as agent_span:
        config: dict[str, Any] = {"recursion_limit": 3 * max_steps + 5}
        if thread_id is not None:
            config["configurable"] = {"thread_id": thread_id}
        handler = telemetry.make_callback_handler()
        if handler is not None:
            config["callbacks"] = [handler]

        final_state: dict[str, Any] | None = None
        try:
            async for stream_mode, chunk in graph.astream(
                init, config=config, stream_mode=["messages", "values"],
            ):
                if stream_mode == "messages":
                    msg_chunk, _meta = chunk
                    if isinstance(msg_chunk, AIMessageChunk):
                        delta = _msg_text(msg_chunk)
                        if delta:
                            yield StreamDelta(kind="token", text=delta)
                    elif isinstance(msg_chunk, ToolMessage):
                        yield StreamDelta(kind="tool_end", name=msg_chunk.name or "")
                elif stream_mode == "values":
                    final_state = chunk
        except GraphRecursionError:
            logger.warning("agent_graph: recursion_limit atingido (backstop, streaming) — max_steps")
            logger.info(
                "turn_end mode=%s stop_reason=%s llm_calls=%d max_steps=%d",
                mode, "max_steps", 0, max_steps,
            )
            degraded = AgentResult(final_text="", steps=[], stop_reason="max_steps", usage={})
            yield StreamDelta(kind="error", text="max_steps")
            yield StreamDelta(kind="done", result=degraded)
            return
        except Exception as e:
            logger.error("agent_graph: grafo falhou (streaming): %s", e)
            if agent_span is not None:
                agent_span.update(level="ERROR", status_message=str(e))
            degraded = AgentResult(final_text="", steps=[], stop_reason="error", usage={})
            yield StreamDelta(kind="error", text=str(e))
            yield StreamDelta(kind="done", result=degraded)
            return

        if final_state is None:
            # Não deveria disparar (astream com stream_mode=["values"] garante ao
            # menos um estado terminal) — backstop no mesmo padrão dos outros
            # caminhos de falha: nunca propaga exceção crua pelo generator (um
            # `assert` puro some silenciosamente com `python -O` e quebraria o
            # contrato de StreamDelta ao vazar `AssertionError`).
            logger.error("agent_graph: astream multimode não emitiu estado final (values)")
            degraded = AgentResult(final_text="", steps=[], stop_reason="error", usage={})
            yield StreamDelta(kind="error", text="sem estado final do stream")
            yield StreamDelta(kind="done", result=degraded)
            return

        stop = _derive_stop_reason(final_state, max_steps)
        # Descoberta B (thread-por-sessão): traduz só o delta deste turno-run
        # (msgs[prior_n_msgs:]) — usage/trace e os called_* derivados de steps
        # refletem o TURNO, não a conversa toda. prior_n_msgs=0 (stateless ou 1º
        # turno) → estado inteiro, byte-idêntico ao de hoje. _derive_stop_reason
        # segue lendo o estado inteiro (last AI + llm_calls são do turno corrente).
        result = _messages_to_agent_result(final_state["messages"][prior_n_msgs:], stop)
        logger.info(
            "turn_end mode=%s stop_reason=%s llm_calls=%d max_steps=%d",
            mode, stop, final_state.get("llm_calls", 0), max_steps,
        )

        if agent_span is not None:
            agent_span.update(
                output={"final_text": result.final_text, "stop_reason": result.stop_reason},
                metadata={
                    "stop_reason": result.stop_reason,
                    "n_steps": len(result.steps),
                    "usage": result.usage,
                },
            )

    if on_step:
        for s in result.steps:
            on_step(s)
    yield StreamDelta(kind="done", result=result)


# =============================================================================
# Etapa 3 — WritingSession com checkpointer Postgres (interrupt/resume)
# =============================================================================
# A WritingSession invoca o grafo com um checkpointer durável, keyed por
# `thread_id` (= "{workspace_id}:{session_id}:{turn_index}"). Isso dá:
#   • durabilidade do estado em-voo entre requests HTTP (multi-instância);
#   • `request_user_info` como `interrupt()` nativo → human-in-the-loop com
#     retomada (`Command(resume=...)`) preservando o raciocínio do agente.
# As tabelas de domínio (writing_sessions/session_turns) seguem AUTORITATIVAS;
# o checkpoint é efêmero (escopo de turno-run).

# ---------------------------------------------------------------------------
# Event loop dedicado (singleton) — isola o checkpointer async dos callers sync
# ---------------------------------------------------------------------------
# O AsyncPostgresSaver mantém um AsyncConnectionPool BOUND ao event loop onde foi
# criado. Os callers da escrita são sync (router via asyncio.to_thread, eval, CLI)
# e o shim run_agent sobe um asyncio.run por chamada — loops efêmeros que matariam
# o pool. Solução: um loop de longa duração numa thread daemon; o pool é criado
# uma vez nele e toda corrotina do checkpointer roda lá via run_coroutine_threadsafe.

_bg_loop: asyncio.AbstractEventLoop | None = None
_bg_loop_lock = threading.Lock()


_bg_loop_started = threading.Event()

def _get_bg_loop() -> asyncio.AbstractEventLoop:
    global _bg_loop
    if _bg_loop is not None:
        return _bg_loop
    with _bg_loop_lock:
        if _bg_loop is None:
            loop = asyncio.new_event_loop()
            def _run_and_signal():
                _bg_loop_started.set()
                loop.run_forever()
            threading.Thread(
                target=_run_and_signal,
                name="lg-checkpointer-loop",
                daemon=True,
            ).start()
            _bg_loop_started.wait(timeout=10)
            _bg_loop = loop
    return _bg_loop


def _run_on_bg_loop(coro):
    """Roda `coro` no loop dedicado a partir de qualquer thread sync (bloqueante)."""
    return asyncio.run_coroutine_threadsafe(coro, _get_bg_loop()).result()


# ---------------------------------------------------------------------------
# Schema dedicado (Etapa 5): a maquinaria que bypassa RLS (checkpointer + Store)
# vive fora de `public` → invisível ao PostgREST do Supabase (config.toml expõe só
# public/storage/graphql_public). search_path inclui public+extensions para resolver
# o tipo `vector` (pgvector vive em `extensions` no Supabase, em `public` no PG local).
# Substitui o band-aid da migration 027 (RLS+revoke tabela-a-tabela). Ver
# supabase/migrations/028_agent_memory_schema.sql.
# ---------------------------------------------------------------------------
AGENT_MEMORY_SCHEMA = "agent_memory"
_AGENT_MEMORY_SEARCH_PATH = f"{AGENT_MEMORY_SCHEMA},public,extensions"


async def _make_agent_memory_pool(dsn: str, *, max_size: int):
    """AsyncConnectionPool com search_path no schema dedicado + garante o schema
    (CREATE SCHEMA IF NOT EXISTS — defensivo, independe da migration 028 ter rodado).
    Compartilhado pelo checkpointer (Et.3) e pelo Store da memória (Et.5)."""
    from psycopg.rows import dict_row
    from psycopg_pool import AsyncConnectionPool

    pool = AsyncConnectionPool(
        conninfo=dsn,
        min_size=1,  # < max_size do Store (2); default 4 > max_size daria erro
        max_size=max_size,
        open=False,
        kwargs={
            "autocommit": True,
            "prepare_threshold": 0,
            "row_factory": dict_row,
            "options": f"-c search_path={_AGENT_MEMORY_SEARCH_PATH}",
        },
    )
    await pool.open()
    async with pool.connection() as conn:
        await conn.execute(f"CREATE SCHEMA IF NOT EXISTS {AGENT_MEMORY_SCHEMA}")
    return pool


# ---------------------------------------------------------------------------
# Checkpointer singleton
# ---------------------------------------------------------------------------
_checkpointer = None
_checkpointer_ready = False
_checkpointer_lock = threading.Lock()


async def _init_checkpointer():
    """Cria o AsyncPostgresSaver (sobre DATABASE_URL) + roda setup() idempotente.

    Sem DATABASE_URL, cai para um InMemorySaver de processo (singleton): interrupt/
    resume funcionam dentro de uma instância (dev/teste), só sem durabilidade
    cross-instância. Em prod DATABASE_URL está sempre setado."""
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        from langgraph.checkpoint.memory import InMemorySaver
        logger.warning(
            "checkpointer: DATABASE_URL ausente — InMemorySaver (sem durabilidade "
            "cross-instância). OK em dev/teste; em prod configure DATABASE_URL.",
        )
        return InMemorySaver()

    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    pool = await _make_agent_memory_pool(
        dsn, max_size=int(os.getenv("CHECKPOINTER_POOL_MAX", "4")),
    )
    saver = AsyncPostgresSaver(pool)
    await saver.setup()  # idempotente — checa a própria tabela de migrations
    logger.info(
        "checkpointer: AsyncPostgresSaver pronto (schema %s)", AGENT_MEMORY_SCHEMA,
    )
    return saver


def shutdown_writing_runtime(timeout: float = 10.0) -> None:
    """Fecha o pool do checkpointer e para o loop dedicado — uso em scripts/CLI no
    exit (evita 'Task was destroyed but it is pending' do AsyncConnectionPool morto
    junto com o processo). No-op se nada foi inicializado. Idempotente."""
    # Flush de traces pendentes do Langfuse antes do exit (Etapa 6): em scripts/CLI
    # curtos o batch exporter pode não esvaziar sozinho. No-op se Langfuse off.
    try:
        from core import telemetry
        telemetry.flush()
    except Exception:  # noqa: BLE001 — teardown best-effort
        pass

    global _bg_loop, _checkpointer, _checkpointer_ready
    global _memory_store, _memory_store_ready
    loop, ckpt, store = _bg_loop, _checkpointer, _memory_store
    if loop is None:
        return

    async def _close():
        # AsyncPostgresStore roda uma task de batch em background (_task) — cancela
        # antes de parar o loop (senão cospe GeneratorExit/Event loop closed no exit).
        task = getattr(store, "_task", None)
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        # AsyncPostgresSaver guarda o pool em .conn; AsyncPostgresStore também.
        for obj in (ckpt, store):
            pool = getattr(obj, "conn", None)
            if pool is not None and hasattr(pool, "close"):
                try:
                    await pool.close()
                except Exception:  # noqa: BLE001 — teardown best-effort
                    pass

    try:
        asyncio.run_coroutine_threadsafe(_close(), loop).result(timeout=timeout)
    except Exception:  # noqa: BLE001
        pass
    loop.call_soon_threadsafe(loop.stop)
    _checkpointer = None
    _checkpointer_ready = False
    _memory_store = None
    _memory_store_ready = False
    _bg_loop = None


def _get_writing_checkpointer():
    """Singleton do checkpointer da escrita, criado no loop dedicado. Nunca lança:
    falha de init degrada para None (escrita segue sem durabilidade/interrupt)."""
    global _checkpointer, _checkpointer_ready
    if _checkpointer_ready:
        return _checkpointer
    with _checkpointer_lock:
        if _checkpointer_ready:
            return _checkpointer
        try:
            _checkpointer = _run_on_bg_loop(_init_checkpointer())
        except Exception as e:
            logger.error(
                "checkpointer: init falhou (%s) — escrita segue sem checkpointer", e,
            )
            _checkpointer = None
        _checkpointer_ready = True
    return _checkpointer


# ---------------------------------------------------------------------------
# Checkpointer LOOP-LOCAL para o explore streaming (Item 3, TASK 3)
# ---------------------------------------------------------------------------
# O probe da TASK 1 (spikes/lever3_threads/FINDINGS.md) confirmou: reusar o saver
# do bg-loop (acima) a partir do loop da request/uvicorn explode com "Lock is bound
# to a different event loop" — o pool/lock do AsyncPostgresSaver fica bound ao loop
# onde foi criado. A escrita cruza inteiro pro bg-loop (run_writing_turn é sync); o
# explore STREAMING não pode (precisa yield-ar tokens de volta ao loop da request).
#
# Solução (emenda de governança 2026-07-18): um AsyncPostgresSaver cujo pool é
# aberto NO loop da request, **singleton POR LOOP** (registry keyed por id(loop)).
# Pool SEPARADO do saver do bg-loop; JAMAIS compartilhado entre loops. Uvicorn tem
# tipicamente um loop por worker → na prática um saver por processo, mas a chave-
# por-loop é o guardrail defensivo (proíbe vazar pool entre loops).
# Keyed pelo OBJETO-loop (WeakKeyDictionary): quando o loop é coletado, a entrada
# some — evita o id-reuse de loops fechados (id(loop) pode ser reciclado entre
# loops curtos, ex.: em testes, devolvendo um saver bound a um loop já morto). Em
# prod (um loop longo por worker uvicorn) é uma entrada só.
_explore_checkpointers: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()
_explore_ckpt_locks: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()
_explore_maint_graphs: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()


async def _init_explore_checkpointer():
    """Cria o AsyncPostgresSaver loop-local (pool aberto no loop CORRENTE). Sem
    DATABASE_URL → InMemorySaver de processo (dev/teste). Nunca reusa o pool do
    bg-loop da escrita."""
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        from langgraph.checkpoint.memory import InMemorySaver
        logger.warning(
            "explore checkpointer: DATABASE_URL ausente — InMemorySaver (sem "
            "durabilidade cross-instância). OK em dev/teste.",
        )
        return InMemorySaver()

    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

    pool = await _make_agent_memory_pool(
        dsn, max_size=int(os.getenv("EXPLORE_CHECKPOINTER_POOL_MAX", "4")),
    )
    saver = AsyncPostgresSaver(pool)
    await saver.setup()  # idempotente
    logger.info(
        "explore checkpointer: AsyncPostgresSaver loop-local pronto (schema %s)",
        AGENT_MEMORY_SCHEMA,
    )
    return saver


async def get_explore_checkpointer():
    """Singleton POR LOOP do checkpointer do explore. Init preguiçoso sob lock (o
    lock também é por-loop). N requests no MESMO loop reusam o MESMO saver — nunca
    instancia pool novo por request. Degrada para None em falha de init (explore
    segue stateless)."""
    loop = asyncio.get_running_loop()
    if loop in _explore_checkpointers:
        return _explore_checkpointers[loop]
    # get/set do lock são síncronos (sem await entre eles) → sem corrida.
    lock = _explore_ckpt_locks.get(loop)
    if lock is None:
        lock = asyncio.Lock()
        _explore_ckpt_locks[loop] = lock
    async with lock:
        if loop in _explore_checkpointers:
            return _explore_checkpointers[loop]
        try:
            saver = await _init_explore_checkpointer()
        except Exception as e:
            logger.error(
                "explore checkpointer: init falhou (%s) — explore segue stateless", e,
            )
            saver = None
        _explore_checkpointers[loop] = saver
        return saver


async def aget_thread_message_count(saver, thread_id: str) -> int:
    """Como `get_thread_message_count`, mas awaited no loop CORRENTE sobre o saver
    loop-local do explore (não cruza pro bg-loop). Fonte do `prior_n_msgs` do
    próximo turno (mesmo padrão da T4 — desvio aprovado: lê do checkpointer, sem
    coluna/migration; cada request rehidrata stateless)."""
    if saver is None:
        return 0
    try:
        tup = await saver.aget_tuple({"configurable": {"thread_id": thread_id}})
    except Exception as e:
        logger.warning("aget_thread_message_count(%s) falhou: %s — assume 0", thread_id, e)
        return 0
    if tup is None:
        return 0
    msgs = tup.checkpoint.get("channel_values", {}).get("messages", [])
    return len(msgs)


def _get_explore_maint_graph(saver):
    """Grafo no-op (aplica RemoveMessage via add_messages) compilado com o saver
    loop-local, cacheado por loop. Espelha `_get_state_maint_graph` (bg-loop)."""
    loop = asyncio.get_running_loop()
    g = _explore_maint_graphs.get(loop)
    if g is not None:
        return g
    graph = StateGraph(AgentState)

    async def _noop(state: AgentState) -> dict:
        return {}

    graph.add_node("noop", _noop)
    graph.add_edge(START, "noop")
    graph.add_edge("noop", END)
    compiled = graph.compile(checkpointer=saver)
    _explore_maint_graphs[loop] = compiled
    return compiled


async def atrim_thread_history(
    saver, thread_id: str, *, keep_human_turns: int, keep_ids: tuple[str, ...],
) -> int:
    """Como `trim_thread_history`, mas awaited no loop CORRENTE sobre o saver
    loop-local. Poda na fronteira de turno (nunca intra-turno). Best-effort."""
    from langchain_core.messages import HumanMessage, RemoveMessage

    if saver is None:
        return 0
    config = {"configurable": {"thread_id": thread_id}}
    try:
        tup = await saver.aget_tuple(config)
    except Exception as e:
        logger.warning("atrim_thread_history(%s): aget_tuple falhou: %s", thread_id, e)
        return 0
    if tup is None:
        return 0
    msgs = tup.checkpoint.get("channel_values", {}).get("messages", [])
    episodic = [m for m in msgs if getattr(m, "id", None) not in keep_ids]
    human_idxs = [i for i, m in enumerate(episodic) if isinstance(m, HumanMessage)]
    if len(human_idxs) <= keep_human_turns:
        return 0
    cut = human_idxs[-keep_human_turns]
    to_remove = [m for m in episodic[:cut] if getattr(m, "id", None)]
    if not to_remove:
        return 0
    removals = [RemoveMessage(id=m.id) for m in to_remove]
    try:
        graph = _get_explore_maint_graph(saver)
        await graph.aupdate_state(config, {"messages": removals})
    except Exception as e:
        logger.warning("atrim_thread_history(%s): aupdate_state falhou: %s", thread_id, e)
        return 0
    logger.info(
        "atrim_thread_history: thread=%s removeu=%d (janela=%d turnos)",
        thread_id, len(removals), keep_human_turns,
    )
    return len(removals)


def get_thread_message_count(thread_id: str) -> int:
    """Nº de mensagens acumuladas na thread durável (Item 3 — thread-por-sessão).

    Fonte de verdade do `prior_n_msgs` do PRÓXIMO turno: cada request rehidrata uma
    WritingSession nova, então o count em-memória não sobrevive — lê-se direto do
    checkpointer. Thread inexistente (1º turno) ou sem checkpointer → 0. Roda no
    bg-loop (o pool do AsyncPostgresSaver fica bound a ele)."""
    ckpt = _get_writing_checkpointer()
    if ckpt is None:
        return 0
    try:
        tup = _run_on_bg_loop(
            ckpt.aget_tuple({"configurable": {"thread_id": thread_id}})
        )
    except Exception as e:
        logger.warning("get_thread_message_count(%s) falhou: %s — assume 0", thread_id, e)
        return 0
    if tup is None:
        return 0
    msgs = tup.checkpoint.get("channel_values", {}).get("messages", [])
    return len(msgs)


_state_maint_graph = None
_state_maint_lock = threading.Lock()


def _get_state_maint_graph():
    """Grafo mínimo (nó no-op) só para aplicar updates de canal via `aupdate_state`
    — o reducer `add_messages` processa `RemoveMessage` na poda de histórico
    (Item 3). Compilado com o checkpointer durável, roda no bg-loop."""
    global _state_maint_graph
    if _state_maint_graph is not None:
        return _state_maint_graph
    with _state_maint_lock:
        if _state_maint_graph is not None:
            return _state_maint_graph
        ckpt = _get_writing_checkpointer()
        g = StateGraph(AgentState)

        async def _noop(state: AgentState) -> dict:
            return {}

        g.add_node("noop", _noop)
        g.add_edge(START, "noop")
        g.add_edge("noop", END)
        _state_maint_graph = g.compile(checkpointer=ckpt)
    return _state_maint_graph


def trim_thread_history(
    thread_id: str, *, keep_human_turns: int, keep_ids: tuple[str, ...],
) -> int:
    """Item 3 (Decisão 2) — poda o histórico episódico da thread durável para a
    janela de paridade, na FRONTEIRA de turno (nunca intra-turno — o spike do #2
    mostrou que `start_on="human"` colapsa na cadeia intra-turno).

    Preserva: as mensagens de id estável (`keep_ids` = system + prefixo) e um
    SUFIXO VÁLIDO do episódico que começa numa `HumanMessage` (senão a API recebe
    um `ToolMessage` órfão de seu `AIMessage`). No-op se sob a janela, sem
    checkpointer, ou sem nada a remover. Retorna quantas mensagens removeu.

    Roda no bg-loop (checkpointer bound a ele). É best-effort: falha vira warning,
    o turno segue (poda é higiene de custo, não correção)."""
    from langchain_core.messages import HumanMessage, RemoveMessage

    ckpt = _get_writing_checkpointer()
    if ckpt is None:
        return 0
    config = {"configurable": {"thread_id": thread_id}}
    try:
        tup = _run_on_bg_loop(ckpt.aget_tuple(config))
    except Exception as e:
        logger.warning("trim_thread_history(%s): aget_tuple falhou: %s", thread_id, e)
        return 0
    if tup is None:
        return 0
    msgs = tup.checkpoint.get("channel_values", {}).get("messages", [])
    # Episódico = tudo exceto os blocos de id estável (system/prefixo).
    episodic = [m for m in msgs if getattr(m, "id", None) not in keep_ids]
    human_idxs = [i for i, m in enumerate(episodic) if isinstance(m, HumanMessage)]
    if len(human_idxs) <= keep_human_turns:
        return 0
    # Mantém a partir da k-ésima HumanMessage contada do fim (janela válida).
    cut = human_idxs[-keep_human_turns]
    to_remove = [m for m in episodic[:cut] if getattr(m, "id", None)]
    if not to_remove:
        return 0
    removals = [RemoveMessage(id=m.id) for m in to_remove]
    try:
        graph = _get_state_maint_graph()
        _run_on_bg_loop(graph.aupdate_state(config, {"messages": removals}))
    except Exception as e:
        logger.warning("trim_thread_history(%s): aupdate_state falhou: %s", thread_id, e)
        return 0
    logger.info(
        "trim_thread_history: thread=%s removeu=%d (janela=%d turnos)",
        thread_id, len(removals), keep_human_turns,
    )
    return len(removals)


# ---------------------------------------------------------------------------
# Memory Store singleton (Etapa 5) — projeção read-optimized dos reflection_insights
# ---------------------------------------------------------------------------
# PostgresStore do LangGraph sobre namespace (workspace_id, "insights"): o
# reflection_service ESPELHA put/delete (a escrita autoritativa segue em
# reflection_insights — tabela rica com supersede/audit/weight_suggestions); a
# WritingSession recupera via search semântico query-conditioned. Embeddings OS
# (core.retrieval.embedder, env-parametrizável) → zero token OpenAI quando
# EMBEDDING_BACKEND=sentence_transformers. Mesmo bg-loop do checkpointer (o pool
# async fica bound a ele). Schema dedicado agent_memory (bypassa RLS, ver acima).

MEMORY_NS_INSIGHTS = "insights"  # 2º elemento do namespace: (workspace_id, "insights")

_memory_store = None
_memory_store_ready = False
_memory_store_lock = threading.Lock()


async def _aembed_for_store(texts: list[str]) -> list[list[float]]:
    """AEmbeddingsFunc do Store. `embed_texts` é BLOQUEANTE (ST/OpenAI sync) — roda
    em thread para não travar o bg-loop (onde o pool async vive). Seam de teste:
    monkeypatch este símbolo para um embed fake (zero token)."""
    from core.retrieval.embedder import embed_texts
    return await asyncio.to_thread(embed_texts, texts)


async def _init_memory_store():
    """Cria o AsyncPostgresStore (schema dedicado) + setup() idempotente, com index
    semântico sobre o campo `insight` (embeddings OS).

    Sem DATABASE_URL retorna None — NÃO há fallback InMemoryStore. Diferente do
    checkpointer (cujo interrupt/resume precisa funcionar em dev sem DB), a memória
    semântica é uma camada de ENRIQUECIMENTO com fallback gracioso para o bloco
    estático (load_active_insights). Um InMemoryStore aqui embedaria a query a cada
    turno via OpenAI (premissa MVP: não queimar tokens) e seria efêmero/inútil. Os
    testes injetam um InMemoryStore + embed fake explicitamente (como o checkpointer
    injeta InMemorySaver)."""
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        logger.info(
            "memory_store: DATABASE_URL ausente — memória semântica OFF "
            "(WritingSession usa o bloco estático de insights).",
        )
        return None

    from langgraph.store.postgres.aio import AsyncPostgresStore

    from core.retrieval.embedder import EMBEDDING_DIMENSIONS

    # Lê os dims do ENV em call-time (não a constante import-time do embedder): a
    # coluna pgvector fixa os dims no 1º setup() e precisa casar com o que `embed_texts`
    # produz. Se o módulo embedder foi importado antes do .env carregar, a constante
    # ficaria defasada (ex.: 1536 default vs 768 do modelo OS configurado).
    dims = int(os.environ.get("EMBEDDING_DIMENSIONS", EMBEDDING_DIMENSIONS))
    index = {"dims": dims, "embed": _aembed_for_store, "fields": ["insight"]}
    pool = await _make_agent_memory_pool(
        dsn, max_size=int(os.getenv("MEMORY_STORE_POOL_MAX", "2")),
    )
    store = AsyncPostgresStore(pool, index=index)
    await store.setup()  # idempotente — tabelas store/store_vectors/store_migrations
    logger.info(
        "memory_store: AsyncPostgresStore pronto (schema %s, dims=%d)",
        AGENT_MEMORY_SCHEMA, dims,
    )
    return store


def _get_memory_store():
    """Singleton do Store da memória (bg-loop). Nunca lança: falha de init degrada
    para None → a WritingSession cai no bloco estático (load_active_insights)."""
    global _memory_store, _memory_store_ready
    if _memory_store_ready:
        return _memory_store
    with _memory_store_lock:
        if _memory_store_ready:
            return _memory_store
        try:
            _memory_store = _run_on_bg_loop(_init_memory_store())
        except Exception as e:
            logger.error("memory_store: init falhou (%s) — memória semântica off", e)
            _memory_store = None
        _memory_store_ready = True
    return _memory_store


# ---------------------------------------------------------------------------
# API pública da memória (sync, bg-loop) — consumida por reflection_service /
# writing_session / tool. Todas degradam graciosamente (nunca propagam exceção):
# Store off/falho → no-op (put/delete) ou lista vazia (search).
# ---------------------------------------------------------------------------

def memory_put(workspace_id: str, key: str, insight: str, *, level: int | None = None) -> None:
    """Espelha um insight no Store (namespace por workspace). `key` = id da row em
    reflection_insights → delete idempotente no supersede/deactivate."""
    store = _get_memory_store()
    if store is None or not insight:
        return
    value = {"insight": insight}
    if level is not None:
        value["level"] = level
    try:
        _run_on_bg_loop(store.aput((workspace_id, MEMORY_NS_INSIGHTS), key, value))
    except Exception as e:  # noqa: BLE001 — projeção best-effort, autoritativo é a tabela
        logger.warning("memory_put falhou (ws=%s key=%s): %s", workspace_id, key, e)


def memory_delete(workspace_id: str, key: str) -> None:
    """Remove um insight do Store (supersede/deactivate). Idempotente."""
    store = _get_memory_store()
    if store is None:
        return
    try:
        _run_on_bg_loop(store.adelete((workspace_id, MEMORY_NS_INSIGHTS), key))
    except Exception as e:  # noqa: BLE001
        logger.warning("memory_delete falhou (ws=%s key=%s): %s", workspace_id, key, e)


def memory_search(workspace_id: str, query: str, *, limit: int = 6) -> list[dict]:
    """Busca semântica por insights do workspace. Retorna lista de dicts
    `{"insight","level","score"}` (vazia se Store off/sem query/falha)."""
    store = _get_memory_store()
    if store is None or not (query or "").strip():
        logger.info("tripwire: memory_search hits=0 reason=store_or_query_absent ws=%s", workspace_id)
        return []
    try:
        items = _run_on_bg_loop(
            store.asearch((workspace_id, MEMORY_NS_INSIGHTS), query=query, limit=limit)
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("memory_search falhou (ws=%s): %s", workspace_id, e)
        logger.warning("tripwire: memory_search hits=0 reason=error ws=%s error=%s", workspace_id, e)
        return []
    out: list[dict] = []
    for it in items or []:
        val = it.value or {}
        out.append({
            "insight": val.get("insight", ""),
            "level": val.get("level"),
            "score": getattr(it, "score", None),
        })
    logger.info("tripwire: memory_search hits=%d ws=%s", len(out), workspace_id)
    return out


# ---------------------------------------------------------------------------
# Entry point da escrita: interrupt/resume sobre o checkpointer
# ---------------------------------------------------------------------------

# Id determinístico do system numa thread-por-sessão (Item 3): `add_messages`
# substitui a mensagem de mesmo id em posição em vez de acumular cópias por turno.
# Só precisa ser único DENTRO da thread — uma constante serve todas as sessões.
WR_SYSTEM_MSG_ID = "wr:system"

# Item 3 (TASK 3): id determinístico do system do EXPLORE numa thread-por-sessão.
# O system do explore é MUTÁVEL por turno (muda com a rota) — com id fixo, o
# add_messages o substitui em posição (descoberta A: sem system stale acumulado).
EXPLORE_SYSTEM_MSG_ID = "explore:system"


@dataclass
class WritingTurnOutcome:
    """Resultado de um turno-run da escrita pelo grafo com checkpointer.

    result:    AgentResult do DELTA deste turno (trace/usage), não do thread todo.
    interrupt: payload de request_user_info ({"field","prompt"}) se o grafo pausou
               num interrupt(); None se o turno completou.
    n_messages: len(messages) do estado final — fronteira para fatiar o delta do
               PRÓXIMO resume (o thread acumula pré+pós-interrupt no mesmo state)."""
    result: AgentResult
    interrupt: dict | None
    n_messages: int


async def _writing_turn_async(
    *,
    system: str,
    initial_messages: list[dict[str, Any]],
    tools: list[BaseTool],
    model: str,
    provider: Provider,
    max_steps: int,
    thread_id: str,
    checkpointer,
    resume: Any | None,
    prior_n_msgs: int,
    span_name: str | None,
    temperature: float | None,
    openai_base_url: str | None,
    openai_api_key: str | None,
    mode: str | None = None,
) -> WritingTurnOutcome:
    from langgraph.types import Command

    chat = _build_chat_model(
        provider, model, temperature=temperature,
        openai_base_url=openai_base_url, openai_api_key=openai_api_key,
    )
    graph = _build_graph(
        chat, tools, max_steps=max_steps,
        checkpointer=checkpointer,
    )
    config: dict[str, Any] = {
        "configurable": {"thread_id": thread_id},
        "recursion_limit": 3 * max_steps + 5,
    }

    if resume is not None:
        payload: Any = Command(resume=resume)
    else:
        payload = {
            "messages": [
                # id determinístico (Item 3): numa thread-por-sessão o system é
                # reconstruído a cada turno; `add_messages` o substitui em posição
                # em vez de acumular. `WR_SYSTEM_MSG_ID` só bate dentro da própria
                # thread, então uma constante serve todas as sessões.
                _build_system_message(system, provider, msg_id=WR_SYSTEM_MSG_ID),
                *_to_lc_messages(initial_messages, provider=provider),
            ],
            "llm_calls": 0,
            "tool_rounds": 0,
            "documents": {},
        }

    from core import telemetry

    with telemetry.agent_run(
        name=span_name or f"agent.{provider}.{model}",
        input={"system": system, "resume": resume is not None},
        metadata={
            "provider": provider, "model": model, "max_steps": max_steps,
            "runtime": "langgraph", "thread_id": thread_id,
            "tools": [t.name for t in tools], "mode": mode,
        },
    ) as agent_span:
        # Spans nativos do grafo aninhados sob o turno (Etapa 6). O critic (subagente
        # dentro de save_draft) propaga este trace via current_trace_context.
        handler = telemetry.make_callback_handler()
        if handler is not None:
            config["callbacks"] = [handler]
        try:
            final = await graph.ainvoke(payload, config=config)
        except GraphRecursionError:
            logger.warning("writing_turn: recursion_limit (backstop) — max_steps")
            logger.info(
                "turn_end mode=%s stop_reason=%s llm_calls=%d max_steps=%d",
                mode, "max_steps", 0, max_steps,
            )
            return WritingTurnOutcome(
                AgentResult(final_text="", steps=[], stop_reason="max_steps", usage={}),
                None, prior_n_msgs,
            )
        except Exception as e:
            logger.error("writing_turn: grafo falhou: %s", e)
            if agent_span is not None:
                agent_span.update(level="ERROR", status_message=str(e))
            return WritingTurnOutcome(
                AgentResult(final_text="", steps=[], stop_reason="error", usage={}),
                None, prior_n_msgs,
            )

        msgs = final["messages"]
        # Delta deste turno-run: no resume o thread acumula as mensagens do turno
        # anterior; só traduzimos as NOVAS (a partir de prior_n_msgs) para não
        # dobrar trace/usage (custo). Em turno fresco prior_n_msgs=0 (delta=tudo).
        delta = msgs[prior_n_msgs:]

        interrupts = final.get("__interrupt__")
        intr_payload: dict | None = None
        if interrupts:
            v = interrupts[0].value
            intr_payload = v if isinstance(v, dict) else {"prompt": str(v)}
            stop: StopReason = "end_turn"
        else:
            stop = _derive_stop_reason(final, max_steps)

        result = _messages_to_agent_result(delta, stop)
        logger.info(
            "turn_end mode=%s stop_reason=%s llm_calls=%d max_steps=%d",
            mode, stop, final.get("llm_calls", 0), max_steps,
        )

        if agent_span is not None:
            agent_span.update(
                output={"final_text": result.final_text, "interrupted": bool(intr_payload)},
                metadata={"stop_reason": result.stop_reason, "n_steps": len(result.steps),
                          "usage": result.usage},
            )

    return WritingTurnOutcome(result, intr_payload, len(msgs))


def run_writing_turn(
    *,
    system: str,
    initial_messages: list[dict[str, Any]],
    tools: list[BaseTool],
    model: str,
    provider: Provider = "anthropic",
    max_steps: int = 8,
    thread_id: str,
    resume: Any | None = None,
    prior_n_msgs: int = 0,
    span_name: str | None = None,
    temperature: float | None = None,
    openai_base_url: str | None = None,
    openai_api_key: str | None = None,
    mode: str | None = None,
) -> WritingTurnOutcome:
    """Roda UM turno-run da escrita pelo grafo com checkpointer durável (sync).

    Diferente de `run_agent_graph_async` (stateless, usado pelos outros call sites),
    este persiste o estado por `thread_id` e suporta interrupt/resume:
      • `resume=None`  → turno fresco (semeia o state de `initial_messages`);
      • `resume=valor` → retoma o thread no ponto do interrupt() com `Command(resume)`.

    Roda no loop dedicado (o checkpointer async fica bound a ele). Retorna um
    `WritingTurnOutcome` (result do delta + payload de interrupt + n_messages)."""
    checkpointer = _get_writing_checkpointer()
    return _run_on_bg_loop(_writing_turn_async(
        system=system,
        initial_messages=initial_messages,
        tools=tools,
        model=model,
        provider=provider,
        max_steps=max_steps,
        thread_id=thread_id,
        checkpointer=checkpointer,
        resume=resume,
        prior_n_msgs=prior_n_msgs,
        span_name=span_name,
        temperature=temperature,
        openai_base_url=openai_base_url,
        openai_api_key=openai_api_key,
        mode=mode,
    ))


# =============================================================================
# Geração de proposta completa (batch) — asyncio.gather + agente por seção
# =============================================================================
# Orquestração PARALELA (spec hardening PR3): as seções não têm dependência
# semântica entre si (a ordem sequencial anterior era acidente de implementação),
# então cada seção roda o agente interno (`_build_graph`, INALTERADO,
# checkpointer=False) como um run stateless independente, com concorrência
# limitada por Semaphore (env GENERATION_CONCURRENCY, default 4 — TPM do
# gpt-4o-mini folgado). A persistência da seção é side effect do save_draft;
# a retomada após crash é feita pelo caller filtrando seções já preenchidas —
# simples e idempotente (ver WritingSession).


@dataclass
class GenerationOutcome:
    """Resultado de uma run de geração em lote.

    sections_done / failed_sections: partição das seções processadas nesta run
    (na ordem de `sections`, independente da ordem de término).
    results: AgentResult por seção processada (trace/usage) — só desta run.
    usage: soma de input/output tokens das seções desta run."""
    sections_done: list[str]
    failed_sections: list[str]
    results: list[AgentResult]
    usage: dict


def _generation_concurrency() -> int:
    """Concorrência do batch de seções (env GENERATION_CONCURRENCY, default 4)."""
    raw = os.getenv("GENERATION_CONCURRENCY", "4")
    try:
        return max(1, int(raw))
    except ValueError:
        logger.warning("GENERATION_CONCURRENCY inválida (%r) — usando 4", raw)
        return 4


def _try_parse_generation_json(text: str) -> dict | None:
    """Tenta extrair {content, citations} da resposta do LLM.

    Aceita JSON puro ou envolvido em ```json ... ```. Retorna dict ou None."""
    if not text:
        return None
    raw = text.strip()
    if "```" in raw:
        raw = re.sub(r"```(?:json)?", "", raw).strip().rstrip("`").strip()
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(data, dict) or not data.get("content", "").strip():
        return None
    return data


async def _generate_section(
    inner_graph,
    section: str,
    *,
    system: str,
    build_section_messages: Callable[[str], list[dict[str, Any]]],
    max_steps: int,
    verify_saved: Callable[[str], bool] | None = None,
    on_result: Callable[[AgentResult], None] | None = None,
    auto_save: Callable[[str, str], None] | None = None,
    callbacks: list[Any] | None = None,
) -> bool:
    """Roda o agente interno para UMA seção e a classifica (True=done, False=failed).

    `verify_saved(section)` decide o sucesso olhando o efeito real (a seção foi
    persistida?), não a fala do agente — o save_draft é a fonte de verdade. Sem
    callback, cai em "stop_reason != error".

    `auto_save(section, text)` parseia a última resposta do agente como
    JSON `{content, citations}`, extrai `content` e persiste via callback. Se o
    parse falhar ou o content for vazio, a seção é marcada como failed e cai no
    fallback universal da WritingSession.

    Isolamento por seção: QUALQUER exceção vira False (seção → failed_sections)
    sem derrubar o lote — o fallback universal da WritingSession cobre o resto."""
    try:
        logger.info("generation: iniciando seção '%s'", section)
        init: AgentState = {
            "messages": [
                SystemMessage(content=system),
                *_to_lc_messages(build_section_messages(section)),
            ],
            "llm_calls": 0,
            "tool_rounds": 0,
            "documents": {},
        }
        config: dict[str, Any] = {"recursion_limit": 3 * max_steps + 5}
        if callbacks:
            config["callbacks"] = callbacks

        final = await inner_graph.ainvoke(init, config=config)
        msgs = final["messages"]
        n_calls = sum(1 for m in msgs if isinstance(m, AIMessage) and m.tool_calls)
        logger.info("generation: seção '%s' concluída (%d steps, %d tool calls)",
                    section, len(msgs), n_calls)
        if n_calls == 0:
            logger.warning("tripwire: generation_tools_zero section=%s n_calls=0", section)
        stop = _derive_stop_reason(final, max_steps)
        result = _messages_to_agent_result(msgs, stop)
        if on_result is not None:
            on_result(result)
        ok = verify_saved(section) if verify_saved else (result.stop_reason != "error")

        if ok and verify_saved:
            logger.info("tripwire: generation_path path=structured section=%s", section)
        elif not ok and auto_save is not None:
            # Contrato quote-first: parseia JSON {content, citations}.
            last_text = result.final_text or ""
            parsed = _try_parse_generation_json(last_text)
            if parsed and parsed.get("content", "").strip():
                content = parsed["content"]
                logger.info("tripwire: generation_path path=structured section=%s", section)
                auto_save(section, content)
                ok = verify_saved(section) if verify_saved else True
            else:
                logger.info("tripwire: generation_path path=fallback_raw section=%s", section)

        if not ok:
            logger.info("generation: '%s' → fallback na WS", section)
        return ok
    except Exception as e:  # noqa: BLE001 — uma seção que quebra não derruba o lote
        logger.error("generation: seção '%s' falhou: %s", section, e)
        return False


async def _generation_turn_async(
    *,
    system: str,
    build_section_messages: Callable[[str], list[dict[str, Any]]],
    sections: list[str],
    tools: list[BaseTool],
    model: str,
    provider: Provider,
    max_steps: int,
    thread_id: str,
    verify_saved: Callable[[str], bool] | None,
    auto_save: Callable[[str, str], None] | None = None,
    span_name: str | None,
    temperature: float | None,
    openai_base_url: str | None,
    openai_api_key: str | None,
) -> GenerationOutcome:
    chat = _build_chat_model(
        provider, model, temperature=temperature,
        openai_base_url=openai_base_url, openai_api_key=openai_api_key,
    )
    # Agente interno: o grafo de escrita EXISTENTE, stateless por seção
    # (checkpointer=False — cada seção é um run independente, sem herança).
    # O mesmo grafo compilado é compartilhado pelos runs concorrentes (o estado
    # vive no invoke, não no grafo).
    inner = _build_graph(
        chat, tools, max_steps=max_steps, checkpointer=False,
    )

    concurrency = _generation_concurrency()
    sem = asyncio.Semaphore(concurrency)
    results: list[AgentResult] = []
    status: dict[str, bool] = {}

    async def worker(section: str, callbacks: list[Any] | None) -> None:
        async with sem:
            ok = await _generate_section(
                inner, section,
                system=system,
                build_section_messages=build_section_messages,
                max_steps=max_steps,
                verify_saved=verify_saved,
                on_result=results.append,
                auto_save=auto_save,
                callbacks=callbacks,
            )
        status[section] = ok

    from core import telemetry

    with telemetry.agent_run(
        name=span_name or f"generation.{provider}.{model}",
        input={"sections": sections},
        metadata={
            "provider": provider, "model": model, "runtime": "langgraph",
            "mode": "generation", "thread_id": thread_id,
            "n_sections": len(sections), "concurrency": concurrency,
            "tools": [t.name for t in tools],
        },
    ) as agent_span:
        handler = telemetry.make_callback_handler()
        callbacks = [handler] if handler is not None else None
        try:
            logger.info("generation: %d seções em paralelo (concurrency=%d, timeout=300s)",
                        len(sections), concurrency)
            await asyncio.wait_for(
                asyncio.gather(*(worker(s, callbacks) for s in sections)),
                timeout=300,
            )
        except asyncio.TimeoutError:
            # Seções em voo são canceladas e caem em failed; as já terminadas
            # ficam preservadas em `status` (o save é side effect por seção).
            logger.error("generation: timeout após 300s (%d seções sem terminar)",
                         sum(1 for s in sections if s not in status))
            if agent_span is not None:
                agent_span.update(level="ERROR", status_message="timeout")
        except Exception as e:  # backstop — _generate_section já isola por seção
            logger.error("generation: orquestração falhou: %s", e)
            if agent_span is not None:
                agent_span.update(level="ERROR", status_message=str(e))

        # Partição na ordem de entrada (a ordem de término é não-determinística).
        done = [s for s in sections if status.get(s)]
        failed = [s for s in sections if not status.get(s)]
        usage = {
            "input_tokens": sum(r.usage.get("input_tokens", 0) for r in results),
            "output_tokens": sum(r.usage.get("output_tokens", 0) for r in results),
        }
        if agent_span is not None:
            agent_span.update(
                output={"sections_done": done, "failed_sections": failed},
                metadata={"usage": usage, "n_done": len(done), "n_failed": len(failed)},
            )

    return GenerationOutcome(
        sections_done=done, failed_sections=failed, results=results, usage=usage,
    )


def run_generation_turn(
    *,
    system: str,
    build_section_messages: Callable[[str], list[dict[str, Any]]],
    sections: list[str],
    tools: list[BaseTool],
    model: str,
    provider: Provider = "anthropic",
    max_steps: int = 8,
    thread_id: str,
    verify_saved: Callable[[str], bool] | None = None,
    auto_save: Callable[[str, str], None] | None = None,
    span_name: str | None = None,
    temperature: float | None = None,
    openai_base_url: str | None = None,
    openai_api_key: str | None = None,
) -> GenerationOutcome:
    """Entry point sync da geração em lote (análogo a `run_writing_turn`).

    Despacha as seções em PARALELO (asyncio.gather; concorrência limitada por
    GENERATION_CONCURRENCY, default 4) num event loop fresco via ``asyncio.run()``
    — o caller é sync e a geração não usa o checkpointer (cada seção é um run
    stateless; a persistência é side effect do save_draft/auto_save).
    `build_section_messages` é chamado por seção para montar o prompt de escrita
    daquela seção; `system` é o prompt do agente interno no modo geração."""
    return asyncio.run(_generation_turn_async(
        system=system,
        build_section_messages=build_section_messages,
        sections=sections,
        tools=tools,
        model=model,
        provider=provider,
        max_steps=max_steps,
        thread_id=thread_id,
        verify_saved=verify_saved,
        auto_save=auto_save,
        span_name=span_name,
        temperature=temperature,
        openai_base_url=openai_base_url,
        openai_api_key=openai_api_key,
    ))
