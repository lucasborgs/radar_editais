# Isolamento multi-tenant — inventário e leak-test

Status: 2026-07-02 · origem: [pre-beta-verification.md](../specs/pre-beta-verification.md)
Frente 1 (P0). Este doc é o **entregável de inventário**: tabela × política
efetiva, as quatro superfícies de defesa, e o furo P0 encontrado + corrigido.

Suíte que verifica tudo aqui: [`tests/test_tenant_isolation.py`](../../tests/test_tenant_isolation.py).

## Modelo de ameaça

Usuário autenticado do workspace **B** tentando ler/escrever estado do workspace
**A**. Quatro superfícies, cada uma com um mecanismo de defesa distinto:

| Superfície | Vetor | Defesa |
|---|---|---|
| **S1** PostgREST direto | supabase-js + anon key + JWT de B | RLS (Postgres) |
| **S2** API FastAPI | endpoint com ID de recurso de A | scoping server-side nos handlers (`get_workspace_id` a partir do JWT) |
| **S3** Camada agêntica | checkpointer + Store (bypassam RLS por design) | namespacing por `workspace_id` / `thread_id` |
| **S4** DEMO_MODE | service-role (RLS zero) | guard de ambiente (recusa boot em prod) |

## S1 — Inventário tabela × política (schema `public`)

Autoritativo, lido do Postgres **após** a migration 034. Todas as tabelas `public`
têm **RLS habilitada** (nenhuma órfã sem RLS). Colunas: policy efetiva e comando
coberto (`[*]`=ALL, `[r]`=SELECT, `[a]`=INSERT, `[w]`=UPDATE, `[d]`=DELETE).

### Workspace/user-scoped (isolamento por tenant — política "own")

| Tabela | Política | Escopo |
|---|---|---|
| `workspaces` | select/insert/update own | `auth.uid() = user_id` (sem DELETE — fail-closed, ver nota) |
| `content_items` | own `[*]` | `workspace_id ∈ (workspaces do user)` |
| `writing_sessions` | own `[*]` | idem |
| `session_turns` | own `[*]` | via join `writing_sessions → workspace` |
| `application_log` | own `[*]` | `workspace_id ∈ …` |
| `application_events` | own `[*]` | via join `application_log → workspace` |
| `matching_weights` | read (globais+own), write/update/delete own | globais (`workspace_id IS NULL`) legíveis; escrita só no próprio |
| `reflection_insights` | own `[*]` | `workspace_id ∈ …` |
| `research_findings` | own `[*]` | `workspace_id ∈ …` |
| `exploration_log` | own `[*]` | `workspace_id ∈ …` |
| `weight_change_log` | own `[*]` | `workspace_id ∈ …` |
| `company_hypergraphs` | select own `[r]` | leitura no próprio; escrita só service-role (task) |
| `user_feedback` | own `[*]` | `user_id = auth.uid()` |

### Leitura compartilhada (globais por design — não é leak)

Dado não-tenant, legível por qualquer `authenticated`; escrita só service-role.

| Tabela | Política |
|---|---|
| `edital_chunks` | `read_authenticated [r]` |
| `discovered_opportunities` | `read_authenticated [r]` |
| `web_sources` | `read_authenticated [r]` |
| `playbook_overlays` | `read_authenticated [r]` (overlay global, destilado cross-tenant) |
| `meta_reflection_runs` | `read_authenticated [r]` |

### Service-only (RLS ligada, **sem policy** → deny-all a anon/authenticated)

Escrita/leitura só via service-role (backend/worker). `authenticated` lê 0 linhas.

| Tabela | Nota |
|---|---|
| `kg_artifacts` | blobs do KG (016) |
| `edital_source_docs` | Documento Canônico durável (032) |
| `pipeline_errors` | trilha de erro do ETL (007) |

### Fora do PostgREST por construção (schema `agent_memory`)

Checkpointer LangGraph + Store de memória (migration 028) vivem em `agent_memory`,
**fora** da lista de schemas do PostgREST (`public, storage, graphql_public` em
`supabase/config.toml`). Invisíveis pela REST API sem depender de RLS. A migration
027 (band-aid RLS em `public`) ficou obsoleta. Isolamento = namespacing (S3).

## S1 — FURO P0 encontrado e corrigido: schema do `procrastinate`

**O furo.** O schema do procrastinate (migration 003, gerado pela lib) vive em
`public`. Por default do Supabase, `anon` e `authenticated` recebem `GRANT ALL`
nas tabelas + `EXECUTE` em todas as funções, e essas tabelas **não tinham RLS**.
Como `public` é servido pelo PostgREST, a fila inteira ficava exposta pela REST
API — **inclusive para `anon` (sem autenticar)**:

- `SELECT procrastinate_jobs` → vaza `args` (`workspace_id`, `edital_id`,
  payloads) de **todos** os tenants.
- `DELETE`/`UPDATE procrastinate_jobs` → cancela/corrompe jobs alheios
  (integridade + DoS da fila).
- `EXECUTE procrastinate_defer_jobs_v1 / _cancel_job_v1 / _fetch_job_v2 / …` →
  enfileirar jobs arbitrários, cancelar ou roubar jobs — controle total da fila.

