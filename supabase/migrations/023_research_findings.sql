-- 023: research_findings — staging area do deep_research (Item 2, Sprint 3)
--
-- A tool `deep_research` é stateless por design (guard-rail da Fase A): descobre
-- fatos com fontes que morrem no turno. A Fase B constrói esta staging area: cada
-- finding chega automaticamente (verified=false), e o humano decide o que promover
-- para a content_library (gate humano permanece — só a fricção do gate é reduzida).
--
-- TTL de 30 dias para findings não revisados é aplicado como FILTRO na leitura
-- (GET) — created_at < now()-30d não aparece. A limpeza física (cron) fica fora de
-- escopo deste item.
--
-- `promoted_to_library_id` referencia content_items: preenchido quando o usuário
-- promove o finding; `reviewed_at` marca o momento da decisão. RLS por workspace.
--
-- Idempotente: pode rodar mais de uma vez sem efeito colateral.

create table if not exists public.research_findings (
  id                      uuid primary key default gen_random_uuid(),
  workspace_id            uuid not null references public.workspaces(id) on delete cascade,
  question                text not null,
  answer                  text,
  sources                 jsonb not null default '[]'::jsonb,
  query                   text,
  verified                boolean not null default false,
  created_at              timestamptz not null default now(),
  reviewed_at             timestamptz,
  promoted_to_library_id  uuid references public.content_items(id) on delete set null
);

create index if not exists research_findings_workspace_idx
  on public.research_findings (workspace_id, created_at desc);

alter table public.research_findings enable row level security;

-- RLS por workspace, mesmo padrão das demais tabelas workspace-scoped
-- (weight_change_log, reflection_insights): o usuário só vê/manipula linhas de
-- workspaces que possui.
drop policy if exists "research_findings_own" on public.research_findings;
create policy "research_findings_own" on public.research_findings
  for all using (
    workspace_id in (select id from public.workspaces where user_id = auth.uid())
  );

comment on table public.research_findings is
  'Staging area do deep_research (Fase B). Findings chegam verified=false; humano promove para content_library. TTL de 30 dias para não-revisados é filtro de leitura. RLS por workspace.';
comment on column public.research_findings.verified is
  'Sempre false na chegada (origem deep_research). A promoção para a library é o gate humano.';
comment on column public.research_findings.promoted_to_library_id is
  'content_items.id criado na promoção. NULL = ainda na fila pendente.';
comment on column public.research_findings.reviewed_at is
  'Preenchido quando o usuário decide (promove). NULL = pendente.';
