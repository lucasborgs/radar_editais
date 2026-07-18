# Plano de tasks — Item 1 (Streaming, `astream_events`)

**Status:** plano de execução · **Data:** 2026-07-18 · **Planejador:** Opus 4.8
**Contrato-fonte:** `docs/specs/langgraph-levers-spec.md` §"Item 1 — Streaming". **Racional:** `docs/specs/langgraph-intelligence-levers.md` (fundo, não normativo).
**Implementação:** Sonnet 5 (item não-crítico; não toca produtores de RLS). **Escopo:** SOMENTE o Item 1 + carona opcional do Item 6b.

Este plano decompõe apenas o Item 1. Não re-litiga portfólio, ordem ou escopo de outros itens.

---

## Estado real verificado (âncoras de código)

Tudo abaixo foi lido no código em 2026-07-18 — o plano é contra isto, não contra memória.

| Fato | Âncora |
|---|---|
| Entry point stateless (explore/profile/subagentes), `checkpointer=False` | `core/llm/agent_graph.py:333` `run_agent_graph_async` |
| O `ainvoke` do explore | `core/llm/agent_graph.py:403` `final = await graph.ainvoke(init, config=config)` |
| Config + callback Langfuse (recursion_limit, handler) | `core/llm/agent_graph.py:398-401` |
| Estado final → AgentResult (tradutor autoritativo) | `core/llm/agent_graph.py:414-416`, `_messages_to_agent_result:228`, `_derive_stop_reason:270` |
| `on_step` é **pós-hoc** (roda depois do turno, iterando `result.steps`) | `core/llm/agent_graph.py:428-430` |
| Factory de ChatModel (Anthropic vs OpenAI-compat) | `core/llm/agent_graph.py:75-115` (ramo OpenAI `99-115`) |
| Entry point da escrita (checkpointer durável, interrupt/resume) | `core/llm/agent_graph.py:789` `_writing_turn_async`, `ainvoke:851` |
| Geração em lote (paralela, sem token stream único) | `core/llm/agent_graph.py:1068` `_generation_turn_async` |
| Facade sync/async (delegam ao grafo) | `core/llm/agent_runtime.py:214` `run_agent_async`, `:271` `run_agent` |
| Contrato `AgentResult`/`TraceStep` (imutável) | `core/llm/agent_runtime.py:90-121` |
| `/explore` é **sync** e faz match/diff/persist **depois** do agente | `backend/routers/explore.py:118-239` |
| O `answer` é **sobrescrito** por `_match_cards_intro` quando houve match | `backend/routers/explore.py:199` |
| ExploreAgent chama `run_agent` (sync) | `core/services/explore_agent.py:337` |
| `/writing/turn` (async → `session.turn` em thread) | `backend/routers/writing.py:232-278` |
| Caller de `run_writing_turn` (resume/prior_n_msgs) | `core/services/writing_session.py:1284-1320` |
| Frontend explore: `frontdoorTurn` = `apiFetch` JSON único | `frontend/src/lib/api.ts:270-296` |
| Frontend writing: `sendWritingTurn` = `apiFetch` JSON único | `frontend/src/lib/api.ts:359-373` |
| Consumo explore (desestrutura `answer` do await) | `frontend/src/app/page.tsx:346-367` |
| Consumo writing | `frontend/src/app/workspace/[sessionId]/page.tsx:331` |
| `apiFetch` (fetch + `res.json()`, sem stream) | `frontend/src/lib/api.ts:76-94` |

**Versões:** `langgraph 1.2.6`, `langchain-core 1.4.8`, `langchain-anthropic 1.4.8`, `langchain-openai 1.3.3`.
`Runnable.astream_events` aceita `version ∈ {v1,v2,v3}` (default `v2`). Existe também `graph.astream(stream_mode=[...])`.

### Duas decisões de arquitetura que este plano fixa

