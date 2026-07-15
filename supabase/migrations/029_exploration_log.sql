-- 029: exploration_log — memória do ExploreAgent entre sessões (Fase 3A)
--
-- Spec histórica: docs/historical/agentic-evolution.md §Fase 3A. O ExploreAgent (KGMatchService
-- modo agent_enabled) é stateless por turno: quando o cliente volta numa nova
-- sessão, o agente não lembra que já recomendou/descartou um edital antes. Esta
-- tabela persiste essas decisões por workspace; no início de cada sessão do
-- agente, as últimas 20 decisões entram no prefixo estável do system prompt (D6).
--
-- Idempotência da escrita (R4): a tool log_exploration_decision usa
--   INSERT ... ON CONFLICT (workspace_id, edital_id)
--   DO UPDATE SET decision=EXCLUDED.decision, reason=EXCLUDED.reason, decided_at=now()
-- — revisitar o mesmo edital ATUALIZA a decisão (a última prevalece) em vez de
-- violar UNIQUE. A UNIQUE(workspace_id, edital_id) abaixo é o alvo desse conflito.
--
-- Memória de cliente (D1/D2): vive em `public` com RLS por workspace — NÃO no KG
-- (que nunca armazena dado de cliente) nem no schema agent_memory (que é a
-- maquinaria do checkpointer/Store que bypassa RLS). Acesso pelo client Supabase
-- autenticado (PostgREST + JWT) — só o caminho autenticado do front-door tem
-- workspace; o /kg-explore público não escreve nem lê memória.
--
-- Idempotente: pode rodar mais de uma vez sem efeito colateral.

create table if not exists public.exploration_log (
  id            uuid primary key default gen_random_uuid(),
  workspace_id  uuid not null references public.workspaces(id) on delete cascade,
  edital_id     text not null,
  decision      text not null check (decision in ('recommended', 'discarded')),
  reason        text,
  decided_at    timestamptz not null default now(),
  unique (workspace_id, edital_id)
);

-- Leitura do bloco de decisões: WHERE workspace_id=? ORDER BY decided_at DESC
-- LIMIT 20. Índice casa o filtro + a ordenação.
create index if not exists exploration_log_workspace_recent_idx
  on public.exploration_log (workspace_id, decided_at desc);

alter table public.exploration_log enable row level security;

-- RLS por workspace, mesmo padrão das demais tabelas workspace-scoped
-- (research_findings, reflection_insights): o usuário só vê/manipula linhas de
-- workspaces que possui.
drop policy if exists "exploration_log_own" on public.exploration_log;
create policy "exploration_log_own" on public.exploration_log
  for all using (
    workspace_id in (select id from public.workspaces where user_id = auth.uid())
  );

comment on table public.exploration_log is
  'Memória do ExploreAgent entre sessões (Fase 3A): decisões de recomendar/descartar edital por workspace. As últimas 20 entram no prefixo do system prompt do agente. RLS por workspace.';
comment on column public.exploration_log.decision is
  'recommended | discarded — decisão do agente sobre o edital para este workspace.';
comment on column public.exploration_log.reason is
  'Justificativa curta da decisão (do agente), exibida ao agente em sessões futuras.';
