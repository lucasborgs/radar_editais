-- 024: playbook_overlays + meta_reflection_runs — learned overlays de playbook (Item 3, Sprint 4)
--
-- Os arquivos .md em skills/ (mechanism/*.md + source/<fonte>/*.md) são o SEED
-- canônico humano do playbook de escrita. Aqui mora o que o SISTEMA aprende: a
-- 4ª camada do loader (core/skills.py), mergeada POR SEÇÃO depois das camadas git
-- (adiciona/sobrepõe aquela seção, nunca reescreve o playbook todo).
--
-- DIFERENÇA-CHAVE de escopo vs. as vizinhas (021/weight_change_log, etc.):
-- estas tabelas NÃO são workspace-scoped. Learned overlays são destilados
-- CROSS-WORKSPACE (meta-reflexão sobre application_log anonimizado de todos os
-- tenants, agregado por (mechanism, source)). São conhecimento GLOBAL do produto,
-- não dado de um tenant — por isso NÃO têm RLS POR WORKSPACE. Mas RLS é LIGADO
-- com policy read-only para `authenticated` (mesmo padrão das linhas globais de
-- matching_weights): fecha write/insert anônimo via PostgREST. A escrita é
-- exclusiva do job futuro `run_meta_reflection` (service-role, bypassa RLS); a
-- leitura no loader também é service-role.
--
-- SCAFFOLD: este migration cria a estrutura. O job `run_meta_reflection` que
-- popula as tabelas fica para quando houver volume cross-tenant (Sprint 4).
-- `meta_reflection_runs` é a trilha de auditoria desse job — criada vazia agora.
--
-- Idempotente: pode rodar mais de uma vez sem efeito colateral.

-- (mechanism, source, section) identificam o ponto de merge no loader.
-- source NULL = overlay vale para o mecanismo em qualquer fonte.
create table if not exists public.playbook_overlays (
  id          uuid primary key default gen_random_uuid(),
  mechanism   text not null,
  source      text,
  section     text not null,
  body        text not null,
  confidence  text,
  origin      text,
  created_at  timestamptz not null default now()
);

create index if not exists playbook_overlays_lookup_idx
  on public.playbook_overlays (mechanism, source);

-- GLOBAL (cross-workspace): RLS ligado, mas read-only para authenticated (sem
-- escopo de workspace). Escrita só via service-role (job futuro). Ver cabeçalho.
alter table public.playbook_overlays enable row level security;
drop policy if exists "playbook_overlays_read" on public.playbook_overlays;
create policy "playbook_overlays_read" on public.playbook_overlays
  for select to authenticated using (true);

comment on table public.playbook_overlays is
  'Learned overlays de playbook — 4ª camada do loader (core/skills.py), mergeada por seção depois das camadas git. GLOBAL/cross-workspace (destilado de todos os tenants via run_meta_reflection); NÃO é workspace-scoped. RLS read-only para authenticated; escrita só service-role. Os .md em skills/ continuam o seed canônico.';
comment on column public.playbook_overlays.source is
  'NULL = overlay vale para o mecanismo em qualquer fonte; preenchido = praxe agência × mecanismo.';
comment on column public.playbook_overlays.section is
  'Nome do `## seção` (ex.: "Praxe da agência") — ponto de merge no Playbook.sections.';
comment on column public.playbook_overlays.origin is
  'Proveniência (ex.: id do meta_reflection_run que escreveu este overlay).';

-- Trilha de auditoria do job futuro run_meta_reflection. Criada vazia agora.
create table if not exists public.meta_reflection_runs (
  id                   uuid primary key default gen_random_uuid(),
  ran_at               timestamptz not null default now(),
  mechanism            text,
  source               text,
  outcomes_considered  int,
  overlays_written     int,
  summary              text
);

-- GLOBAL: mesmo racional de playbook_overlays — RLS read-only para authenticated.
alter table public.meta_reflection_runs enable row level security;
drop policy if exists "meta_reflection_runs_read" on public.meta_reflection_runs;
create policy "meta_reflection_runs_read" on public.meta_reflection_runs
  for select to authenticated using (true);

comment on table public.meta_reflection_runs is
  'Trilha de auditoria do job run_meta_reflection (Item 3): cada linha = uma execução da meta-reflexão cross-tenant que destilou overlays. GLOBAL/cross-workspace, RLS read-only para authenticated. Scaffold — criada vazia; populada quando o job entrar.';
comment on column public.meta_reflection_runs.outcomes_considered is
  'Quantos outcomes (application_log anonimizado) a reflexão agregou.';
comment on column public.meta_reflection_runs.overlays_written is
  'Quantos playbook_overlays esta execução inseriu/atualizou.';
