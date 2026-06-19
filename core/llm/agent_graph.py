"""Spike Etapa 1 — runtime ReAct sobre LangGraph (StateGraph), atrás de flag.

Prova de fundação da migração (docs/specs/langgraph-migration.md):
substitui o loop hand-rolled de `run_agent_async` por um `StateGraph` de 3 nós
(agent / tools / reflect), traduzindo o estado final de volta para o contrato
`AgentResult` — sem tocar em nenhum call site.

Dispatch: `run_agent_async` delega aqui quando `AGENT_RUNTIME=langgraph`. O
default (`legacy`) mantém 100% o loop atual.

Decisões fechadas (Etapa 1):
  • Cap de iterações = contador `llm_calls` em state (paridade exata com
    `for ... range(max_steps)`), NÃO `recursion_limit`. Este vira só backstop.
  • Reflexão = nó dedicado condicional entre `tools` e `agent` (visível no trace),
    não `pre_model_hook` (que só existe no prebuilt do qual saímos).

Fora de escopo do spike (etapas seguintes): telemetria via callback (Etapa 6),
checkpointer (Etapa 3), troca de `@tool` p/ LangChain (Etapa 2 — aqui usamos o
bridge `Tool → StructuredTool`).
"""
from __future__ import annotations

import logging
import os
from collections.abc import Callable
from typing import Annotated, Any
from typing_extensions import TypedDict

