-- ============================================================
-- Radar de Editais — schema inicial
-- Aplica via: Supabase Dashboard → SQL Editor
-- ============================================================

-- Workspaces: um por usuário (auth.users gerenciado pelo Supabase Auth)
create table if not exists public.workspaces (
  id         uuid default gen_random_uuid() primary key,
  user_id    uuid references auth.users(id) on delete cascade not null unique,
  profile    jsonb not null default '{}'::jsonb,
  created_at timestamptz default now(),
  updated_at timestamptz default now()
);

alter table public.workspaces enable row level security;

create policy "workspace_select_own" on public.workspaces
  for select using (auth.uid() = user_id);

create policy "workspace_insert_own" on public.workspaces
  for insert with check (auth.uid() = user_id);

create policy "workspace_update_own" on public.workspaces
  for update using (auth.uid() = user_id);

-- Content Library: documentos e narrativas da empresa
create table if not exists public.content_items (
  id           uuid default gen_random_uuid() primary key,
  workspace_id uuid references public.workspaces(id) on delete cascade not null,
  title        text not null,
  type         text not null check (type in (
                 'proposal', 'project_description', 'team_bio', 'technical_doc', 'other'
               )),
  content      text not null,
  source_url   text,
  tags         text[] not null default '{}',
  -- campos gerados por LLM após upload:
  summary      text,
  key_facts    text[] not null default '{}',
  themes       text[] not null default '{}',
  created_at   timestamptz default now(),
  updated_at   timestamptz default now()
);

alter table public.content_items enable row level security;

create policy "content_items_own" on public.content_items
  for all using (
    workspace_id in (
      select id from public.workspaces where user_id = auth.uid()
    )
  );

-- Trigger: atualiza updated_at automaticamente
create or replace function public.set_updated_at()
returns trigger language plpgsql as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

create trigger workspaces_updated_at
  before update on public.workspaces
  for each row execute function public.set_updated_at();

create trigger content_items_updated_at
  before update on public.content_items
  for each row execute function public.set_updated_at();
