# Mapa de alavancas — enriquecimento da camada de inteligência (LangGraph)

**Status:** plano de trilha (vivo) · **Data:** 2026-07-18 · **Autoria da decisão:** Lucas + Claude.
**Objetivo:** aproveitar ao máximo o que o LangGraph (e o ecossistema já instalado) acrescenta ao runtime agêntico, sem construir infraestrutura de harness. Antecede a trilha de extração/KG (parte 2).

## Enquadramento e calibração

Decisão de contexto (2026-07-18): **não** migrar para o Claude Agent SDK agora. O spike (`spikes/agent_sdk_explore/results/report.md`, memória `project_agent_sdk_spike`) confirmou que o SDK é uma **rede de segurança de performance** viável para quando o sistema virar produto; por ora, o sistema existe para auto-capacitação, então o LangGraph vira terreno de aprofundamento.

Calibração escolhida (perguntas de 2026-07-18):

| Eixo | Escolha | Consequência para este mapa |
|---|---|---|
| Superfície | **Runtime compartilhado** | Foco no `_build_graph` de `src/radar/core/llm/agent_graph.py`, que serve explore e writing. Melhorias que servem os dois de uma vez são priorizadas. |
| Foco | **Equilíbrio** (valor × aprendizado) | Cada alavanca é pontuada por valor no sistema **e** por ensinar uma feature nova do LangGraph. |
| Régua | **Spike-first calibrado** (revista 2026-07-18) | Spike throwaway onde há incógnita comportamental ou feature nova a aprender; direto a plan→impl onde a mecânica é conhecida. O gate formal entra na promoção. Detalhes na spec de execução. |

Premissas do Lucas que regem tudo: **simplicidade, praticidade, eficácia**. Restrição dura: **não construir um "Claude Code" próprio** — cada alavanca usa uma feature que o LangGraph já entrega pronta; se algo começar a virar infraestrutura de harness, para-se e reavalia.

Escopo do "ecossistema": apenas o que **já está instalado** — LangGraph core + checkpointer/store Postgres + Langfuse. Sem LangSmith (trocaria observabilidade) e sem LangGraph Platform (superfície de deploy nova).

**Recorte importante:** "camada de inteligência via LangGraph" = os dois grafos ReAct (explore + writing). O **matching é deliberadamente sem-LLM** (KG + RRF, ver memória `project_match_rag_boundary`) — não é um grafo LangGraph, então melhorá-lo é outra trilha, e boa parte dela é a parte 2 (extração → KG → match). As duas frentes do Lucas se encontram no KG, não aqui.

Base de diagnóstico do estado atual: leitura de `src/radar/core/llm/agent_graph.py` (runtime único de produção) — ver também `docs/reference/grantable-benchmark.md` §3, que ranqueia as mesmas debilidades.

---

## Sumário do ranking

Ordenado por valor × aprendizado × durabilidade (sobrevive a uma futura migração pro SDK?), dentro do runtime compartilhado.

| # | Alavanca | Valor | Aprendizado | Sobrevive à migração SDK? | Risco |
|---|---|---|---|---|---|
| 1 | Streaming (`astream_events`) | Alto (UX percebida) | Novo | **Sim** — vira contrato do frontend | Baixo |
| 2 | Gestão de contexto (`trim_messages` + nó de resumo) | Alto (teto do raciocínio) | Novo | **Sim** — é conhecimento/disciplina sua | Médio |
| 3 | Thread por sessão (checkpointer como memória) | Alto (remove context-eng. duplicado) | Novo | Parcial — o SDK resolve, mas o modelo de dados fica | Médio-alto |
| 4 | `Send` API (fan-out nativo) | Médio (o `gather` atual funciona) | Novo | Não — o SDK gerencia paralelismo | Médio |
| 5 | Mais `interrupt()` (human-in-the-loop) | Médio (fit filosófico) | Baixo (já usa 1×) | Não — o SDK tem permissões | Médio |
| 6 | Guarda por estado (budget consciente) | Médio (anti-truncamento) | Baixo (estado já existe; `RemainingSteps` como estudo) | **Sim** — é disciplina de design de estado | Baixo |

**Sequência recomendada:** #1 → #2 → #3, com #4 e #5 como "spikes de sábado" (aprendizado rico, valor moderado; #5 é semi-dependente — exige checkpointer no caminho-alvo) e #6 barata a qualquer momento. Os dois primeiros são os que mais rendem e os únicos que sobrevivem intactos a uma migração futura — por isso abrem a fila.

