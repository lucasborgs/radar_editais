"""Spike TASK 1 (Item 3 — Thread por sessão / checkpointer como memória).

Throwaway. Nada aqui vira código de produção. Prova, contra o grafo REAL do
explore (`_build_graph` + tools/system reais) sobre o AsyncPostgresSaver REAL
(`_get_writing_checkpointer`), as duas condições do checkpoint GO/NO-GO da spec:

  (a) uma thread de ESCOPO DE SESSÃO acumula o histórico e os turnos 2/3 NÃO
      re-seedam — o produtor manda só a mensagem nova e o modelo ainda "vê" o
      turno 1 (prova de memória lida do checkpointer, não reinjetada);
  (b) `aupdate_state` a partir de um checkpoint intermediário FORKA em duas
      continuações divergentes do mesmo ponto.

E retira o desconhecido de infra da descoberta #2 do plano (o PROBE de
loop-binding): rodar UM ainvoke com o saver a partir de um loop DIFERENTE do
bg-loop dedicado e registrar se explode com "Lock is bound to a different event
loop". Esse veredito alimenta o desenho da TASK 3 (explore precisará de saver
loop-local ou de cruzar pro bg-loop) — NÃO decide o GO.

GAP CONHECIDO (mesmo dos spikes anteriores): sem ANTHROPIC_API_KEY local —
`resolve_agent_provider` cai no fallback OpenAI (gpt-4o-mini), que é o provider
canônico de todos os ambientes do projeto. O mecanismo LangGraph provado aqui
(acumulação de thread, fork, loop-binding) é do LangGraph/psycopg, não do
backend do modelo.

Uso:
    .venv/bin/python3 -m spikes.lever3_threads.demo
"""
from __future__ import annotations

import asyncio
import time

from dotenv import load_dotenv

load_dotenv()

from langchain_core.messages import HumanMessage  # noqa: E402

from radar.core.llm.agent_graph import (  # noqa: E402
    _build_chat_model,
    _build_graph,
    _build_system_message,
    _get_writing_checkpointer,
    _run_on_bg_loop,
    shutdown_writing_runtime,
)
from radar.core.llm.agent_runtime import resolve_agent_provider  # noqa: E402
from radar.core.services.explore_agent import (  # noqa: E402
    EXPLORE_AGENT_SYSTEM,
    ExploreAgent,
)

MAX_STEPS = 8
SESS = f"sess-{int(time.time())}"
THREAD_ID = f"wsSPIKE:{SESS}"

# Turnos scriptados: o turno 1 planta um FATO idiossincrático (nome de projeto
# improvável de vir de tool/edital), o turno 3 pede para recuperá-lo. Se o modelo
# no turno 3 cita "Fotossíntese Artificial Aurora-7" tendo recebido só a msg do
# turno 3 no payload, a memória veio do checkpointer, não de re-seed.
HUMAN_1 = (
    "Antes de tudo, anote este contexto do meu projeto para a conversa toda: "
    "o projeto se chama 'Fotossíntese Artificial Aurora-7' e atua em energia "
    "renovável. Só confirme em uma frase que anotou; não chame ferramentas agora."
)
HUMAN_2 = (
    "Em uma frase: por que prazos de submissão importam ao escolher um edital? "
    "Não chame ferramentas."
)
HUMAN_3 = (
    "Qual é o NOME do meu projeto que mencionei no início da conversa, e em que "
    "área ele atua? Responda em uma frase, sem chamar ferramentas."
)

FORK_A = "Continuação A: liste em uma frase um risco de foco exclusivo em energia solar."
FORK_B = "Continuação B: liste em uma frase uma vantagem de energia eólica offshore."


def _count_humans(messages) -> int:
    return sum(1 for m in messages if isinstance(m, HumanMessage))


def _last_ai_text(messages) -> str:
    from langchain_core.messages import AIMessage
    for m in reversed(messages):
        if isinstance(m, AIMessage):
            t = getattr(m, "text", None)
            if callable(t):
                t = t()
            if isinstance(t, str) and t.strip():
                return t
            c = m.content
            if isinstance(c, str) and c.strip():
                return c
    return ""


