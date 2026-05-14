-- ============================================================
-- Radar de Editais — Wave 1 schema (Fase 1 de ADR-001)
--
-- Adiciona as tabelas que destravam:
--   • Persistência de WritingSession (ADR B1)
--   • ApplicationLog + satélite append-only (ADR B2, Gap 1)
--   • edital_chunks para RAG (ADR A3, B3)
--   • matching_weights por workspace (ADR A5)
--   • reflection_insights placeholder (Fase 2)
--   • Decay temporal em content_items (ADR B4)
--   • Consent opt-in em workspaces (ADR C3)
--
-- Todas as tabelas multi-tenant carregam RLS via auth.uid() (ADR B5/M7).
-- Aplicação: supabase db reset (local) ou via Dashboard (prod).
-- ============================================================


-- ────────────────────────────────────────────────────────────
-- 1. ALTER workspaces — consent opt-in para agregação anônima (ADR C3)
-- ────────────────────────────────────────────────────────────

alter table public.workspaces
  add column if not exists contribute_to_global_weights boolean not null default false;


-- ────────────────────────────────────────────────────────────
-- 2. ALTER content_items — importance, decay, archive, enrichment status (ADR B4, M8)
-- ────────────────────────────────────────────────────────────

alter table public.content_items
  add column if not exists importance_score int not null default 5
    check (importance_score between 1 and 10),
  add column if not exists last_referenced_at timestamptz not null default now(),
  add column if not exists archived_at timestamptz,
  add column if not exists enrichment_status text not null default 'pending'
    check (enrichment_status in ('pending', 'processing', 'done', 'failed'));

-- Index parcial para listagem default (não-arquivados)
create index if not exists content_items_active_idx
  on public.content_items (workspace_id, created_at desc)
  where archived_at is null;

-- Index para consultas de itens enriquecidos
create index if not exists content_items_enrichment_idx
  on public.content_items (workspace_id, enrichment_status);


-- ────────────────────────────────────────────────────────────
-- 3. writing_sessions (header) + session_turns (1 row por turno) — ADR B1
-- ────────────────────────────────────────────────────────────

create table if not exists public.writing_sessions (
  id               uuid primary key default gen_random_uuid(),
  workspace_id     uuid not null references public.workspaces(id) on delete cascade,
  edital_id        text not null,
  status           text not null default 'active'
                   check (status in ('active', 'completed', 'abandoned')),
  summary          text,                                         -- histórico comprimido
  proposal_outline jsonb not null default '[]'::jsonb,           -- lista de títulos de seção
  section_drafts   jsonb not null default '{}'::jsonb,           -- {section_title: content}
  created_at       timestamptz not null default now(),
  updated_at       timestamptz not null default now()
);

create index if not exists writing_sessions_workspace_idx
  on public.writing_sessions (workspace_id, status, updated_at desc);

alter table public.writing_sessions enable row level security;

create policy "writing_sessions_own" on public.writing_sessions
  for all using (
    workspace_id in (select id from public.workspaces where user_id = auth.uid())
  );

drop trigger if exists writing_sessions_updated_at on public.writing_sessions;
create trigger writing_sessions_updated_at
  before update on public.writing_sessions
  for each row execute function public.set_updated_at();


create table if not exists public.session_turns (
  id           bigserial primary key,
  session_id   uuid not null references public.writing_sessions(id) on delete cascade,
  turn_index   int  not null,
  role         text not null check (role in ('user', 'assistant', 'system')),
  content      text not null,
  tokens       int,                                              -- prompt + completion
  section_hint text,                                             -- seção ativa neste turno
  compliance_flags jsonb,                                        -- ComplianceMonitor (Fase 2)
  created_at   timestamptz not null default now(),
  unique (session_id, turn_index)
);

create index if not exists session_turns_lookup_idx
  on public.session_turns (session_id, turn_index);

alter table public.session_turns enable row level security;

create policy "session_turns_own" on public.session_turns
  for all using (
    session_id in (
      select id from public.writing_sessions
      where workspace_id in (select id from public.workspaces where user_id = auth.uid())
    )
  );


-- ────────────────────────────────────────────────────────────
-- 4. application_log + application_events (satélite append-only) — ADR B2
--    Status enum permissivo (todos), update-in-place no log,
--    cada transição vira row no events via trigger.
-- ────────────────────────────────────────────────────────────

