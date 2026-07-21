# Spec — dispatch de skills sem parede (versão cirúrgica)

**Status:** implementado e mergeado · **Data:** 2026-07-20 · **Fluxo SDD:** spec (Fable) → planejamento de tasks (Opus 4.8) → implementação (Sonnet 5) → governança (Fable) gate/git.
**Origem:** discussão de produto 2026-07-20 — comparação com Grantable (`docs/reference/grantable-benchmark.md`) revelou que os "3 modos" do Radar são **paredes**, não capacidades; o Grantable roda 6 skills invocáveis por slash-command num chat contínuo, sem bloqueio cross-skill.
**Restrição desta fase (Lucas):** zero eval/golden longo. Validação = `--limit 1` por caso, garantindo só o *wiring* (a skill dispara o produtor certo, com o toolset certo). Qualidade de escrita/matching fica para o lote final de validação (junto com o Item #3).

---

## Achado que muda o desenho: a "parede" é mais fraca do que parecia

Investigação no código real (2026-07-20) corrigiu a premissa inicial:

1. **O usuário já digita slash-commands num chat único.** `frontend/src/app/workspace/[sessionId]/page.tsx:264-296` já detecta `/explorer`, `/plan`, `/escrita` na MESMA caixa de texto e troca `activeMode`. A superfície de "um chat, comandos dentro dele" **já existe** — não é preciso construir UI nova.
2. **A parede não é um bloqueio de código — é uma INSTRUÇÃO DE PROMPT.** `src/radar/core/services/workspace_service.py:29-39` (`_REDIRECT_BLOCK`) é injetado no system prompt de cada modo mandando o LLM **recusar educadamente** pedidos fora do escopo declarado (`MODE_CONFIG[mode]["out_of_scope"]`) e devolver uma mensagem fixa de redirect. Não há `if` no `dispatch()` (`:171-235`) que impeça a ação — é o próprio modelo, instruído, que se recusa.
3. **`/plan` não é um grafo.** `src/radar/core/kg/planning_node.py::generate_plan` é uma chamada LLM avulsa (`client.chat.completions.create`), sem tools, sem loop ReAct — estruturalmente diferente dos outros dois "modos", que são o grafo LangGraph compartilhado (`src/radar/core/llm/agent_graph.py::_build_graph`) com toolsets distintos.

Consequência: a cirurgia é **menor** do que uma reescrita de dispatcher — é (a) trocar a instrução de recusa por uma de transição fluida, (b) promover capacidades que já existem mas não são invocáveis pelo usuário, e (c) dissolver o `/plan` (que nunca foi um par estrutural dos outros dois) para dentro do fluxo de escrita.

---

## Candidatos a skill (mapeamento contra o que já existe)

| Candidato | Hoje no Radar | O que muda |
|---|---|---|
| `/explorer` (rebatizável `/prospecting` ou mantido) | `ExploreAgent` — RAG+KG, já tem `find_matching_editais`/`find_matching_entities` como tools condicionais | Sem mudança de produtor; só sai da parede |
| `/grant-writing` (rebatiza `/escrita`) | `WritingSession` | **Absorve o `/plan`** como fase interna (ver abaixo) |
| `/review` — **NOVO comando do usuário** | Critic **já existe**, mas roda só automaticamente dentro de `save_draft` ([writing_tools.py:373](../../src/radar/core/llm/agent_tools/writing_tools.py#L373)) — usuário nunca pode pedir "revise agora" | Expor como comando: dispara `run_critic` sobre a seção corrente sob demanda |
| `/profile` | `ProfileExtractor` ([src/radar/core/ingestion/profile_extractor.py:190](../../src/radar/core/ingestion/profile_extractor.py#L190)) — já é um produtor isolado, third mode fora do workspace hoje | Vira comando dentro do MESMO chat, em vez de fluxo separado de onboarding |
| `/boilerplate` | Gap conhecido (perfil←proposta é parcial) | **Fora de escopo** desta spec — não construir agora |
| `/archive` | Sem equivalente identificado | **Fora de escopo** — sem demanda de produto ainda |

**Decisão de design: `/plan` deixa de ser skill-par e vira fase interna do `/grant-writing`.** Racional: o Grantable não tem um "/plan" separado — planejamento é uma das 5 fases do `/grant-writing`. Hoje o `/plan` do Radar já é consumido *como dado* pela escrita (grava em `section_drafts["__plan__"]`, lido como outline); a mudança é só deixar de expor isso como modo peer com parede própria.

---

## Desenho da cirurgia

### 1. Trocar `_REDIRECT_BLOCK` por uma instrução de transição fluida

Em vez de "recuse e devolva mensagem fixa", o novo bloco instrui o modelo a **reconhecer a intenção e confirmar a transição**, deixando o *código* (não o modelo) decidir se troca de produtor:

```
MODO ATUAL: /{mode}
Se o usuário pedir algo de outra skill (ex.: escrever estando em /explorer),
NÃO recuse — responda reconhecendo o pedido e informe que está trocando para
/{skill} para atendê-lo. O sistema troca de contexto automaticamente.
```

A detecção de "isto pertence a outra skill" já existe parcialmente: `src/radar/core/services/explore_routing.py::route_message` (keywords + classificador LLM opcional) resolve *dentro* do explore quando a pergunta é de escrita. Generalizar esse roteador para os 3(agora N) produtores é o coração técnico da task.

### 2. `/profile` entra no chat do workspace

Hoje `ProfileExtractor` roda num fluxo separado (onboarding). Task: expor como 4º branch do `dispatch()` (`src/radar/core/services/workspace_service.py:171`), reusando o produtor tal como está — sem tocar `src/radar/core/llm/agent_graph.py` nem o runtime compartilhado (é aditivo, mesmo padrão dos itens da trilha LangGraph).

### 3. `/review` — promover o Critic a comando do usuário

Hoje `run_critic` só dispara dentro de `save_draft`. Task: nova branch de dispatch que chama `run_critic` diretamente sobre a seção corrente da sessão de escrita ativa (sem gravar nada — é consulta, não side-effect). **Não toca o contrato interno do critic** (mesmo `run_subagent`, mesmo toolset estreito `read_target_context`/`read_company_profile`/`read_proposal_sections`).

### 4. `/plan` dissolve — sem branch própria no dispatch

Task: remover `MODE_PLAN`/`_dispatch_plan` como modo peer; `generate_plan` (`src/radar/core/kg/planning_node.py`) vira uma **tool ou chamada interna** do fluxo de escrita, acionada quando o usuário pede "planeje" dentro de `/grant-writing` (ou automaticamente no primeiro turno, como já ocorre parcialmente via `_first_turn_with_generation`). Isto elimina a inconsistência estrutural (modo que não é grafo) e o "menos contexto do que a função suporta" (`analysis=""`) encontrado nesta sessão.

---

## Fora de escopo (explícito)

- **Qualquer troca dinâmica de toolset DENTRO de uma thread viva do LangGraph.** O toolset é vinculado (`bind_tools`) na compilação do grafo (`agent_graph.py:145`) — trocar de skill continua significando **trocar de produtor/recompilar**, não um mecanismo de "progressive disclosure" à la Agent SDK `SKILL.md`. Tentar replicar isso à mão é o harness-smell que a trilha já rejeitou (mesma classe de decisão do Item #2 arquivado).
- **Playbooks** (`src/radar/core/skills.py`) — intocados. Skill (o que fazer) e playbook (como escrever, uma vez dentro do "fazer") são eixos ortogonais; nenhuma skill nova muda o merge de competência por mecanismo/agência.
- **`/boilerplate` e `/archive`** — sem produtor existente; não nascem nesta spec.
- **Item #3 (threads) e seu lote final de gate** — trilha paralela, não tocada aqui.
- **Migração para Claude Agent SDK** — decisão já tomada (não agora); esta spec não a reabre.

---

## Validação desta fase

Por instrução do Lucas: **sem eval/golden longo.** Critério de aceite por task = teste de *wiring*, análogo ao `--limit 1`:
- Cada skill nova/realocada dispara o produtor certo com o toolset certo (asserção de montagem, sem chamar LLM em massa).
- Transição fluida testada com 1 caso real por par de skills (ex.: pedir escrita estando em explorer) — confirma troca de produtor, não qualidade da resposta.
- Gate de qualidade (a resposta pós-transição é boa, o `/review` sob demanda é útil) fica **diferido para o lote final**, junto com o Item #3 e o `estilo_escrita`.

---

## Próximo passo

Planejamento de tasks (Opus 4.8) a partir desta spec — decompor em tasks autocontidas (provavelmente: 1 roteador generalizado, 1 skill `/profile` no dispatch, 1 skill `/review` sob demanda, 1 dissolução do `/plan`), com critério de aceite verificável sem tokens além do wiring mínimo.