1. **AgentResult vem do ESTADO FINAL, não dos chunks acumulados.** O streaming é um canal lateral (só emite tokens ao vivo). O `AgentResult` autoritativo é reconstruído do estado final do grafo pelo **mesmo** `_messages_to_agent_result`/`_derive_stop_reason` de hoje. Isso mata por construção a pegadinha de paridade de `usage`/`trace`: não reimplementamos a agregação. A task 1 valida que o estado final está disponível no fim do stream.

2. **Gotcha de usage por provider.** ChatAnthropic emite `usage_metadata` no stream nativamente. **ChatOpenAI NÃO emite usage em streaming sem `stream_usage=True`** (ou `stream_options={"include_usage": true}`). É exatamente por isso que a régua manda validar os providers OpenAI-compat reais, não só Anthropic. A task 1 mede isto; a task 2 aplica o fix no `_build_chat_model`.

---

## Sequência e dependências

```
TASK 1 (spike/demo em spikes/)  ──►  ★ CHECKPOINT GO/NO-GO ★
                                          │ (GO)
                                          ▼
TASK 2 (entry point streaming, agent_graph.py)  ── base compartilhada
   │
   ├─► TASK 3 (SSE /explore/stream, backend)  ─►  TASK 4 (frontend explore SSE)   ← a fatia que prova o valor
   │
   └─► TASK 6 (writing streaming — 2º produtor)   ← follow-on, adiável sem bloquear 3+4

TASK 5 (opcional/carona, Item 6b) — mesmo arquivo da TASK 2 → sequenciar DEPOIS dela (ou mesma branch)
```

- **TASK 1 é obrigatória e bloqueante.** Nenhuma outra roda antes do checkpoint.
- **TASK 2 → 3 → 4** é o caminho que entrega o ganho visível (explore com tokens ao vivo).
- **TASK 6** (writing) reusa a máquina da TASK 2 mas tem superfície própria (router + frontend) e wrinkles próprios (interrupt/resume, tool_trace, delta por `prior_n_msgs`). **Pode ser adiada** sem bloquear o valor do explore — respeitando "menor mudança que prova o valor".
- **TASK 5** (Item 6b) é ortogonal a streaming **no valor**, mas toca o mesmo `agent_graph.py` da TASK 2 → **sequenciar após a TASK 2 ou fazer na mesma branch** (evita conflito de merge). Incluída como carona porque a spec previu.

Régua de "pronto" (herdada da spec): comportamento-preservador ⇒ **sem gate de eval**; validação = **smoke via skill `verify`** dirigindo o chat real + os critérios de aceite por task.

---

## TASK 1 — Demo de streaming + medição (spike throwaway) · **BLOQUEANTE**

**Objetivo.** Provar, contra o grafo REAL do explore, que (a) dá para emitir tokens antes de o turno terminar e (b) o estado final do stream permite reconstruir `AgentResult` com paridade de `usage`/`trace` — nos dois transportes de provider do sistema.

**Arquivo (único, throwaway):** `spikes/lever1_streaming/demo.py` (+ `__init__.py` se necessário). Nada em `core/` de produção.

**O que o script faz:**
1. Monta as tools e o system do explore como em produção — importa `EXPLORE_AGENT_SYSTEM` e `build_explore_tools()` (`core/services/explore_agent.py:194-203`), sem perfil (caminho mais simples).
2. Compila o grafo real via `_build_graph(chat, tools, max_steps=..., checkpointer=False)` (`core/llm/agent_graph.py:129`) — mesma construção do entry point stateless.
3. Roda **duas variações de transporte** sobre o mesmo input e compara:
   - (A) `graph.astream_events(init, config, version="v3")` — consome `on_chat_model_stream` (delta de token em `event["data"]["chunk"]`) e captura o estado final no `on_chain_end` da raiz (`event["data"]["output"]`).
   - (B) `graph.astream(init, config, stream_mode=["messages", "values"])` — `("messages", (chunk, meta))` dá os tokens; o último `("values", state)` dá o estado final.
   - Registra qual dá token+estado-final de forma mais limpa (isso alimenta a escolha da TASK 2).