do $$
begin
  if not exists (select 1 from pg_type where typname = 'application_status') then
    create type public.application_status as enum (
      'matched',
      'brief_gerado',
      'proposta_iniciada',
      'submetida',
      'em_analise',
      'aprovada',
      'reprovada',
      'desistiu'
    );
  end if;
end$$;


create table if not exists public.application_log (
  id               uuid primary key default gen_random_uuid(),
  workspace_id     uuid not null references public.workspaces(id) on delete cascade,
  edital_id        text not null,                                -- FINEP id (KG file-based, sem FK)
  session_id       uuid references public.writing_sessions(id) on delete set null,
  match_score      numeric(5,2),                                 -- 0.00 - 100.00
  match_dimensions jsonb not null default '{}'::jsonb,           -- breakdown por dimensão
  status           public.application_status not null default 'matched',
  feedback_notas   text,
  created_at       timestamptz not null default now(),
  updated_at       timestamptz not null default now(),
  unique (workspace_id, edital_id)                               -- 1 application por edital por workspace
);

create index if not exists application_log_workspace_status_idx
  on public.application_log (workspace_id, status);
create index if not exists application_log_edital_idx
  on public.application_log (edital_id);

alter table public.application_log enable row level security;

create policy "application_log_own" on public.application_log
  for all using (
    workspace_id in (select id from public.workspaces where user_id = auth.uid())
  );

drop trigger if exists application_log_updated_at on public.application_log;
create trigger application_log_updated_at
  before update on public.application_log
  for each row execute function public.set_updated_at();


create table if not exists public.application_events (
  id             bigserial primary key,
  application_id uuid not null references public.application_log(id) on delete cascade,
  from_status    public.application_status,                      -- null no INSERT inicial
  to_status      public.application_status not null,
  payload        jsonb not null default '{}'::jsonb,             -- snapshot de campos relevantes
  occurred_at    timestamptz not null default now()
);

create index if not exists application_events_lookup_idx
  on public.application_events (application_id, occurred_at);

alter table public.application_events enable row level security;

create policy "application_events_own" on public.application_events
  for all using (
    application_id in (
      select id from public.application_log
      where workspace_id in (select id from public.workspaces where user_id = auth.uid())
    )
  );

-- Trigger: registra eventos no INSERT inicial e em toda mudança de status
create or replace function public.log_application_event()
returns trigger language plpgsql security definer as $$
begin
  if (tg_op = 'INSERT') then
    insert into public.application_events (application_id, from_status, to_status, payload)
    values (
      new.id, null, new.status,
      jsonb_build_object(
        'match_score', new.match_score,
        'match_dimensions', new.match_dimensions
      )
    );
  elsif (tg_op = 'UPDATE' and old.status is distinct from new.status) then
    insert into public.application_events (application_id, from_status, to_status, payload)
    values (
      new.id, old.status, new.status,
      jsonb_build_object('feedback_notas', new.feedback_notas)
    );
  end if;
  return new;
end;
$$;

drop trigger if exists application_log_status_event on public.application_log;
create trigger application_log_status_event
  after insert or update on public.application_log
  for each row execute function public.log_application_event();


-- ────────────────────────────────────────────────────────────
-- 5. edital_chunks — RAG (ADR A3, B3)
--    Dados públicos (editais FINEP): leitura para qualquer authenticated,
--    escrita apenas via service-role (jobs de chunking).
-- ────────────────────────────────────────────────────────────

create table if not exists public.edital_chunks (
  id          uuid primary key default gen_random_uuid(),
  edital_id   text not null,
  chunk_index int  not null,
  text        text not null,
  section     text,                                              -- título da seção (ex: "Art. 3º — Elegibilidade")
  source_file text,                                              -- ex: "edital_principal.pdf"
  page_range  text,                                              -- ex: "4-5"
  embedding   vector(1536),                                      -- OpenAI text-embedding-3-large
  text_search tsvector generated always as
              (to_tsvector('portuguese', text)) stored,
  created_at  timestamptz not null default now(),
  unique (edital_id, chunk_index)
);

create index if not exists edital_chunks_embedding_idx
  on public.edital_chunks using hnsw (embedding vector_cosine_ops);
create index if not exists edital_chunks_text_search_idx
  on public.edital_chunks using gin (text_search);
create index if not exists edital_chunks_edital_idx
  on public.edital_chunks (edital_id);

alter table public.edital_chunks enable row level security;

create policy "edital_chunks_read_authenticated" on public.edital_chunks
  for select to authenticated using (true);