Reproduzido ao vivo (Supabase local, anon key): `curl` leu `args` de um job e
deletou o job (HTTP 204).

**O fix.** [`migration 034`](../../supabase/migrations/034_procrastinate_lockdown.sql):
`ENABLE RLS` (deny-all) nas tabelas + `REVOKE ALL` de anon/authenticated em
tabelas, sequences e funções `procrastinate*` (varredura dinâmica, sobrevive a
bumps da lib). O worker/backend conecta via `DATABASE_URL` como role `postgres`
(dono, BYPASSRLS) → não afetado. Mesma filosofia da 027; relocar o schema do
procrastinate seria invasivo demais pré-beta. Pós-fix: anon recebe
`42501 permission denied` na tabela e no RPC; o owner segue lendo/escrevendo.

> **⚠️ PENDÊNCIA DE DEPLOY (P0).** Em 2026-07-02 a suíte apontada ao projeto
> **remoto** ainda reprova em `test_procrastinate_surface_negada` — ou seja, o
> furo **está aberto em produção/staging**: a migration 034 foi aplicada só no
> Supabase local. **Antes do beta externo: `supabase db push`** (aplica 034 +
> 032/033 pendentes no remoto). Enquanto não subir, a fila de jobs segue legível
> e mutável pela anon key em produção.

## S2 — Scoping nos handlers

Todo handler autenticado resolve `workspace_id = get_workspace_id(db, user_id)`
com `user_id` derivado do JWT (`CurrentUserId`) — nunca de input do cliente. Os
endpoints com ID de recurso (`/writing/{session_id}/*`, `/library/{item_id}`,
`/applications/{application_id}/status`, `/research/{finding_id}/promote`, etc.)
filtram por `workspace_id` **e** herdam o RLS do cliente `get_db` (JWT do request).
Defesa em profundidade: `WritingSession._load_from_db` rejeita explicitamente
sessão de outro workspace mesmo sob um cliente que bypassa RLS. Nenhum handler
aceita `thread_id` do cliente — o servidor sempre o monta (S3).

`/discovered-opportunities/*` usa service-role de propósito (fila de curadoria
global, gate humano) — não é dado tenant.

## S3 — Namespacing da camada agêntica

Checkpointer e Store bypassam RLS (conexão direta `DATABASE_URL`). Isolamento =
namespace:

- `thread_id = "{workspace_id}:{session_id}:{turn_index}"` (turnos) /
  `"{ws}:{sid}:generation"` (batch) — montado sempre a partir de
  `self.workspace_id` da sessão carregada do DB.
- Store: namespace `(workspace_id, "insights")`; `memory_put/search/delete`
  recebem `workspace_id` do **caller** (closure server-side nas factories de
  tool), nunca de input do usuário ou output do modelo → prompt injection não
  escolhe namespace.
- Subagentes/caminho stateless compilam o grafo com `checkpointer=False` (não
  `None`) — `None` faria o LangGraph **herdar** o checkpointer do pai. Travado
  por teste (`test_subagente_nao_herda_checkpointer_do_pai`).

## S4 — DEMO_MODE

`DEMO_MODE=1` usa service-role (RLS bypass) e colapsa todos os tenants num
workspace único sem login. `backend.api._guard_demo_mode()` **recusa o boot** se
`DEMO_MODE` + ambiente de produção (`RAILWAY_ENVIRONMENT`/`ENVIRONMENT=production`)
sem override `DEMO_MODE_ALLOW_PROD=1`. **Checklist de deploy do beta: garantir
`DEMO_MODE` ausente/`0` em produção.**

## Notas / dívidas menores (não-bloqueantes)

- `workspaces` não tem policy de DELETE → nenhum usuário apaga workspace pela
  REST API (fail-closed; deleção só via service-role/cascade). Intencional.
- `log_application_event` é `SECURITY DEFINER` e executável por authenticated,
  mas é **função de trigger** (sem args, usa variáveis `TG_*`) — não é
  utilizável para exfiltrar dado de outro tenant fora do contexto do trigger.
- Não há views em `public` nem tabela `public` com RLS desligada (pós-034).

## Como rodar o leak-test

Gated em env (pula sem elas). Contra Supabase local:

```bash
supabase start && supabase migration up      # aplica migrations (inclui 034)
eval "$(supabase status -o env | sed 's/^/LOCAL_/')"
export SUPABASE_URL="$LOCAL_API_URL" \
       SUPABASE_ANON_KEY="$LOCAL_ANON_KEY" \
       SUPABASE_SERVICE_KEY="$LOCAL_SERVICE_ROLE_KEY" \
       SUPABASE_JWT_SECRET="$LOCAL_JWT_SECRET" \
       DATABASE_URL="$LOCAL_DB_URL"
pytest tests/test_tenant_isolation.py -v
```

Contra staging: aponte as mesmas envs ao projeto remoto. `SUPABASE_JWT_SECRET`
só existe em projetos com JWT legado (HS256); projetos ES256 exigem gerar o token
de teste via `signInWithPassword` (ver nota em `_make_user_jwt`). **O
`schemas` do PostgREST remoto é config separada da local — confirmar que
`agent_memory` está fora da lista no projeto de produção.**