4. Roda o `ainvoke` de referência (`core/llm/agent_graph.py:403`) sobre o mesmo input.
5. **Mede** latência-até-primeiro-token (streaming) vs latência-total (`ainvoke`), e imprime tokens ao vivo no terminal.
6. **Reconstrói** `AgentResult` do estado final do stream usando os tradutores REAIS (`_messages_to_agent_result` + `_derive_stop_reason`) e compara campo-a-campo com o `AgentResult` do `ainvoke`: `final_text`, `stop_reason`, `usage.input_tokens`, `usage.output_tokens`, nº de `steps` e nomes/ordem das tools.
7. Roda o item 6 **para os dois providers**:
   - **Anthropic** (`provider="anthropic"`, `ANTHROPIC_API_KEY`).
   - **OpenAI-compat real do sistema** (`provider="openai"`, via `AGENT_OPENAI_BASE_URL`/`AGENT_OPENAI_API_KEY` — o mesmo endpoint agêntico de produção). Testar **com e sem `stream_usage=True`** no `ChatOpenAI` para provar empiricamente o gap de usage.
8. Chama `shutdown_writing_runtime()` no fim (flush de telemetria; `core/llm/agent_graph.py:558`).

**Critérios de aceite (todos observáveis no terminal):**
- [ ] Tokens do explore aparecem **incrementalmente** antes de o turno terminar (não em bloco no fim), em pelo menos um transporte.
- [ ] Latência-até-primeiro-token < latência-total do `ainvoke` (registrar os dois números).
- [ ] O `AgentResult` reconstruído do estado final **bate** com o do `ainvoke` em `final_text`, `stop_reason`, contagem de `steps` e nomes de tools.
- [ ] **Paridade de usage** confirmada para Anthropic; e para OpenAI-compat com `stream_usage=True` (documentar que sem a flag o usage vem zerado — evidência do fix da TASK 2).
- [ ] Anotação curta (no topo do script ou num `FINDINGS.md` ao lado): transporte escolhido (astream_events v3 vs astream multi-mode), números de latência, e o veredito da flag OpenAI.

---

## ★ CHECKPOINT GO / NO-GO ★ (após TASK 1, antes de qualquer outra)

**GO** exige as DUAS condições da spec:
1. **(a)** Tokens saíram antes de o turno terminar (streaming real observado).
2. **(b)** O estado final do stream permitiu reconstruir `AgentResult` com **paridade de `usage`/`trace`** — incluindo o provider OpenAI-compat (com o fix `stream_usage` identificado).

- **GO** → seguir para TASK 2 com o transporte escolhido pela TASK 1.
- **NO-GO** (tokens só saem no fim, OU não há paridade de usage/trace reconstruível, OU o provider OpenAI-compat não streama) → **parar**. Registrar o motivo no `FINDINGS.md`, arquivar as tasks 2-6 e reportar. Não implementar entry point de produção contra um sinal que não apareceu. (Freio de mão anti-sunk-cost — princípio da spec.)

---

## TASK 2 — Entry point streaming aditivo (`agent_graph.py`) · dep: GO

**Objetivo.** Um novo entry point streaming ao lado do `ainvoke`, sem tocar `run_agent_graph_async` nem nenhum call site. Reusa os tradutores de estado final.

