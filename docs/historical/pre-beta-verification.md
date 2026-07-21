# Spec: Verificação pré-beta — leak-test multi-tenant + regressão de grounding

> **Registro histórico:** investigação concluída. O inventário de isolamento
> vigente está em [`docs/reference/tenant-isolation.md`](../reference/tenant-isolation.md)
> e gates externos adiados estão em [`docs/BACKLOG.md`](../BACKLOG.md).

Status original: proposta · 2026-07-02 · origem: priorização pós-hardening (conversa
Fable 5). Complementa [hardening-pre-beta.md](hardening-pre-beta.md) — aquela
spec adiciona defesas; esta VERIFICA que o isolamento e a qualidade existentes
de fato seguram, antes do beta externo.

## Decisão de sequência

Trabalho **sequencial**, não paralelo — cada frente exige um modo distinto
(adversarial / forense) e contexto profundo numa sessão só:

1. **Frente 1 — leak-test de isolamento multi-tenant** (P0, entregável fechado)
2. **Frente 2 — regressão de grounding 0.92→0.17** (forense, depois da 1)
3. **Frente 3 — gates de eval pendentes**: não é frente, é **regra de operação**
   (ver ao final). Se dissolve no fluxo; não tem entregável próprio.

---

## Fatos verificados no código (2026-07-02, branch feat/hardening-pre-beta)

| # | Fato | Evidência |
|---|---|---|
| V1 | Checkpointer (`AsyncPostgresSaver`) e Store conectam via `DATABASE_URL` (role postgres → **bypassa RLS**). Isolamento = namespacing puro | `core/llm/agent_graph.py:546-613` |
| V2 | `thread_id` = `"{workspace_id}:{session_id}:{turn_index}"` (turnos) e `"{ws}:{sid}:generation"` (batch) | `core/services/writing_session.py:1112,1247`, `agent_graph.py:498` |
| V3 | Store de memória cross-session: namespace `(workspace_id, "insights")`; `memory_put/delete/search` recebem `workspace_id` do caller | `core/llm/agent_graph.py:690-698,778-815` |
| V4 | Tabelas do checkpointer+Store vivem no schema `agent_memory`, fora da lista do PostgREST (`public, storage, graphql_public`) → invisíveis pela REST API por construção. Migration 027 (band-aid RLS em `public`) obsoleta | `supabase/migrations/028_agent_memory_schema.sql` |
| V5 | `get_db` devolve cliente Supabase carregando o JWT do usuário → RLS avaliado por request. Esta é a camada real de defesa nas tabelas `public` | `core/infra/auth.py:194-219` |
| V6 | `workspace_id` é derivado server-side (`get_workspace_id(db, user_id)`, user_id do JWT); `WritingSession._load_from_db` rejeita mismatch de workspace | `backend/routers/writing.py:180,235`, `writing_session.py:628` |
| V7 | DEMO_MODE usa service-role (RLS bypass); scoping vira responsabilidade exclusiva dos handlers | `core/infra/auth.py:148-149,208-214` |
| V8 | Migrations com policy "own" por workspace: workspaces, content_items, writing_sessions, session_turns, application_log/events, matching_weights, reflection_insights, weight_change_log, research_findings, user_feedback, exploration_log, company_hypergraphs. `read_authenticated` (dado compartilhado): edital_chunks, discovered_opportunities, web_sources, playbook_overlays. **Não conferidas policy-a-policy**: kg_artifacts (016), edital_source_docs (032), conversations (020), agent_writing_state (014) — o inventário completo é parte da Frente 1 | grep em `supabase/migrations/` |
| V9 | Eval de grounding: suíte `writing` (`python -m radar.core.eval writing`), evaluator `eval_grounding` per-claim; prereqs SUPABASE+LLM+`EVAL_WORKSPACE_ID` | `core/eval/writing.py:119,155,213` |

---

## Frente 1 — Leak-test de isolamento multi-tenant (P0)

**Risco que cobre**: vazamento de dados entre workspaces é o único bug capaz de
matar a confiança no produto no primeiro mês de beta. Marcado como risco-mor da
migração LangGraph desde 2026-06-25; nunca testado adversarialmente.

**Modelo de ameaça**: usuário autenticado do workspace B tentando ler/escrever
estado do workspace A. Quatro superfícies, cada uma com mecanismo de defesa
diferente — o teste ataca as quatro:

