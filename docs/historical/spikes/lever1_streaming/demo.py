"""Spike TASK 1 (Item 1 — Streaming): demo/medição contra o grafo REAL do explore.

Throwaway. Nada aqui vira código de produção — só valida, contra `_build_graph`
e os tradutores reais de `core/llm/agent_graph.py`, se dá para (a) emitir tokens
antes do fim do turno e (b) reconstruir `AgentResult` com paridade de
usage/trace a partir do estado final do stream.

GAP CONHECIDO (decisão tomada com o usuário antes de rodar, ver FINDINGS.md):
não há ANTHROPIC_API_KEY neste ambiente local (mesmo gap do spike anterior,
`spikes/agent_sdk_explore`). Este script roda só o transporte OpenAI (via
OPENAI_API_KEY — mesmo branch de código de `_build_chat_model` que um
OpenAI-compat real usaria; só falta o `base_url` custom). O veredito de
transporte (astream_events vs astream multi-mode) e o achado do gap de
`stream_usage` são válidos independente do provider — são mecânica do
LangChain/LangGraph, não do backend do modelo.

Uso:
    .venv/bin/python3 -m spikes.lever1_streaming.demo
"""
from __future__ import annotations

import asyncio
import time

from dotenv import load_dotenv

load_dotenv()

from langchain_core.messages import AIMessageChunk  # noqa: E402

from radar.core.llm.agent_graph import (  # noqa: E402
    _build_chat_model,
    _build_graph,
    _build_system_message,
    _derive_stop_reason,
    _messages_to_agent_result,
    _to_lc_messages,
    shutdown_writing_runtime,
)
from radar.core.llm.agent_runtime import AgentResult, resolve_agent_provider  # noqa: E402
from radar.core.services.explore_agent import (  # noqa: E402
    EXPLORE_AGENT_SYSTEM,
    ExploreAgent,
)

MESSAGE = "Quais editais estão abertos agora? Liste até 3 com um resumo breve de cada um."
MAX_STEPS = 8


def _delta_text(chunk) -> str:
    c = chunk.content
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        parts = []
        for block in c:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
        return "".join(parts)
    return ""


def _tool_names(result: AgentResult) -> list[str]:
    return [s.name for s in result.steps if s.kind == "tool" and s.name]


def _print_result(label: str, result: AgentResult) -> None:
    print(f"\n--- {label} ---")
    print(f"stop_reason={result.stop_reason} n_steps={len(result.steps)} "
          f"usage={result.usage} tools={_tool_names(result)}")
    print(f"final_text[:200]={result.final_text[:200]!r}")


def _build_init(system: str, messages: list[dict], provider: str):
    return {
        "messages": [
            _build_system_message(system, provider),
            *_to_lc_messages([dict(m) for m in messages], provider=provider),
        ],
        "llm_calls": 0,
        "tool_rounds": 0,
        "documents": {},
    }


async def run_ainvoke_reference(chat, tools, system, messages, provider) -> tuple[AgentResult, float]:
    graph = _build_graph(chat, tools, max_steps=MAX_STEPS, checkpointer=False)
    init = _build_init(system, messages, provider)
    t0 = time.monotonic()
    final = await graph.ainvoke(init, config={"recursion_limit": 3 * MAX_STEPS + 5})
    total = time.monotonic() - t0
    stop = _derive_stop_reason(final, MAX_STEPS)
    result = _messages_to_agent_result(final["messages"], stop)
    return result, total


async def run_astream_events_v2(chat, tools, system, messages, provider) -> tuple[AgentResult, float, float]:
    graph = _build_graph(chat, tools, max_steps=MAX_STEPS, checkpointer=False)
    init = _build_init(system, messages, provider)
    t0 = time.monotonic()
    ttft = None
    final_state = None
    n_tokens = 0
    async for event in graph.astream_events(
        init, config={"recursion_limit": 3 * MAX_STEPS + 5}, version="v2",
    ):
        if event["event"] == "on_chat_model_stream":
            delta = _delta_text(event["data"]["chunk"])
            if delta:
                n_tokens += 1
                if ttft is None:
                    ttft = time.monotonic() - t0
                    print(f"[astream_events] primeiro token em {ttft:.3f}s: {delta!r}")
        elif event["event"] == "on_chain_end":
            out = event["data"].get("output")
            if isinstance(out, dict) and {"messages", "llm_calls", "tool_rounds", "documents"} <= out.keys():
                final_state = out
    total = time.monotonic() - t0
    assert final_state is not None, "astream_events v2: não capturei o estado final da raiz"
    stop = _derive_stop_reason(final_state, MAX_STEPS)
    result = _messages_to_agent_result(final_state["messages"], stop)
    print(f"[astream_events v2] tokens capturados={n_tokens} latência_total={total:.3f}s")
    return result, ttft or total, total