**Arquivos:**
- `core/llm/agent_graph.py` — adicionar (não alterar os existentes):
  - `_build_chat_model` (`:75`): no ramo OpenAI (`:99-115`), setar `stream_usage=True` no kwargs do `ChatOpenAI`. Seguro para o path não-streaming (a flag só afeta streaming). Comentar a razão (gap de usage do OpenAI em stream, achado na TASK 1). **Não** mexer no ramo Anthropic.
  - Novo dataclass leve de evento, ex.: `StreamDelta(kind: Literal["token","tool_start","tool_end","done","error"], text: str = "", name: str = "", result: AgentResult | None = None)`.
  - Nova async generator `run_agent_graph_streaming(...)` — **mesma assinatura** de `run_agent_graph_async` (`:333`), retornando `AsyncIterator[StreamDelta]`:
    - Reusa `_build_chat_model`, `_to_lc_messages`, `_build_system_message`, o `init` state e o bloco `telemetry.agent_run` + `make_callback_handler` idênticos ao `:355-401`.
    - Consome o grafo pelo transporte escolhido na TASK 1 (`astream_events(version="v3")` ou `astream(stream_mode=[...])`), passando `config` com `callbacks` do Langfuse (paridade de telemetria com `:399-401`).
    - `yield StreamDelta(kind="token", text=delta)` por delta de token; opcionalmente `tool_start`/`tool_end` para indicador de "pensando".
    - No fim: captura o **estado final**, chama `_derive_stop_reason` + `_messages_to_agent_result` (os MESMOS de `:414-416`) e faz `yield StreamDelta(kind="done", result=<AgentResult>)`.
    - Espelha o tratamento de erro de `:404-412` (GraphRecursionError → max_steps; Exception → error) emitindo `StreamDelta(kind="error"/"done")` com `AgentResult` degradado — nunca propaga exceção crua pelo generator.
- `core/llm/agent_runtime.py` — adicionar shim `run_agent_streaming_async` que delega (espelha `run_agent_async:214-268`), mantendo a fachada. Não alterar `run_agent`/`run_agent_async`.

**Restrições:** aditivo puro. `run_agent`, `run_agent_async`, `run_agent_graph_async`, `run_writing_turn`, `run_generation_turn` e todos os call sites ficam **byte-idênticos**. O contrato `AgentResult` não muda.

**Critérios de aceite:**
- [ ] `tsc`/import: o módulo importa sem erro; `run_agent_graph_async` intacto (diff só adiciona símbolos + a flag `stream_usage`).
- [ ] Script de smoke (pode ser um segundo arquivo em `spikes/` ou reuso do da TASK 1) consome `run_agent_graph_streaming` e imprime: tokens ao vivo + o `AgentResult` final do evento `done`, com `usage` não-zero nos dois providers.
- [ ] Paridade: rodar o mesmo input por `run_agent_graph_async` e por `run_agent_graph_streaming` e conferir `final_text`/`stop_reason`/`usage` iguais (tolerância de usage: idênticos com `stream_usage=True`).
- [ ] Nenhum call site existente alterado (grep confirma que só os novos símbolos foram adicionados).

---

## TASK 3 — Endpoint SSE `/explore/stream` (backend) · dep: TASK 2

**Objetivo.** Expor o turno de explore como Server-Sent Events, aditivo ao `POST /explore` atual, preservando TODO o pós-processamento (match cards, profile diff, persistência, vereditos).

**Arquivos:**
- `backend/routers/explore.py` — adicionar `@router.post("/explore/stream")` **`async def`** (o `/explore` sync em `:118` fica intacto):
  - Reusa o mesmo `ExploreRequest`, o mesmo rate-limit (`_explore_limit`/`_explore_key`), o mesmo `_profile_context_block`.
  - Precisa de uma variação de `explore_agent` que **streame** em vez de retornar string. Duas opções — escolher a de menor toque:
    - **(preferida)** adicionar `explore_agent.explore_stream(...)` em `core/services/explore_agent.py` que espelha `_explore_agent:205-367` mas troca `run_agent(...)` (`:337`) por `run_agent_streaming_async(...)`, repassando os `StreamDelta` de token e devolvendo no fim o `(AgentResult, meta)` para o router computar `called_match`/`truncated` (`:346-356`).
  - O handler retorna `fastapi.responses.StreamingResponse(gen(), media_type="text/event-stream")` onde `gen()`:
    1. emite frames `event: token\ndata: {"text": "..."}\n\n` conforme os deltas chegam;
    2. ao receber o `done` do agente, roda o **pós-processamento existente** (match_v3, ProfileExtractor diff, `_match_cards_intro`, persist, vereditos) — hoje sync em `:174-239`; num handler async, envolver os trechos bloqueantes em `await asyncio.to_thread(...)`;
    3. emite um frame terminal `event: done\ndata: {<JSON idêntico ao retorno do /explore atual>}\n\n` (mesmas chaves: `answer`, `truncated`, `matched_editais`, `matched_entities`, `profile_diff`, `session_id`, `entry_ids`, `next_action`, ...);
    4. em erro, emite `event: error\ndata: {"message": "..."}\n\n`.
  - **Wrinkle documentado (não é bug):** quando o agente chama tools de match, o `answer` final é SOBRESCRITO por `_match_cards_intro` (`:199`). Logo os tokens streamados são um **preview progressivo**; o `done.answer` é **autoritativo** e pode divergir do que foi streamado. O contrato SSE deixa isso explícito: o frontend trata `done.answer` como verdade final.
  - **Headers anti-buffering (obrigatório — ver "Validação end-to-end" abaixo).** O `StreamingResponse` DEVE setar: `Cache-Control: no-cache, no-transform`, `X-Accel-Buffering: no` (desliga buffering em proxies estilo nginx), `Connection: keep-alive`. Além disso, garantir que nenhum middleware de compressão (`GZipMiddleware`/similar) capture `text/event-stream` — gzip bufferiza o corpo inteiro e zera o ganho de TTFT. Conferir a stack de middlewares do app FastAPI e excluir o content-type do stream se necessário.