**Execução:** o contrato por item (spike, sinal de sucesso, gate de promoção, pegadinhas, fluxo SDD) vive em `docs/specs/langgraph-levers-spec.md` — este mapa guarda o racional; a spec de execução é a fonte para planejamento/implementação. A #6 nasceu na revisão de 2026-07-18 e está detalhada só na spec.

---

## Alavanca 1 — Streaming de eventos (`astream_events`)

**Estado atual.** Todos os entry points (`run_agent_graph_async`, `run_writing_turn`, `run_generation_turn`) usam `graph.ainvoke(...)` e só traduzem para `AgentResult` **no fim**; o callback `on_step` dispara depois do turno inteiro. Resultado: o usuário espera o turno completo sem nenhum feedback — o maior gap de *sensação* hoje.

**O que muda.** O grafo passa a emitir eventos ao vivo (tokens do LLM, entrada/saída de nós, início/fim de tool) enquanto processa. O frontend do chat renderiza incremental.

**Feature que ensina.** `graph.astream_events(input, version="v3")` e suas projeções tipadas: `messages` (saída do modelo em content blocks), `values` (updates de estado), `subgraphs` (execução aninhada — relevante porque o critic é subagente), `lifecycle`. É API nova para você.

**Spike (validação barata).** Num script throwaway, rodar um turno de explore com `astream_events` e imprimir os eventos no terminal, lado a lado com o `ainvoke` atual — sentir a diferença de latência-até-primeiro-token. Não tocar `src/radar/core/` de produção; só provar o mecanismo. Sucesso = ver tokens saindo antes do turno terminar.

**Risco.** Baixo. É aditivo; o contrato `AgentResult` pode ser reconstruído do stream no fim (`values`/`messages` finais).

**Durabilidade.** Alta — se um dia migrar pro SDK, o frontend já consome stream; o investimento não se perde.