async def run_astream_multimode(chat, tools, system, messages, provider) -> tuple[AgentResult, float, float]:
    graph = _build_graph(chat, tools, max_steps=MAX_STEPS, checkpointer=False)
    init = _build_init(system, messages, provider)
    t0 = time.monotonic()
    ttft = None
    final_state = None
    n_tokens = 0
    async for mode, chunk in graph.astream(
        init, config={"recursion_limit": 3 * MAX_STEPS + 5}, stream_mode=["messages", "values"],
    ):
        if mode == "messages":
            msg_chunk, _meta = chunk
            # "messages" mode emite tanto AIMessageChunk (token delta real) quanto
            # ToolMessage completo (resultado da tool, não é um delta) — achado da
            # TASK 1: sem este filtro, o texto bruto da tool é lido como "token".
            if not isinstance(msg_chunk, AIMessageChunk):
                continue
            delta = _delta_text(msg_chunk)
            if delta:
                n_tokens += 1
                if ttft is None:
                    ttft = time.monotonic() - t0
                    print(f"[astream multimode] primeiro token em {ttft:.3f}s: {delta!r}")
        elif mode == "values":
            final_state = chunk
    total = time.monotonic() - t0
    assert final_state is not None, "astream multimode: não capturei o estado final (values)"
    stop = _derive_stop_reason(final_state, MAX_STEPS)
    result = _messages_to_agent_result(final_state["messages"], stop)
    print(f"[astream multimode] tokens capturados={n_tokens} latência_total={total:.3f}s")
    return result, ttft or total, total


async def probe_openai_usage_gap(model: str) -> None:
    """Item 7 do plano: prova empírica do gap de usage no streaming da OpenAI."""
    from langchain_openai import ChatOpenAI

    msgs = [{"role": "user", "content": "Diga 'oi' em 3 palavras."}]
    from langchain_core.messages import HumanMessage

    for stream_usage in (False, True):
        kw = {"model": model, "timeout": 60, "max_retries": 2}
        if stream_usage:
            kw["stream_usage"] = True
        chat = ChatOpenAI(**kw)
        full = None
        async for chunk in chat.astream([HumanMessage(content=msgs[0]["content"])]):
            full = chunk if full is None else full + chunk
        um = getattr(full, "usage_metadata", None)
        print(f"[usage-gap] stream_usage={stream_usage} → usage_metadata={um}")


def _compare(ref: AgentResult, other: AgentResult, label: str) -> bool:
    ok = True
    checks = {
        "final_text": ref.final_text == other.final_text,
        "stop_reason": ref.stop_reason == other.stop_reason,
        "n_steps": len(ref.steps) == len(other.steps),
        "tools": _tool_names(ref) == _tool_names(other),
        "usage": ref.usage == other.usage,
    }
    print(f"\n[paridade {label} vs ainvoke]")
    for k, v in checks.items():
        print(f"  {k}: {'OK' if v else 'DIVERGIU'}")
        if not v:
            ok = False
    return ok


async def main() -> None:
    provider, model = resolve_agent_provider("anthropic", "claude-sonnet-4-6")
    print(f"provider resolvido={provider} model={model}")
    if provider != "openai":
        print("AVISO: provider != openai — ambiente mudou desde o gap documentado.")

    tools = ExploreAgent()._explore_tools()
    system = EXPLORE_AGENT_SYSTEM
    messages = [{"role": "user", "content": MESSAGE}]

    # temperature=0 reduz (não elimina) a variância entre as 3 chamadas
    # independentes ao modelo — sem isto, final_text/n_steps/tools divergem
    # entre ainvoke/astream_events/multimode por estocástico puro, não por
    # bug de plumbing (achado da TASK 1).
    chat = _build_chat_model(provider, model, temperature=0)

    ref_result, ref_total = await run_ainvoke_reference(chat, tools, system, messages, provider)
    _print_result("ainvoke (referência)", ref_result)

    ev_result, ev_ttft, ev_total = await run_astream_events_v2(chat, tools, system, messages, provider)
    _print_result("astream_events v2", ev_result)

    mm_result, mm_ttft, mm_total = await run_astream_multimode(chat, tools, system, messages, provider)
    _print_result("astream multimode", mm_result)

    print("\n=== LATÊNCIA ===")
    print(f"ainvoke total-until-response: {ref_total:.3f}s")
    print(f"astream_events v2 TTFT={ev_ttft:.3f}s  total={ev_total:.3f}s")
    print(f"astream multimode TTFT={mm_ttft:.3f}s  total={mm_total:.3f}s")

    ok_ev = _compare(ref_result, ev_result, "astream_events")
    ok_mm = _compare(ref_result, mm_result, "astream multimode")

    print("\n=== GAP DE USAGE (OpenAI stream_usage) ===")
    await probe_openai_usage_gap(model)

    print("\n=== VEREDITO ===")
    print(f"astream_events paridade completa: {ok_ev}")
    print(f"astream multimode paridade completa: {ok_mm}")
    print(f"tokens antes do fim (streaming real): "
          f"astream_events={ev_ttft < ref_total}, multimode={mm_ttft < ref_total}")

    shutdown_writing_runtime()


if __name__ == "__main__":
    asyncio.run(main())
