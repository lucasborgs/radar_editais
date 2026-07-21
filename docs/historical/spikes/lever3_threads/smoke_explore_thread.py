"""Smoke T3 (throwaway): explore_stream REAL multi-turno com thread-por-sessão.

Dirige o produtor vivo (`ExploreAgent.explore_stream`) contra o saver loop-local +
Postgres LOCAL :54322 + gpt-4o-mini. Prova o critério de promoção do explore:
  • turno 1 planta um fato; turno 2 pergunta sobre ele passando history=[] (NÃO
    re-seeda) — se o agente lembra, é o checkpointer replayando a thread.
  • turno anônimo (thread_id=None) segue stateless (não lembra) — caminho de hoje.

Rodar do worktree (lição #4: sys.path). Ambiente: .env.staging-local + OPENAI.
"""
from __future__ import annotations

import asyncio
import os
import sys
import uuid

sys.path.insert(0, os.getcwd())

from core.environment import load_environment_profile  # noqa: E402

load_environment_profile()

from core.services.explore_agent import ExploreAgent  # noqa: E402

FACT = "Meu projeto se chama Zephyr-9 e atua na área de energia eólica offshore."
WS = os.environ.get("EVAL_WORKSPACE_ID", "dca65d63-a340-498f-92df-f2634316df32")


async def _run(agent, message, thread_id, history):
    answer = ""
    async for ev in agent.explore_stream(
        message, history=history, thread_id=thread_id,
    ):
        if ev.kind == "final":
            answer = ev.answer
    return answer


async def main() -> int:
    agent = ExploreAgent()
    tid = f"{WS}:smoke-{uuid.uuid4().hex[:8]}"

    print(f"[thread] {tid}\n")
    a1 = await _run(agent, FACT + " Guarde isso.", tid, history=[])
    print(f"[turno 1] {a1[:160]}\n")

    # Turno 2: history=[] DE PROPÓSITO — se lembrar, foi o checkpointer, não re-seed.
    a2 = await _run(agent, "Qual é o nome do meu projeto e em que área ele atua?", tid, history=[])
    print(f"[turno 2 (history=[])] {a2[:240]}\n")

    remembered = "zephyr" in a2.lower() or "eólica" in a2.lower() or "eolica" in a2.lower()
    print(f"[MEMÓRIA via checkpointer] {'OK ✅' if remembered else 'FALHOU ❌'}")

    # Controle: thread_id=None (anônimo) → stateless, NÃO lembra sem history.
    a3 = await _run(agent, "Qual é o nome do meu projeto?", None, history=[])
    stateless_forgot = "zephyr" not in a3.lower()
    print(f"[turno controle (thread=None, history=[])] {a3[:160]}")
    print(f"[STATELESS sem memória] {'OK ✅' if stateless_forgot else 'INESPERADO ❌'}")

    print(f"\n=== SMOKE {'PASS' if (remembered and stateless_forgot) else 'FAIL'} ===")
    return 0 if (remembered and stateless_forgot) else 1


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    finally:
        from core.llm.agent_graph import shutdown_writing_runtime
        shutdown_writing_runtime()
