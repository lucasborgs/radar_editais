# TASK 1 — Findings (Thread por sessão / checkpointer como memória, Item 3)

**Data:** 2026-07-18 · **Script:** `docs/historical/spikes/lever3_threads/demo.py` (throwaway)
**Grafo:** real (`_build_graph` + tools/system reais do explore, sem perfil) · **Saver:** **AsyncPostgresSaver real** (`_get_writing_checkpointer`, sobre `DATABASE_URL`) · **Modelo:** `gpt-4o-mini` real (fallback OpenAI — sem `ANTHROPIC_API_KEY` local, gap conhecido) · `temperature=0`.
**Thread única:** `wsSPIKE:{sess}` para os 3 turnos. Cenário roda no **bg-loop** (`_run_on_bg_loop`); o probe roda num loop novo (`asyncio.run`) de propósito.

## Sinal do checkpoint GO/NO-GO — as duas condições da spec

### (a) Histórico acumulado, lido do checkpointer SEM re-seed — **CONFIRMADO**

| Turno | Payload enviado | msgs acumuladas no estado | HumanMessages |
|---|---|---|---|
| 1 | `[system, human_1]` | 3 | 1 |
| 2 | **só** `[human_2]` | 5 | 2 |
| 3 | **só** `[human_3]` | 7 | 3 |

- Turnos 2 e 3 mandaram **só a mensagem nova** (sem system, sem reinjeção de histórico) e o estado acumulou os 3 turnos — o `add_messages` reducer sobre a thread durável faz o replay.
- **Prova de memória viva:** o turno 1 plantou um fato idiossincrático ("o projeto se chama *Fotossíntese Artificial Aurora-7*, atua em energia renovável"). O turno 3 perguntou o nome/área **sem reenviar nada do turno 1** e o modelo respondeu: *"Seu projeto se chama 'Fotossíntese Artificial Aurora-7' e atua na área de energia renovável."* → o modelo **viu** o turno 1 no turno 3 porque o checkpointer o replayou, não porque o produtor reinjetou.

### (b) Fork: duas continuações divergentes do mesmo ponto — **CONFIRMADO**

- Fonte do fork: checkpoint do **fim do turno 2** (msgs=5), localizado varrendo `aget_state_history`.
- Fork A (msgs=7): *"Um risco de foco exclusivo em energia solar é a vulnerabilidade a variações climáticas e sazonais…"*
- Fork B (msgs=7): *"Uma vantagem da energia eólica offshore é que ela pode gerar eletricidade em locais com ventos mais fortes e constantes…"*
- **Distintas** e ambas **descendem do turno 2** (7 msgs cada, nenhuma herdou o turno 3) → dois ramos irmãos do mesmo checkpoint.

## API de fork usada (achado que corrige o desenho ingênuo)

O plano descreveu o fork como `aupdate_state(config_com_checkpoint_id, {messages:[human_alt]})` + rodar a continuação. **Isso não funciona a partir do fim de um turno** (checkpoint TERMINAL, `next==()`): `aupdate_state` só appenda a mensagem, o grafo continua "done", e `ainvoke(None)` vira **no-op** — o agente nunca processa a msg nova (a 1ª rodada do spike devolveu, nos dois "forks", o eco literal da resposta do turno 2; `fork_distinct=False`).

**Forma canônica que funciona (usada na versão final):** invocar **com input** apontando o `checkpoint_id` histórico —
`graph.ainvoke({"messages":[human_alt]}, config={**snapshot.config, "recursion_limit":…})`. O LangGraph descende um novo checkpoint daquele ponto (fork) e **re-entra o grafo do START** com a msg mesclada. Duas invocações do mesmo `checkpoint_id` (turno 2, que **não** é o tip — o tip é o turno 3) geram os dois ramos.
**Implicação para produção:** o fork é **demo throwaway** (Decisão 4 do plano); nenhum caminho de produção escreve continuações forkadas. Este achado só documenta a API correta caso o time-travel volte a ser considerado.

## PROBE de loop-binding (descoberta #2) — **EXPLODE, confirmado**

Rodar UM `ainvoke` com o **mesmo saver singleton** a partir de um loop NOVO (`asyncio.run`), com um `ChatOpenAI` **fresco** construído dentro desse loop (para isolar a causa — o único objeto cross-loop é o saver):

```
RuntimeError: <asyncio.locks.Lock object at 0x… [locked]> is bound to a different event loop
```

`probe_is_loop_binding = True`. É **exatamente** o erro que o comentário `agent_graph.py:391-397` descreve: o `AsyncConnectionPool`/lock do `AsyncPostgresSaver` está **bound ao bg-loop** onde foi criado. A escrita não sofre porque `run_writing_turn` cruza inteiro pro bg-loop via `_run_on_bg_loop` (sync). **O explore streaming não pode cruzar assim** — ele precisa yield-ar tokens de volta ao loop da request continuamente.

### Consequência de desenho para a TASK 3 (NÃO decide o GO — é infra bounded)

O explore streaming stateful **não pode** reusar o saver singleton do bg-loop a partir do loop da request/uvicorn. Duas saídas viáveis:

1. **Saver loop-local** para o explore: um `AsyncPostgresSaver` cujo pool é aberto **no loop da request** (pool SEPARADO do saver do bg-loop da escrita). Nunca compartilhar um pool entre loops. → é a opção default que a TASK 3 deve documentar no topo da função nova.
2. Cruzar pro bg-loop — **descartada para streaming**: mataria a emissão contínua de tokens (o stream teria que atravessar a fronteira de thread a cada delta).

O ramo `checkpointer=False` dos subagentes (`agent_graph.py:391-397`) **continua load-bearing** — nunca virar `None`, nem no explore com thread.

## Latência / custo (aprox., 1 run limpo, gpt-4o-mini)

- Turnos conversacionais curtos (sem tools nesta run — as instruções pediam "não chame ferramentas"): ~1–3s cada. Fork: 2 invocações adicionais. Probe: 1 invocação abortada no lock (custo ~0). Custo total desprezível (~6 chamadas curtas ao gpt-4o-mini).
- Observação: o payload dos turnos 2/3 é **só a msg nova**; o custo de input por turno cresce com o histórico replayado pelo checkpointer (o que motiva o **trim de paridade** na fronteira de turno, Decisão 2 do plano — sem ele, sessão longa cresce o input indefinidamente).

## Veredito (o sinal; a promoção é decisão da governança)

- **(a)** histórico acumulado + memória sem re-seed: **True**
- **(b)** fork em duas continuações do mesmo ponto: **True**
- probe loop-binding: **exploded=True, is_loop_binding=True** (input de desenho da T3)

Ambas as condições de GO da spec satisfeitas contra o grafo e o saver reais. O único desconhecido de infra do item (loop-binding) foi retirado e tem desenho de saída para a T3.