**Leitura:**
- [Streaming (visão geral)](https://docs.langchain.com/oss/python/langgraph/streaming)
- [Event streaming — `astream_events` v3 e projeções](https://docs.langchain.com/oss/python/langgraph/event-streaming)

---

## Alavanca 2 — Gestão de contexto (`trim_messages` + nó de resumo)

**Estado atual.** O contexto por turno é limitado por dois cortes brutos: `TOOL_RESULT_CHAR_CAP` (trunca cada resultado de tool acima do orçamento, `agent_graph.py` no nó `tools`) e `max_steps=8` (encerra o turno à força via `after_tools`/`finalize`). Foi decisão explícita ("cabe sem poda"), válida para turnos curtos — mas é literalmente **o teto de profundidade do raciocínio**: qualquer tarefa que precise de 15+ passos ou resultados longos morre truncada.

**O que muda.** Trocar o corte cego por poda seletiva (`trim_messages`) e/ou um nó de sumarização que condensa resultados antigos em vez de descartá-los. Destrava turnos mais longos sem estourar contexto — é a alavanca mais "inteligência de verdade" do mapa.

**Feature que ensina.** A util `trim_messages` (estratégia `last`, `token_counter`, `start_on`/`end_on`) e o padrão de **nó de resumo** dentro do `StateGraph`.

**⚠️ Pegadinha (já sinalizada).** Os docs empurram para `SummarizationMiddleware`/middleware — **isso pressupõe o agente prebuilt (`create_agent`)**, e seu runtime é um `StateGraph` montado à mão. Para o seu caso, o caminho é a util `trim_messages` (funciona sobre qualquer lista de mensagens) e/ou um nó de resumo próprio — **não** o middleware. Se a leitura assumir `create_agent`, você saiu do seu terreno.

**Spike.** Pegar um histórico real que hoje é truncado, aplicar `trim_messages` com um orçamento de tokens e comparar o que o modelo "vê" antes/depois; opcionalmente, um nó de resumo que dispara acima de um limiar. Sucesso = turno mais longo cabendo sem perda das evidências que importam.

**Risco.** Médio — muda o que o modelo enxerga; precisa de olho para não resumir fora evidências load-bearing (citações de edital, constraints).

**Durabilidade.** Alta — a disciplina de context engineering é conhecimento seu; sobrevive a qualquer harness.

**Leitura:**
- [Short-term memory — trimming e summarization](https://docs.langchain.com/oss/python/langchain/short-term-memory)
- [Messages — a util `trim_messages`](https://docs.langchain.com/oss/python/langchain/messages)
- [Context engineering in agents (o "porquê")](https://docs.langchain.com/oss/python/langchain/context-engineering)

---

## Alavanca 3 — Thread por sessão (checkpointer como memória conversacional)

**Estado atual.** `thread_id = "{workspace_id}:{session_id}:{turn_index}"` — a durabilidade é de **escopo de turno-run**. A cada turno os produtores (`writing_session`, explore) **reconstroem o histórico** e re-semeiam o grafo. O checkpointer `AsyncPostgresSaver` (schema dedicado `agent_memory`, fora do RLS) já existe e é robusto, mas serve como memória de interrupt/resume, não como memória de conversa.

**O que muda.** Mover a thread para o escopo de sessão elimina a reconstrução de histórico por turno (context engineering duplicado) e abre time-travel/forking — ex.: comparar duas direções de um draft a partir do mesmo checkpoint.

**Feature que ensina.** Persistence/threads e time-travel: `update_state` cria um checkpoint que **ramifica** (não faz rollback destrutivo); checkpoints por super-step.

**Spike.** Rodar uma conversa de explore de 3 turnos com `thread_id` por sessão e um único checkpointer durável; inspecionar o histórico acumulado sem re-semear; testar um fork via `update_state` a partir de um checkpoint intermediário. Sucesso = 2º/3º turnos sem o produtor remontar o histórico.

**Risco.** Médio-alto — toca os produtores (`writing_session`/explore), não só o grafo. Fazer **depois** de #1 e #2 estarem firmes.

**Durabilidade.** Parcial — o SDK resolveria isso sozinho, mas o modelo de dados "thread por sessão" e o aprendizado de persistence ficam.

**Leitura:**
- [Persistence — thread e checkpoint](https://docs.langchain.com/oss/python/langgraph/persistence)
- [Checkpointers — o `AsyncPostgresSaver` que você já usa](https://docs.langchain.com/oss/python/langgraph/checkpointers)
- [Time travel](https://docs.langchain.com/oss/python/langgraph/use-time-travel)

---

## Alavanca 4 — `Send` API (fan-out nativo / map-reduce)

**Estado atual.** A geração de proposta em lote (`run_generation_turn`) faz o paralelismo **fora do grafo**: `asyncio.gather` + `Semaphore` (`GENERATION_CONCURRENCY`), com o grafo interno stateless por seção. Funciona, mas evidencia que o grafo é um while-loop — não há estado/checkpoint/trace por ramo.

**O que muda.** Trazer o fan-out para dentro do grafo com `Send`: o mesmo nó é invocado N vezes em paralelo com estados diferentes, e os resultados agregam de volta no estado principal — com checkpoint e span por ramo. Habilita expansão paralela em multi-hop no explore e, principalmente, **fan-out de subagentes** — como `run_subagent` já existe (`src/radar/core/llm/agent_runtime.py:327`; usado por critic e deep_research), `Send` paralelizaria vários `deep_research` ou uma comparação multi-edital de uma vez, hoje impossível no loop sequencial.

**Feature que ensina.** A classe `Send` em conditional edges, o padrão map-reduce nativo (mapa gera lista → nó aplicado a cada item em paralelo → redução).

**Spike.** Reescrever a geração de 3 seções com `Send` em vez de `gather`; conferir que o trace do Langfuse mostra um ramo por seção (vs a orquestração opaca atual). Sucesso = paralelismo com observabilidade por ramo.

**Risco.** Médio — a reescrita da geração é cirúrgica, mas mexe num caminho que já funciona; só vale se o ganho de observabilidade/estado por ramo importar.

**Durabilidade.** Baixa — o SDK gerencia paralelismo de subagentes; este é aprendizado-primeiro.

**Leitura:**
- [Graph API overview — a classe `Send`](https://docs.langchain.com/oss/python/langgraph/graph-api)
- [Use the graph API — how-to map-reduce](https://docs.langchain.com/oss/python/langgraph/use-graph-api)

---

## Alavanca 5 — Mais `interrupt()` (human-in-the-loop)

**Estado atual.** Existe **um** ponto de interrupt: `request_user_info` na escrita (`run_writing_turn`, via `Command(resume=...)`). É a materialização parcial da filosofia "AI age, humano corrige" (memória `project_grantable_philosophy`, `project_knowledge_evolution_spec`).

**O que muda.** Adicionar gates de aprovação em pontos de maior consequência — ex.: antes de gravar/alterar perfil ou constraints derivadas, ou uma clarificação estruturada no explore quando a pergunta é ambígua.

**Feature que ensina.** Os 4 padrões de interrupt (aprovar/rejeitar, editar estado, revisar tool call, revisar entrada) — você já usa 1, então o aprendizado marginal é menor. **Nota de implementação:** ao resumir, o LangGraph **reexecuta o nó desde o início** (não retoma na linha do `interrupt()`), então o nó tem que ser idempotente até o ponto de pausa.

**Spike.** Adicionar um gate de aprovação antes de uma escrita de perfil num fluxo de explore autenticado; resumir com allow/deny e verificar o efeito. Sucesso = a gravação só acontece após o allow.

**Risco.** Médio — a idempotência do nó reexecutado é a pegadinha; testar o replay.

**Durabilidade.** Baixa — o SDK tem sistema de permissões próprio.

**Leitura:**
- [Interrupts — os 4 padrões e a regra de replay do nó](https://docs.langchain.com/oss/python/langgraph/interrupts)

---

## Capacidades já construídas (recorte 2026-07-18)

Ao comparar com o harness do Claude, ficou claro que boa parte do que o Claude "tem a mais" o Radar **já construiu**, em versão domínio-específica — não são gaps nem justificam SDK:

- **Sistema de playbook/skills** (`src/radar/core/skills.py`): markdown por mecanismo + overlay de agência, merge por seção com roteamento por consumidor (Redator/ComplianceMonitor/Critic). Já é um "interpretador de skills" domínio-específico. **Diferença vs SKILL.md do Claude:** edição hoje é git (dev) ou overlay no banco (dormante), não o usuário final no produto; escopo é craft de escrita, não slash-commands genéricos.
- **Subagentes** (`run_subagent`, `src/radar/core/llm/agent_runtime.py:327`): subagente-como-tool com provider/modelo/`max_steps` próprios, span aninhado, isolamento (`checkpointer=False`) e degradação graciosa. Critic e deep_research já são subagentes. O que o Claude adiciona é compaction *dentro* do subagente (= alavanca #2) e orquestração paralela nativa (= alavanca #4) — não a primitiva.
- **Pesquisa web** (`src/radar/core/deep_research.py`): subagente com tools reais `web_search` + `fetch_url`, findings persistidos em `research_findings`. Diferença vs Claude é só hospedagem (client-side vs server-side Anthropic), não capacidade.

**Alavanca adjacente (fora do LangGraph, mas de inteligência):** os `playbook_overlays` no banco são uma **camada dormente** de skills aprendidas/editáveis em runtime, já lida por `src/radar/core/skills.py::_load_overlays` mas nunca escrita por um produtor. Ativá-la (um produtor que grava overlays a partir de correção humana ou de padrões aprendidos) daria editabilidade de skills em runtime **sem** LangGraph nem SDK — reusa infra que já existe. Não entra na fila das 5 alavancas (não é feature LangGraph), mas fica registrada aqui como enriquecimento de baixo custo quando fizer sentido.

## Débitos e observações de código (achados ao diagnosticar)

Não são alavancas, mas apareceram na leitura de `agent_graph.py` e valem registro:

- **Campo `documents` no state carregado sem uso aparente** neste arquivo (todo nó o repassa, ninguém lê/escreve). Confirmar se é usado noutro módulo antes de remover.
- **`temperature` segue plumbada** por toda a assinatura; é rejeitada pelos modelos Anthropic atuais (inócua enquanto o provider default for OpenAI-compat, mas é ruído).
- **`AsyncPostgresStore` da memória semântica já está de pé** (`memory_search`, embeddings OS). Se a biblioteca de boilerplate (gap de produto do benchmark §2.2-d) entrar um dia, o storage já existe — namespace `(workspace, "boilerplate")`.

## Orientação de leitura

Para alinhar o modelo mental do `StateGraph` antes de mergulhar nas alavancas: [Thinking in LangGraph](https://docs.langchain.com/oss/python/langgraph/thinking-in-langgraph).

Nota sobre os docs: a fonte canônica atual é `docs.langchain.com/oss/python` (docs consolidados). O antigo `langchain-ai.github.io/langgraph` ainda tem how-tos mas está sendo aposentado — se um link de lá aparecer, prefira o equivalente em `docs.langchain.com`.

## Próximo passo

Abrir o **spike da Alavanca 1 (streaming)**: `astream_events` num entry point, eventos no terminal, throwaway, sem tocar `src/radar/core/` de produção. É o de menor risco, ensina uma feature nova e sobrevive a qualquer decisão futura.
