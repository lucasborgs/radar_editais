# Plano de tasks — Item 6: Guarda por estado (budget consciente / anti-truncamento)

**Status:** implementado e mergeado · **Data:** 2026-07-18 · **Autor do plano:** Opus 4.8
**Fonte-contrato:** `docs/specs/langgraph-levers-spec.md` § Item 6 · **Dados:** `spikes/lever2_context/FINDINGS.md`
**Implementação:** Sonnet 5 (nenhuma task qualifica para Opus — não toca produtores de risco/RLS)
**Régua:** forma (b) da spec dispensa spike (mecânica conhecida) → direto plan→impl.

---

## Contexto herdado (não re-litigar)

- O **#2 está ARQUIVADO** (governança, 2026-07-18); o #6 herdou a frente. A sequência é `#1 (✅) → #6 → #3`.
- O único truncamento **real** observado no spike foi por **contagem de passos** (`max_steps`), não por contexto:
  `search_edital → read_exact_chunk ×5 → search_edital → read_exact_chunk ×3` esgotou `AGENT_MAX_STEPS=10`
  **sem escrever a seção** (7.520 tokens — longe de qualquer janela). Também: `list_icts`/`list_investidores`
  repetidos até 6× sem dedup. Budget-awareness ataca os dois padrões.
- Só existe `OPENAI_API_KEY` (gpt-4o-mini) em todos os ambientes.
- Tetos por modo (constantes reais): **10** chat/writing (`AGENT_MAX_STEPS`, [writing_session.py:76](../../core/services/writing_session.py#L76)),
  **12** profile (`EXTRACTOR_AGENT_MAX_STEPS`, [profile_extractor.py:123](../../core/profile_extractor.py#L123)),
  **15** explore (`EXPLORE_AGENT_MAX_STEPS`, [explore_agent.py:154](../../core/services/explore_agent.py#L154)),
  **5** deep_research ([deep_research.py:27](../../core/deep_research.py#L27)); refine 20 / batch 2 conforme a spec.

## Achados de código que ancoram o plano (verificados)

| Fato | Evidência |
|---|---|
| O campo `truncated` **nunca é persistido** — só existe no `meta` do response em memória | [explore_agent.py:403](../../core/services/explore_agent.py#L403), [explore_agent.py:582](../../core/services/explore_agent.py#L582), [writing_session.py:1410](../../core/services/writing_session.py#L1410) — todos `result.stop_reason == "max_steps"` |
| O INSERT em `session_turns` **não tem** coluna `truncated`/`stop_reason` | payload em [writing_session.py:2196-2206](../../core/services/writing_session.py#L2196-L2206) só grava role/content/section_hint/tool_use/tokens |
| O span `agent_run` **já grava `stop_reason`** + `max_steps` no metadata | `span.update(...)` em [agent_graph.py:426-434](../../core/llm/agent_graph.py#L426-L434); metadata inicial com `max_steps` em [agent_graph.py:397-401](../../core/llm/agent_graph.py#L397-L401) |
| O span **não carrega `mode`**; nome é genérico `agent.{provider}.{model}` e produtores não passam `span_name` | [agent_graph.py:395](../../core/llm/agent_graph.py#L395); chamadas em [explore_agent.py:362](../../core/services/explore_agent.py#L362) e [explore_agent.py:565](../../core/services/explore_agent.py#L565) sem `span_name=` |
| A força de `finalize` já injeta `_FINALIZE_PROMPT` prefixado `[Aviso interno — não é mensagem do usuário]` | nó `finalize` em [agent_graph.py:198-202](../../core/llm/agent_graph.py#L198-L202); prompt em [agent_runtime.py:131-137](../../core/llm/agent_runtime.py#L131-L137) |
| `after_tools` é o ponto de ramificação por budget (`llm_calls >= max_steps → finalize`) | [agent_graph.py:188-217](../../core/llm/agent_graph.py#L188-L217) |
| Contador `llm_calls` incrementa em `agent`/`agent_final`, vive no estado | [agent_graph.py:151](../../core/llm/agent_graph.py#L151), [agent_graph.py:161](../../core/llm/agent_graph.py#L161); campo em [agent_graph.py:67](../../core/llm/agent_graph.py#L67) |
| Já existe teste anti-leak estrutural + teste de finalize (harness reutilizável) | `test_no_internal_human_message_injected_in_turn` [test_agent_graph_golden.py:163](../../tests/test_agent_graph_golden.py#L163); `test_max_steps_stop_reason` [test_agent_graph_golden.py:130](../../tests/test_agent_graph_golden.py#L130) |
| Harness roda casos em **loop sequencial** `for item in items` | `_run_local` [harness.py:316](../../core/eval/harness.py#L316); casos são stateless (sessões distintas) |

---

## Decisão de arquitetura de MEDIR (Task 1) — span, não coluna

O contrato manda "preferir o caminho mais barato (span Langfuse **ou** coluna em `session_turns`)".
**Decisão: span**, com um adendo de log estruturado. Racional:

- **Coluna** exigiria migration + tocar o payload do INSERT em cada produtor (writing + explore) + threading do
  `stop_reason` até o ponto de persistência. Mais superfície, toca produtores.
- **Span** já carrega `stop_reason` ([agent_graph.py:430](../../core/llm/agent_graph.py#L430)); falta só a dimensão
  **`mode`** para segmentar. É **um campo** no metadata + um kwarg opcional threaded — não toca schema de banco.
- **Porém**, o span depende de Langfuse **habilitado** e de consulta via SDK/UI — inviável como fonte da comparação
  (c) num contexto de *smoke* (sem tráfego de produção, Langfuse pode estar off). Por isso a Task 1 entrega
  **também** uma **linha de log estruturada no fim do turno** (sempre ligada, grep-ável) — é o que a Task 3
  efetivamente consome. Custo: uma linha `logger.info` já dentro do bloco que tem `result.stop_reason`.

---

## Tasks

### Task 1 — MEDIR: `mode` no span + log estruturado de fim de turno
**Depende de:** nada · **Modelo:** Sonnet · **Toca produtores?** superfície mínima (passam `mode`/`span_name`)

**Arquivos:**
- [core/llm/agent_graph.py](../../core/llm/agent_graph.py): adicionar kwarg opcional `mode: str | None = None` às entradas
  `run_agent_graph_async` ([:341](../../core/llm/agent_graph.py#L341)), `run_agent_graph_streaming` ([:468](../../core/llm/agent_graph.py#L468))
  e ao `_writing_turn_async`/`generation` ([:971](../../core/llm/agent_graph.py#L971), [:1250](../../core/llm/agent_graph.py#L1250));
  incluir `"mode": mode` no `metadata=` de `telemetry.agent_run(...)` ([:397](../../core/llm/agent_graph.py#L397), [:530](../../core/llm/agent_graph.py#L530), [:1008](../../core/llm/agent_graph.py#L1008));
  emitir **uma** linha de log estruturada após `_derive_stop_reason` (ao lado de [:423](../../core/llm/agent_graph.py#L423) e do bloco streaming [:585](../../core/llm/agent_graph.py#L585)):
  `logger.info("turn_end mode=%s stop_reason=%s llm_calls=%d max_steps=%d", mode, stop, final.get("llm_calls", 0), max_steps)`.
- [core/llm/agent_runtime.py](../../core/llm/agent_runtime.py): repassar `mode` em `run_agent_async`/`run_agent`/`run_agent_streaming_async`
  ([:214](../../core/llm/agent_runtime.py#L214), [:271](../../core/llm/agent_runtime.py#L271), [:313](../../core/llm/agent_runtime.py#L313)) → grafo.
- Produtores passam `mode`: explore ([explore_agent.py:362](../../core/services/explore_agent.py#L362), [:565](../../core/services/explore_agent.py#L565)) → `mode="explore"`;
  writing ([writing_session.py](../../core/services/writing_session.py)) → `mode="writing"`; profile → `mode="profile"`. (batch/refine se já roteados pelo grafo.)

**Fora de escopo desta task:** migration; coluna em `session_turns`; qualquer consulta programática a Langfuse.

**Critério de aceite (verificável):**
1. `pytest tests/test_agent_graph_golden.py -q` verde (o kwarg é opcional; `test_graph_honors_span_name` [:259](../../tests/test_agent_graph_golden.py#L259) não regride).
2. Rodar um turno de explore e um de writing pelo caminho real (script em `spikes/lever6_budget/` ou `verify`) e
   confirmar **no stdout/log** a linha `turn_end mode=explore stop_reason=… llm_calls=… max_steps=15` e
   `turn_end mode=writing … max_steps=10`.
3. Com Langfuse habilitado (se disponível localmente), o span do turno mostra `mode` no metadata; se off, a linha de log
   ainda aparece (prova de independência de Langfuse).

---

### Task 2 — AVISO DE ÚLTIMO PASSO: nó `budget_notice` no grafo + teste anti-leak
**Depende de:** nada (pode ser paralela à T1) · **Modelo:** Sonnet · **Runtime compartilhado — serve todos os modos**

**Mecânica.** Hoje `after_tools` ([agent_graph.py:188-196](../../core/llm/agent_graph.py#L188-L196)) ramifica em 2:
`llm_calls >= max_steps → finalize` senão `agent`. Transformar em **3 vias**, espelhando o padrão do nó `finalize`:

```
after_tools(state):
    if llm_calls >= max_steps:        return "finalize"        # backstop duro (já existe)
    if llm_calls == max_steps - 1:    return "budget_notice"   # NOVO — penúltimo passo com tools
    return "agent"
```

Novo nó `budget_notice` (irmão de `finalize`, [:198-202](../../core/llm/agent_graph.py#L198-L202)) injeta **uma** `HumanMessage`
com o aviso e faz `add_edge("budget_notice", "agent")` — o modelo ainda tem essa rodada com tools, mas sabe que é a
última. Registrar a constante ao lado de `_FINALIZE_PROMPT` ([agent_runtime.py:131](../../core/llm/agent_runtime.py#L131)),
**com o mesmo prefixo anti-leak** `"[Aviso interno — não é mensagem do usuário] "`:

> `_LAST_STEP_PROMPT = "[Aviso interno — não é mensagem do usuário] Este é o último passo em que você pode chamar tools. Conclua agora a resposta ao usuário com o que já tem; se ainda precisar de um dado, faça no máximo UMA última chamada e então responda."`

**Por que penúltimo (`== max_steps - 1`) e não no `finalize`:** o `finalize` já é o corte cego (roda **sem** tools,
[:155-163](../../core/llm/agent_graph.py#L155-L163)). O aviso do #6 chega **um passo antes**, quando o modelo ainda pode
fechar graciosamente com uma última tool — atacando exatamente o padrão do spike (gastar todos os passos em
`read_exact_chunk` sem nunca escrever).

**Arquivos:** [core/llm/agent_graph.py](../../core/llm/agent_graph.py) (nó + edge + condição em `after_tools` + registro no
`add_conditional_edges` [:214-217](../../core/llm/agent_graph.py#L214-L217)); [core/llm/agent_runtime.py](../../core/llm/agent_runtime.py) (constante);
[tests/test_agent_graph_golden.py](../../tests/test_agent_graph_golden.py) (novos testes).

**Critério de aceite (verificável):**
1. **Injeção:** novo teste `test_last_step_notice_injected` — script de 3 tools com `max_steps=3`; asserta que a chamada
   do modelo no passo `max_steps-1` recebeu uma `HumanMessage` contendo `"último passo em que você pode chamar tools"`
   (padrão de `test_max_steps_stop_reason` [:152-156](../../tests/test_agent_graph_golden.py#L152-L156)).
2. **ANTI-LEAK (OBRIGATÓRIO):** novo teste `test_last_step_notice_never_leaks` — asserta que `graph.final_text` **não**
   contém `"não é mensagem do usuário"` nem o texto do aviso, mesmo quando o aviso foi injetado (mesma classe do bug
   `_REFLECT_PROMPT`, memória `project_reflect_leak_bug`; estende a garantia de `test_no_internal_human_message_injected_in_turn`
   [:163-191](../../tests/test_agent_graph_golden.py#L163-L191) para o caso em que a nota **é** injetada).
3. **Não-regressão de topologia:** `test_graph_topology_has_no_reflect_or_prune_nodes` [:194](../../tests/test_agent_graph_golden.py#L194)
   atualizado para incluir `budget_notice` no conjunto esperado; nenhum nó `reflect`/`prune` reintroduzido.
4. **Turno curto intocado:** em turno que termina naturalmente antes de `max_steps-1`, nenhuma nota é injetada
   (o `test_no_internal_human_message_injected_in_turn` continua verde sem alteração).
5. `pytest tests/test_agent_graph_golden.py tests/test_agent_runtime.py -q` verde.

**ADENDO DE GOVERNANÇA (2026-07-18) — threshold obrigatório na T2:** o aviso só é injetado quando **`max_steps >= 3`**.
Motivo: com `max_steps=2` (batch de geração), `llm_calls == 1 == max_steps-1` dispara o aviso em **toda** seção do batch
que chame tool — vira fixture constante num caminho de produção calibrado (JSON estruturado), não um aviso; e o design
do batch já é "busca uma vez e escreve", tornando-o redundante. Critério de aceite adicional: **teste
`test_no_notice_when_max_steps_2`** — com `max_steps=2`, nenhuma nota injetada em nenhum passo. Nota de consciência:
subagentes (critic/deep_research, `max_steps=5`) receberão o aviso no passo 4 — comportamento desejado, sem exceção.

---

### Task 3 — COMPARAR: smoke de taxa de truncamento antes/depois, por modo
**Depende de:** Task 1 (log estruturado) + Task 2 (o aviso a ser medido) · **Modelo:** Sonnet

**Abordagem.** Script throwaway em `spikes/lever6_budget/` que dirige um **conjunto fixo** de turnos representativos
por modo — incluindo o caso reproduzido no spike (writing pedindo "Equipe técnica" com instrução que força
`search_edital` + `read_exact_chunk` múltiplos; explore multi-hop que dispara `list_icts`/`list_investidores`).
Rodar a bateria **duas vezes** — com o nó `budget_notice` desligado (baseline, ex. via env/monkeypatch do
`after_tools` para nunca ramificar ao aviso) e ligado — e **contar `stop_reason=max_steps` por modo** grepando a
linha `turn_end` da Task 1.

**Critério de aceite (verificável):**
1. Tabela antes/depois: `#turnos`, `#truncados`, `taxa` por modo (explore, writing), a partir do log `turn_end`.
2. **Sinal de sucesso da spec:** queda (ou não-aumento) da taxa de truncamento **sem inflar a média de `llm_calls`/turno**
   (a média de passos vem do mesmo log). Se a taxa baseline já for ~0 em todos os modos → registrar e **arquivar o item**
   (gatilho previsto na spec § Item 6, forma (a)).
3. Resultado salvo em `spikes/lever6_budget/FINDINGS.md` (keep: os números; throwaway: o script).

**Nota de gate.** Conforme a spec, **nenhum eval** (o aviso não muda a evidência normativa que o modelo vê — só
sinaliza budget). Gate de promoção = smoke via `verify` dirigindo chat/writing reais + esta comparação (c).

---

### Task 4 — CARONA (chore isolado): paralelizar o loop de casos do harness
**Depende de:** nada (independente das T1-T3) · **Modelo:** Sonnet · **Não toca runtime do agente**

**Problema.** `_run_local` roda os casos em `for item in items` **sequencial** ([harness.py:316](../../core/eval/harness.py#L316));
a suíte writing N=12 leva ~25-35 min (medido no spike) porque cada caso é tempo-de-parede de chamadas sequenciais ao
gpt-4o-mini. Os casos são **stateless** (sessões distintas) → paralelizáveis.

**Escopo.** Substituir o loop sequencial por execução concorrente com **worker pool limitado** (ex.
`ThreadPoolExecutor(max_workers=…)` — `suite.task` é chamado de forma síncrona), **preservando**:
- **Isolamento de falha por caso** (o `try/except` de [harness.py:320-325](../../core/eval/harness.py#L320-L325) por item).
- **Ordem determinística** dos `item_results` (coletar por índice, não por ordem de término).
- Os `run_evaluators` continuam rodando **após** todos os itens ([harness.py:347-357](../../core/eval/harness.py#L347-L357)).
- Concorrência configurável por env (default modesto, ex. `EVAL_MAX_WORKERS=4`) para não estourar rate limit do
  gpt-4o-mini.

**Fora de escopo:** o caminho `_run_langfuse` ([harness.py:390](../../core/eval/harness.py#L390)) — `run_experiment` tem
sua própria orquestração; esta task mira só o fallback local que o spike mediu.

**Critério de aceite (verificável):**
1. Rodar uma suíte pequena (`--limit 3`) em local e confirmar `item_results` na **mesma ordem** e com os **mesmos
   scores** do caminho sequencial (paridade de resultado; a evidência que o modelo vê não muda).
2. Um caso que levanta exceção continua isolado (não derruba os outros; entra em `errors` como antes).
3. Redução de tempo-de-parede observável numa suíte com ≥3 casos (medir `--limit 3` antes/depois).
4. `pytest` do harness/eval verde (se houver cobertura); senão, smoke com `--limit`.

---

## Grafo de dependências

```
T1 (medir) ─┐
            ├─► T3 (comparar)   [T3 precisa do log de T1 e do aviso de T2]
T2 (aviso) ─┘
T4 (carona harness) ── independente, pode rodar a qualquer momento
```

Ordem sugerida de merge: **T1 → T2 → T3**; **T4** em paralelo/antes (destrava rodar qualquer eval mais rápido).

## Fora de escopo (explícito — não fazer neste item)

- **Subir `max_steps`** — decisão separada de **governança**, *gated* pelos dados da Task 3 (e reabriria o #2 via
  gatilho ii). Este plano só **mede e avisa**; não mexe nos tetos.
- **Detecção genérica de progresso/loop** ("últimas N tools sem informação nova") — *harness smell* registrado na
  spec; se aparecer a necessidade, é gatilho de reavaliação (SDK?), não de construir à mão.
- **Qualquer poda/resumo de contexto** (`trim_messages`, nó de resumo, detector de densidade) — é o **#2 arquivado**.
- **Coluna nova em `session_turns`** / migration — a Task 1 mede via span + log; a coluna só se um gatilho futuro pedir.
- **`read_exact_chunk` em lote / dedup de `list_*`** — mitigações de granularidade que o spike sugeriu; são ataque ao
  *sintoma* pelo lado das tools, não a guarda por estado. Registrar como achado avulso, não implementar aqui.

## Restrições honradas

- Implementação com **Sonnet 5**; tasks autocontidas com critério de aceite verificável.
- **Gate = smoke (`verify`) + comparação (c)**, **sem eval** (o aviso não altera a evidência normativa vista pelo modelo).
- Mudanças em `agent_graph.py` (`_build_graph`) **servem todos os modos de uma vez** (runtime compartilhado).
- Critério **anti-leak obrigatório** na Task 2 — teste dedicado de que o texto injetado nunca vira resposta ao usuário.