**Formato SSE (mínimo, fixado aqui — ver "Fora de escopo" sobre por que não é um protocolo genérico):**
```
event: token   data: {"text": "<delta>"}
event: tool    data: {"name": "<tool>", "phase": "start"|"end"}   (opcional)
event: done    data: { ...payload idêntico ao /explore atual... }
event: error   data: {"message": "<msg>"}
```

**Critérios de aceite:**
- [ ] `curl -N` em `/explore/stream` com um body de explore mostra frames `token:` chegando incrementalmente e um `done:` final com o mesmo shape do `/explore`.
- [ ] O `done.answer` para uma pergunta que dispara match traz o texto de `_match_cards_intro` + `matched_editais`/`matched_entities` populados (paridade com `/explore`).
- [ ] Persistência (`persist_frontdoor_turn`) e enfileiramento de vereditos ocorrem exatamente como no `/explore` (conferir `session_id`/`entry_ids` no `done` e a linha em `writing_sessions`/`session_turns`).
- [ ] `POST /explore` (sync) continua respondendo idêntico (não removido, não alterado).
- [ ] Headers anti-buffering presentes na resposta (`curl -i` mostra `X-Accel-Buffering: no` e `Cache-Control: no-transform`); nenhum middleware comprime `text/event-stream`.
- [ ] **Smoke via skill `verify`** dirigindo o `/explore/stream` real (não só `curl`): confirmar TTFT incremental e paridade do `done` com o `/explore`. É o gate de promoção do item (sem eval, o `verify` é a validação formal).

---

## TASK 4 — Consumo SSE no frontend do explore · dep: TASK 3

**Objetivo.** Renderizar tokens ao vivo na bolha do assistente do explore, aplicando o payload `done` no fim. Aditivo — o caminho `frontdoorTurn` fica até o cutover.

**Arquivos:**
- `frontend/src/lib/api.ts` — adicionar `exploreStream(...)` ao lado de `frontdoorTurn` (`:270`). Usa `fetch` com `body` igual, header `Accept: text/event-stream`, e lê `res.body.getReader()` + `TextDecoder`, parseando frames SSE (`event:`/`data:` separados por `\n\n`). Callback API: `onToken(text)`, `onDone(payload)`, `onError(msg)`. Reusa `getAccessToken()`/`API_BASE_URL` (não passar por `apiFetch:76`, que faz `res.json()`).
- `frontend/src/app/page.tsx` — no handler que hoje faz `await frontdoorTurn(...)` (`:346-367`): trocar (atrás de um flag/branch) por `exploreStream`, acumulando tokens numa bolha de assistente "em progresso" e, no `onDone`, aplicar o mesmo destructuring atual (`answer`, `truncated`, `matched_editais`, `matched_entities`, `session_id`, `entry_ids`, `next_action`) — substituindo o texto da bolha por `done.answer` (autoritativo, cobre o caso match onde diverge do streamado).

