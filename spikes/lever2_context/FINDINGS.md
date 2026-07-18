# Item 2 — Findings (Gestão de contexto)

**Data:** 2026-07-18 · **Script:** `spikes/lever2_context/demo.py` · **Runtime real:**
`ExploreAgent.explore_with_meta` / `WritingSession.turn` → `core/llm/agent_graph.py`
(mesmo `_build_graph`, mesmos system prompts/tools de produção). Captura sem
tocar `core/`: monkeypatch de `agent_graph._messages_to_agent_result` (chamada
tanto por `run_agent_graph_async` quanto por `_writing_turn_async`) para
guardar a lista real de `AnyMessage` de cada run.

## Por que os históricos foram gerados AO VIVO (economia, ordem mandada)

Tentamos primeiro (a) reconstruir de `session_turns.tool_use` (persistido,
zero custo de runtime). Levantamento em produção (29 sessões, 60 turnos):

| | valor |
|---|---|
| maior tool-result única persistida | 2.932 chars (`search_edital`) |
| maior soma de tool-outputs num turno | 6.006 chars, 3 tools |
| `TOOL_RESULT_CHAR_CAP` (teto por tool) | 8.000 chars |

Nenhum turno persistido chega perto do cap por-tool nem exibe sinal de
`max_steps` (o campo `truncated` é só do response em memória — nunca é
persistido, então nem dá pra filtrar por "turnos cortados" no histórico). O
formato (`role`/`content`/`tool_use[]`) seria suficiente para reconstruir uma
`AgentState.messages` plausível, mas os dados reais disponíveis não
representam os casos que hoje sofrem corte — só confirmam que "hoje" o
writing golden mal foi exercido em produção (ver seção final). Por isso os
2 históricos abaixo são runs ao vivo (b) contra os produtores reais.

## História A — Explore multi-hop

Pergunta desenhada pra forçar `list_editais` + N×`get_edital` + `list_icts`/
`list_investidores` na mesma resposta. **Achado lateral real, não planejado**:
a 1ª tentativa (`tema='inovação'`) voltou 0 resultados — `_theme_match` não
casa com um tema tão genérico (os setores reais no catálogo são coisas como
`Multissetorial`/`Agro`/`Energia`, não "inovação"). Ajustei a pergunta pra não
filtrar por tema; nas execuções seguintes o agente **repetiu `list_icts`/
`list_investidores` até 6× num único turno** (uma vez por "tema envolvido"
identificado, sem deduplicar) — um comportamento caro e redundante que existe
hoje independente deste item, e que é exatamente o tipo de coisa que um teto
de `max_steps` mais generoso (que o Item 2 abriria caminho para) tornaria
*mais* frequente/caro se nada mais mudar. Fica anotado para o Item 6 ou como
achado avulso — fora do escopo de decidir aqui.

Um dos runs (17 mensagens, 6.298 tokens, 24 hits de citação) terminou em
`end_turn` (não truncou). Usado no experimento de trim abaixo.

## História B — Writing com citações real (e um truncamento REAL observado)

Uma sessão nova cai no branch F4 "plan-first" do primeiro turno
(`_first_turn_with_generation` — só gera um plano estruturado, zero tool
call, nada útil pra capturar). Resolvido retomando uma sessão **real já
existente** no workspace de eval (`EVAL_WORKSPACE_ID`, criada por uma run
anterior do golden v2 — `finep:769`, perfil "tratorbr"), pedindo uma nova
seção ("Equipe técnica") com instrução que força `search_edital` +
`read_exact_chunk` real.

**Resultado real (não fabricado): `truncated=True`.** O turno bateu
`AGENT_MAX_STEPS=10` (modo chat/writing) depois de:

```
search_edital → read_exact_chunk ×5 → search_edital → read_exact_chunk ×3 (cortado)
```

32 mensagens capturadas, 7.520 tokens totais, 15 hits de citação (valores,
percentuais, prazos). **Achado central**: o corte NÃO foi por estouro de
contexto/tokens — 7.520 tokens está longe de qualquer limite de janela real.
Foi por **contagem de chamadas**: o agente gasta 1 `llm_call` por
`read_exact_chunk`, e ler 5 trechos de UMA busca + 3 de outra já consome 8
dos 10 passos disponíveis, sem sequer ter chegado a escrever a seção. **Isto
é orçamento de PASSOS (Item 6), não de CONTEXTO (Item 2)** — `trim_messages`
não teria evitado este truncamento específico; só aumentar `max_steps` ou
tornar a leitura de trechos menos granular (ex.: `read_exact_chunk` em lote)
resolveria. Registro isto porque contradiz a premissa implícita da régua do
item ("o corte de hoje é de contexto") no ÚNICO caso real de truncamento que
observei — os dois problemas coexistem e merecem tratamento separado.