from langchain_core.messages import (
    AIMessage,
    AnyMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.tools import StructuredTool
from langgraph.errors import GraphRecursionError
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode

from core.llm.agent_runtime import (
    _MAX_RETRIES,
    _PLAN_TOOL_NAMES,
    _REFLECT_CHAR_THRESHOLD,
    _REFLECT_PROMPT,
    _TIMEOUT,
    TOOL_RESULT_CHAR_CAP,
    AgentResult,
    Provider,
    StopReason,
    Tool,
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
    rounds_since_reflect: int
    chars_since_reflect: int
    reflect_pending: bool


# =============================================================================
# Bridge Tool → StructuredTool (preserva _cap + error-string; removido na Etapa 2)
# =============================================================================

def _to_lc_tool(t: Tool) -> StructuredTool:
    """Envolve nossa `Tool` numa `StructuredTool` LangChain preservando:
      • degradação graciosa (erro → string `"Erro ao executar 'X': …"`),
      • cap central de tool-result (`_cap`).
    O schema de args é inferido da função original (mesmos type hints)."""
    async def _runner(**kwargs: Any) -> str:
        out = await t.call_async(kwargs)
        return _cap(out, TOOL_RESULT_CHAR_CAP, tool_name=t.name)

    # from_function infere args_schema da func original; coroutine executa o wrapper.
    return StructuredTool.from_function(
        func=t.func,
        coroutine=_runner,
        name=t.name,
        description=t.description,
    )


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
    kw = {"model": model, "timeout": _TIMEOUT, "max_retries": _MAX_RETRIES}
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

def _build_graph(model, lc_tools: list[StructuredTool], *, max_steps: int, reflect_every: int | None):
    bound = model.bind_tools(lc_tools) if lc_tools else model
    tool_node = ToolNode(lc_tools)

    async def agent(state: AgentState) -> dict:
        resp = await bound.ainvoke(state["messages"])
        return {"messages": [resp], "llm_calls": state["llm_calls"] + 1}

    def should_continue(state: AgentState) -> str:
        last = state["messages"][-1]
        if not getattr(last, "tool_calls", None):
            return END  # fim natural do turno
        return "tools"

    async def tools(state: AgentState) -> dict:
        out = await tool_node.ainvoke(state)
        tmsgs = out["messages"]
        chars = sum(len(str(m.content)) for m in tmsgs)
        rsr = state["rounds_since_reflect"] + 1
        csr = state["chars_since_reflect"] + chars
        # Sinais leves de reflexão dinâmica (spec 08, espelha o loop legado).
        had_error = any(
            str(m.content).startswith(("Erro:", "Erro ao executar")) for m in tmsgs
        )
        plan_changed = any(getattr(m, "name", None) in _PLAN_TOOL_NAMES for m in tmsgs)
        big_output = csr >= _REFLECT_CHAR_THRESHOLD
        hit_ceiling = bool(reflect_every) and rsr >= reflect_every
        do_reflect = bool(reflect_every) and (
            had_error or plan_changed or big_output or hit_ceiling
        )
        return {
            "messages": tmsgs,
            "tool_rounds": state["tool_rounds"] + 1,
            "rounds_since_reflect": rsr,
            "chars_since_reflect": csr,
            "reflect_pending": do_reflect,
        }

    def after_tools(state: AgentState) -> str:
        # Cap pós-tools (paridade com o legado: as tools da última rodada SÃO
        # executadas — já rodaram no nó `tools` — e só então o teto corta).
        if state["llm_calls"] >= max_steps:
            return END
        return "reflect" if state.get("reflect_pending") else "agent"

    def reflect(state: AgentState) -> dict:
        return {
            "messages": [HumanMessage(content=_REFLECT_PROMPT)],
            "rounds_since_reflect": 0,
            "chars_since_reflect": 0,
            "reflect_pending": False,
        }

    g = StateGraph(AgentState)
    g.add_node("agent", agent)
    g.add_node("tools", tools)
    g.add_node("reflect", reflect)
    g.add_edge(START, "agent")
    g.add_conditional_edges("agent", should_continue, {END: END, "tools": "tools"})
    g.add_conditional_edges("tools", after_tools, {END: END, "reflect": "reflect", "agent": "agent"})
    g.add_edge("reflect", "agent")
    return g.compile()


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


# =============================================================================
# Entry point (delegado pela facade quando AGENT_RUNTIME=langgraph)
# =============================================================================

def _to_lc_messages(initial: list[dict[str, Any]]) -> list[AnyMessage]:
    out: list[AnyMessage] = []
    for m in initial:
        role, content = m.get("role"), m.get("content", "")
        if role == "assistant":
            out.append(AIMessage(content=content))
        elif role == "system":
            out.append(SystemMessage(content=content))
        else:
            out.append(HumanMessage(content=content))
    return out


async def run_agent_graph_async(
    *,
    system: str,
    initial_messages: list[dict[str, Any]],
    tools: list[Tool],
    model: str,
    provider: Provider = "anthropic",
    max_steps: int = 8,
    on_step: Callable[[TraceStep], None] | None = None,
    reflect_every: int | None = None,
    span_name: str | None = None,
    temperature: float | None = None,
    openai_base_url: str | None = None,
    openai_api_key: str | None = None,
) -> AgentResult:
    """Equivalente LangGraph de `run_agent_async` (mesma assinatura/contrato)."""
    lc_tools = [_to_lc_tool(t) for t in tools]
    chat = _build_chat_model(
        provider, model,
        temperature=temperature,
        openai_base_url=openai_base_url,
        openai_api_key=openai_api_key,
    )
    graph = _build_graph(chat, lc_tools, max_steps=max_steps, reflect_every=reflect_every)

    init: AgentState = {
        "messages": [SystemMessage(content=system), *_to_lc_messages(initial_messages)],
        "llm_calls": 0,
        "tool_rounds": 0,
        "rounds_since_reflect": 0,
        "chars_since_reflect": 0,
        "reflect_pending": False,
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
            "tools": [t.name for t in tools],
        },
    ) as agent_span:
        try:
            final = await graph.ainvoke(
                init, config={"recursion_limit": 3 * max_steps + 5},
            )
        except GraphRecursionError:
            # Backstop — não deveria disparar (teto real é llm_calls). Trata como max_steps.
            logger.warning("agent_graph: recursion_limit atingido (backstop) — max_steps")
            return AgentResult(final_text="", steps=[], stop_reason="max_steps", usage={})
        except Exception as e:
            logger.error("agent_graph: grafo falhou: %s", e)
            if agent_span is not None:
                agent_span.update(level="ERROR", status_message=str(e))
            return AgentResult(final_text="", steps=[], stop_reason="error", usage={})

        msgs = final["messages"]
        last_ai = next((m for m in reversed(msgs) if isinstance(m, AIMessage)), None)
        stop: StopReason = "max_steps" if (last_ai and last_ai.tool_calls) else "end_turn"
        result = _messages_to_agent_result(msgs, stop)

        # Telemetria mínima (Etapa 1): spans por-step replicados pós-hoc a partir
        # do trace — preserva o rollup de custo por turno (usage_details) que o
        # runtime legado emite. Timing real + nesting nativo = Etapa 6 (callbacks).
        _replay_step_spans(result, model)

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


def _replay_step_spans(result: AgentResult, model: str) -> None:
    """Emite spans Langfuse por-step a partir do trace já materializado.

    Reusa os context managers de `core.telemetry` (sem dep LangChain). Custo
    (usage_details) é exato; o timing é aproximado (pós-hoc) — a versão com
    timing real via callbacks nativos LangChain é a Etapa 6."""
    from core import telemetry

    if not telemetry.is_enabled():
        return
    for i, s in enumerate(result.steps):
        if s.kind == "llm":
            with telemetry.llm_generation(
                name=f"llm.step_{i}", model=model, input=None,
                metadata={"tool_uses": s.tool_uses},
            ) as g:
                if g is not None:
                    g.update(output={"text": s.text})
                    if s.usage:
                        g.update(usage_details={
                            "input": s.usage.get("input_tokens", 0),
                            "output": s.usage.get("output_tokens", 0),
                        })
        else:
            with telemetry.tool_call(name=f"tool.{s.name}", input=s.input) as t:
                if t is not None:
                    t.update(output=s.output)