**Critérios de aceite:**
- [ ] No chat de explore, uma resposta longa (pergunta conceitual sem match) aparece **token a token**.
- [ ] Uma pergunta que dispara match: os cards e o texto final (`_match_cards_intro`) aparecem via `onDone`, idênticos ao comportamento não-streaming (o preview streamado é substituído sem glitch visível de dados).
- [ ] `truncated`/`next_action`/`session_id` continuam funcionando (aviso de corte, oferta de planejamento, persistência).
- [ ] Fallback: se o browser/rede não suportar o stream, ou em erro, cai para `frontdoorTurn` (caminho antigo intacto).
- [ ] **Smoke via skill `verify`** dirigindo o chat real, **dois casos** (sem match e com match). É o gate de promoção do item (comportamento-preservador ⇒ sem eval; o `verify` é a única validação formal).
- [ ] **Validação end-to-end através do caminho de produção** (ver seção dedicada abaixo) — não só localhost.

### Validação end-to-end através do caminho de produção (Tasks 3+4) · **crítico**

O deploy real é **Docker + Cloudflare Tunnel** (memória `project_v3_progress`), não localhost direto. Proxies (Cloudflare, o próprio tunnel, qualquer reverse-proxy) podem **bufferizar** a resposta SSE e entregá-la em bloco no fim — o que **zera silenciosamente o ganho de TTFT** e faz o streaming "funcionar" em dev e falhar em prod sem erro nenhum. É o tipo de falha que só aparece deployado.

**Portanto o smoke de promoção NÃO é só localhost:**
- [ ] Medir TTFT **através da URL do tunnel** (prod ou staging deployado), não do `localhost:8000`. Comparar com o TTFT medido localmente — se convergirem para ~latência-total, o proxy está bufferizando.
- [ ] Confirmar que os headers anti-buffering da Task 3 sobrevivem ao Cloudflare Tunnel (o Cloudflare respeita `text/event-stream` para não-bufferizar, mas validar empiricamente, não por fé).
- [ ] Se houver buffering: investigar Cloudflare (regras de proxy/compressão para o content-type), a config do tunnel e qualquer reverse-proxy no Docker antes de declarar o item pronto.

---

## TASK 6 — Streaming da escrita (2º produtor) · dep: TASK 2 · **adiável**

**Objetivo.** Estender o streaming ao turno de escrita, reusando a máquina da TASK 2. **Follow-on**: não bloqueia o valor do explore (tasks 3-4). Marcada adiável porque tem wrinkles próprios.

**Wrinkles verificados (por que é mais pesado que o explore):**
- Path com **checkpointer durável** e **interrupt/resume** (`_writing_turn_async:789`, caller em `writing_session.py:1284-1320`). O streaming precisa conviver com o `Command(resume=...)` e com o `WritingTurnOutcome` (result do **delta** via `prior_n_msgs:871`, `interrupt` payload, `n_messages`).
- Resposta rica: `WritingTurnResponse` (`backend/routers/writing.py:92-115`) com `tool_trace`, `pending_user_input`, `sections_done`, `draft_ready`, `plan`. O `done` SSE tem que carregar tudo isso.
- Roda no **bg-loop dedicado** (`_run_on_bg_loop:480`) — a variação streaming precisa emitir os deltas cruzando essa fronteira de thread (fila/queue thread-safe entre o bg-loop e o handler async).
- **Geração em lote** (`run_generation_turn:1167`, `/writing/{id}/generate`) é paralela e **não tem token stream único** → **fora de escopo** deste item (ver abaixo).

**Arquivos (quando executada):** `core/llm/agent_graph.py` (variação streaming de `_writing_turn_async`), `core/services/writing_session.py` (`session.turn` streamando), `backend/routers/writing.py` (`/writing/turn/stream`), `frontend/src/lib/api.ts` + `frontend/src/app/workspace/[sessionId]/page.tsx:331`.