## Experimento `trim_messages` — 2 configurações × 3 orçamentos × 2 históricos

`token_counter=chat_model` (real, `ChatOpenAI.get_num_tokens_from_messages`,
gpt-4o-mini — único provider disponível), `strategy="last"`,
`include_system=True`.

### Achado 1 — `start_on="human"` é traiçoeiro no shape intra-turno daqui

`start_on`/`end_on` foram desenhados pra alinhar corte a fronteiras de TURNO
numa memória multi-sessão (várias trocas Human/AI). O `AgentState.messages`
de UM turno ReAct tem o shape oposto: 1 `HumanMessage` seguido de uma cadeia
longa de `AIMessage`(vazia, só tool_call)/`ToolMessage`. Com esse shape,
`start_on="human"` colapsa de forma NÃO-gradual assim que o orçamento força
excluir o único ponto de ancoragem válido:

| Explore (17 msgs, 6.298 tok) | budget | `start_on="human"` | sem `start_on` |
|---|---|---|---|
| 90% | 5.668 | **1/17 msgs, 0 hits mantidos** (colapso total) | 21/24 msgs¹, 11 hits mantidos |
| 60% | 3.778 | **1/17 msgs, 0 hits mantidos** (idêntico ao 90%) | 13/24 msgs¹, 4 hits mantidos |
| 35% | 2.204 | **1/17 msgs, 0 hits mantidos** (idêntico) | 7/24 msgs¹, 4 hits mantidos |

