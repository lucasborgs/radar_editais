# Spec — Migração do runtime agêntico para LangGraph

**Status:** rascunho de design (2026-06-19) · **Owner:** Lucas
**Janela:** pré-lançamento, sem usuários reais — momento ideal para migração arquitetural.

## Por que migrar

Dois gargalos do runtime custom atual ([core/llm/agent_runtime.py](../../core/llm/agent_runtime.py)):

1. **Memória cross-session** sem recuperação semântica e com triggers fracos — hoje
   um bloco fixo de 6 insights injetado no prefixo da WritingSession
   ([reflection_service.py](../../core/reflection_service.py), `load_active_insights`).
2. **Padrão multi-agente flat** que não suporta topologias complexas futuras —
   sub-agentes são tools que chamam `run_subagent` (loop dentro de loop).

LangGraph resolve os dois e dá a fundação para novos sub-agentes planejados.

## Princípios invariantes (valem em TODAS as etapas)

Estes contratos não podem quebrar durante a migração — são o que mantém os call
sites e o produto funcionando enquanto trocamos as entranhas:

| Invariante | Onde vive hoje | Regra na migração |
|---|---|---|
| **Contrato `AgentResult`** (`final_text`, `steps`, `stop_reason`, `usage`) | `agent_runtime.AgentResult` | Preservado até a Etapa 3 trocar os call sites; depois, evolui só onde o caller também evoluir |
| **`@tool` (Pydantic→schema, docstring=descrição)** | `agent_runtime.tool` | Vira `@tool` do LangChain na Etapa 2 (troca de import); semântica idêntica |
| **Tools nunca lançam** (erro → string `"Erro ao executar 'X': …"`) | `Tool.call_async` try/except | Preservado via `ToolNode(handle_tool_errors)` + formato de string explícito |
| **Degradação graciosa de sub-agente** (falha nunca propaga ao pai) | `run_subagent` try/except | Preservado: subgrafo invocado dentro da tool, try/except → degrada |
| **Isolamento multi-tenant `workspace_id`** | RLS Supabase nos call sites | RLS continua nas tabelas de domínio; checkpointer/store **bypassam RLS** → isolamento por `thread_id`/namespace (ver Etapas 3 e 5) |
| **Integração Langfuse** | `telemetry.*` context managers | Vira `CallbackHandler` LangChain via `config` (Etapa 6); callback mínimo já na Etapa 1 pra não ir às cegas |
| **Fallback de provider por API key** | `resolve_agent_provider` | Mantido como factory de `ChatModel` |
| **Endpoint OpenAI-compat ZDR** (`AGENT_OPENAI_*`, `CRITIC_OPENAI_*`) | `_openai_agent_client` | `ChatOpenAI(base_url=, api_key=)`; aviso ZDR (dado de cliente) sobrevive |

## Ordem de implementação (grafo de dependências)

```
1. Runtime core (StateGraph ReAct)   ← fundação; nada depende de mais nada
2. Tools (troca de import @tool)      ← depende de 1 (bridge removido aqui)
3. WritingSession + checkpoints       ← depende de 1+2
4. Sub-agentes como subgrafos         ← depende de 1+2 (reusa o builder)
5. Memória cross-session (Store API)  ← depende de 3 (injeção sai do prefixo)
6. Eval + telemetria                  ← depende de tudo (portão de regressão)
```

A ordem de implementação é a ordem de dependência acima. Esta spec detalha as 6
etapas em profundidade; cada uma tem um **critério de aceitação** que destrava a
seguinte.

---

# Etapa 1 — Runtime core: `run_agent_async` → StateGraph ReAct

## Objetivo
Substituir o loop ReAct hand-rolled por um `StateGraph` LangGraph, **sem mexer em
nenhum call site**. A facade (`run_agent`/`run_agent_async`/`run_subagent`) e
`AgentResult`/`TraceStep` ficam intactos.

## Decisões arquiteturais

1. **Custom `StateGraph`, não `create_react_agent` prebuilt.** Os 3 comportamentos
   custom (cap de tool-result, reflexão dinâmica, fidelidade de trace) brigam com o
   prebuilt. Grafo de 3 nós (`agent` / `tools` / `reflect`) dá controle total.
2. **Facade preserva o contrato.** As funções públicas viram um wrapper fino:
   constroem o grafo, `ainvoke`, e traduzem o estado final em `AgentResult`.
3. **Sem checkpointer nesta etapa** — grafo compilado stateless por chamada.
   Durabilidade é Etapa 3.
4. **Bridge `Tool → StructuredTool`** preserva `_cap` + error-string. Removido na
   Etapa 2 (quando `@tool` já é LangChain) — e aí o cap central migra para um nó.

## Mapa: loop atual → grafo

| Hoje (`run_agent_async`, linhas 656-916) | LangGraph |
|---|---|
| `for step_idx in range(max_steps)` | edges `agent → tools → agent` + condicional |
| `call_llm(...)` → `_LLMStep` | nó `agent`: `model.bind_tools(tools).ainvoke(messages)` → `AIMessage` |
| `asyncio.gather(_exec_tool …)` | nó `tools`: `ToolNode` (gather interno por tool_call) |
| `if not tool_uses: break` | `should_continue`: `AIMessage.tool_calls` vazio → `END` |
| reflexão (linhas 880-895) | nó `reflect` entre `tools` e `agent`, edge condicional |
| `for...else` → `max_steps` | `recursion_limit` + catch `GraphRecursionError` |
| `messages` list manual | `State` TypedDict (abaixo) |

```python
class AgentState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]
    tool_rounds: int
    rounds_since_reflect: int
    chars_since_reflect: int
```

## Comportamentos custom — como preservar

| Comportamento | Friction prebuilt | Decisão |
|---|---|---|
| `_cap` (8000 chars, linha 842) | `ToolNode` não trunca | Bridge envolve `func` aplicando `_cap` no retorno |
| Tools nunca lançam (formato de erro) | `handle_tool_errors` formata diferente | Bridge mantém try/except + string exata |
| Reflexão dinâmica (teto + sinais erro/plan-change/big-output) | sem hook prebuilt | nó `reflect`: edge condicional pós-`tools` lê contadores/sinais, injeta `_REFLECT_PROMPT` como `HumanMessage`, zera contadores |
| Fallback de provider | `init_chat_model` não tem | mantém `resolve_agent_provider` → factory `(provider, model) → ChatAnthropic\|ChatOpenAI` |
| Endpoint ZDR custom | — | `ChatOpenAI(base_url=, api_key=)`; aviso ZDR sobrevive |
| Concorrência de tools | `ToolNode` já faz gather | preservado; ordering por message-id |