async def _scenario(saver, chat, tools, system, findings: dict) -> None:
    """Turnos 1-3 numa ÚNICA thread + fork. Roda inteiro no bg-loop (onde o pool
    do saver está bound) via _run_on_bg_loop no chamador."""
    graph = _build_graph(chat, tools, max_steps=MAX_STEPS, checkpointer=saver)
    config = {"configurable": {"thread_id": THREAD_ID}, "recursion_limit": 3 * MAX_STEPS + 5}

    # --- Turno 1: semeia system + primeira human (único turno que manda o system) ---
    t0 = time.monotonic()
    payload1 = {
        "messages": [_build_system_message(system, "openai"), HumanMessage(content=HUMAN_1)],
        "llm_calls": 0, "tool_rounds": 0, "documents": {},
    }
    final1 = await graph.ainvoke(payload1, config=config)
    findings["t1_latency"] = time.monotonic() - t0
    findings["t1_humans"] = _count_humans(final1["messages"])
    findings["t1_total_msgs"] = len(final1["messages"])
    print(f"[turno 1] msgs={len(final1['messages'])} humans={findings['t1_humans']} "
          f"resp={_last_ai_text(final1['messages'])[:120]!r}")

    # --- Turno 2: SÓ a mensagem nova (sem re-seed, sem system) ---
    t0 = time.monotonic()
    final2 = await graph.ainvoke({"messages": [HumanMessage(content=HUMAN_2)]}, config=config)
    findings["t2_latency"] = time.monotonic() - t0
    findings["t2_humans"] = _count_humans(final2["messages"])
    findings["t2_total_msgs"] = len(final2["messages"])
    print(f"[turno 2] payload=SÓ_msg_nova → msgs_acumuladas={len(final2['messages'])} "
          f"humans={findings['t2_humans']} resp={_last_ai_text(final2['messages'])[:120]!r}")

    # --- Turno 3: SÓ a mensagem nova; deve recuperar o fato do turno 1 ---
    t0 = time.monotonic()
    final3 = await graph.ainvoke({"messages": [HumanMessage(content=HUMAN_3)]}, config=config)
    findings["t3_latency"] = time.monotonic() - t0
    findings["t3_humans"] = _count_humans(final3["messages"])
    findings["t3_total_msgs"] = len(final3["messages"])
    t3_text = _last_ai_text(final3["messages"])
    findings["t3_text"] = t3_text
    findings["t3_recalled"] = "aurora-7" in t3_text.lower() or "aurora" in t3_text.lower()
    print(f"[turno 3] payload=SÓ_msg_nova → msgs_acumuladas={len(final3['messages'])} "
          f"humans={findings['t3_humans']}")
    print(f"[turno 3] RESPOSTA: {t3_text!r}")
    print(f"[turno 3] recuperou o nome do projeto do turno 1? {findings['t3_recalled']}")

    # --- Fork a partir do fim do turno 2 (aget_state_history + aupdate_state) ---
    # Captura o checkpoint imediatamente após o turno 2 varrendo o histórico.
    hist = []
    async for snap in graph.aget_state_history(config):
        hist.append(snap)
    findings["history_len"] = len(hist)
    # O histórico vem do mais recente ao mais antigo. Queremos o snapshot cujo
    # estado tem exatamente as mensagens até o fim do turno 2 (antes do turno 3).
    t2_msgs = findings["t2_total_msgs"]
    fork_source = None
    for snap in hist:
        if len(snap.values.get("messages", [])) == t2_msgs:
            fork_source = snap
            break
    if fork_source is None:
        # fallback: pega o snapshot com nº de msgs <= t2_msgs mais próximo
        candidates = [s for s in hist if len(s.values.get("messages", [])) <= t2_msgs]
        fork_source = max(candidates, key=lambda s: len(s.values.get("messages", [])))
    findings["fork_source_msgs"] = len(fork_source.values.get("messages", []))
    findings["fork_source_ckpt"] = fork_source.config["configurable"].get("checkpoint_id")
    print(f"[fork] source checkpoint_id={findings['fork_source_ckpt']} "
          f"(msgs={findings['fork_source_msgs']}) — forkando 2 continuações")

    # O fim do turno 2 é um checkpoint TERMINAL (next==()): `aupdate_state` ali
    # só appenda a mensagem mas deixa o grafo "done" → ainvoke(None) é no-op (o
    # agente nunca processa a msg nova). A forma canônica de forkar uma
    # continuação conversacional é INVOCAR COM INPUT apontando o checkpoint_id
    # histórico: o LangGraph descende um novo checkpoint daquele ponto (fork) e
    # re-entra o grafo do START com a msg mesclada. Duas invocações do MESMO
    # checkpoint_id (turno 2, que NÃO é o tip — o tip é o turno 3) geram dois
    # ramos irmãos divergentes.
    fork_cfg = {**fork_source.config, "recursion_limit": 3 * MAX_STEPS + 5}
    final_a = await graph.ainvoke({"messages": [HumanMessage(content=FORK_A)]}, config=fork_cfg)
    text_a = _last_ai_text(final_a["messages"])

    final_b = await graph.ainvoke({"messages": [HumanMessage(content=FORK_B)]}, config=fork_cfg)
    text_b = _last_ai_text(final_b["messages"])

    findings["fork_a_ckpt"] = final_a.get("__checkpoint_id__")  # informativo apenas
    findings["fork_b_ckpt"] = None
    findings["fork_a_msgs"] = len(final_a["messages"])
    findings["fork_b_msgs"] = len(final_b["messages"])
    findings["fork_a_text"] = text_a
    findings["fork_b_text"] = text_b
    findings["fork_distinct"] = text_a.strip() != text_b.strip() and bool(text_a.strip()) and bool(text_b.strip())
    # Ambos os ramos descendem do turno 2 (msgs=fork_source_msgs) → cada um tem
    # ~fork_source_msgs + (human novo + resposta), e NENHUM contém o turno 3.
    findings["fork_branches_from_source"] = (
        findings["fork_a_msgs"] > findings["fork_source_msgs"]
        and findings["fork_b_msgs"] > findings["fork_source_msgs"]
        and findings["fork_a_msgs"] < findings["t3_total_msgs"] + 2  # não herdou o turno 3 inteiro
    )
    print(f"[fork A] msgs={findings['fork_a_msgs']} resp={text_a[:140]!r}")
    print(f"[fork B] msgs={findings['fork_b_msgs']} resp={text_b[:140]!r}")
    print(f"[fork] continuações distintas do MESMO ponto? {findings['fork_distinct']} "
          f"(ramos descendem do turno 2? {findings['fork_branches_from_source']})")