**Critérios de aceite (quando executada):**
- [ ] Turno de escrita conversacional streama tokens; o `done` traz `WritingTurnResponse` completo (incl. `tool_trace`, `draft_ready`).
- [ ] `interrupt/resume` **não regride**: um turno que pausa em `request_user_info` emite o `pending_user_input` corretamente e o resume continua.
- [ ] Paridade de `usage`/trace com o `run_writing_turn` não-streaming.
- [ ] Smoke via `verify` num fluxo real de escrita com pelo menos uma tool (`save_draft`).

---

## TASK 5 — (carona, opcional) Item 6b: aviso de "último passo disponível" · sequenciar após TASK 2

**Objetivo (do Item 6b da spec).** Reduzir truncamentos abruptos tornando o agente consciente do budget: quando `llm_calls == max_steps - 1`, injetar um aviso "último passo disponível — conclua com o que tem". Ortogonal a streaming **no valor**, mas **não no merge**: toca o MESMO arquivo `core/llm/agent_graph.py` que a TASK 2 (nó `tools`/`after_tools`/factory). Para não criar conflito bobo, **sequenciar após a TASK 2** (ou fazer as duas na mesma branch). Mecânica conhecida ⇒ sem spike (plan→impl direto).

**Arquivo:** `core/llm/agent_graph.py` — a condição vive no nó `tools` (`:163-178`) ou numa checagem em `after_tools` (`:180-188`). Os contadores já estão no estado (`llm_calls`, `max_steps` é closure de `_build_graph`). Mecanismo mais limpo: no nó `tools`, após capear os tool-results, se `state["llm_calls"] == max_steps - 1`, **append** de uma mensagem de aviso (curta) ao retorno `messages`. Reusar o padrão já seguro do `_FINALIZE_PROMPT` (`core/llm/agent_runtime.py:131`), que já é texto interno injetado e comprovadamente não vaza (roda antes do `agent_final` sem tools). Definir a constante ao lado, ex. `_BUDGET_WARNING`.

**Pegadinha crítica (mesma classe do bug `_REFLECT_PROMPT` — memória `project_reflect_leak_bug`):** o aviso entra no histórico e **não pode vazar como resposta ao usuário**. A construção atual do runtime já elimina a HumanMessage-interna-no-meio-do-turno (docstring `agent_graph.py:8-18`); reintroduzir uma exige provar que ela não reaparece no `final_text`.

**Critérios de aceite:**
- [ ] Num turno forçado a chegar em `max_steps-1` (rodar um caso com `max_steps` baixo e tools que sempre pedem continuação), o aviso é injetado e o agente **conclui** no passo seguinte.
- [ ] **Anti-leak (obrigatório):** o texto de `_BUDGET_WARNING` **nunca** aparece em `AgentResult.final_text` nem em nenhuma resposta ao usuário — assert explícito no smoke, cobrindo o caso em que o passo final NÃO chama tools (o mais arriscado). Rodar ≥3× (o bug do reflect era intermitente).
- [ ] Taxa de truncamento (`stop_reason == "max_steps"`) medida antes/depois num punhado de turnos reais: não piora; idealmente cai.
- [ ] Sem gate de eval (não muda a evidência vista, só avisa budget) — smoke via `verify`.
- [ ] **Pré-passo recomendado (spec 6a):** antes de implementar, medir a taxa real de truncamento por modo (o campo `truncated` já existe nos resultados). Se ~0, **arquivar a task** em vez de implementar.

---

## Fora de escopo (explícito)

