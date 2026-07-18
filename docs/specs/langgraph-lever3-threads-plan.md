# Plano de tasks — Item 3 (Thread por sessão / checkpointer como memória)

**Status:** plano de execução · **Data:** 2026-07-18 · **Planejador:** Opus 4.8
**Contrato-fonte:** `docs/specs/langgraph-levers-spec.md` §"Item 3 — Thread por sessão". **Racional de fundo:** `docs/specs/langgraph-intelligence-levers.md` (não normativo).
**Pareamento absorvido:** `docs/specs/langgraph-lever1-streaming-plan.md` §"TASK 6 — Streaming da escrita" (decisão explícita abaixo: **re-adiada com o frame de interrupt já desenhado**).
**Implementação:** **Opus 4.8** — exceção à régua Sonnet, porque o item toca os dois produtores + o schema `agent_memory` + a fronteira de RLS/isolamento do checkpointer. Tasks são autocontidas com critério de aceite verificável.
**Escopo:** SOMENTE o Item 3. Não re-litiga portfólio, sequência (#1/#6 em prod, #2 arquivado) nem itens fechados.

---

## Estado real verificado (âncoras de código, lidas em 2026-07-18)

O plano é contra o código, não contra memória.

| Fato | Âncora |
|---|---|
| Grafo compila com `checkpointer` parametrizável; `g.compile(checkpointer=checkpointer)` | `core/llm/agent_graph.py:143`, `:237` |
| **Entry point stateless** (explore/profile/subagentes): `checkpointer=False` explícito | `core/llm/agent_graph.py:358` `run_agent_graph_async`, `:399` |
| **Entry streaming** (o caminho VIVO do explore pós-item 1): também `checkpointer=False`, roda `graph.astream(...)` | `core/llm/agent_graph.py:498` `run_agent_graph_streaming`, `:545` |
| Comentário-aviso: `checkpointer=None` faria o subgrafo **HERDAR** o saver do pai via contextvar → "Lock bound to a different event loop"; `False` corta a herança | `core/llm/agent_graph.py:391-397` |
| **Entry da escrita** (checkpointer durável, interrupt/resume): recebe `thread_id`+`checkpointer`, `graph.ainvoke(payload)` | `core/llm/agent_graph.py:998` `_writing_turn_async`, `:1061` |
| Delta do turno-run = `msgs[prior_n_msgs:]` (não dobra trace/usage no resume) | `core/llm/agent_graph.py:1085` |
| Interrupt lido de `final["__interrupt__"]`; `WritingTurnOutcome(result, interrupt, n_messages)` | `core/llm/agent_graph.py:984`, `:1087-1096` |
| Fachada sync do turno de escrita | `core/llm/agent_graph.py:1112` `run_writing_turn` |
| Checkpointer durável = `AsyncPostgresSaver` sobre `DATABASE_URL`; fallback `InMemorySaver` sem DB | `core/llm/agent_graph.py:739` `_init_checkpointer`, `:816` `_get_writing_checkpointer` |
| **Saver bound ao bg-loop dedicado** (singleton); toda corrotina do checkpointer roda lá via `_run_on_bg_loop`/`run_coroutine_threadsafe` | `core/llm/agent_graph.py:655-661`, `:681` |
| Schema dedicado `agent_memory` **bypassa RLS** por design (checkpointer + Store) | `core/llm/agent_graph.py:695-702`, migration `028_agent_memory_schema.sql` |
| **Escrita — turno fresco:** `thread_id = f"{ws}:{session}:{user_turn_index}"` (thread NOVO por turno) + re-seed via `_build_agent_initial_messages` | `core/services/writing_session.py:1313`, `:1310` |
| **Escrita — resume:** reusa `resume_ctx["thread_id"]` + `prior_n_msgs=resume_ctx["n_msgs"]` p/ fatiar delta | `core/services/writing_session.py:1297-1306` |
| `_pending_user_input` guarda `thread_id`+`n_msgs` do interrupt em aberto | `core/services/writing_session.py:1363-1368`, `:1168` |
| `_build_agent_initial_messages` re-injeta `self._history` no prefixo estável todo turno | `core/services/writing_session.py:2086`, `:2144` |
| Bound de contexto da escrita HOJE: `_compress_history` resume acima de `HISTORY_WINDOW*2` | `core/services/writing_session.py:2377` |
| Geração em lote: thread `f"{ws}:{session}:generation"`, mas roda `checkpointer=False` (thread_id inerte lá) | `core/services/writing_session.py:1468`, `run_generation_turn` → `core/llm/agent_graph.py:545` |
| **Explore — VIVO/streaming:** re-seed do histórico `for turn in (history or [])[-8:]`; SEM `thread_id`, SEM checkpointer | `core/services/explore_agent.py:218` `explore_stream`, `:292`, `:362` `run_agent_streaming_async` |
| **Explore — cópia sync espelhada** (fallback): mesmo re-seed, warned no docstring "qualquer mudança tem que ser replicada" | `core/services/explore_agent.py` `_explore_agent` (~L428) |
| `/explore` sync + `/explore/stream` async no router | `backend/routers/explore.py:118`, rota stream (item 1) |
| **Leak-test de isolamento durável** (GATE de segurança): interrupt de A invisível pelo `thread_id` de B contra o Postgres real | `tests/test_checkpointer_postgres.py:132` `test_cross_workspace_isolation_durable` (gated em `DATABASE_URL`; exige `scripts/setup_checkpointer.py` antes) |
| Interrupt/resume durável (custo só do delta) | `tests/test_checkpointer_postgres.py:102` |
| Interrupt/resume unit (InMemorySaver): pausa, resume, delta-usage, thread isola estado, subagente stateless dentro da escrita | `tests/test_agent_graph_checkpointer.py:69,84,116,146,191,215,265` |
| **Purge de checkpoints** (cron semanal): deleta threads cujo último `ts` > `retention_days`; comentário assume "**cada turno é um thread permanente** … threads velhos são lixo puro" | `core/tasks.py:379` `_purge_stale_checkpoints`, cron `:428` |
| Telemetria `turn_end` (item 6): `stop_reason`/`llm_calls`/`max_steps` por turno, em prod | `core/llm/agent_graph.py:1097-1100` |

**Versões / ambiente:** `langgraph 1.2.6`, `langchain-core 1.4.8`; **só `OPENAI_API_KEY`** em todos os ambientes (`gpt-4o-mini`, o `resolve_agent_provider` sempre cai no fallback OpenAI — herança do gate do item 1). Harness de eval é **paralelo** (`EVAL_MAX_WORKERS`): a suíte writing **N=12 roda em minutos** e é viável como gate.

### Duas descobertas do código que reorientam o plano (leia antes das decisões)

1. **O isolamento multi-tenant NÃO é RLS — é namespacing de `thread_id`.** As tabelas do checkpointer vivem em `agent_memory`, que bypassa RLS por design (`agent_graph.py:695-702`). O que impede o workspace B de ler o estado de A é o **prefixo `{ws}` no `thread_id`** (leak-test `test_checkpointer_postgres.py:132`). A nova convenção `"{ws}:{session}"` **preserva** o prefixo `{ws}` → o invariante continua válido **por construção**, mas o leak-test tem que ser **re-rodado E estendido** à convenção nova e ao explore (que hoje nem toca o checkpointer). Isto é o guardrail #1.

2. **O explore streaming vive no LOOP DA REQUEST; o saver durável é BOUND AO BG-LOOP.** Hoje explore roda `checkpointer=False` e nunca cruza loops. "Ligar o checkpointer" no explore streaming (`run_agent_graph_streaming:498`) significaria usar o `AsyncPostgresSaver` cujo pool está preso ao bg-loop dedicado (`agent_graph.py:655-661`) a partir do loop async da request → o **mesmo** "Lock is bound to a different event loop" que o comentário `:391-397` descreve. A escrita não sofre disso porque `run_writing_turn` **cruza inteiro pro bg-loop** via `_run_on_bg_loop` (sync). O streaming não pode cruzar assim (precisa yield-ar tokens de volta pro loop da request continuamente). **Este é o único desconhecido de infra do item** e é o que a Task 1 tem que retirar antes de tocar produtor. Não estava explícito na spec.

---

## Decisões de arquitetura (tomadas, com racional)

### Decisão 1 — Fatiamento: spike no explore, **1ª promoção no explore**, escrita depois

- **A Task 1 (spike) é no explore**, como a régua da spec manda (conversa de 3 turnos): prova o **mecanismo LangGraph** genérico — thread acumula e é relido sem re-seed; `update_state` forka — que vale para os dois produtores. O spike roda throwaway **no bg-loop** (script), então prova o modelo de dados limpo.
- **1ª promoção: explore.** Racional: (a) semântica mais simples (sem interrupt/resume, sem delta-slicing, sem risco de dobrar trace); (b) é o produtor que **mais ganha** — hoje 100% stateless, re-seeda do DB todo turno (`explore_agent.py:292`); (c) o único risco novo (loop-binding, descoberta #2) é bounded e a Task 1 já o retira; (d) fatia promovível sozinha, atrás de smoke + leak-test estendido, sem tocar o caminho de escrita de produção.
- **2ª promoção: escrita.** Reusa o padrão já provado no explore, aplicado ao caminho de MAIOR risco (interrupt/resume em prod), atrás do **gate de eval de writing**. É incremental sobre a máquina que já existe (`_writing_turn_async` já tem thread+checkpointer+delta) — vira **re-escopo de `thread_id` + generalização do delta pra todo turno**, não infra nova.
- **Tensão registrada honestamente:** a spec chamou o explore de "produtor mais simples". É verdade na *semântica*; na *infra* o explore é mais difícil (loop-binding), a escrita é mais difícil no *comportamento* (interrupt/resume não pode regredir). O fatiamento coloca cada risco na task certa em vez de misturá-los.

### Decisão 2 — Crescimento da thread: trim de **paridade entra JÁ (na promoção)**; janela maior fica com gatilho

- Hoje cada produtor **limita** o contexto ao re-seedar: explore corta `[-8:]` (`explore_agent.py:292`), escrita resume via `_compress_history` (`writing_session.py:2377`). **Thread-por-sessão remove esse corte** se lermos o histórico cru do checkpointer → crescimento ilimitado = regressão silenciosa de custo/latência.
- Portanto o trim **não é adiável**: na promoção de cada produtor, aplicar `trim_messages(strategy="last", start_on="human", token_counter=...)` **na fronteira de turno** (herança empírica do spike do #2: `start_on="human"` é a ferramenta certa AQUI, nunca na cadeia intra-turno), dimensionado para **preservar a janela de hoje** (~8 turnos no explore). Isso é **paridade**, não feature nova.
- O que fica com **gatilho** (não agora): janela *maior* ou política *density-aware*. Gatilho = telemetria `turn_end` (item 6) mostrar pressão real de tokens numa sessão longa, OU o teto de passos subir. É a mesma disciplina "medir primeiro" do Item 2; reabrir aí é candidato a momento-SDK, não a construir motor de compaction à mão.

### Decisão 3 — Streaming da escrita (Task 6 do item 1): **RE-ADIADA, com o frame de interrupt já desenhado aqui**

- **Absorver não:** juntar o streaming SSE da escrita (cruzamento bg-loop→fila async, `WritingTurnResponse` rico) COM o re-escopo do thread da escrita = **duas mudanças difíceis no caminho de maior risco ao mesmo tempo**. Viola "menor mudança que prova o valor".
- **O que travava a Task 6 era o frame de interrupt no contrato SSE** — e este plano **desenha esse frame** (§Task 6-design abaixo). Com o desenho pronto e o thread da escrita já estabilizado pela Task 5, o streaming da escrita vira um **follow-on limpo e gate-separável**, não mais um item bloqueado. A dívida de pareamento é quitada intelectualmente; a implementação sai depois.

### Decisão 4 — `session_turns` continua a fonte de verdade de EXIBIÇÃO; o checkpointer vira a memória de CONTEXTO do agente. Coexistem.

- Hoje `session_turns` (DB, `persist_turn`) + `self._history` são a verdade da conversa, **lida pelo frontend (history/dashboards)**. Mudar isso = blast radius enorme, fora de escopo.
- Mudança do item: os produtores **param de RE-SEEDAR** o contexto do agente a partir de `session_turns`/`_history`; o **checkpointer** passa a replayar esse contexto. `session_turns` **continua sendo ESCRITO** (`persist_turn` intacto) para exibição/analytics. Resultado: **escrita dupla, leitura única por consumidor** — frontend lê `session_turns`, agente lê o checkpointer.
- Risco reconhecido e bounded: após um **fork/time-travel**, o que o agente viu (checkpointer) pode divergir do que está em `session_turns`. Por isso o **fork é demo throwaway** (Task 1), **não promovido** — nenhum caminho de produção escreve continuações forkadas.

---

## Sequência e dependências

```
TASK 1 (spike explore 3-turnos + fork, spikes/, no bg-loop)  ──►  ★ CHECKPOINT GO/NO-GO ★
                                                                        │ (GO)
                                                                        ▼
TASK 2 (GUARDRAILS baseline — rodar leak-test + interrupt/resume VERDES antes de tocar produtor)
                                                                        │
                                                                        ▼
TASK 3 (PROMOÇÃO explore: thread-por-sessão + ligar checkpointer + trim paridade)   ← fatia 1, promovível só
   │        gate: leak-test ESTENDIDO (explore) + subagente-stateless + smoke explore multi-turno
   ▼
TASK 4 (PROMOÇÃO escrita: re-escopo thread {ws}:{sess} + delta todo-turno + trim paridade)   ← fatia 2
   │        gate: eval writing (N=12) + interrupt/resume NÃO regride + leak-test
   ▼
TASK 5 (robustez transversal: turnos concorrentes na mesma thread + atualizar purge/comentário)
                                                                        
TASK 6-design (frame de interrupt no SSE — DESENHO, quita o pareamento; implementação = follow-on)
```

- **TASK 1 é obrigatória e bloqueante.** Nada de produção antes do checkpoint.
- **TASK 2 roda os guardrails cedo** (spec: "leak-test RLS + interrupt/resume ANTES de tocar produtor") — baseline verde é pré-condição das Tasks 3/4; versões ESTENDIDAS viram gate dentro de cada promoção.
- **TASK 3 → 4** são fatias promovíveis separadamente (como no item 1).
- **TASK 5** só faz sentido depois que ≥1 produtor está em thread-por-sessão.

---

## TASK 1 — Spike: thread por sessão + fork (explore, throwaway) · **BLOQUEANTE**

**Objetivo.** Provar, contra o grafo REAL do explore, que (a) uma thread de **escopo de sessão** sobre um checkpointer durável acumula o histórico e o **2º/3º turnos NÃO re-seedam** (o produtor manda só a mensagem nova), e (b) `update_state`/`aupdate_state` a partir de um checkpoint intermediário **forka** em duas continuações. E retirar o desconhecido de infra da descoberta #2.

**Arquivo (único, throwaway):** `spikes/lever3_threads/demo.py` (+ `__init__.py`; +`FINDINGS.md` ao lado). Nada em `core/` de produção.

**O que o script faz:**
1. Monta tools+system do explore como em produção (`build_explore_tools`/`EXPLORE_AGENT_SYSTEM`, sem perfil — caminho mais simples), modelo scriptado OU `gpt-4o-mini` real (barato).
2. Obtém o **AsyncPostgresSaver real** via `_get_writing_checkpointer()` (`agent_graph.py:816`) — o durável, não InMemory; roda tudo no **bg-loop** (`_run_on_bg_loop`) para não esbarrar no loop-binding **neste** script.
3. Compila `_build_graph(chat, tools, max_steps=..., checkpointer=<saver>)` com `config={"configurable":{"thread_id": f"wsSPIKE:{sess}"}}` — **um único thread pros 3 turnos**.
4. **Turno 1:** `ainvoke({"messages":[system, human_1], ...})`. **Turnos 2 e 3:** `ainvoke({"messages":[human_2]})` — **só a mensagem nova**, sem re-seed. Confere no estado final que o `messages` acumulou os 3 turnos (o modelo "viu" o turno 1 no turno 3 sem o produtor reinjetar).
5. **Fork:** captura o `checkpoint_id` do fim do turno 2 (`aget_state_history`), chama `aupdate_state(config_com_checkpoint_id, {"messages":[human_alt]})` e roda duas continuações divergentes a partir do mesmo ponto; imprime as duas.
6. **Probe de loop-binding (descoberta #2):** tenta rodar UM `ainvoke` com esse saver a partir de um loop **diferente do bg-loop** (ex.: `asyncio.run` novo) e **registra se explode** com "Lock is bound to a different event loop". Isso não decide o GO, mas **alimenta o desenho da Task 3** (explore precisará de saver loop-local ou de cruzar pro bg-loop).
7. `shutdown_writing_runtime()` no fim.

**Critérios de aceite (observáveis no terminal + anotados no `FINDINGS.md`):**
- [ ] Turnos 2 e 3 rodam com payload de **só a mensagem nova** e o estado final contém o histórico dos 3 turnos (prova: nº de HumanMessages ≥ 3 sem o produtor tê-las reinjetado).
- [ ] O modelo demonstra memória do turno 1 no turno 3 (ex.: referencia um fato dado no turno 1) — evidência qualitativa impressa.
- [ ] `aupdate_state` a partir do checkpoint do turno 2 produz **duas continuações distintas** (fork observável).
- [ ] `FINDINGS.md` registra: o veredito do probe de loop-binding (explode? em que condição?), a API de fork usada (`aget_state_history` + `aupdate_state`), e latência/custo aproximados.

---

## ★ CHECKPOINT GO / NO-GO ★ (após TASK 1, antes de qualquer produção)

**GO** exige as duas condições do "sinal de sucesso" da spec:
1. Histórico acumulado **lido do checkpointer sem re-seed** (turnos 2/3 sem reinjeção).
2. Um **fork** produzindo duas continuações do mesmo ponto.

- **GO** → seguir para TASK 2. O veredito do probe de loop-binding é **input de desenho** da Task 3 (não bloqueia o GO — é problema de infra bounded, não de viabilidade do modelo).
- **NO-GO** (o produtor precisa re-seedar mesmo com thread de sessão, OU o fork não funciona na versão pinada) → **parar**, registrar no `FINDINGS.md`, arquivar Tasks 2-6 e reportar. Freio anti-sunk-cost (princípio da spec).

---

## TASK 2 — Guardrails baseline (rodar VERDES antes de tocar produtor) · dep: GO

**Objetivo.** Estabelecer que os dois guardrails de segurança/comportamento **passam hoje**, para que qualquer regressão nas Tasks 3/4 seja atribuível à mudança. Nenhum código de produção nesta task — é execução + (se preciso) reparo de fixture.

**Arquivos:** só execução — `tests/test_checkpointer_postgres.py`, `tests/test_agent_graph_checkpointer.py`. Pré-passo: `scripts/setup_checkpointer.py` (cria tabelas do saver) + `DATABASE_URL` local.

**Passos:**
1. Rodar `scripts/setup_checkpointer.py`; depois `pytest tests/test_checkpointer_postgres.py -v` (leak-test durável + interrupt/resume durável) e `pytest tests/test_agent_graph_checkpointer.py -v`.
2. Confirmar que `test_cross_workspace_isolation_durable` (`:132`) e os 7 unit de interrupt/resume passam **antes** de qualquer mudança.

**Critérios de aceite:**
- [x] Leak-test durável (`:132`) e interrupt/resume durável (`:102`) **verdes** localmente contra Postgres real.
- [x] Os 7 unit de `test_agent_graph_checkpointer.py` verdes (incl. `test_subagent_inside_checkpointed_writing_turn:215` e `test_thread_id_isolates_state:191`).
- [x] Baseline registrado (comando + saída) — é o "antes" comparável dos gates das Tasks 3/4.

### ✅ LINHA DE BASE — guardrails (registrada 2026-07-18)

**Alvo:** Postgres **LOCAL** (Supabase `127.0.0.1:54322`, `ENVIRONMENT=test`) — nunca prod. Tabelas `agent_memory.{checkpoints,checkpoint_blobs,checkpoint_writes}` criadas via `scripts/setup_checkpointer.py`. **Comando:**
```
DATABASE_URL=postgresql://postgres:postgres@127.0.0.1:54322/postgres \
SUPABASE_URL=http://127.0.0.1:54321 ENVIRONMENT=test INTEGRATION_TARGET=local \
.venv/bin/python -m pytest tests/test_checkpointer_postgres.py tests/test_agent_graph_checkpointer.py -v
```
**Resultado: 9 passed / 0 failed / 0 skipped (3.15s).** Qualquer regressão nas Tasks 3-5 é atribuída contra estes números:

| # | Teste | Tipo | Baseline |
|---|---|---|---|
| B1 | `test_checkpointer_postgres::test_interrupt_resume_durable` | durável (PG real) | PASS |
| B2 | `test_checkpointer_postgres::test_cross_workspace_isolation_durable` (**leak-test / gate segurança**) | durável (PG real) | PASS |
| B3 | `test_agent_graph_checkpointer::test_interrupt_pauses_with_payload` | unit (InMemory) | PASS |
| B4 | `test_agent_graph_checkpointer::test_resume_continues_from_interrupt` | unit | PASS |
| B5 | `test_agent_graph_checkpointer::test_resume_token_usage_is_delta_only` (**delta não dobra** — o wrinkle da T4) | unit | PASS |
| B6 | `test_agent_graph_checkpointer::test_batched_tool_reexecutes_on_resume` | unit | PASS |
| B7 | `test_agent_graph_checkpointer::test_thread_id_isolates_state` | unit | PASS |
| B8 | `test_agent_graph_checkpointer::test_subagent_inside_checkpointed_writing_turn` (**subagente stateless**) | unit | PASS |
| B9 | `test_agent_graph_checkpointer::test_subagent_graph_compiles_with_checkpointer_false` | unit | PASS |

**Nota:** B2 é o leak-test **atual** (convenção `{ws}:{session}:{turn}`). A versão **estendida** (`{ws}:{session}` p/ explore e escrita) é gate NOVO dentro das Tasks 3/4 — deve entrar como B2′/B2″ ao lado deste, não substituí-lo. B5 e B8 são os guardrails mais sensíveis às Tasks 4 e 3 respectivamente.

---

## TASK 3 — Promoção EXPLORE: thread por sessão + ligar checkpointer · dep: TASK 2

**Objetivo.** O explore streaming (caminho vivo) passa a usar **uma thread de sessão** sobre o checkpointer durável, **parando de re-seedar** o histórico do DB; o corte de janela (paridade com `[-8:]` de hoje) migra pra fronteira de turno via `trim_messages`.

**Sub-decisão de infra (resolvida pelo probe da Task 1; emenda de governança 2026-07-18):** se o probe confirmou o loop-binding, o explore recebe um **`AsyncPostgresSaver` loop-local** — e este saver é **singleton POR LOOP**, obrigatoriamente:
- **Registry keyed pelo loop**, espelhando o padrão do bg-loop da escrita (`_get_writing_checkpointer:816` — dupla-checagem sob lock, `_checkpointer`/`_checkpointer_ready`/`_checkpointer_lock`). A versão da request usa uma estrutura análoga keyed pelo `asyncio.get_running_loop()` (ex.: `WeakKeyDictionary`/dict por `id(loop)` sob lock), de modo que **cada loop tem no máximo um saver**. Uvicorn tipicamente tem um loop por worker → na prática um saver por processo, mas a chave-por-loop é o guardrail defensivo.
- **NUNCA instanciar por request.** Um `AsyncPostgresSaver`/pool novo por request vaza conexões e recria pool a cada turno — proibido. A request pega o singleton do seu loop via o registry (init preguiçoso na 1ª vez, sob lock).
- **Pool SEPARADO do saver do bg-loop da escrita**, com **tamanho limitado** (`max_size` explícito, como `_make_agent_memory_pool:706`), e **pools JAMAIS compartilhados entre loops** (é a origem do "Lock bound to a different event loop").
- Alternativa só se o probe da Task 1 mostrar que cruzar pro bg-loop é viável sem matar o streaming: reusar o saver existente (aí não há saver novo). Documentar a escolha no topo da função nova.

**Arquivos:**
- `core/llm/agent_graph.py` — **aditivo**: dar a `run_agent_graph_streaming` (`:498`) (e por simetria `run_agent_graph_async:358`, se o fallback sync for migrado) parâmetros **opcionais** `thread_id: str | None = None` e um provedor de checkpointer para o path stateful. Quando `thread_id is None` → comportamento **byte-idêntico** de hoje (`checkpointer=False`, sem re-seed mudado). Quando setado → compila com o saver loop-local e passa `config={"configurable":{"thread_id": thread_id}}`. O tradutor de `AgentResult` (`_messages_to_agent_result`) continua saindo do estado final — sem mudança de contrato. **Não** tocar o ramo `checkpointer=False` dos subagentes (`:391-397` continua valendo — subagente do `deep_research` do explore roda via `run_agent_graph_async` com `False`).
- `core/services/explore_agent.py` — em `explore_stream` (`:218`): (a) computar `thread_id = f"{workspace_id}:{session_id}"` (exigir `workspace_id`; explore anônimo sem workspace **continua stateless**, `thread_id=None` → caminho de hoje); (b) **parar de re-seedar** `for turn in (history or [])[-8:]` (`:292`) quando há thread — só a mensagem nova (+ hint) vai no payload; (c) aplicar `trim_messages(strategy="last", start_on="human", token_counter=..., ...)` na fronteira, dimensionado pra ~8 turnos (paridade). Replicar a mudança de payload na **cópia sync `_explore_agent`** (~L428) OU deixá-la explicitamente no caminho antigo (fallback re-seeda) — decidir e comentar (o docstring `:242-245` avisa que as cópias divergem).
- `backend/routers/explore.py` — passar `session_id`/`workspace_id` já resolvidos ao `explore_stream` como escopo do thread (já disponíveis no handler; sem rota nova).

**Restrições:** aditivo puro no grafo — `thread_id=None` reproduz hoje. `run_writing_turn`/`run_generation_turn` e os call sites de escrita **byte-idênticos**. Contrato `AgentResult` imutável.

**Critérios de aceite:**
- [ ] Conversa de explore autenticada de ≥3 turnos: turnos 2/3 **não re-seedam** (log/asserção de que o payload leva só a msg nova) e o agente demonstra memória do turno 1.
- [ ] **Leak-test ESTENDIDO** (novo caso em `test_checkpointer_postgres.py`, espelhando `:132`): dois workspaces com `thread_id="{wsA}:sess"` vs `"{wsB}:sess"` — B **não** lê o estado de A. Verde contra Postgres real.
- [ ] **Saver singleton por loop (emenda de governança):** N requests de explore no mesmo loop reusam o **MESMO** `AsyncPostgresSaver` (asserção: nenhum pool novo por request; registry keyed pelo loop). Nenhum saver/pool instanciado por request.
- [ ] **Subagente stateless preservado:** um `deep_research` disparado dentro de um turno de explore com thread NÃO persiste no checkpointer (espelho de `test_agent_graph_checkpointer.py:215`, adaptado ao explore) — sem "Lock bound to a different event loop".
- [ ] Explore **anônimo/sem workspace** continua stateless (`thread_id=None`, caminho de hoje intacto).
- [ ] Janela cortada: sessão longa (>8 turnos) não cresce o payload indefinidamente (trim na fronteira observável).
- [ ] `session_turns` continua sendo escrito (history/dashboard do frontend não regride).
- [ ] **Smoke via `verify`** dirigindo o `/explore/stream` real multi-turno (com e sem match). É o gate de promoção do explore (sem eval — explore não tem golden de match como gate deste eixo; ver "fora de escopo").

---

## TASK 4 — Promoção ESCRITA: re-escopo de thread + delta em todo turno · dep: TASK 3

**Objetivo.** A escrita passa de **thread-por-turno** (`{ws}:{session}:{turn}`) para **thread-por-sessão** (`{ws}:{session}`); o `interrupt/resume` vira um **caso particular** do mesmo mecanismo de delta que agora rege TODO turno; o re-seed de `self._history` no `_build_agent_initial_messages` para (o checkpointer replaya).

**Wrinkle central (verificado).** Hoje `prior_n_msgs=0` em turno fresco porque cada turno é um thread novo (`writing_session.py:1313`, `:1321`). Com thread-por-sessão, o thread **acumula todos os turnos**, então **todo turno após o 1º** precisa `prior_n_msgs = <n_messages do estado final do turno anterior>` pra fatiar só o delta (`agent_graph.py:1085`) e **não dobrar trace/usage**. Isso exige a `WritingSession` **persistir `last_n_messages` após cada turno** — hoje `n_messages` só é guardado dentro de `_pending_user_input` para interrupts (`:1367`). É uma adição de estado da sessão (coluna/campo), não só de fluxo.

**Arquivos:**
- `core/services/writing_session.py`:
  - `_turn_agent` (`:1263`): `thread_id = f"{self.workspace_id}:{self.session_id}"` **sempre** (fresco e resume convergem no mesmo thread); `prior_n_msgs = self._last_n_messages` (0 no 1º turno); parar de reinjetar `self._history` no `_build_agent_initial_messages` (só o `messages.extend(self._history)` em `:2144` sai quando há thread — é o histórico episódico que o checkpointer agora replaya). Após o turno, gravar `self._last_n_messages = outcome.n_messages` e persistir.
  - **Prefixo estável idempotente (obrigatório — Adendo de governança abaixo).** O prefixo (perfil/card/**outline**/playbook) **continua entrando a cada turno** (o outline é MUTÁVEL — cada `save_draft` o altera; congelá-lo na thread = bug "outline invisível" de 2026-07-02 por outro caminho). MAS re-injetá-lo como mensagens normais numa thread durável faz o `add_messages` reducer **APPENDAR** → N cópias, com versões conflitantes do outline no mesmo estado. Mecanismo exigido: o bloco de prefixo entra como **uma mensagem de id determinístico estável** (ex.: `id="ws-stable-prefix"`), reconstruída fresca a cada invoke — o `add_messages` **substitui em posição** (mesmo id → replace, não append), mantendo **uma** cópia sempre-fresca no índice fixo. Alternativa equivalente: um nó pre-model que reconstrói/substitui o prefixo a partir do estado atual da sessão (via `RemoveMessage` + re-append). Confirmar o comportamento replace-por-id do `add_messages` na versão pinada antes de escolher. **Nuance de delta:** o prefixo fica no índice 0 (< `prior_n_msgs` nos turnos ≥2) → não entra no slice de trace/usage (`msgs[prior_n_msgs:]`), então não dobra contagem; o token real que o modelo viu vem do `usage_metadata` do AIMessage, não do slice.
  - `interrupt/resume` (`:1293-1306`): deixa de precisar do `resume_ctx["thread_id"]` separado — o thread já é o da sessão; o resume continua sendo `Command(resume=user_message)` com `prior_n_msgs = last_n_messages`. `_pending_user_input` guarda só `field/prompt` (o `thread_id` vira redundante; manter por compat ou remover — decidir e comentar).
  - Persistência do `last_n_messages`: campo na linha da sessão (schema `writing_sessions`), carregado no rehidrato (junto de `_history_summary` em `:756`).
  - Aplicar `trim_messages(start_on="human", ...)` na fronteira de turno pra paridade com `_compress_history` (a sumarização atual pode ser aposentada OU mantida como camada de exibição — **manter** para não mexer no dashboard; o trim é só do contexto do agente).
- `core/services/writing_session.py:1468` (geração em lote): **fora de escopo** — segue `checkpointer=False` (thread_id inerte). Nenhuma mudança.
- `core/llm/agent_graph.py`: **nenhuma mudança de assinatura** — `run_writing_turn`/`_writing_turn_async` já aceitam `thread_id`+`prior_n_msgs`. Só o **valor** passado muda (vem do produtor).

**Restrições:** a geração em lote e todos os caminhos `checkpointer=False` permanecem stateless. Contrato `AgentResult`/`WritingTurnOutcome` imutável.

**Critérios de aceite:**
- [ ] Conversa de escrita de ≥3 turnos: turnos 2/3 **não re-seedam** `_history`; `prior_n_msgs` fatia o delta corretamente (trace/usage do turno N ≈ só o turno N, sem dobrar — asserção sobre `result.usage`).
- [ ] **`interrupt/resume` NÃO regride:** um turno que pausa em `request_user_info` emite `pending_user_input` e o resume continua e fecha (rodar o cenário de `test_checkpointer_postgres.py:102` adaptado ao thread-por-sessão; e os unit `:84,116` verdes).
- [ ] `last_n_messages` persiste e sobrevive ao rehidrato da sessão (reabrir a sessão num processo novo continua o delta certo).
- [ ] Prefixo estável (perfil/card/outline/playbook) **continua** entrando fresco a cada turno (não é histórico — não pode sumir com a parada de re-seed).
- [ ] **Idempotência do prefixo (Adendo de governança):** após 3 turnos na mesma thread, o estado **persistido** contém **zero ou uma** cópia do bloco de prefixo — teste que **inspeciona a thread** (`aget_state`), não só o comportamento. E: um `save_draft` no turno 2 que muda o outline → o turno 3 vê o outline **novo** (não o do turno 1).
- [ ] **Gate de eval de writing (N=12, `EVAL_MAX_WORKERS`)** verde — é o eixo com checkpointer durável (spec). Baseline v3 próprio, não paridade com legado.
- [ ] **Leak-test** (`:132`) verde com a convenção `{ws}:{session}` da escrita.
- [ ] **Smoke via `verify`** dirigindo chat de escrita real multi-turno com ≥1 tool (`save_draft`) e um ciclo interrupt→resume.
- [ ] `session_turns`/`persist_turn` continuam escrevendo (history do frontend intacta).

---

## TASK 5 — Robustez transversal: turnos concorrentes + higiene do purge · dep: TASK 3 (e 4, se promovida)

**Objetivo.** Cobrir o risco de **duas abas** na mesma sessão escrevendo a mesma thread, e reconciliar o **purge** com o novo modelo (o comentário de `core/tasks.py:385-387` assume thread-por-turno-descartável — agora falso).

**Arquivos:**
- `tests/` (novo, gated em `DATABASE_URL`): teste de **dois `ainvoke` concorrentes no mesmo `thread_id`** sobre o AsyncPostgresSaver real — observar o comportamento (o LangGraph/Postgres saver usa versionamento otimista de checkpoint; documentar se o 2º sobrescreve, serializa, ou conflita) e **fixar o contrato**: o pior caso aceitável é "last-write-wins sem corromper o estado"; corrupção (mensagens intercaladas de dois turnos) é falha.
- `core/services/writing_session.py` / `explore_agent.py`: se o teste mostrar corrupção, adicionar um **guard de concorrência por sessão** (lock leve / rejeição de turno concorrente com aviso), **mínimo** — sem construir framework. Se o saver já serializa com segurança, só documentar.
- `core/tasks.py:379-425`: **atualizar o comentário** (`:385-387`) — thread não é mais "lixo puro após o turno"; é a **memória viva da sessão**. A semântica de `retention_days` muda de sentido: purgar uma thread de sessão = **descartar a memória de uma sessão abandonada** há > retention (correto, mas agora é uma decisão de produto, não higiene de lixo). Confirmar que uma sessão **ativa** (checkpoint recente) nunca é purgada e que o `retention_days` atual é adequado ao novo sentido (ajustar se preciso).

**Critérios de aceite:**
- [ ] Teste de concorrência: dois turnos simultâneos na mesma thread **não corrompem** o estado (contrato fixado); se houver guard, ele rejeita/serializa com mensagem clara.
- [ ] `_purge_stale_checkpoints` continua verde (`test_purge_checkpoints.py`) e o comentário reflete thread-por-sessão; sessão ativa não é purgada (teste ou asserção do critério `max(ts) < now - retention`).
- [ ] Documentado: `retention_days` = janela de memória de sessão abandonada (não lixo de turno).

---

## TASK 6-design — Frame de `interrupt` no contrato SSE (DESENHO; quita o pareamento do item 1)

**Não é implementação.** É o desenho que **destrava** a Task 6 do plano do item 1 (streaming da escrita), cuja dependência declarada era exatamente "um frame de `interrupt` no contrato SSE" que o explore não tem. Entregar o desenho aqui quita a dívida de pareamento; a implementação é **follow-on** após a Task 4 estabilizar o thread da escrita.

**Desenho do frame (fixado):** o contrato SSE da escrita estende a taxonomia mínima do item 1 (`token`/`tool`/`done`/`error`) com **um** frame novo:
```
event: interrupt   data: {"field": "<campo>", "prompt": "<pergunta>"}
```
- Emitido quando `final["__interrupt__"]` está presente (`agent_graph.py:1087`) — em vez do `done`. Carrega o mesmo payload que hoje vira `pending_user_input` (`writing_session.py:1363-1368`).
- O `done` da escrita, quando NÃO há interrupt, carrega o `WritingTurnResponse` completo (`tool_trace`, `draft_ready`, `sections_done`, `plan`) — como o plano do item 1 já previa.
- O frontend, ao receber `interrupt`, renderiza a pergunta como bolha do assistente e habilita a resposta (que no próximo turno vira o `Command(resume)` — agora sobre a **mesma thread de sessão**, Task 4).
- **Cruzamento de loop (o que torna a implementação um follow-on, não parte desta task):** o streaming da escrita precisa emitir deltas do **bg-loop** (onde o saver vive) de volta ao handler async da request via **fila thread-safe** — o mesmo wrinkle que o item 1 §Task 6 (`:209`) documentou. Só se ataca depois que a Task 4 fixou o thread-por-sessão da escrita.

**Critério de "pronto" desta task:** o frame acima registrado como contrato (nesta seção) + uma nota em `docs/specs/langgraph-lever1-streaming-plan.md` §Task 6 apontando que o frame de interrupt está desenhado aqui e a dependência do Item 3 é o thread-por-sessão (Task 4), não mais um bloqueio de contrato.

---

## Gate de promoção do item (consolidado)

Por fatia, na promoção:
1. **Eval de writing** (suíte N=12, `EVAL_MAX_WORKERS` — agora viável em minutos) — gate da **Task 4** (o caminho com checkpointer durável). Correção absoluta, baseline v3 próprio (não paridade com legado).
2. **Leak-test de isolamento** (`test_checkpointer_postgres.py:132`) re-rodado E estendido às convenções novas (`{ws}:{session}` p/ explore e escrita) — gate das Tasks 3 **e** 4.
3. **Smoke via `verify`** dirigindo chat real **multi-turno** (explore na Task 3; escrita + ciclo interrupt/resume na Task 4).

---

## Fora de escopo (explícito)

- **Geração em lote com estado** (`run_generation_turn`, `/writing/{id}/generate`, thread `:generation`). Segue `checkpointer=False`; o `thread_id` lá é inerte. Ligar checkpointer na geração N-paralela é outro desenho (ligado ao Item 4 `Send`).
- **Migrar a fonte de verdade de exibição** para o checkpointer. `session_turns` continua a verdade do frontend/dashboards (Decisão 4). Blast radius fora de escopo.
- **Política density-aware / janela maior de contexto** (Item 2 arquivado). Só o trim de **paridade** (`start_on="human"`, ~janela de hoje) entra; janela maior é trigger-gated por telemetria `turn_end`.
- **Streaming da escrita (implementação).** Re-adiada (Decisão 3); só o **frame de interrupt** é desenhado (Task 6-design). Follow-on após Task 4.
- **Fork/time-travel em produção.** É demo throwaway na Task 1; nenhum caminho de produção escreve continuações forkadas (evita divergência checkpointer×session_turns).
- **Trocar o isolamento de namespacing por RLS no `agent_memory`.** O schema bypassa RLS por design; o guardrail é o namespacing de `thread_id` + o leak-test. Reprojetar isso é fora de escopo.
- **Motor de compaction / detector de densidade / framework de concorrência genérico.** Harness-smell (gatilho de reavaliação SDK, não de construir à mão). O guard de concorrência da Task 5, se necessário, é o mínimo por-sessão.
- **Outros itens da trilha** (#4 `Send`, #5 `interrupt` adicional, adjacente `playbook_overlays`).

---

## Adendo de governança (2026-07-18) — decisão do prefixo estável na T4

**Ratificada a decisão de RE-INJETAR o prefixo estável (perfil/card/outline/playbook) a cada turno** — argumento decisivo além do
racional do plano: o **outline é mutável** (cada `save_draft` o altera); se entrasse na thread como memória do turno 1, o agente
veria outline **stale** nos turnos seguintes — a mesma classe do bug "outline invisível" de 2026-07-02, reintroduzida por outro
caminho. Prefixo é contexto semântico vivo, não episódico.

**Requisito adicional (obrigatório na T4):** a re-injeção deve ser **idempotente em relação à thread durável** — o prefixo NÃO
pode ser appendado ao estado persistido a cada turno (senão o checkpointer acumula N cópias de perfil/card/outline, com versões
conflitantes do outline dentro da mesma memória). Entra por-invoke fora do estado persistido, ou substitui em vez de acumular.
**Critério de aceite:** após 3 turnos na mesma thread, o estado persistido contém **zero ou uma** cópia do bloco de prefixo — teste
que inspecione a thread, não só o comportamento.

## Notas de execução para o implementador (Opus 4.8)

- Comece pela **TASK 1** e **pare no checkpoint**. O probe de loop-binding alimenta o desenho de infra da Task 3 — não pule.
- **Guardrails cedo:** rode a TASK 2 (leak-test + interrupt/resume verdes) **antes** de tocar qualquer produtor. É a linha de base que torna qualquer regressão atribuível.
- O ramo `checkpointer=False` dos subagentes (`agent_graph.py:391-397`) é **load-bearing** contra o bug "Lock bound to a different event loop" — nunca troque por `None`, nem no explore com thread.
- `thread_id=None` no explore é o caminho de hoje **byte-idêntico** — explore anônimo/sem workspace segue stateless.
- `prior_n_msgs` passa a reger **todo** turno da escrita (não só resume); persistir `last_n_messages` na sessão é a peça nova — sem ela, trace/usage dobram.
- Eval só na TASK 4; explore valida por `verify` (sem golden de match como gate deste eixo). Rode `tsc --noEmit` (não `npm run build`) no frontend com dev server ativo.