-- ────────────────────────────────────────────────────────────
-- 6. matching_weights — pesos do HybridMatch (ADR A5)
--    workspace_id NULL = pesos globais default
--    ReflectionService (Fase 2) escreve rows com workspace_id específico
-- ────────────────────────────────────────────────────────────

create table if not exists public.matching_weights (
  id           uuid primary key default gen_random_uuid(),
  workspace_id uuid references public.workspaces(id) on delete cascade,  -- NULL = global
  dimension    text not null check (dimension in (
                 'elegibilidade', 'tematico', 'trl', 'mecanismo', 'contrapartida'
               )),
  weight       numeric(5,2) not null check (weight >= 0 and weight <= 100),
  source       text not null default 'manual'
               check (source in ('manual', 'reflection', 'global_aggregated')),
  created_at   timestamptz not null default now(),
  updated_at   timestamptz not null default now(),
  unique (workspace_id, dimension)
);

-- Constraint adicional para o caso global (workspace_id IS NULL) — UNIQUE acima não cobre NULLs
create unique index if not exists matching_weights_global_uniq
  on public.matching_weights (dimension)
  where workspace_id is null;

create index if not exists matching_weights_workspace_idx
  on public.matching_weights (workspace_id) where workspace_id is not null;

alter table public.matching_weights enable row level security;

-- Leitura: globais (workspace_id IS NULL) + pesos do próprio workspace
create policy "matching_weights_read" on public.matching_weights
  for select to authenticated using (
    workspace_id is null
    or workspace_id in (select id from public.workspaces where user_id = auth.uid())
  );

-- Escrita pelo usuário: apenas em pesos do seu workspace
create policy "matching_weights_write_own" on public.matching_weights
  for insert with check (
    workspace_id in (select id from public.workspaces where user_id = auth.uid())
  );
create policy "matching_weights_update_own" on public.matching_weights
  for update using (
    workspace_id in (select id from public.workspaces where user_id = auth.uid())
  );
create policy "matching_weights_delete_own" on public.matching_weights
  for delete using (
    workspace_id in (select id from public.workspaces where user_id = auth.uid())
  );
-- Pesos globais (workspace_id IS NULL) só podem ser manipulados via service-role.

drop trigger if exists matching_weights_updated_at on public.matching_weights;
create trigger matching_weights_updated_at
  before update on public.matching_weights
  for each row execute function public.set_updated_at();

-- Seed: pesos globais default (replicam _WEIGHTS hardcoded em hybrid_match_service.py)
insert into public.matching_weights (workspace_id, dimension, weight, source) values
  (null, 'elegibilidade', 30, 'manual'),
  (null, 'tematico',      25, 'manual'),
  (null, 'trl',           20, 'manual'),
  (null, 'mecanismo',     15, 'manual'),
  (null, 'contrapartida', 10, 'manual')
on conflict do nothing;


-- ────────────────────────────────────────────────────────────
-- 7. reflection_insights — saída do ReflectionService (Fase 2 placeholder)
-- ────────────────────────────────────────────────────────────

create table if not exists public.reflection_insights (
  id           uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references public.workspaces(id) on delete cascade,
  level        int not null check (level >= 1 and level <= 3),
  -- level 1 = observação direta ("perdeu 3 editais de TRL 4-6")
  -- level 2 = padrão sintetizado ("problema é maturidade, não alinhamento")
  -- level 3 = meta-insight ("estratégia deve priorizar editais TRL 6+")
  insight      text not null,
  evidence     jsonb not null default '[]'::jsonb,               -- refs a application_log/session_turns
  outcomes_window_start timestamptz,                             -- range de outcomes considerados
  outcomes_window_end   timestamptz,
  active       boolean not null default true,                    -- usuário pode desativar via UI
  created_at   timestamptz not null default now()
);

create index if not exists reflection_insights_lookup_idx
  on public.reflection_insights (workspace_id, active, level);

alter table public.reflection_insights enable row level security;

create policy "reflection_insights_own" on public.reflection_insights
  for all using (
    workspace_id in (select id from public.workspaces where user_id = auth.uid())
  );


-- ============================================================
-- Fim da migration 004 (Wave 1).
-- Próximos passos:
--   • Wave 2 Track A: persistir WritingSession (usar writing_sessions + session_turns)
--   • Wave 2 Track A: chunker + embedder populando edital_chunks
--   • Wave 2 Track B: HybridMatch lendo matching_weights
--   • Fase 2: ReflectionService escrevendo reflection_insights
-- ============================================================