### S1 — PostgREST direto (supabase-js + anon key + JWT de B)
A defesa é RLS (V5/V8). Trabalho:
- Inventário completo: para CADA tabela em `public`, listar policy efetiva
  (own / read_authenticated / deny-all / **sem RLS = furo**). Fechar os "não
  conferidos" de V8.
- Teste automatizado: com dois usuários/workspaces de teste, tentar
  select/insert/update/delete cross-workspace em cada tabela via PostgREST.
  Esperado: 0 rows / erro. Qualquer row de A visível para B = falha.
- Confirmar que `agent_memory` segue fora da lista de schemas do PostgREST
  (`supabase/config.toml` local + config do projeto remoto — são configs
  separadas; a remota é a que importa).

### S2 — API FastAPI (endpoints com IDs de recurso)
A defesa é scoping nos handlers (V6). Trabalho:
- Enumerar endpoints que recebem ID de recurso (session_id, conversation_id,
  item_id da library, application_id, exploration…) e testar: JWT de B + ID de
  A → esperado 403/404, nunca 200 com dado de A.
- Atenção especial ao resume de interrupt (thread_id persistido no pendente):
  confirmar que o cliente nunca fornece thread_id — só o servidor monta (V2).

### S3 — Camada agêntica (checkpointer + Store, sem RLS por design)
A defesa é namespacing (V1-V3). Trabalho:
- Auditar todo call site de `memory_put/search/delete` e de montagem de
  `thread_id`: o `workspace_id` vem SEMPRE de objeto server-side (sessão
  carregada do DB), nunca de input do usuário ou de output do modelo
  (prompt injection não pode escolher namespace).
- Confirmar que subagentes/caminho stateless (`checkpointer=False`) não herdam
  checkpointer do pai (comentário em `agent_graph.py:421-427` — validar com
  teste, não só comentário).

### S4 — DEMO_MODE (service-role, RLS zero)
- Confirmar o guard de ambiente do PR1 do hardening; garantir que beta externo
  roda com `DEMO_MODE=0` (checklist de deploy, não só código).

**Entregáveis**:
1. Inventário tabela × policy (seção neste doc ou `docs/security/` — decidir).
2. Suíte `tests/integration/test_tenant_isolation.py` (integration, dois usuários de teste,
   roda contra Supabase local; documentar como rodar contra staging).
3. Fixes do que o teste pegar (PRs pequenos, um por furo).
4. Wiring no CI (job opcional/manual se depender de Supabase local).

**Critério de done**: suíte verde nas 4 superfícies + inventário sem tabela
`public` órfã de política.

---

## Frente 2 — Regressão de grounding (0.92→0.17)

> **RESOLVIDA (2026-07-02): era artefato de eval, não regressão.** Ver "Resultado"
> ao final desta seção. A premissa abaixo (baseline 0.92) não se sustenta — o
> número nunca reproduziu e a métrica é instável sobre o fixture. Mantido o texto
> original para rastreabilidade.

**Contexto**: queda observada na main após o merge dos agent patterns
deepagents (a1c8d5308, 2026-06-13); investigação ficou em suspenso.

**Hipótese nova (2026-07-02)**: os dois bugs corrigidos hoje — vazamento do
texto do `_REFLECT_PROMPT` como resposta final e outline invisível no chat
livre — são candidatos fortes a causa raiz: reflexão interna vazada como
"seção" produz claims sem grounding nenhum. **Passo 1 é re-rodar, não
bisectar.**

**Sequência**:
1. Rodar `python -m radar.core.eval writing` na branch atual (prereqs V9).
2. Se grounding recuperou (≥ ~0.9): documentar causa = bugs de 2026-07-02,
   fechar a investigação, registrar baseline novo.
3. Se NÃO recuperou: bisect `a1c8d5308..main` rodando a suíte (custo LLM por
   iteração — usar `--limit` para triagem, full run só na confirmação).
4. Fix ou issue documentada com reprodução.

**Critério de done**: causa raiz identificada + grounding de volta ao patamar
~0.9 (ou decisão explícita documentada de por que o patamar mudou).

### Resultado (2026-07-02, HEAD e61fa2985 — `eval_results/20260702_173942_writing.json`)