def _probe_loop_binding(saver, provider, model, system, tools, findings: dict) -> None:
    """PROBE (descoberta #2): roda UM ainvoke com o saver a partir de um loop
    NOVO (asyncio.run), diferente do bg-loop onde o pool está bound. Registra se
    explode com 'Lock is bound to a different event loop'. Constrói um chat FRESCO
    dentro do loop novo para que o ÚNICO objeto cross-loop seja o saver (isola a
    causa — se explodir, é o pool do checkpointer, não o cliente httpx do LLM)."""

    async def _probe():
        chat = _build_chat_model(provider, model, temperature=0)
        graph = _build_graph(chat, tools, max_steps=MAX_STEPS, checkpointer=saver)
        cfg = {"configurable": {"thread_id": f"wsSPIKE-probe:{SESS}"},
               "recursion_limit": 3 * MAX_STEPS + 5}
        payload = {
            "messages": [_build_system_message(system, "openai"),
                         HumanMessage(content="Diga 'ok' em uma palavra. Não chame ferramentas.")],
            "llm_calls": 0, "tool_rounds": 0, "documents": {},
        }
        return await graph.ainvoke(payload, config=cfg)

    try:
        asyncio.run(_probe())
        findings["probe_exploded"] = False
        findings["probe_error"] = None
        print("[probe] ainvoke do saver a partir de um loop NOVO: NÃO explodiu "
              "(saver tolera cross-loop nesta versão pinada)")
    except Exception as e:  # noqa: BLE001 — é exatamente o que queremos capturar
        msg = f"{type(e).__name__}: {e}"
        findings["probe_exploded"] = True
        findings["probe_error"] = msg
        is_loop_bind = "different event loop" in str(e).lower() or "bound to a different" in str(e).lower()
        findings["probe_is_loop_binding"] = is_loop_bind
        print(f"[probe] ainvoke do saver a partir de um loop NOVO: EXPLODIU → {msg}")
        print(f"[probe] é o erro de loop-binding da descoberta #2? {is_loop_bind}")


def main() -> None:
    provider, model = resolve_agent_provider("anthropic", "claude-sonnet-4-6")
    print(f"provider resolvido={provider} model={model} thread_id={THREAD_ID}")
    if provider != "openai":
        print("AVISO: provider != openai — ambiente mudou desde o gap documentado.")

    saver = _get_writing_checkpointer()
    saver_kind = type(saver).__name__
    print(f"checkpointer real: {saver_kind}")
    if saver_kind != "AsyncPostgresSaver":
        print(f"AVISO: esperava AsyncPostgresSaver (durável), obtive {saver_kind}. "
              "Sem DATABASE_URL o spike roda em InMemory (ainda prova o mecanismo, "
              "mas não a durabilidade cross-instância).")

    tools = ExploreAgent()._explore_tools()
    system = EXPLORE_AGENT_SYSTEM
    findings: dict = {"provider": provider, "model": model, "saver": saver_kind}

    # Cenário (turnos + fork) roda no bg-loop — onde o pool do saver está bound.
    chat = _build_chat_model(provider, model, temperature=0)
    _run_on_bg_loop(_scenario(saver, chat, tools, system, findings))

    # Probe: loop DIFERENTE (asyncio.run) — o teste da descoberta #2.
    _probe_loop_binding(saver, provider, model, system, tools, findings)

    print("\n=== RESUMO (para o FINDINGS.md) ===")
    for k in (
        "t2_humans", "t3_humans", "t3_recalled", "fork_distinct",
        "fork_source_ckpt", "probe_exploded", "probe_is_loop_binding",
    ):
        print(f"  {k} = {findings.get(k)!r}")

    # Sinal de GO: (a) turnos 2/3 sem re-seed acumularam histórico + memória viva;
    # (b) fork produziu 2 continuações distintas.
    cond_a = (findings.get("t2_humans", 0) >= 2 and findings.get("t3_humans", 0) >= 3
              and findings.get("t3_recalled"))
    cond_b = findings.get("fork_distinct", False)
    print("\n=== VEREDITO (governança decide a promoção; aqui é só o sinal) ===")
    print(f"  (a) histórico acumulado + memória sem re-seed: {cond_a}")
    print(f"  (b) fork em duas continuações do mesmo ponto: {cond_b}")
    print(f"  probe loop-binding (input de desenho da T3, NÃO decide GO): "
          f"exploded={findings.get('probe_exploded')} "
          f"is_loop_binding={findings.get('probe_is_loop_binding')}")

    shutdown_writing_runtime()


if __name__ == "__main__":
    main()