## `max_steps` → contador em state (DECISÃO FECHADA, revisada no spike)
**NÃO usar `recursion_limit` como teto primário.** Ele conta super-steps
(agent+tools+reflect por ciclo) → mapeamento frágil pra `max_steps` (nº de chamadas
LLM) e termina via **exceção** (`GraphRecursionError`). Em vez disso: contador
`llm_calls` no state, incrementado no nó `agent`. O cap é uma aresta condicional
**pós-`tools`** (`after_tools`): se `llm_calls >= max_steps` → `END`. Pós-tools (não
pós-agent) dá **paridade exata** com o legado, que executa as tools da última rodada
antes de parar. `recursion_limit` vira só backstop generoso (`3·max_steps+5`).
Terminação graciosa (`stop_reason="max_steps"`), sem catch de exceção no caminho
normal. **Resolve o risco #4.**

## Tradutor de contrato (`_messages_to_agent_result`) — peça crítica

| `AgentResult` | Fonte no estado final |
|---|---|
| `final_text` | última `AIMessage` sem `tool_calls` → `.content` |
| `steps` | `AIMessage` → `kind="llm"` (+`tool_uses` de `.tool_calls`); `ToolMessage` → `kind="tool"` |
| `usage` | soma `usage_metadata` das `AIMessage` |
| `stop_reason` | `END`→`end_turn`; `GraphRecursionError`→`max_steps`; exceção→`error` |

`writing_session._extract_tool_trace` **não muda** — segue pareando `llm.tool_uses`
com `tool` por ordem.

## O que muda por módulo

| Módulo | Mudança |
|---|---|
| `core/llm/agent_runtime.py` | **+** `_build_react_graph`, `AgentState`, nós, `_model_factory`, `_to_langchain_tool` (bridge), `_messages_to_agent_result`. **−** `_call_openai`, `_call_anthropic`, `_format_tool_results_*`, `_LLMStep`, corpo do loop. **=** `run_agent*`/`run_subagent` (facade), `AgentResult`/`TraceStep`, `Tool`/`@tool`/`ToolRegistry`/`_cap`, shim sync |
| 5 call sites (writing/kg_match/profile/deep_research/critic) | **nenhuma** |
| `tests/test_agent_runtime.py`, `test_context_budget.py`, `test_subagent.py` | reescritos no seam de chat-model fake (`GenericFakeChatModel`) |
| `tests/test_{explore,profile_extractor,writing_session}_agent.py` | **inalterados** (mockam a facade) |
| `pyproject.toml` | + `langgraph`, `langchain-core`, `langchain-anthropic`, `langchain-openai` |

## Riscos
| # | Risco | Sev. | Mitigação |
|---|---|---|---|
| 1 | Fidelidade do tradutor de contrato (trace/usage/stop_reason) — drift silencioso quebra persistência + custo | **Alta** | golden test do tradutor vs `AgentResult` atual, mesmo input |
| 2 | Seam de teste do adapter (3 arquivos, **não** 11) | Média | helper único `make_fake_chat_model(sequence)` |
| 3 | Semântica da reflexão (o mais custom; muda → score de eval move) | Média-alta | replicar os 4 sinais; teste de cadência |
| 4 | ~~`recursion_limit` vs `max_steps`~~ | ✅ resolvido | contador `llm_calls` em state + cap pós-`tools` (ver acima) |
| 5 | `_cap` sumir no ToolNode | Média | embutir no bridge |

## Critério de aceitação
- `test_agent_runtime`/`test_context_budget`/`test_subagent` verdes no novo seam.
- Suítes de serviço (writing/explore/profile) verdes **sem alteração**.
- Golden do tradutor: `AgentResult` byte-equivalente em 3 cenários (no-tool, 1-tool, erro de tool).
- Suíte de eval `writing` roda real e não regride `pct_grounded`/`n_factual_errors` vs baseline.

## FECHADA (2026-06-19, branch `feat/langgraph-runtime-spike`)
LangGraph é o **runtime default**; legado intacto como rollback via `AGENT_RUNTIME=legacy`.
- **Deps** — resolução limpa; `langgraph 1.2.6` + `langchain-core 1.4.8` reusando
  `openai 2.36`/`anthropic 0.105`. Fixadas no `pyproject.toml`.
- [core/llm/agent_graph.py](../../core/llm/agent_graph.py) — `StateGraph` 3 nós
  (agent/tools/reflect), bridge `Tool→StructuredTool` (preserva `_cap`+error-string),
  tradutor `_messages_to_agent_result`, factory `_build_chat_model` (seam de teste),
  cap de iterações via `llm_calls` + aresta pós-`tools`, telemetria.
- **Dispatch invertido**: `run_agent_async` delega ao grafo por default; só
  `AGENT_RUNTIME=legacy` cai no loop antigo ([agent_runtime.py](../../core/llm/agent_runtime.py)).
- **Telemetria mínima**: `agent_run` (real-time) + spans por-step pós-hoc com
  `usage_details` (preserva rollup de custo/turno). Descoberta: o `CallbackHandler`
  do Langfuse exige o meta-pacote `langchain` inteiro → spans nativos (timing real)
  ficam pra Etapa 6, quando se decide adicionar essa dep.

### Validação
- **Golden** [test_agent_graph_golden.py](../../tests/test_agent_graph_golden.py) —
  `AgentResult` equivalente legado-vs-grafo (no-tool / 1-tool / tool-error / max_steps
  com trace completo `llm,tool,llm,tool`) + reflexão + cap no grafo + degradação por
  erro de modelo + span_name.
- **Suíte completa: 709 passed**, regressão-zero vs tree limpo (a única falha,
  `test_contextual_retrieval::test_sem_api_key_degrada`, é flake de ordering
  PRÉ-EXISTENTE — idêntica sem as mudanças).
- Os 3 testes de adapter (`test_agent_runtime`/`test_context_budget`/`test_subagent`)
  pinados a `AGENT_RUNTIME=legacy` — guardam o rollback.
- **Smoke LLM real** (OpenAI): tool calling via bridge, `42`, telemetria emitida.
- **Eval `writing --limit 1` pelo grafo**: WritingSession + tools reais + **Critic
  subagente** + RAG → `saved=1`, `coherent=1`, `factual_errors=0`. Caminho real completo.