Re-run no HEAD atual (com os 2 fixes de hoje). **Recuperou parcialmente, NÃO ao
patamar 0.9:**

| Métrica | Valor |
|---|---|
| `mean_pct_grounded` | **0.434** (era ~0.055 na corrida ambígua de 13:46 → os fixes ajudaram MUITO) |
| `mean_coherent` | **0.167** (1 de 6 — qualidade ruim, eixo separado) |
| `mean_n_factual_errors` | 0.83 / caso |

Por caso (grounding × tool-use):

| Caso | pct_grounded | turns | tools | latência |
|---|---|---|---|---|
| finep774 descrição | 0.333 | 1 | **0** | 261s |
| finep774 justificativa | (0 claims) | 3 | 3 | 100s |
| finep774 metodologia | 0.25 | 2 | 2 | 585s |
| finep769 objetivo | 0.125 | 1 | **0** | 164s |
| finep769 justificativa | 0.714 | 2 | 2 | 277s |
| finep769 metodologia | 0.75 | 2 | 3 | 307s |

**Achado (mais barato que bisect):** os 2 piores casos (descrição 0.33, objetivo
0.12) são os que rodaram em **1 turno com `tools=0`** — o caminho de **geração em
lote do 1º turno** (`_first_turn_with_generation`), que escreve as 8 seções sem
chamar `search_edital` por seção. Os casos que retrieveram (`tools>0`) pontuam
melhor (0.71, 0.75). Hipótese de mecanismo: **a geração em lote não se ancora em
retrieval por seção** → claims sem respaldo. Não é monotônico (metodologia774
teve tools=2 e 0.25), então retrieval ajuda mas não é a história toda.

**O baseline 0.92 é fantasma — regressão JÁ resolvida como artefato (jun/2026).**
A premissa desta frente (queda 0.92→0.17) foi investigada e **fechada em
2026-06-13/14** como **artefato de eval**, não regressão de produto
([[project_agent_patterns_deepagents]]):

- O "0.92" **nunca reproduziu** — melhor real medido = 0.625, também sobre 2 casos.
- `pct_grounded` é **instável sobre o fixture de 6 casos**: o nº de casos com
  claims extraíveis varia por rodada (2→4→3), fazendo o agregado oscilar
  **0.05–0.625 SEM nada mudar de fundo** (drafts estocásticos + fixture minúsculo).
- Retriever/chunker estavam saudáveis; os suspeitos de commit (RRF, tiktoken)
  foram refutados manualmente.

Os **0.434 de hoje caem dentro dessa faixa de ruído conhecida.** Os 2 fixes de
hoje empurraram o número pra cima do piso (0.055→0.434), coerente. Logo:

**Decisão: bisect NÃO se justifica** — seria caçar ruído contra um baseline que
não existe. A premissa da spec (0.92→0.17) precedeu/ignorou a resolução de junho.
Fechamento: *causa raiz = instrumento instável, não regressão*. O trabalho real
(no BACKLOG desde junho) é **tornar o gate confiável** (N-runs/temp fixa, fixture
maior, denominador significativo) antes de tratar `pct_grounded` como barra de
qualidade — e os resíduos de escrita (mischaracterizar escopo, inventar passo
procedural). Sinal novo desta corrida: os 2 piores casos são `tools=0` (geração
em lote do 1º turno sem retrieval por seção) — alavanca concreta se formos mexer.
Latência é eixo à parte (já coberta por "geração paralela" na spec de hardening).

---

## Frente 3 — Regra de operação: gates pendentes

Não é workstream. Regra: **branch retomada com gate de eval pendente → o gate
roda ANTES de qualquer código novo nela.** Pendências conhecidas (2026-07-02):

- `feat/elig-constraints-producer` — 3 gates NÃO rodados (PR2+PR3 WIP)
- agentic-evolution F2/F3A (PRs #36/#37) — gates de env não rodados
- Eval de escrita como gate da remoção do legacy (spec robustez)

## Fora de escopo

- Pentest externo / infra (hosting Docker, Cloudflare, Vercel) — só a camada aplicação+dados.
- Hardening novo (rate limit, SSRF etc.) — já coberto pela spec de hardening.
- Refatorar o modelo de isolamento (ex.: RLS no checkpointer) — só se o
  leak-test provar furo que namespacing não resolve.