¹ run separado (24 msgs / 7.817 tokens, mesma pergunta, tool-calls repetidos
— variância do LLM entre execuções; a COMPARAÇÃO start_on vs sem-start_on
importa, não o total exato de mensagens.

Com `start_on="human"` o resultado é **idêntico nos 3 orçamentos** — sinal de
que, uma vez que o budget não cabe mais o `HumanMessage` inicial + toda a
cauda, o algoritmo não degrada proporcionalmente: ele desiste e devolve
praticamente nada. **Sem `start_on`**, a degradação é gradual e proporcional
(21→13→7 mensagens conforme o orçamento cai), do jeito que se espera de um
"corte suave".

**Recomendação empírica**: para o shape intra-turno deste runtime, usar
`trim_messages(strategy="last", include_system=True)` **sem** `start_on`/
`end_on`. Esses parâmetros fazem sentido pra memória entre turnos (Item 3),
não pra poda dentro de um turno único.

### Achado 2 — mesmo sem `start_on`, a ordem de corte é por IDADE, não por IMPORTÂNCIA

No histórico de writing (32 msgs, 7.520 tokens, `start_on="human"`), o corte
de 90% do orçamento (perde só 752 tokens) já derruba as duas primeiras
mensagens de contexto estático — **PERFIL DA EMPRESA** (Capital Social R$
400.000) e **CARD DA FONTE** (objetivo/requisitos do edital) — simplesmente
porque são as mais ANTIGAS da lista, não porque sejam as menos importantes.
No histórico de explore (sem `start_on`), o mesmo padrão aparece: a
`list_editais` completa (10 hits, o catálogo dos 35 editais) é a primeira a
cair aos 90%, enquanto chamadas tardias e repetidas de `list_icts`/
`list_investidores` (baixo valor, 0 hits) sobrevivem até orçamentos bem mais
apertados só por serem mais recentes. Em orçamentos moderados/apertados
(60%/35%), o corte por idade eventualmente alcança os `get_edital` reais
(citações de R$/TRL/prazo) e os derruba — exatamente a "evidência load-bearing"
que o item pede pra preservar.

**Conclusão do experimento**: `trim_messages` puro (em QUALQUER configuração
testada) não é evidence-aware — ele não sabe distinguir uma fonte normativa
(`get_edital`/`search_edital`) de uma listagem de navegação de baixo valor
(`list_icts` redundante). Em orçamentos apertados (a situação que o item quer
resolver — turnos que HOJE não cabem), ele eventualmente come citação. Isto
bate com a "pegadinha" já prevista na spec.

## Protótipo opcional — resumo seletivo por densidade

Implementado (`summarize_low_density` em `demo.py`): para `ToolMessage`s com
poucos "hits" de citação (regex sobre R$/TRL/%/art./datas/prazos) e >400
chars, chama gpt-4o-mini pra resumir a ~250 chars preservando números
verbatim; mensagens de alta densidade (fonte normativa) não são tocadas.

**Rodou de fato** sobre o histórico de explore: 5 candidatos (a listagem de
`list_editais`, 2× `get_edital` de baixa densidade, `list_icts`,
`list_investidores`) resumidos de ~4.500 chars totais pra ~1.400 chars.
**Custo real medido: 1.528 tokens de input + 394 de output para 5
chamadas** (~380 tokens/chamada em média) — não-trivial se disparado a cada
turno que exceda o limiar; precisa ser gated por um limiar real (só dispara
se o turno já ultrapassou budget), não rodar sempre.

**Achado crítico sobre o próprio protótipo**: o detector de densidade por
regex classificou como "baixa densidade" (e portanto resumiu) 2 respostas de
`get_edital` que tinham objetivo/status mas nenhum número no formato que o
regex reconhece — não dá pra saber, sem ler o texto completo, se o resumo
perdeu algo relevante que o regex simplesmente não captura (ex.: "3,3
bilhões" sem o "R$" colado, valores por extenso, prazos em formato não
numérico). **Isto é exatamente o risco que a spec já sinalizava** ("resumir
só os de baixa densidade") — o protótipo prova que a MECÂNICA funciona
(chamar o LLM, preservar formato, medir custo), mas não resolve o problema
real: um detector de densidade confiável é o cerne do trabalho, não um
regex de 6 padrões escrito num throwaway.

## Veredito do checkpoint

- **Sinal de sucesso pedido** ("turno mais longo cabendo, evidência
  preservada"): **não alcançado por `trim_messages` sozinho** em nenhuma
  configuração testada — em orçamentos que forçam corte de verdade, ele
  eventualmente derruba citação real (Achado 2). Alcançável só com uma
  política density-aware (resumo seletivo), cujo protótipo funciona
  mecanicamente mas cujo detector de densidade não está pronto pra produção.
- **Achado que muda a sequência do plano**: o único truncamento REAL
  observado neste spike foi por contagem de passos (`max_steps`), não por
  contexto — ou seja, o Item 2 sozinho não teria evitado o corte real que
  encontrei. Isto sugere que Item 2 e Item 6 (guarda por estado) precisam
  ser avaliados juntos no plano, não em sequência estrita.

## ★ REGRA DE ESCALADA — trade-off registrado, não decidido ★

Não escolho entre as opções abaixo — os dados:

| Opção | Preserva citação em orçamento apertado? | Custo por turno | Risco |
|---|---|---|---|
| `trim_messages` puro (last, sem start_on) | **Não** — derruba `get_edital`/`search_edital` reais quando o corte é profundo (Achado 2) | Zero (função pura, sem LLM) | Silencioso: o modelo nunca sabe que perdeu contexto |
| Resumo seletivo por densidade (protótipo) | Parcialmente — mecânica preserva o que o detector reconhece, mas o detector-regex tem falsos negativos comprovados (2 de 5 casos no run real) | ~380 tokens/chamada resumida (gpt-4o-mini) | Requer um detector de densidade muito mais robusto que 6 regexes antes de ir a produção — trabalho não-trivial |
| Não fazer nada (status quo: `_cap` cego + `max_steps` baixo) | N/A — corta a resposta inteira, não seletivamente | Zero | É o problema que o item existe pra resolver; e o truncamento real observado nem era de contexto (ver acima) |

**Pré-requisito do gate (writing golden + grounding), status real verificado
neste spike**: rodei `python -m core.eval run writing_v2 --limit 1` — RODA e
produz métricas reais (`mean_pct_grounded=1.0`, `mean_n_factual_errors=1`
numa amostra de 1). **Isto já corrige parcialmente a memória
`project_writing_agent_evolution`** ("golden N=3 nunca rodou"): o mecanismo
roda de fato, sem nenhum blocker de plumbing.

Também disparei a run completa (N=12, `writing_v2.json`, sem `--limit`) pra
medir o tempo real de rodar o golden inteiro — **cancelada após ~13 minutos
sem terminar** (a régua era 5 min de espera adicional; ainda estava no meio
dos 12 casos, log mostrava só ~5-6 casos processados). Cada caso roda até 4
turnos de agente ReAct real + juízes LLM de grounding/coerência — o custo é
tempo de parede (chamadas sequenciais ao gpt-4o-mini), não falha de
mecanismo. **Estimativa pra tornar o gate rodável**: com 12 casos a ~2-3 min/
caso neste ritmo, a suíte completa leva **~25-35 min** — viável como gate de
CI/promoção (roda uma vez, não interativo), mas PRECISA rodar em background/
job, não numa sessão interativa como esta. `core/eval/harness.py` processa os
casos em loop `for item in items` — **sequencial, sem paralelismo** (confirmado
lendo o código, não só inferido). Paralelizar os 12 casos (cada um já é
stateless — sessões distintas) cortaria o tempo de parede proporcionalmente
e é uma mudança pequena e isolada no harness, não no runtime do agente —
candidato óbvio pro plano do Opus antes de rodar o gate como CI de verdade.

**GO/NO-GO desta spike**: dados coletados, sem perda de fidelidade nos 2
históricos reais. A decisão de qual política adotar (trim puro vs resumo
seletivo vs redesenhar o detector) fica para a governança, conforme a régua.
