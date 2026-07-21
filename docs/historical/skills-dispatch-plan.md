# Plano de tasks — dispatch de skills sem parede (cirúrgico)

**Status:** implementado e mergeado · **Data:** 2026-07-20 · **Planejador:** Opus 4.8
**Fonte:** [docs/specs/skills-dispatch-cirurgico.md](../specs/skills-dispatch-cirurgico.md)
**Fluxo SDD:** spec (Fable) → **este plano (Opus)** → implementação (Sonnet 5) → gate/git (Fable).

**Restrição desta fase (Lucas):** zero eval/golden longo. Critério de aceite por task = **wiring** — a
skill dispara o produtor/toolset certo, provado por asserção de montagem (stub, sem LLM) e, no máximo,
**1 caso real por transição** (`--limit 1`). Qualidade de resposta fica para o lote final (com Item #3 +
`estilo_escrita`).

---

## Decisões de escopo travadas nesta sessão (Lucas, 2026-07-20)

Verificação de código revelou que **existem dois geradores de plano** escrevendo a mesma chave
`section_drafts["__plan__"]` com **schemas divergentes**:

| Gerador | Schema | Acionado por |
|---|---|---|
| `core/kg/planning_node.py::generate_plan` | `{title, sections, alignment, compliance_hints}` | modo `/plan` ([workspace_service.py:365](../../core/services/workspace_service.py#L365)) **e** [backend/routers/planning.py:38](../../backend/routers/planning.py#L38) |
| `writing_session._generate_plan_first_turn` | `{title, sections, critical_questions, mismatch_warnings}` | F4 plan-first, 1º turno da escrita ([writing_session.py:1824](../../core/services/writing_session.py#L1824)) |

Diante disso, Lucas travou o caminho **mínimo/cirúrgico**:

- **D1 — dissolução do `/plan` = só o modo-peer do chat.** Remove `MODE_PLAN` do dispatch do workspace,
  de `VALID_MODES`, o comando `/plan` da caixa de texto e o redirect de `explore_routing`. **Mantém** o
  router `/planning` e a página `/workspace/planning` como superfícies separadas — repontando o "adjust"
  delas para fora do endpoint `/mode`.
- **D2 — `planning_node.generate_plan` fica quieto.** `writing_session._generate_plan_first_turn` é o
  planner canônico do fluxo de escrita. `generate_plan` **não** é wireado no chat nem vira tool do writing
  (o toolset do writing fica **intocado**); sobrevive apenas atrás do router `/planning`. Evita drift de
  schema e não amplia superfície do agente.

**Débito registrado (fora desta fase):** os dois schemas de `__plan__` continuam coexistindo e a página
standalone `/workspace/planning` continua viva. Consolidar/aposentar isso é follow-up, não desta spec.

---

## Âncoras de código confirmadas (2026-07-20)

- Dispatcher e a "parede": [workspace_service.py:171](../../core/services/workspace_service.py#L171)
  (`dispatch`), `_REDIRECT_BLOCK` em [:29](../../core/services/workspace_service.py#L29),
  `MODE_CONFIG` em [:41](../../core/services/workspace_service.py#L41).
- Roteador puro: [explore_routing.py:107](../../core/services/explore_routing.py#L107) (`route_message`),
  [:182](../../core/services/explore_routing.py#L182) (`redirect_for` — hoje devolve string de recusa),
  intents `PLAN_ACTION`/`WRITING_ACTION` em [:28-29](../../core/services/explore_routing.py#L28).
- `route_message` só é chamado hoje dentro de
  [`_dispatch_explorer`:284](../../core/services/workspace_service.py#L284).
- Toolset é estático por compilação (`bind_tools`): [agent_graph.py:145](../../core/llm/agent_graph.py#L145)
  — **confirma** o fora-de-escopo de troca dinâmica de toolset numa thread viva.
- Critic sob demanda: [critic_agent.py:312](../../core/llm/agent_tools/critic_agent.py#L312)
  (`run_critic(draft, section_title, session, trace_context=None)`), hoje só chamado dentro de `save_draft`
  ([writing_tools.py:373](../../core/llm/agent_tools/writing_tools.py#L373)).
- Perfil: [profile_extractor.py:190](../../core/ingestion/profile_extractor.py#L190) (`ProfileExtractor`),
  `.extract(url, agent_enabled=False)` em [:206](../../core/ingestion/profile_extractor.py#L206); `extract_from_text`
  em [:231](../../core/ingestion/profile_extractor.py#L231). Filosofia: **só retorna sugestão, nunca salva.**
- Endpoint do modo: [backend/routers/workspace.py:42](../../backend/routers/workspace.py#L42)
  (valida `req.mode not in VALID_MODES` → 422).
- Frontend: `parseCommand` (regex `explorer|plan|escrita|help`) em
  [page.tsx:75](../../frontend/src/app/workspace/[sessionId]/page.tsx#L75); `WELCOME_BY_MODE` em
  [:47](../../frontend/src/app/workspace/[sessionId]/page.tsx#L47); troca de modo em
  [:284-296](../../frontend/src/app/workspace/[sessionId]/page.tsx#L284).
- Página standalone que quebra ao remover `MODE_PLAN`: `handleAdjustWithChat` chama
  `workspaceMode(sessionId, "plan", …)` em
  [planning/page.tsx:157](../../frontend/src/app/workspace/planning/page.tsx#L157); linkada de
  [page.tsx:103](../../frontend/src/app/page.tsx#L103) e
  [WorkspaceHeader.tsx:57](../../frontend/src/components/workspace/WorkspaceHeader.tsx#L57).

---

## Ordem e dependências

Cadeia linear (minimiza conflito — todas tocam `workspace_service.py` e/ou o frontend do chat):

```
Task 4 (dissolve /plan)  →  Task 1 (roteador fluido)  →  Task 2 (/profile)  →  Task 3 (/review)
```

- **Task 4 primeiro:** remove o `/plan` para que o roteador da Task 1 não precise mapear/rotear para um
  modo que vai deixar de existir.
- **Task 1 depende de 4:** o handoff mapeia `PLAN_ACTION → escrita` (escrita absorve o plano); só faz
  sentido depois que `/plan` deixou de ser modo-peer.
- **Task 2 introduz o scaffolding "ação one-shot vs modo sticky"** (`VALID_ACTIONS`) que a **Task 3 reusa**.

Distinção estrutural introduzida na Task 2 e usada por 2+3:

- **Modos sticky** (`VALID_MODES` = `{explorer, escrita}` após a Task 4): mudam `activeMode`, filtram
  histórico por modo, o usuário "fica" neles.
- **Ações one-shot** (`VALID_ACTIONS` = `{profile, review}`): executam e retornam, **sem** mudar o modo
  ativo (mesmo padrão do `/help`). O endpoint aceita `mode ∈ VALID_MODES ∪ VALID_ACTIONS`; o campo `mode`
  na resposta continua sendo o modo sticky corrente.

---

## Task 4 — Dissolver `/plan` (modo-peer)

**Objetivo:** eliminar a inconsistência estrutural (um "modo" que não é grafo) removendo `/plan` do
dispatch do chat, **sem** tocar `generate_plan` nem aposentar a superfície standalone (D1/D2).

**Arquivos:**
- `core/services/workspace_service.py` — remover `MODE_PLAN`, sua entrada em `MODE_CONFIG`, o branch
  `elif mode == MODE_PLAN` no `dispatch()`, `_dispatch_plan`, `_adjust_plan`, `_format_plan_response`
  (mover a lógica de ajuste — ver abaixo); ajustar `VALID_MODES` → `{explorer, escrita}` e a mensagem de
  erro em [:200](../../core/services/workspace_service.py#L200); tirar `/plan` dos textos de `welcome`.
- `core/services/explore_routing.py` — em `redirect_for`, remover o branch `PLAN_ACTION` (a Task 1
  reescreve esta função; se a Task 1 vier junta, coordenar). O intent `PLAN_ACTION` **permanece** no enum
  (Task 1 o remapeia para escrita).
- `backend/routers/workspace.py` — nada além do que já vem de `VALID_MODES` (import inalterado).
- `backend/routers/planning.py` — **NOVO endpoint** `POST /planning/{session_id}/adjust` que recebe a
  instrução de ajuste e reusa a lógica que hoje está em `_adjust_plan` (ajuste 1-shot do `__plan__` via
  LLM). É o novo lar do "adjust" que sai do endpoint `/mode`.
- `frontend/src/app/workspace/[sessionId]/page.tsx` — `parseCommand` regex → `explorer|escrita|help`;
  remover `plan` de `WELCOME_BY_MODE` e do tipo `wsMode`.
- `frontend/src/app/workspace/planning/page.tsx` — repontar `handleAdjustWithChat` de
  `workspaceMode(sessionId, "plan", …)` para o novo `POST /planning/{id}/adjust` (nova fn em `api.ts`).

**Não implementar:** deletar `generate_plan`; deletar a página `/workspace/planning` ou o router
`/planning`; unificar os dois schemas de `__plan__` (débito registrado).

**Critério de aceite (wiring):**
1. `"plan" not in VALID_MODES`; `dispatch(mode="plan", …)` retorna erro de modo inválido (sem exceção).
2. `POST /planning/generate` continua funcionando **inalterado** (asserção: mesma resposta para o mesmo
   input — stub de `generate_plan`, sem LLM).
3. `POST /planning/{id}/adjust` invoca a lógica de ajuste (stub do LLM de ajuste → asserção de chamada) e
   persiste em `__plan__`; a página standalone deixa de chamar o endpoint `/mode`.
4. `parseCommand("/plan foo")` não retorna `command: "plan"` (não há mais esse literal no union).
5. `tsc --noEmit` limpo (não `npm run build` — dev server ativo, ver memória).

---

## Task 1 — Roteador de transição fluida (coração técnico)

**Objetivo:** trocar a **recusa** ("digite /escrita") por **reconhecer + trocar de produtor**. O *código*
decide o handoff (não o modelo); trocar de skill = chamar outro produtor (dispatch fresco), **nunca** troca
dinâmica de toolset numa thread viva do LangGraph.

**Arquivos:**
- `core/services/explore_routing.py` — substituir `redirect_for` (que devolve string de recusa) por uma
  função pura de **destino de handoff**, ex.:
  `handoff_target(decision: RouteDecision, current_mode: str) -> str | None` — retorna o modo-produtor
  alvo, ou `None` se o alvo == modo atual (fica). Mapeamento após a Task 4:
  `WRITING_ACTION → escrita`, `PLAN_ACTION → escrita` (escrita absorve o plano), intents factuais/de
  exploração (`EDITAL_*`, `ENTITY_FACT`, `DISCOVERY`, `MATCH_PROFILE`, `CONCEPTUAL`) → `explorer`. Função
  pura, sem I/O — testável isolada.
- `core/services/workspace_service.py` —
  1. **Subir `route_message` para o `dispatch()`** (roda uma vez, com `RouteContext(mode=modo_atual, …)`),
     em vez de só dentro de `_dispatch_explorer`. Passar a `RouteDecision` adiante para o `_dispatch_explorer`
     (que já a consome via `route_decision=` em [:328](../../core/services/workspace_service.py#L328)) para
     não duplicar classificação.
  2. Se `handoff_target(...)` != modo atual e != `None`: chamar o **produtor alvo** (`_dispatch_escrita`
     ou `_dispatch_explorer`) e **prefixar** um reconhecimento curto de transição
     (ex.: `"↪ Entendi que você quer escrever — troquei para /escrita.\n\n"`). O campo `mode` da resposta
     passa a ser o **modo alvo** (para o front sincronizar).
  3. Trocar o texto de `_REDIRECT_BLOCK` (hoje "recuse educadamente") pela instrução de **transição
     fluida** da spec (reconhecer o pedido e informar a troca — "o sistema troca de contexto
     automaticamente"). Após a Task 4 esse bloco só é injetado em `_dispatch_escrita`
     ([:489](../../core/services/workspace_service.py#L489)).
- `frontend/src/app/workspace/[sessionId]/page.tsx` — **honrar `res.mode`** após a resposta: quando o
  backend fizer handoff fluido (sem o usuário digitar slash-command), atualizar `wsMode` a partir de
  `res.mode`. Pequeno, mas fecha o loop visual.

**Pares de transição em escopo (produtores que existem pós-spec):** `explorer → escrita` (pedir escrita
enquanto explora) e `escrita → explorer` (fazer pergunta factual enquanto escreve). `profile`/`review` são
disparados por comando explícito (Tasks 2/3), **não** por linguagem natural — roteamento NL para eles fica
fora de escopo.

**Não implementar:** troca de toolset dentro de uma thread viva (`bind_tools` é estático —
[agent_graph.py:145](../../core/llm/agent_graph.py#L145)); roteamento NL para `/profile` ou `/review`.

**Critério de aceite (wiring):**
1. Teste unitário puro de `handoff_target`: tabela intent×modo_atual → alvo esperado (inclui
   `WRITING_ACTION@explorer→escrita`, `EDITAL_FACT@escrita→explorer`, `WRITING_ACTION@escrita→None`).
   Sem LLM.
2. `dispatch(mode="explorer", message="escreva a seção de impacto")` **invoca o produtor de escrita**
   (monkeypatch `_dispatch_escrita`/`WritingSession.turn` → stub; asserção de que foi chamado) e o campo
   `mode` da resposta == `"escrita"`. Sem LLM em massa.
3. `dispatch(mode="escrita", message="qual o prazo do edital?")` invoca o explorer (stub, asserção).
4. O texto de `_REDIRECT_BLOCK` não contém mais "recuse"/"NÃO tente executar"; contém a instrução de
   transição.
5. (opcional, ≤1 LLM call/par) 1 smoke real por par confirmando que a troca produz uma resposta do
   produtor certo — **não** avalia qualidade.

---

## Task 2 — `/profile` como ação no chat do workspace

**Objetivo:** expor `ProfileExtractor` como ação one-shot dentro do mesmo chat, reusando o produtor como
está. **Só retorna sugestão de perfil — nunca salva** (filosofia "AI drafts, human reviews").

**Arquivos:**
- `core/services/workspace_service.py` — introduzir `VALID_ACTIONS = frozenset({"profile", "review"})` e
  um branch de ação no `dispatch()` (ou um `dispatch_action` paralelo) para `profile`:
  - parsear URL da `message`; se houver URL → `ProfileExtractor().extract(url)` →
    formatar `ExtractResult.profile` + `confidence` como texto legível de **sugestão**;
  - sem URL → responder pedindo a URL (ou ecoar o perfil atual, se já houver) — **sem** chamar LLM à toa;
  - **não** persiste nada; `mode` da resposta continua o modo sticky corrente.
- `backend/routers/workspace.py` — endpoint aceita `req.mode ∈ VALID_MODES ∪ VALID_ACTIONS`; ações não
  disparam `mode_welcome` sticky.
- `frontend/src/app/workspace/[sessionId]/page.tsx` — `parseCommand` reconhece `/profile`; `runTurn` trata
  como one-shot (envia ao endpoint, **não** muda `wsMode`, como o `/help`).

**Não implementar:** salvar/aplicar o perfil (isso é o diff unificado já existente no explore_turn +
gate `CNPJ_LOOKUP_ENABLED` — fora de escopo); onboarding novo; caminho `agent_enabled=True`.

**Critério de aceite (wiring):**
1. `dispatch(mode="profile", message="https://acme.com")` chama `ProfileExtractor.extract` com a URL
   (monkeypatch → stub `ExtractResult`; asserção de chamada + de que a URL foi repassada). Sem LLM.
2. A resposta contém os campos sugeridos + confiança e **nenhuma** escrita em `writing_sessions`/perfil
   (asserção de que nenhum `.update(...)`/save foi chamado).
3. `mode` da resposta == modo sticky anterior (ação não muda o modo).
4. `parseCommand("/profile https://x")` reconhece o comando; `wsMode` não muda no front.

---

## Task 3 — `/review` sob demanda (Critic como comando)

**Objetivo:** promover o Critic a comando do usuário. Hoje `run_critic` só roda dentro de `save_draft`;
o usuário nunca pode pedir "revise agora". `/review` dispara `run_critic` sobre a **seção corrente** —
**consulta, sem side-effect** (não grava, não muda o outline).

**Arquivos:**
- `core/services/workspace_service.py` — branch de ação `review` (reusa o `VALID_ACTIONS` da Task 2):
  - instanciar `WritingSession` (mesmo padrão de `_dispatch_escrita`,
    [:480](../../core/services/workspace_service.py#L480));
  - resolver a seção-alvo: título passado como arg (`/review <título>`); se vazio, usar a seção corrente
    (o front passa o `targetTitle` — [page.tsx:64](../../frontend/src/app/workspace/[sessionId]/page.tsx#L64));
    se ambíguo/ausente, retornar lista curta do outline pedindo qual seção (sem chamar o critic);
  - ler o conteúdo salvo dessa seção de `section_drafts` e chamar
    `run_critic(content, target_title, session)` (mesmo contrato interno, **sem** tocar o toolset do
    critic);
  - formatar `CriticResult` (`approved`, `issues`, `feedback`) como texto; **nenhum** `set_section_content`,
    **nenhum** save.
- `backend/routers/workspace.py` — já coberto pela Task 2 (`VALID_ACTIONS`).
- `frontend/src/app/workspace/[sessionId]/page.tsx` — `parseCommand` reconhece `/review`; one-shot,
  não muda `wsMode`; envia o `targetTitle` corrente como arg quando o usuário não especifica seção.

**Não implementar:** persistir o veredito do critic; mudar o contrato/toolset interno do critic; rodar
critic em todas as seções (é 1 seção por chamada).

**Critério de aceite (wiring):**
1. `dispatch(mode="review", message="Impacto")` chama `run_critic` com `(<conteúdo da seção>, "Impacto",
   session)` (monkeypatch `run_critic` → `CriticResult` stub; asserção de chamada e dos args). ≤1 LLM call
   real se o smoke opcional for rodado; a asserção de montagem não precisa de LLM.
2. Nenhuma escrita: asserção de que `set_section_content`/`.update(section_drafts)` **não** foi chamado.
3. `/review` sem arg e com `targetTitle` presente usa essa seção; sem seção resolvível → resposta lista o
   outline e **não** chama o critic.
4. `parseCommand("/review Impacto")` reconhece o comando; `wsMode` não muda.

---

## Fora de escopo (explícito, para todas as tasks)

- **Troca dinâmica de toolset dentro de uma thread viva do LangGraph** — `bind_tools` é estático por
  compilação ([agent_graph.py:145](../../core/llm/agent_graph.py#L145)). Trocar de skill = trocar de
  produtor (dispatch fresco), não progressive disclosure à la Agent SDK.
- **`core/skills.py` (playbooks)** — intocados. Skill (o que fazer) e playbook (como escrever) são eixos
  ortogonais.
- **`/boilerplate` e `/archive`** — sem produtor existente; não nascem aqui.
- **Deletar `generate_plan`, unificar os dois schemas de `__plan__`, aposentar a página
  `/workspace/planning`** — débito registrado (D1/D2), follow-up.
- **Salvar/aplicar perfil e veredito do critic** — as ações `/profile` e `/review` só retornam sugestão
  ("AI drafts, human decides").
- **Item #3 (threads), `estilo_escrita` e o lote final de gate de qualidade** — trilha paralela; a
  qualidade das respostas pós-transição, do `/review` e do `/profile` é avaliada lá, não aqui.

---

## Resumo da malha de arquivos

| Arquivo | T4 | T1 | T2 | T3 |
|---|:--:|:--:|:--:|:--:|
| `core/services/workspace_service.py` | ● | ● | ● | ● |
| `core/services/explore_routing.py` | ● | ● | | |
| `backend/routers/workspace.py` | | | ● | (T2) |
| `backend/routers/planning.py` | ● (adjust) | | | |
| `frontend/.../[sessionId]/page.tsx` | ● | ● | ● | ● |
| `frontend/.../planning/page.tsx` | ● | | | |
| `frontend/src/lib/api.ts` | ● (adjust fn) | | | |

Como todas as tasks tocam `workspace_service.py` e o `page.tsx` do chat, **implementar na ordem
4 → 1 → 2 → 3** e rebasear entre elas para manter os diffs pequenos e o gate por task limpo.