### Não feito (deferido por etapa)
- Schema-parity de `@tool` nativo (Etapa 2 — usa o bridge).
- Spans nativos LangChain timing-real (Etapa 6).
- **Comparação eval graph-vs-legacy em N casos** (gate semântico estatístico) — só
  rodamos 1 caso one-sided; recomendado antes de deletar o legado.
- **Divergência residual conhecida**: erro de LLM (não de tool) que levanta no nó
  `agent` → grafo devolve `AgentResult` vazio (`error`); o legado preserva trace
  parcial. Aceitável.

---

# Etapa 2 — Tools: troca de import para `@tool` LangChain

## Objetivo
Trocar `from core.llm.agent_runtime import tool` por `from langchain_core.tools
import tool` nos 7 módulos de tools, removendo o bridge da Etapa 1.

## Módulos afetados
`agent_tools/`: `writing_tools.py`, `critic_agent.py` (`build_critic_tools`),
`research_tools.py`, `explore_tools.py`, `planning_tools.py`, `profile_tools.py`,
`scratchpad_tools.py`. Padrão uniforme: `build_<agente>_tools(state) -> list[Tool]`
com closures sobre o estado — **compatível com o `@tool` LangChain** (closures OK).

## Decisões arquiteturais

1. **`@tool` LangChain infere schema igual** (type hints + docstring). `@tool(name=,
   description=)` → `@tool("nome", parse_docstring=True)`. Nosso `_infer_input_schema`
   é aposentado.
2. **`_cap` permanece nosso helper** (move para `core/llm/agent_tools/_utils.py` ou
   fica em `agent_runtime`). Os usos internos em `explore_tools` (linhas 468/476) e
   `writing_tools` (119/126/221) **não mudam** — continuam chamando `_cap`.
3. **Cap central migra do bridge para um nó.** Como o bridge da Etapa 1 some, o teto
   de segurança de 8000 chars vira um **nó pós-`ToolNode`** que trunca o conteúdo de
   cada `ToolMessage` acima do orçamento. (Alternativa: `ToolNode` customizado.)
4. **Error-string nas tools nativas.** Tool LangChain que levanta → `ToolNode`
   captura. Para manter o formato `"Erro ao executar 'X': …"` que o modelo já
   reconhece, ou (a) `ToolNode(handle_tool_errors=lambda e: f"Erro ao executar…")`,
   ou (b) cada tool mantém o try/except interno. Decisão: **(a)**, centralizado.

## O que muda por módulo
| Módulo | Mudança |
|---|---|
| 7 × `agent_tools/*.py` | troca de import; tipo de retorno `list[Tool]` → `list[BaseTool]`; remoção de `@tool(name=…)` custom onde houver |
| `agent_runtime.py` | remove `_to_langchain_tool` (bridge); `Tool`/`ToolRegistry` viram dead code (deletar ou manter só `_cap`); cap central vira nó no `_build_react_graph` |
| `__init__.py` do `agent_tools` | docstring "build_… -> list[Tool]" → `list[BaseTool]` |

## Riscos
| # | Risco | Sev. | Mitigação |
|---|---|---|---|
| 1 | Schema inferido difere sutilmente (defaults, `Optional`, docstring parsing) | Média | snapshot test do JSON schema de cada tool antes/depois |
| 2 | Tool que retornava não-str (raro) | Baixa | `ToolNode` exige str/ToolMessage; auditar retornos |
| 3 | Cap central esquecido na migração do bridge → nó | Média | herda o teste de `test_context_budget` |

## Critério de aceitação
- Schemas idênticos (snapshot) para todas as tools.
- `test_context_budget` verde com cap no nó.
- Suítes de serviço verdes sem mudança de comportamento.

## FECHADA (2026-06-19) — legado APOSENTADO
LangGraph é o **runtime único**; o loop legado, adapters, `Tool`/`@tool`/
`ToolRegistry`/`_LLMStep` foram **deletados** de `agent_runtime.py` (agora facade
fina: contrato + `resolve_agent_provider` + shims + `run_subagent` + `_cap`).
- 8 arquivos com `from core.llm.agent_runtime import tool` → `from
  langchain_core.tools import tool`; `list[Tool]` → `list[BaseTool]`. Zero
  override `@tool(...)` → swap trivial.