- **Polimento visual do streaming.** Animação de digitação fina, render de markdown parcial durante o stream, skeletons — só o mínimo para mostrar tokens ao vivo e aplicar o `done`. Refino de UX é trilha separada.
- **Contrato SSE genérico multi-produtor.** Fixamos um taxonomia mínima (`token`/`tool`/`done`/`error`) suficiente para explore, reusada pela escrita. **Não** construir protocolo negociado, versionado ou multiplexado — isso é *harness smell* (gatilho de reavaliação SDK, não de construir à mão).
- **Streaming da geração em lote** (`run_generation_turn`, `/writing/{id}/generate`). São N seções em paralelo sem um token stream único coerente; a orquestração é `asyncio.gather`. Streamar isso é outro desenho (ligado ao Item 4 `Send`), fora do Item 1.
- **Mudança no contrato `AgentResult`/`TraceStep`.** Imutável. Todo o valor do plano depende de o streaming ser um canal lateral e o `AgentResult` sair do mesmo tradutor de estado final.
- **`on_step` real-time.** Não reaproveitamos `on_step` (pós-hoc, `agent_graph.py:428`) como mecanismo de streaming; ele fica como está. O canal ao vivo é o generator novo.
- **Migração dos call sites atuais.** `run_agent`/`run_agent_async`/`run_agent_graph_async` e o `POST /explore` sync permanecem até o cutover explícito do frontend. Nada é removido neste item.
- **Outros itens da trilha** (#2 contexto, #3 threads, #4 Send, #5 interrupt, adjacente playbook_overlays). Este plano cobre só o #1 (+ carona #6b).

---

## Notas de execução para o implementador (Sonnet 5)

- Comece pela **TASK 1** e **pare no checkpoint**. Não adiante código de produção antes do GO.
- O transporte de stream (astream_events v3 vs astream multi-mode) é **decidido pela TASK 1**, não assumido aqui.
- Ao editar `_build_chat_model`, o `stream_usage=True` é o único toque no path existente e é seguro; qualquer outra alteração em símbolos existentes é fora de escopo (aditivo puro).
- Rode `tsc --noEmit` (não `npm run build`) para checar o frontend com o dev server ativo (memória `feedback_dev_build_conflict`).
- Validação é **smoke via skill `verify`** dirigindo o chat real — não há gate de eval (comportamento-preservador).

---

## Adendo pós-checkpoint (2026-07-18) — decisões ratificadas

Checkpoint da TASK 1: **GO ratificado** (governança). Fonte: `spikes/lever1_streaming/FINDINGS.md`.

1. **Transporte decidido: `graph.astream(init, config, stream_mode=["messages","values"])`.** `astream_events(version="v3")` está indisponível na versão pinada (`langgraph==1.2.6`, retorna objeto experimental não-iterável); v2 funciona mas exige heurística de estado final. No astream multimode o último item de `values` é o estado terminal **garantido pela API**.
2. **Critério de aceite ADICIONADO à TASK 2/3:** filtro `isinstance(msg, AIMessageChunk)` no modo `"messages"` — sem ele, `ToolMessage` bruto vaza como texto do assistente (bug real observado no spike; mesma classe do reflect-leak, memória `project_reflect_leak_bug`).
3. **Critério de paridade REVISADO:** campos estruturais (`stop_reason`/`n_steps`/`tools`) comparáveis 1:1; para `final_text`/`usage`, o sinal é **ausência de zeragem/vazio**, não igualdade entre execuções independentes (variância residual do LLM, mesmo com temp=0).
4. **`stream_usage=True` mantido como defensivo** (langchain-openai 1.3.3 já popula usage em stream por padrão — premissa do plano não reproduziu; o fix é inócuo e protege contra regressão de lib).
5. **Smoke Anthropic — REBAIXADO a condição diferida (correção 2026-07-18, gate):** verificado nos containers que **não existe `ANTHROPIC_API_KEY` em nenhum ambiente** (prod/staging/local — só `OPENAI_API_KEY`; confirmado por Lucas). O `resolve_agent_provider` sempre cai no fallback OpenAI: **o caminho testado no spike É o caminho real de produção**, e o Anthropic é intestável hoje. Condição registrada: **quando uma `ANTHROPIC_API_KEY` for introduzida em qualquer ambiente, rodar o smoke de streaming Anthropic antes de ativá-la** (risco residual: extração de texto de content-blocks em `_msg_text` nunca exercitada em stream). O gate do item fica: smoke SSE via Docker (staging) pré-merge + smoke via Cloudflare Tunnel pós-deploy (aceitável porque o frontend tem fallback automático pro sync).

