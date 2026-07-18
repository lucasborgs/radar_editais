# Spec de execução — alavancas de inteligência (LangGraph)

**Status:** proposta · **Data:** 2026-07-18 · **Fonte de avaliação:** `docs/specs/langgraph-intelligence-levers.md` (o mapa; não repetir o racional aqui).
**Escopo:** contrato de execução das 6 alavancas do runtime compartilhado + 1 adjacente, já com as correções de 2026-07-18 (subagentes/web/playbook **já existem**; `Send` serve fan-out de subagentes; `playbook_overlays` como alavanca adjacente; #6 guarda por estado adicionada na revisão de 2026-07-18).

## Princípios de execução (regem toda a spec)

- **Spike-first calibrado (régua revista 2×, 2026-07-18).** Três formas, por tipo de incógnita: (a) **spike separado** onde a incógnita é comportamental e a política é a própria entrega (#2); (b) **spike dobrado como task 1 do plano** com checkpoint go/no-go explícito — as tasks seguintes só executam se a task 1 confirmar (#1, #3; o freio de mão contra sunk cost do plano é o checkpoint); (c) **sem spike** onde a mecânica é conhecida (adjacente; #6 mínimo). #4/#5 seguem spikes de sábado (aprendizado é o objetivo). O gate formal só entra na **promoção**.
- **Fluxo SDD.** Spec: Fable 5 (este documento). Planejamento de tasks: Opus 4.8. Implementação: Sonnet 5; itens críticos com Opus — hoje só o **#3** qualifica (toca produtores + RLS do checkpointer). Spikes são interativos (Lucas + modelo), fora da esteira SDD; a esteira entra na promoção.
- **Simplicidade / praticidade / eficácia.** Menor mudança que prova o valor. Nada de configurável/genérico onde um caso concreto resolve.
- **Não construir harness.** Só usar features prontas do LangGraph. Se um item começar a virar subsistema genérico (motor de compaction, sistema de skills, framework de permissões), **para e reavalia** — é sinal de gatilho de SDK, não de construir à mão.
- **Runtime compartilhado.** Toda mudança mira `_build_graph`/entry points de `core/llm/agent_graph.py`, servindo explore e writing. Mudanças que tocam só um produtor são sinalizadas.
- **Não re-especificar o que já existe.** Subagentes (`run_subagent`), pesquisa web (`deep_research`) e playbook/skills (`core/skills.py`) estão construídos — a spec os *usa*, não os reconstrói.

## Régua: "spike pronto" vs "promoção pronta"

| Estágio | Critério de pronto |
|---|---|
| **Spike pronto** | Sinal de sucesso do item observado num script/variação throwaway; anotação curta do achado (custo/latência/comportamento); decisão go/no-go de promoção. Sem tocar `core/` de produção. |
| **Promoção pronta** | Mudança no `core/` de produção + o **gate do eixo afetado** rodado (coluna "Gate de promoção" por item). Comportamento-preservador (streaming) dispensa gate de eval, mas pede smoke via skill `verify`. |

## Sequência e dependências

**Sequência REVISADA pós-spike do #2 (2026-07-18):** `#1 (✅ prod) → #6 guarda por estado → #3 threads`. O **#2 está ARQUIVADO com gatilhos** (ver item — o spike refutou trim puro e mostrou que o gargalo real de hoje é passos, não contexto; o #6 herdou a frente). `#4 Send` é independente ("spike de sábado"). `#5 interrupt` é **semi-dependente**: exige checkpointer no caminho-alvo, que hoje só a escrita tem (ver pegadinha do item). A adjacente (`playbook_overlays`) é ortogonal ao LangGraph e pode entrar a qualquer momento.

---

## Item 1 — Streaming (`astream_events`) — ✅ PROMOVIDO (fatia explore) 2026-07-18

**Status:** mergeado na main (merge `897ca9631`) e deployado em prod. Gate: smoke SSE Docker-staging (TTFT 2.63s vs done 3.96s) + smoke via Cloudflare Tunnel em prod (535 tokens, span 5.83s, sem buffering). Smoke Anthropic **diferido** (sem key em nenhum ambiente — ver adendo do plano). Pendência viva: streaming da escrita (Task 6), pareada ao Item 3. Achado operacional do gate: a WAF da Cloudflare barrou cliente não-navegador no endpoint novo (403 sem User-Agent de browser) — irrelevante para o frontend real, relevante para smokes futuros.

- **Régua (revista):** sem spike separado — **a task 1 do plano É o demo** (script em `spikes/`, throwaway), com **checkpoint go/no-go** antes das demais tasks.
- **Objetivo da task 1.** Provar que dá para emitir tokens/passos ao vivo do grafo existente e que o `AgentResult` pode ser reconstruído do stream no fim (sem quebrar o contrato dos call sites).
- **Abordagem.** Script throwaway em `spikes/`: compila o grafo de explore como hoje, roda `graph.astream_events(input, version="v3")`, consome as projeções `messages`/`values` e imprime no terminal. Comparar latência-até-primeiro-token vs `ainvoke`. Atenção: streaming de tokens depende do suporte do chat model — validar com os providers OpenAI-compat reais do sistema, não só Anthropic.
- **Sinal de sucesso.** Tokens saindo antes do turno terminar; `values`/`messages` finais equivalentes ao `AgentResult` atual.
- **Keep vs throwaway.** *Keep:* o padrão de consumo do stream (vira base do entry point de produção e do contrato SSE do frontend). *Throwaway:* o script de comparação.
- **Promoção.** Novo entry point streaming em `agent_graph.py` (aditivo, ao lado do `ainvoke`) + consumo SSE no frontend. **Gate:** nenhum de eval (comportamento-preservador) — smoke via `verify` dirigindo o chat real, **incluindo o caminho de produção completo (Docker + Cloudflare Tunnel)**: proxies podem bufferizar SSE e matar o ganho de TTFT silenciosamente; localhost não prova.
- **Pegadinha.** O contrato `AgentResult` (trace/usage) tem que ser montado a partir do stream final; garantir paridade de `usage` com o caminho `ainvoke`.
- **Decisão de fechamento (2026-07-18, planejamento).** O item **promove pela fatia explore** (entry point compartilhado + SSE + frontend do explore). O streaming da **escrita** fica como pendência nomeada — pareada ao **Item 3** (que já toca `writing_session`) ou antes se a UX gritar; o contrato dela exige um frame de `interrupt` que o explore não tem. Plano de tasks: `docs/specs/langgraph-lever1-streaming-plan.md`.

## Item 2 — Gestão de contexto (`trim_messages` + nó de resumo) — ⏸️ ARQUIVADO COM GATILHOS (governança, 2026-07-18)

**Decisão pós-spike** (`spikes/lever2_context/FINDINGS.md`): **NO-GO para promoção agora.**
- `trim_messages` puro **REFUTADO**: corta por idade, não importância — derruba citação real (`get_edital`) em orçamento apertado e mantém navegação redundante; perda silenciosa. Falha o sinal de sucesso do próprio item. Achado adicional: `start_on="human"` colapsa no shape intra-turno (transferido ao Item 3, onde é a ferramenta certa).
- Resumo seletivo por densidade: mecânica provada (~380 tok/chamada), mas detector confiável é **o cerne do trabalho** — construí-lo = "motor de compaction" = o harness-smell que esta spec manda parar e reavaliar (candidato a gatilho de SDK, não a construção à mão).
- Premissa empírica caiu: o único truncamento real observado foi por **contagem de passos** (`max_steps`, 7.520 tokens — longe de janela), não contexto. O teto de hoje é o do Item 6.

**Gatilhos de reabertura:** (i) telemetria do #6 mostrar truncamento real por overflow de tokens; (ii) subida de `max_steps` (pós-#6) criar pressão de contexto real. Na reabertura, caminho = density-aware, começando pela pergunta "é o momento-SDK?".

**Legado do spike (não perder):** writing golden **RODA** (`--limit 1` produz métricas — corrige a memória "nunca rodou"); suíte N=12 ~25-35min por ser sequencial (`core/eval/harness.py`, loop `for` confirmado no código) — paralelizar é chore pequeno/isolado, carona no próximo plano. Comportamentos de desperdício observados (`read_exact_chunk`×5 por busca; `list_icts`/`list_investidores`×6 sem dedup) anotados como motivação do #6.

*(Contrato original abaixo, mantido para a eventual reabertura.)*

- **Objetivo do spike.** Provar que poda seletiva/resumo destrava turnos mais longos sem perder evidência load-bearing (citações de edital, constraints), substituindo o corte cego (`TOOL_RESULT_CHAR_CAP` + os tetos de `max_steps` por modo: 10 chat / 12 profile / 15 explore / 20 refine / 2 batch).
- **Abordagem.** Pegar um histórico real hoje truncado; aplicar `trim_messages` (estratégia `last`, `token_counter`, `start_on`/`end_on`) num script throwaway e comparar o que o modelo "vê" antes/depois. Opcional: um nó de resumo simples que dispara acima de um limiar de tokens.
- **Contrato.** Na promoção, o nó `tools` de `_build_graph` troca o `_cap` cego por poda/resumo; `max_steps` pode subir. **Usar a util `trim_messages` e/ou um nó próprio — NÃO `SummarizationMiddleware`** (pressupõe `create_agent`; o runtime é `StateGraph` à mão).
- **Sinal de sucesso.** Turno mais longo cabendo em contexto, com as evidências que importam preservadas (checar num caso de writing com citações).
- **Keep vs throwaway.** *Keep:* a política de trim/resumo (é disciplina sua, sobrevive a migração). *Throwaway:* o comparador.
- **Promoção.** Mudança no nó `tools` + eventual nó de resumo. **Gate:** eval de **grounding + writing golden** (muda o que o modelo vê — risco de resumir fora citação; ver memória `project_writing_agent_evolution`). **Pré-requisito do gate:** tornar o writing golden **rodável** e a métrica de grounding confiável — dívida conhecida (o golden N=3 nunca rodou; a métrica já deu falso alarme uma vez). Sem isso o gate não informa; pagar essa dívida é parte do item, antes da promoção.
- **Pegadinha.** Resumo destrutivo não pode comer constraint/citação; preferir preservar tool-results de fonte normativa e resumir só os de baixa densidade.

## Item 3 — Thread por sessão (checkpointer como memória)

- **Régua (revista):** spike dobrado como **task 1 exploratória do plano** com checkpoint go/no-go — os guardrails de risco já existem como testes (leak-test RLS + interrupt/resume da escrita), então o vehicle certo é um plano que os roda cedo, não um spike solto.
- **Herança do spike do #2 (2026-07-18):** `trim_messages(start_on="human"/end_on)` é a ferramenta certa **aqui** (fronteira de turno, memória multi-troca) — no shape intra-turno ela colapsa para quase-vazio (comprovado empiricamente). Quando a thread por sessão acumular turnos, o corte de histórico entre turnos usa `start_on`; nunca aplicar na cadeia intra-turno.
- **Objetivo da task 1.** Provar que uma thread por sessão (em vez de por turno) elimina a reconstrução de histórico pelos produtores e habilita fork/time-travel.
- **Abordagem.** Rodar uma conversa de explore de 3 turnos com `thread_id` de escopo de sessão sobre um único checkpointer durável; conferir que o 2º/3º turno não re-semeiam o histórico. Testar um fork via `update_state` a partir de um checkpoint intermediário.
- **Contrato.** Muda a convenção de `thread_id` (hoje `"{ws}:{session}:{turn_index}"` → `"{ws}:{session}"`) e **toca os produtores** (`writing_session`, explore) — não só o grafo. **Nuance verificada (2026-07-18):** só a escrita usa checkpointer hoje; o explore roda `checkpointer=False` (stateless total, sem `thread_id` algum). Para o explore, este item significa **ligar** o checkpointer, não só re-escopar a chave — é trabalho maior e distinto por produtor.
- **Sinal de sucesso.** Histórico acumulado lido do checkpointer sem re-seed; um fork produzindo duas continuações a partir do mesmo ponto.
- **Keep vs throwaway.** *Keep:* o modelo de dados "thread por sessão". *Throwaway:* o demo de fork.
- **Promoção.** Refactor dos produtores para thread por sessão. **Gate:** eval de **writing** (o caminho com checkpointer durável é o da escrita) + reteste do leak-test de RLS do checkpointer (memória `project_langgraph_migration`).
- **Pegadinha.** É o item de maior risco (mexe em produtor + no schema `agent_memory`); só depois de #1/#2 firmes. O `interrupt/resume` da escrita não pode regredir.

## Item 4 — `Send` (fan-out nativo, incl. subagentes)

- **Objetivo do spike.** Provar o fan-out dentro do grafo com estado/checkpoint/trace por ramo, incluindo **paralelizar subagentes** (`run_subagent` já existe).
- **Abordagem.** Duas variações throwaway: (a) reescrever a geração de 3 seções com `Send` em vez do `asyncio.gather`+`Semaphore` de `run_generation_turn`; (b) um fan-out de N `deep_research` em paralelo via `Send`. Conferir no Langfuse um span por ramo.
- **Sinal de sucesso.** Paralelismo com observabilidade por ramo (vs a orquestração opaca atual do `gather`).
- **Keep vs throwaway.** Aprendizado-primeiro; *keep* só se a observabilidade/estado por ramo provar valor sobre o `gather` que já funciona.
- **Promoção (condicional).** Substituir a orquestração da geração por `Send`. **Gate:** eval de **writing** (a geração em lote é caminho de escrita). Só promover se o ganho superar o custo de reescrever algo que funciona.
- **Pegadinha.** `Send` muda a semântica de estado (estado por ramo ≠ estado do grafo); a redução de volta tem que casar com o contrato de `AgentResult`/persistência por seção.

## Item 5 — `interrupt()` adicional (human-in-the-loop)

- **Objetivo do spike.** Provar um gate de aprovação antes de uma ação consequente (ex.: gravar/alterar perfil ou constraint derivada), no padrão "AI age, humano corrige".
- **Abordagem.** Adicionar um `interrupt()` antes de uma escrita de perfil num fluxo de explore autenticado (throwaway/branch); resumir com `Command(resume=allow|deny)` e verificar o efeito.
- **Sinal de sucesso.** A gravação só acontece após `allow`; `deny` aborta sem efeito colateral.
- **Keep vs throwaway.** *Keep* o padrão se o gate for útil ao produto; senão descarta.
- **Promoção (condicional).** Gate real no fluxo de perfil. **Gate:** eval do eixo tocado (matching/profile) + teste do replay.
- **Pegadinha.** Ao resumir, **o nó reexecuta desde o início** (não da linha do `interrupt()`) — o nó tem que ser idempotente até o ponto de pausa, senão a ação roda duas vezes.
- **Dependência (correção 2026-07-18).** `interrupt()` **exige checkpointer no caminho** — e explore/profile rodam `checkpointer=False` hoje. Além disso, o diff de perfil do explore acontece **fora do grafo** (call separado no produtor — memória `project_profile_input_decisions`), onde `interrupt()` não alcança. O spike deve mirar uma ação **dentro** de um grafo com checkpointer: ou `submit_profile` no modo profile usando o fallback `InMemorySaver` de dev, ou assumir dependência do #3 (explore com checkpointer). "Spike de sábado" só na primeira forma.

---

## Item 6 — Guarda por estado (budget consciente / anti-truncamento) — ✅ PROMOVIDO 2026-07-18

**Status:** mergeado na main (merge `625cca853`) e deployado (app+worker). T3 pós-reword: taxa de truncamento writing **1.00→0.50**, avg_llm_calls **11.00→10.50** — regra pré-registrada satisfeita nos 2 critérios (N=4, direcional). Telemetria `turn_end` viva em prod. **Lição que fica:** a 1ª versão do aviso ("faça UMA última chamada") INDUZIU chamada extra e piorou a métrica — reword para proibição direta resolveu; é a 2ª confirmação da lição `project_radar_cards_persist` (mencionar opção induz o modelo a exercê-la), agora em prompt de sistema de grafo. Explore nunca aciona o aviso (avg 4 de teto 15) — letra morta inofensiva lá. Subir `max_steps` segue decisão separada, agora COM telemetria para embasá-la.

**Origem:** discussão de 2026-07-18 — `max_steps` é backstop correto, mas **burro**: corta sem o modelo saber que o orçamento estava acabando, produzindo respostas truncadas via `finalize` forçado.

**Munição do spike do #2 (2026-07-18):** o truncamento real observado foi exatamente desta classe — `search_edital → read_exact_chunk ×5 → search_edital → read_exact_chunk ×3` esgotou os 10 passos SEM escrever a seção. Padrões de desperdício documentados: leitura de trechos 1-a-1 e `list_icts`/`list_investidores` repetidos até 6× sem dedup. Budget-awareness ataca os dois. **Correção do passo (a):** o campo `truncated` NUNCA é persistido (achado do spike — só existe no response em memória; `session_turns` não o tem) — medir exige primeiro **persistir/telemetrar** o `stop_reason` por turno. **Herança:** a Task 5 do plano do item 1 (carona 6b) não foi implementada — este item a absorve. **Fora de escopo daqui:** subir `max_steps` — é decisão separada de governança, gated pelos dados deste item (e reabriria o #2 via gatilho ii).

- **Objetivo.** Reduzir truncamentos abruptos (`stop_reason == "max_steps"`) tornando o agente **consciente do budget restante**; abrir caminho para tetos por custo/tokens no estado.
- **Abordagem (em 3 passos, parar no primeiro que resolver).**
  - (a) **Medir primeiro:** taxa real de truncamento por modo via Langfuse/telemetria — o campo `truncated` já existe nos resultados dos produtores. Se for ~0, arquivar o item.
  - (b) **Mudança mínima (sem feature nova):** em `after_tools`, quando `llm_calls == max_steps - 1`, injetar aviso "último passo disponível — conclua com o que tem". Os contadores já estão no estado (`llm_calls`/`max_steps`); é uma condição + uma mensagem.
  - (c) **Referência de aprendizado:** o managed value `RemainingSteps` + `recursion_limit` nativos — estudar; adotar só se substituir (b) com vantagem real (o contador próprio já funciona).
- **Sinal de sucesso.** Queda na taxa de truncamento sem inflar a média de steps por turno.
- **Régua.** Forma (b) dispensa spike (mecânica conhecida) — direto a plan→impl. A forma (c) é leitura/spike de aprendizado.
- **Promoção.** Mudança pequena em `_build_graph` — serve todos os modos de uma vez. **Gate:** nenhum de eval (não muda a evidência vista pelo modelo, só avisa budget) — smoke via `verify` + comparação antes/depois da taxa de truncamento.
- **Fora de escopo (harness smell).** Detecção genérica de progresso/loop ("últimas N tools sem informação nova") — se essa necessidade aparecer, é gatilho de reavaliação (SDK?), não de construir à mão.
- **Pegadinha.** O aviso entra como mensagem no histórico — **não pode vazar para o usuário final**. É a mesma classe do bug do `_REFLECT_PROMPT` (memória `project_reflect_leak_bug`): validar que o texto injetado nunca aparece como resposta.

---

## Item adjacente — Ativar `playbook_overlays` (skills editáveis em runtime)

**Fora do escopo LangGraph, mas é enriquecimento de inteligência de baixo custo.** O reader já existe (`core/skills.py::_load_overlays`, `load_playbook` mergeia overlays do banco por seção); falta só um **produtor** que grava em `playbook_overlays`.

- **Objetivo do spike.** Provar o ciclo completo: gravar um overlay de teste → `load_playbook` mergeia na competência do Redator/Monitor.
- **Abordagem.** Inserir uma linha de teste em `playbook_overlays` (mechanism/source/section/body) e confirmar que `load_playbook(mech, src)` traz a seção mergeada; depois, um produtor mínimo que grava overlay a partir de uma correção humana.
- **Sinal de sucesso.** Overlay escrito aparece na composição sem tocar em git.
- **Promoção.** Produtor a partir de correção humana ou padrão aprendido. **Gate:** eval de **writing** (muda a competência injetada). Liga com a filosofia "AI age, humano corrige" (memória `project_knowledge_evolution_spec`).
- **Nota.** Não consome LangGraph nem SDK — reusa infra dormente. É a resposta "domínio-específica" ao gap de skills editáveis do Grantable, sem harness.

---

## Fora de escopo (explícito)

- Reconstruir subagentes, pesquisa web ou sistema de skills — **já existem**.
- Infraestrutura de harness: motor de compaction genérico, sistema de skills tipo SKILL.md para usuário final, framework de permissões genérico. (Gatilho de SDK, não de construir à mão.)
- Migração para o Claude Agent SDK (rede de segurança para fase de produto — memória `project_agent_sdk_spike`).
- Matching (é sem-LLM, não é grafo LangGraph — memória `project_match_rag_boundary`). Melhorá-lo é a próxima trilha (extração → KG).