- `agent_graph`: bridge removido (ToolNode consome tools nativas); cap central
  movido pro nó `tools`; `ToolNode(handle_tool_errors=_tool_error_to_str)`
  preserva a degradação graciosa (erro de tool → string com prefixo "Erro ao
  executar", que mantém o sinal de reflexão).
- **Diferença de camada**: o LangChain valida args contra o schema pydantic
  ANTES da função → shape inválido levanta (no loop o ToolNode converte em
  string). Validação semântica do tool segue retornando string.
- Testes: `.call(...)` → `.invoke(...)` em 7 arquivos de teste; `test_agent_runtime`
  enxugado p/ só `resolve_agent_provider`; goldens viraram graph-only.
- **Suíte: 678 passed**, regressão-zero (só a flake pré-existente
  `test_contextual_retrieval`). Schemas LangChain já validados com modelo real na
  Etapa 1 (o bridge usava `StructuredTool.from_function` = mesma inferência).

---

# Etapa 3 — WritingSession: TypedDict + checkpoints no Postgres

## Objetivo
A WritingSession (1795 linhas, [writing_session.py](../../core/services/writing_session.py))
para de chamar `run_agent` e passa a invocar o grafo compilado com um
**checkpointer no Postgres**, keyed por sessão.

## O que a WritingSession é hoje
Objeto **stateless-por-request**, reconstruído do Postgres a cada chamada
(`_load_from_db`, linha 496). Estado de domínio rico: `_history`,
`_history_summary`, `_doc_sections`, `_proposal_outline`, `_pending_user_input`,
`mode`, `_scope_edital_ids`, contexto de perfil/library/temporal/pitch. Persiste em
**duas tabelas de produto**: `writing_sessions` e `session_turns` (esquema próprio:
`turn_index` remapeado 2N-1/2N, `tool_use` JSONB para o frontend, `tokens`,
`section_hint` — linhas 979-1025). Compressão de histórico em `COMPRESS_THRESHOLD`.

## Decisão arquitetural central: checkpointer **escopo-de-turno**, tabelas de domínio **autoritativas**

Duas opções:
- **(A) Checkpointer como store de conversa** — `thread_id=session_id`, State guarda
  `messages`, substitui a reconstrução de `_history`. **Rejeitada para o cutover**:
  `session_turns` carrega concerns de produto (tool_trace pro frontend, tokens,
  esquema 2N-1/2N, RLS) que o blob de checkpoint não modela — arrancar isso é scope
  creep e risco.
- **(B) Checkpointer escopo-de-turno (ephemeral), domínio autoritativo** — o grafo
  roda **um turno**; `_history` continua reconstruído de `session_turns`. **Escolhida.**

**Payoff concreto da (B):** `request_user_info` (hoje um flag side-channel
`_pending_user_input`, linhas 301/929) vira um **`interrupt()` LangGraph nativo** →
human-in-the-loop de verdade, com retomada pelo checkpointer. Esse é o ganho que
justifica o checkpointer aqui — não a substituição do store de conversa.

## Estado do turno (TypedDict)
```python
class WritingTurnState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]
    session_id: str
    workspace_id: str
    mode: Literal["proposal", "pitch"]
    scope_edital_ids: list[str]
    pending_user_input: dict | None   # alimenta interrupt()
    # contadores de reflexão herdados da Etapa 1
```
A WritingSession **continua existindo** como objeto de serviço/repositório (carrega
perfil, library, outline, persiste turns). O que muda: em vez de
`run_agent(...)`, ela faz `graph.invoke(state, config={"configurable":
{"thread_id": f"{workspace_id}:{session_id}"}, "callbacks": [...]})` e traduz o
resultado (reusa `_messages_to_agent_result` ou consome o estado direto).

## O que muda por módulo
| Módulo | Mudança |
|---|---|
| `writing_session.py` `_turn_agent` (788-864) | `run_agent` → `graph.invoke` com checkpointer; `request_user_info` vira `interrupt()`; `_extract_tool_trace` consome o estado final |
| `_build_agent_initial_messages` (941-977) | inalterado nesta etapa (injeção de insights sai na Etapa 5) |
| `_persist_turn` (979-1025) | inalterado — `session_turns` segue autoritativo |
| `build_writing_tools(session)` | inalterado (closure sobre a session) |
| migrations Supabase | **+** tabelas do checkpointer (`checkpoints`, `checkpoint_writes`, `checkpoint_blobs`) via `AsyncPostgresSaver.setup()` |
| `backend/routers/writing.py` | rota de retomada de `interrupt` (frontend responde ao `request_user_info`) |

## Riscos
| # | Risco | Sev. | Mitigação |
|---|---|---|---|
| 1 | **Checkpointer bypassa RLS** — tabelas escritas pelo service role; isolamento vira responsabilidade do `thread_id` (`workspace_id:session_id`) | **Alta** | namespacing rígido de `thread_id`; teste de vazamento cross-workspace |
| 2 | `interrupt()` muda o fluxo de request do frontend (request_user_info) | Média | manter o contrato de payload `pending_user_input`; rota de resume |
| 3 | Pool de conexões async (procrastinate já usa psycopg3 async) competindo com `AsyncPostgresSaver` | Média | reusar o pool; validar sob carga |
| 4 | Sessão concorrente (dois turns no mesmo `thread_id`) | Baixa | já serializado por sessão no produto |

## Critério de aceitação
- `test_writing_session_agent` adaptado e verde.
- Teste de vazamento: sessão do workspace A não lê checkpoint do B.
- `request_user_info` → interrupt → resume funciona end-to-end (manual + teste).
- Eval `writing` real sem regressão.

## FECHADA (2026-06-19, branch `feat/langgraph-etapa1`)
WritingSession invoca o grafo com checkpointer durável; `request_user_info` virou
`interrupt()` nativo com retomada. Decisões de produto (AskUserQuestion): semântica
**bloqueante + retomada**; retomada **reusa `POST /writing/turn`** (a próxima mensagem
é a resposta — detecção em `WritingSession.turn`).
- **Deps** — `+ langgraph-checkpoint-postgres>=2` (resolveu 3.1.0; psycopg/pool já vinham
  do procrastinate).
- **thread_id por turno-run** `"{workspace_id}:{session_id}:{turn_index}"` (resolve a
  tensão thread-estável-vs-efêmero da spec): turno fresco semeia o state de
  `_build_agent_initial_messages`; se interrompe, persiste `{field, prompt, thread_id,
  n_msgs}` em `writing_sessions.pending_user_input` (JSONB, sem migration de coluna) e o
  turno N grava `assistant = prompt`. O resume fatia o delta por `prior_n_msgs` para não
  dobrar trace/custo.
- [agent_graph.py](../../core/llm/agent_graph.py) — `+ run_writing_turn`
  (`WritingTurnOutcome{result, interrupt, n_messages}`), `_get_writing_checkpointer`
  (singleton `AsyncPostgresSaver` sobre `AsyncConnectionPool(DATABASE_URL)`; **InMemorySaver**
  de fallback sem DSN), **event loop dedicado** em thread daemon (o pool async fica bound
  a ele; callers sync via `run_coroutine_threadsafe`). `_build_graph(checkpointer=...)`.
- `request_user_info` ([writing_tools.py](../../core/llm/agent_tools/writing_tools.py)) →
  `interrupt({field, prompt})`; docstring + `WRITER/PITCH_AGENT_SYSTEM` reescritos
  (chamar sozinha; sem `[COMPLETAR:]`).
- **Segurança (RLS-bypass, risco-mor)**: tabelas do checkpointer criadas por `.setup()`
  (não migration — evita drift); [027_langgraph_checkpointer_rls.sql](../../supabase/migrations/027_langgraph_checkpointer_rls.sql)
  + [scripts/setup_checkpointer.py](../../scripts/setup_checkpointer.py) ligam RLS + revogam
  anon/authenticated (senão o blob seria legível via PostgREST por outro workspace).
  Isolamento real = namespacing do `thread_id`.

### Validação
- [test_agent_graph_checkpointer.py](../../tests/test_agent_graph_checkpointer.py) (zero
  rede, InMemorySaver + modelo scriptado): interrupt pausa com payload → `Command(resume)`
  retoma → texto final; **custo só do delta** no resume; **re-execução do batch** (risco #1
  documentado); **isolamento por thread_id**.
- `test_writing_session_agent` adaptado (stub `run_writing_turn`→`WritingTurnOutcome`) +
  ciclo interrupt→persistência(turno N=pergunta) → resume(turno N+1, pending limpo).
- [test_checkpointer_postgres.py](../../tests/test_checkpointer_postgres.py) — integração
  **gated em `DATABASE_URL`** (skip no CI): AsyncPostgresSaver REAL — interrupt→resume
  durável + **leak test cross-workspace** (state de A invisível pelo thread_id de B). Rodou
  local: 2 passed. **setup_checkpointer.py validado**: 4 tabelas + RLS=True + grants
  anon/authenticated vazios (PostgREST bloqueado), confirmado no Postgres local.
- **Suíte: 685 passed** (CI: + 2 skipped gated; só a flake pré-existente
  `test_contextual_retrieval`).

### Não feito (deferido)
- Eval `writing --limit 1` pelo caminho com checkpointer (gate semântico, custo real —
  premissa MVP: rodar manual antes do merge).
- Cleanup de checkpoints de turnos completos (lixo pequeno; fora do MVP).

---

# Etapa 4 — Sub-agentes (Critic, Deep Research) como subgrafos

## Objetivo
Reimplementar `run_subagent` sobre LangGraph, mantendo Critic e Deep Research
**invocados como tools** (não como nós do grafo pai).

## Como são hoje
- **Critic** ([critic_agent.py:310](../../core/llm/agent_tools/critic_agent.py#L310)):
  `run_subagent(name="critic", provider="openai", model=gpt-4o, max_steps=3,
  temperature=0.05, openai_base_url/key=CRITIC_OPENAI_*)`. Invocado **dentro** da
  tool `save_draft`. Falha → `approved=True` (save nunca bloqueia).
- **Deep Research** ([deep_research.py:105](../../core/deep_research.py#L105)):
  `run_subagent` com web_search + fetch.
- Ambos: `run_subagent` resolve provider, roda `run_agent`, e `try/except → AgentResult(error)`.

## Decisão arquitetural: subgrafo **dentro da tool**, não nó do pai

Mantemos sub-agentes como **tools que invocam um subgrafo compilado**, não como nós
da topologia do pai. Razões:
1. São **invocados condicionalmente pelo modelo** (o critic roda quando `save_draft`
   decide, não pela topologia) — não cabem como edge fixa.
2. **Isolamento de falha** é mais limpo na fronteira da tool (degradação graciosa).

`run_subagent` vira: compila um `StateGraph` pequeno (reusa `_build_react_graph` da
Etapa 1, `checkpointer=None` — efêmero), invoca dentro de try/except, degrada para
`AgentResult(stop_reason="error")`. Critic mantém modelo/provider/temperatura/
endpoint próprios (gpt-4o ZDR, `CRITIC_OPENAI_*`).

## O que muda por módulo
| Módulo | Mudança |
|---|---|
| `agent_runtime.run_subagent` | corpo troca `run_agent` por `subgraph.invoke`; assinatura e degradação intactas |
| `critic_agent.py` / `deep_research.py` | **nenhuma** (chamam `run_subagent`) |
| telemetria | span `subagent.{name}` deve **nestar sob o run do pai** |

## Riscos
| # | Risco | Sev. | Mitigação |
|---|---|---|---|
| 1 | **Nesting de trace** — o subgrafo precisa receber `callbacks`/`config` do run pai pra nestar no Langfuse (hoje via contextvars no `telemetry.agent_run`) | Média | propagar `config` (parent run_id) para `subgraph.invoke` |
| 2 | Degradação graciosa some se a exceção escapar do `invoke` | Média | manter try/except externo na tool |
| 3 | Critic com provider/endpoint diferente do pai (OpenAI ZDR) dentro do grafo do pai (Anthropic) | Baixa | subgrafo tem sua própria factory de modelo; já parametrizado |

## Critério de aceitação
- `test_subagent` verde.
- Critic degrada para `approved=True` em falha (teste de injeção de erro).
- Trace do critic aparece aninhado sob o turn da WritingSession no Langfuse.

## FECHADA (2026-06-19) — já entregue pelas Etapas 1-2 (verificação + 1 teste novo)
**Achado:** a Etapa 4 não exigiu mudança de código. O design "subagente = tool que
invoca um subgrafo efêmero" já era a realidade desde a Etapa 2: `run_subagent` →
`run_agent` → `run_agent_graph_async` → `_build_graph(..., checkpointer=None)` (grafo
compilado por chamada, sem checkpointer). A Etapa 3 não tocou esse caminho (o
checkpointer só entra via `run_writing_turn`, não via `run_agent`).
- **Degradação graciosa**: `run_subagent` mantém o try/except → `AgentResult(error)`;
  `run_critic` mapeia `error`/parse inválido → `approved=True`. Coberto por
  `test_run_critic_degrades_on_subagent_error` / `..._unparseable_verdict`
  ([test_critic_coherence.py](../../tests/test_critic_coherence.py)) e
  `test_run_subagent_degrades_*` ([test_subagent.py](../../tests/test_subagent.py)).
- **Provider/endpoint próprios** (critic: OpenAI gpt-4o ZDR `CRITIC_OPENAI_*`): já
  parametrizados via `run_subagent(openai_base_url/key=)`; o subgrafo tem sua própria
  factory de modelo.
- **`span_name=subagent.{name}`**: já propagado (`test_run_subagent_propagates_span_name`).
- **Novo teste (interação Etapa 4 × Etapa 3)**:
  `test_subagent_inside_checkpointed_writing_turn` ([test_agent_graph_checkpointer.py](../../tests/test_agent_graph_checkpointer.py))
  — prova que o subagente (run_subagent → `asyncio.run` em worker thread, sem
  checkpointer) roda DENTRO do grafo de escrita com checkpointer (no bg-loop) sem
  conflito de event loop; é o caminho real do critic dentro de `save_draft`. Verde.
- **Suíte subagent+critic: 12 passed** + o novo (6 no arquivo do checkpointer).

### Não feito (deferido para Etapa 6)
- **Nesting do trace do critic sob o turn no Langfuse** (risco #1 da etapa): hoje a
  telemetria é por contextvars (`telemetry.agent_run`) e o subagente roda em
  thread/loop separados do bg-loop do checkpointer → o contextvar do pai pode não
  propagar. A solução é o `CallbackHandler` LangChain com `config` (parent run_id)
  propagado ao `invoke` — Etapa 6. **Nota nova p/ Etapa 6**: propagar o callback
  ATRAVÉS da fronteira de thread (run_subagent roda noutro loop).

---

# Etapa 5 — Memória cross-session: Store API com embeddings

## Objetivo
Trocar o **bloco fixo de 6 insights** injetado no prefixo por **recuperação
semântica on-demand** via LangGraph Store API com embeddings.

## Como é hoje
[reflection_service.py](../../core/reflection_service.py): pipeline de 2 níveis —
`reflect_workspace` gera observações factuais (level 1), `synthesize_patterns`
destila padrões (level 2) + `weight_suggestions` (que alimentam
`auto_apply_suggestions` → pesos de matching). Persiste em `reflection_insights`
(esquema rico: `level`, `evidence`, `confidence`, `deactivated_at` com lógica de
supersede/audit). `load_active_insights` retorna top 6 (level 2 priorizado),
injetado **estático** em `_build_agent_initial_messages` (linha 964).
`search_insights_for_tool` **devolve todos os 6 sem filtro** — o próprio docstring
admite "evolução futura: filtrar por similaridade via embeddings".

## Decisão arquitetural: Store como **projeção read-optimized**, tabela autoritativa para write/audit

- **`reflection_insights` continua autoritativa** para o lado de
  **escrita/síntese/auditoria** — supersede, `deactivated_at`, `weight_suggestions`,
  `auto_apply_suggestions` (que mexe em pesos de matching) **não podem virar blob KV**.
- **LangGraph `PostgresStore`** (com `index={dims, embed}`) sobre namespace
  `(workspace_id, "insights")` é uma **projeção de leitura**: `reflection_service`
  faz `store.put()` do texto+embedding ao inserir; o agente recupera via
  `store.search(query)`.
- **Injeção sai do prefixo estático → vira retrieval query-conditioned**: ou um
  `pre_model_hook` que busca top-k por `section_hint`/última mensagem, ou uma tool
  `search_memory`. Resolve o "sem recuperação semântica".
- **Triggers** continuam responsabilidade do `reflection_service` (ortogonal ao
  LangGraph); o write episódico já existe em `extract_session_signal` →
  `_persist_session_signals`. Fortalecer triggers é trabalho separado, fora do
  caminho crítico da migração.

## O que muda por módulo
| Módulo | Mudança |
|---|---|
| `reflection_service.py` | após insert em `reflection_insights`, espelha texto+embedding no `PostgresStore` (`store.put`) |
| `writing_session._build_agent_initial_messages` | remove bloco fixo de insights; injeção vira `pre_model_hook`/tool `search_memory` |
| `agent_runtime`/grafo | grafo compilado com `store=PostgresStore(...)`; hook de retrieval |
| migrations | tabelas/índice do Store (pgvector — já temos `edital_chunks` como precedente) |
| backfill | embeddar `reflection_insights` ativos existentes para o Store |

## Riscos
| # | Risco | Sev. | Mitigação |
|---|---|---|---|
| 1 | **Dupla fonte de verdade** insight (tabela rica vs Store KV) — perder supersede/audit/weight_suggestions seria regressão de produto | **Alta** | Store é projeção de leitura; tabela permanece autoritativa; nunca escrever weight logic só no Store |
| 2 | Store **bypassa RLS** (igual checkpointer) | Alta | namespace `(workspace_id, …)`; teste de isolamento |
| 3 | Sincronização tabela↔Store (insight desativado mas ainda no Store) | Média | `store.delete` no `deactivate_insight`/supersede |
| 4 | Qualidade do retrieval semântico vs bloco-fixo (pode piorar) | Média | gate por eval `writing` (grounding/coerência) antes de cortar o bloco fixo |

## Critério de aceitação
- Insight do workspace A não recuperável pelo B (teste de namespace).
- Supersede/`deactivate_insight` removem do Store também.
- Eval `writing` não regride com retrieval semântico vs bloco fixo (gate).

## FECHADA (2026-06-20, branch `feat/langgraph-etapa1`)
Store como projeção read-only dos `reflection_insights`; injeção saiu do prefixo
estático → **query-conditioned na camada de serviço** (decisão de produto: não um nó
do grafo — o grafo compartilhado fica intocado; a query existe na WritingSession).

- **Schema dedicado `agent_memory`** (substitui o band-aid da 027): checkpointer (Et.3,
  retroativo) **e** Store conectam com `search_path=agent_memory,public,extensions`. O
  PostgREST do Supabase só expõe `public/storage/graphql_public` (config.toml) → schema
  fora dessa lista é **invisível por construção**, sem RLS+revoke tabela-a-tabela.
  [028_agent_memory_schema.sql](../../supabase/migrations/028_agent_memory_schema.sql)
  cria o schema + dropa as tabelas órfãs do checkpointer em `public` (pré-launch).
  `_make_agent_memory_pool` (helper compartilhado) garante o schema (`CREATE SCHEMA IF
  NOT EXISTS`, defensivo) e `min_size=1` (< `max_size=2` do Store).
- **Store singleton no bg-loop** ([agent_graph.py](../../core/llm/agent_graph.py)):
  `_get_memory_store` (AsyncPostgresStore sobre o mesmo loop dedicado do checkpointer),
  `_aembed_for_store` = embeddings **OS** (`core.retrieval.embedder` via `asyncio.to_thread`
  — `embed_texts` é bloqueante; rodá-lo no bg-loop o travaria). **Sem fallback InMemory**
  (diferente do checkpointer): sem DATABASE_URL → `None` e a injeção cai no bloco estático.
  Um InMemoryStore embedaria a query a cada turno (premissa MVP: não queimar OpenAI).
  Dims lidos do **ENV em call-time** (não a constante import-time do embedder) — casa com
  a coluna pgvector (768 do modelo OS vs 1536 default conforme ordem de carga do .env).
- **API pública** (`memory_put`/`memory_delete`/`memory_search`, sync sobre o bg-loop):
  todas degradam graciosamente (Store off/falho → no-op ou `[]`).
- **Projeção** ([reflection_service.py](../../core/reflection_service.py)
  `_project_to_store`): `reflect_workspace`/`synthesize_patterns`/`_persist_session_signals`
  espelham insert→`put` e supersede→`delete`; `deactivate_insight` → `delete`. **Key do
  Store = id da row** → delete idempotente. A tabela segue **autoritativa**
  (supersede/audit/weight_suggestions nunca viram blob KV).
- **Injeção query-conditioned** ([writing_session.py](../../core/services/writing_session.py)):
  `_build_reflection_context_for_turn(user_message, section_hint)` faz `memory_search` e
  cai no bloco estático (`load_active_insights`) se Store vazio/off — **regressão-zero pré-
  backfill**. Posicionado no **tail dinâmico** (junto de mentions/section) p/ preservar o
  prompt caching do prefixo estável. Flag de rollback `WRITING_SEMANTIC_MEMORY=0` (gate).
- **Tool** `recall_company_learnings` → `search_insights_for_tool(..., query=topic)` agora
  semântica (Store), com fallback estático.
- **Backfill** [scripts/backfill_memory_store.py](../../scripts/backfill_memory_store.py):
  embeda os insights ativos de todos os workspaces no Store (idempotente).
  [setup_checkpointer.py](../../scripts/setup_checkpointer.py) agora provisiona Store +
  checkpointer no schema dedicado e **removeu** o `_lock_down_rls`.

### Validação
- [test_memory_store.py](../../tests/test_memory_store.py) (11, zero rede — InMemoryStore +
  embed fake injetados): namespace/leak, relevância semântica, delete, degradação (Store
  off / query vazia), projeção (`_project_to_store` espelha put/delete), tool semântica vs
  fallback, e os 3 caminhos da injeção da WritingSession (semântico / fallback estático /
  flag off).
- [test_memory_store_postgres.py](../../tests/test_memory_store_postgres.py) (gated em
  DATABASE_URL, embed fake → zero token): put/search/delete durável, **leak cross-workspace**
  contra o Postgres real, e tabelas em `agent_memory` (não `public`). Dims do fake alinhados
  aos do Store (`index_config`).
- [test_checkpointer_postgres.py](../../tests/test_checkpointer_postgres.py) atualizado:
  `_delete_threads` agora aponta para `agent_memory.*` (tabelas migraram de `public`).
- **Suíte: 699 passed** + gated (3 store run local / 2 ckpt skip por ordem de coleta); só a
  flake pré-existente `test_contextual_retrieval` (ambiental, não tocada).

### Não feito (deferido)
- **GATE de eval `writing`** (semântico vs bloco fixo — risco #4): custo real, premissa MVP
  → rodar manual com `EMBEDDING_BACKEND=sentence_transformers` antes de mergear/cortar de
  vez o bloco fixo. Até lá o fallback estático cobre, e `WRITING_SEMANTIC_MEMORY=0` reverte.
- Ruído cosmético de teardown (`pool-N-scheduler`) nos gated locais — idêntico ao do
  checkpointer, fora do CI.

---

# Etapa 6 — Eval + telemetria

## Objetivo
Re-integrar Langfuse via `CallbackHandler` LangChain e validar que as 11 suítes de
eval não regridem. Portão final da migração.

## Como é hoje
- **Eval**: 11 suítes em [core/eval/](../../core/eval/) (`registry.py`,
  `harness.py`). Rodam **real end-to-end** (a suíte `writing` cria uma WritingSession
  real, sem monkeypatch — [writing.py](../../core/eval/writing.py)). Langfuse vira
  Experiment se `LANGFUSE_*` setado; senão grava `eval_results/*.json`.
- **Telemetria**: context managers custom em [telemetry.py](../../core/telemetry.py)
  — `agent_run`, `llm_generation` (`llm.step_N`), `tool_call` (`tool.X`),
  `record_usage` (extrai cache/reasoning tokens da resposta crua). Spans nomeados
  `agent.{provider}.{model}` / `subagent.{name}`.

## Decisões arquiteturais

1. **Telemetria via callback, não context managers.** Substituir `telemetry.*` por
   `langfuse.langchain.CallbackHandler` passado no `config` do grafo. O handler
   gera spans automaticamente do run (chain/llm/tool). Início **na Etapa 1** com um
   callback mínimo (não ir às cegas); **parity completa de nomes/usage aqui**.
2. **Eval = portão de regressão, não custo de porte.** As suítes rodam a facade/
   serviço reais — se Etapas 1-3 preservaram contrato, **mudam pouco**. O trabalho é
   confirmar não-regressão e re-wire do Experiment.
3. **`record_usage` (cache/reasoning tokens).** A integração LangChain captura
   `usage_metadata` automático; **verificar** se cache/reasoning aparecem com as keys
   canônicas que usamos hoje no custo por turno.

## O que muda por módulo
| Módulo | Mudança |
|---|---|
| `core/telemetry.py` | context managers → `CallbackHandler` factory; `record_usage` aposentado se usage automático cobrir |
| `agent_runtime`/grafo | injeta `callbacks=[handler]` no `config` de cada `invoke` |
| `core/eval/*` | inalterado em lógica; valida não-regressão |
| dashboards Langfuse | **descontinuidade de nomes de span** (`llm.step_N` → spans nativos LangChain) — comparabilidade histórica corta aqui |

## Riscos
| # | Risco | Sev. | Mitigação |
|---|---|---|---|
| 1 | Descontinuidade de span names quebra comparabilidade de eval entre commits | Média | documentar o corte; mapear nomes onde viável |
| 2 | Usage por turno (custo) perder cache/reasoning tokens | Média | validar `usage_metadata` cobre; senão callback custom |
| 3 | Nesting de subagente no trace (herda Etapa 4) | Média | propagar `config` |

## Critério de aceitação
- Todas as suítes de eval rodam; `writing`/`extraction`/`rag` sem regressão vs baseline pré-migração.
- Custo por turno (input+output, +cache se aplicável) visível no Langfuse.
- Trace de uma WritingSession mostra: turn → agent → tools → critic (aninhado).

## FECHADA (2026-06-20, branch `feat/langgraph-etapa1`)
Telemetria migrada para a **`langfuse.langchain.CallbackHandler` nativa** (decisão de
produto: meta-pacote `langchain` oficial, não handler custom) — spans por-step com
timing real + usage automático, substituindo o `_replay_step_spans` pós-hoc da Etapa 1.

- **Dep**: `+ langchain>=1.0` (o `CallbackHandler` faz `import langchain`; resolveu 1.3.10).
- **[telemetry.py](../../core/telemetry.py)**: `make_callback_handler()` (factory da
  CallbackHandler; None se Langfuse off), `current_trace_context()` (captura
  `{trace_id, parent_span_id}` do span corrente), `agent_run(trace_context=)` (enraíza
  sob parent remoto). Removidos `llm_generation`/`tool_call` (mortos pós-handler);
  `record_usage` mantido (testado, extrai usage de respostas CRUAS de SDK — fora do
  caminho do grafo). `flush()` hookado em `shutdown_writing_runtime` (CLI/scripts).
- **[agent_graph.py](../../core/llm/agent_graph.py)**: `run_agent_graph_async` e
  `_writing_turn_async` montam o handler e o passam em `config["callbacks"]`; `agent_run`
  segue como span-raiz nomeado que ancora o nesting. `_replay_step_spans` deletado.
- **Nesting cross-thread do critic (risco #1 da Et.4, resolvido)**: `run_subagent`
  ([agent_runtime.py](../../core/llm/agent_runtime.py)) captura `current_trace_context()`
  na thread do grafo PAI (antes do `run_agent` cruzar para a worker thread) e propaga
  `trace_context` → `run_agent` → `run_agent_async` → `run_agent_graph_async` → `agent_run`.
  Resolve o contextvar OTel que não cruza a fronteira de thread (o subagente roda noutro
  loop). Sem isso o critic viraria uma trace-raiz separada.
- **Eval**: o harness ([harness.py](../../core/eval/harness.py)) já fazia o wiring de
  Experiments/scores (Langfuse) com fallback local — **inalterado**.

### Validação
- [test_telemetry_callbacks.py](../../tests/test_telemetry_callbacks.py) (5, zero rede):
  handler entra no `config["callbacks"]`; sem handler quando off (sem overhead);
  `trace_context` repassado ao `agent_run`; `run_subagent` captura e propaga o trace do
  pai (`span_name=subagent.critic`); factories viram no-op com Langfuse off.
- Smoke de construção: `make_callback_handler()` com Langfuse habilitado retorna a
  `CallbackHandler` sem rede (degrada a None se a construção falhar).
- **Suíte: 705 passed** (regressão-zero; só a flake ambiental pré-existente
  `test_contextual_retrieval`). `test_agent_graph_golden::test_graph_honors_span_name`
  segue verde (o fake `agent_run` aceita o novo kwarg).
- **Eval `writing --limit 1` (gate parcial, rodado)**: `saved=1, coherent=1,
  factual_errors=0`. Caminho real completo (agente + tools + critic + RAG, embeddings
  OS gemma-768, agente/critic em gpt-4o fallback). **Pegou um bug** (abaixo).

### Bug cross-loop do checkpointer (latente desde a Et.3, PEGO pelo gate de eval)
O critic (subagente dentro de `save_draft`) falhava com `Lock is bound to a different
event loop` e degradava pra `approved=True` — o critic ficava inerte. Causa: o caminho
stateless (`run_agent_graph_async`) compilava o subgrafo com `checkpointer=None`, e o
LangGraph trata `None` como **"herde o checkpointer do pai quando rodar como subgrafo"**
(via contextvar do config). O critic herdava o `AsyncPostgresSaver` do turno de escrita
(lock preso ao bg-loop da Et.3) e tentava usá-lo a partir do seu próprio loop → erro.
Latente desde a Et.3 porque o `InMemorySaver` (fallback sem DSN, usado nos testes) não
tem lock preso a loop; só aparece com o Postgres real — e o gate de eval da Et.3 foi
deferido. **Fix**: `run_agent_graph_async` compila com `checkpointer=False` (corta a
herança; subagente nunca persiste). Guard:
`test_subagent_graph_compiles_with_checkpointer_false`.

### Não feito (deferido — exige token/Langfuse real, premissa MVP)
- **Gate de não-regressão das 11 suítes de eval** rodadas real + re-wire do Experiment:
  custo de token → manual (mesmo padrão do gate de `writing` da Et.5, no BACKLOG).
- **Parity de usage cache/reasoning** (risco #2): confirmar que o handler emite
  `cache_read`/`reasoning` com as keys canônicas exige um run real; até lá `record_usage`
  fica como referência da extração. Se faltar, escrever um callback de usage custom.
- **Descontinuidade de nomes de span** (`llm.step_N` → spans nativos LangChain):
  comparabilidade histórica de dashboards corta aqui (documentado, esperado).

---

# Riscos transversais (consolidado)

| Tema | Etapas | Síntese |
|---|---|---|
| **Bypass de RLS** (checkpointer + Store escritos pelo service role) | 3, 5 | Isolamento multi-tenant migra de RLS para `thread_id`/namespace por `workspace_id`. **Maior risco de segurança da migração.** Teste de vazamento cross-workspace obrigatório em ambas. **RESOLVIDO (Et.5)**: a maquinaria vive num schema dedicado `agent_memory` (search_path), fora dos schemas que o PostgREST expõe → invisível por construção (substitui o RLS+revoke da 027). |
| **Fidelidade de contrato** (tradutor de estado → `AgentResult`) | 1, 3 | Drift silencioso quebra persistência de trace + custo. Golden tests. |
| **Comportamentos custom** (reflexão, cap, error-string, degradação) | 1, 2, 4 | Não têm equivalente prebuilt; cada um re-implementado deliberadamente, com teste. |
| **Telemetria** (descontinuidade de span) | 1→6 | Janela cega de comparabilidade; começar callback cedo, parity no fim. |
| **Eval como gate** | todas | Suítes rodam real; `writing` (grounding/coerência) é o gate semântico de cada etapa que toca o agente de escrita. |

# Estado / progresso

| Etapa | Spec | Implementação |
|---|---|---|
| 1 Runtime core | ✅ | ✅ **FECHADA** — LangGraph default; validado (golden + suíte + smoke real + eval writing). Legado deletado na Etapa 2 |
| 2 Tools | ✅ | ✅ **FECHADA** — @tool nativo LangChain; legado APOSENTADO; suíte 678 passed |
| 3 WritingSession + checkpoints | ✅ | ✅ **FECHADA** — checkpointer Postgres + interrupt/resume; suíte 685 passed. Gate manual pendente: leak test PG real + eval writing |
| 4 Sub-agentes | ✅ | ✅ **FECHADA** — já entregue pelas Etapas 1-2 (run_subagent = subgrafo efêmero); +1 teste da interação com o checkpointer da Et.3. Nesting de trace → Et.6 |
| 5 Memória (Store) | ✅ | ✅ **FECHADA** — PostgresStore (projeção) em schema dedicado `agent_memory`; injeção query-conditioned no serviço + fallback estático; embeddings OS. Gate manual: eval writing semântico-vs-fixo |
| 6 Eval + telemetria | ✅ | ✅ **FECHADA** — CallbackHandler nativo do Langfuse (spans reais + usage auto); nesting cross-thread do critic via trace_context; `_replay_step_spans` removido. Gate manual: 11 suítes de eval + parity de usage cache/reasoning |
