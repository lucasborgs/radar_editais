-- 038 — execução auditável da promoção da Descoberta.
--
-- `discovered_opportunities.status` permanece editorial (pending/promoted/rejected).
-- Esta tabela registra o processamento técnico posterior, independente para
-- Radar/gold e RAG, sem permitir conteúdo pending no catálogo ou nos chunks.

create table if not exists public.discovery_promotion_runs (
    id                          uuid primary key default gen_random_uuid(),
    discovered_opportunity_id   uuid not null
                                references public.discovered_opportunities(id)
                                on delete cascade,
    route                       text not null
                                check (route in ('web_source', 'direct_pdf', 'evidence_package')),
    status                      text not null default 'queued'
                                check (status in ('queued', 'awaiting_fetch', 'processing',
                                                  'ready', 'partial_failure', 'failed')),
    edital_id                   text,
    web_source_id               uuid references public.web_sources(id) on delete set null,
    evidence_version            integer not null default 1,
    stages                      jsonb not null default '{}'::jsonb,
    error_summary               text,
    started_at                  timestamptz not null default now(),
    completed_at                timestamptz,
    updated_at                  timestamptz not null default now()
);

create index if not exists discovery_promotion_runs_opportunity_idx
    on public.discovery_promotion_runs (discovered_opportunity_id, started_at desc);

create table if not exists public.discovery_promotion_events (
    id                  uuid primary key default gen_random_uuid(),
    promotion_run_id    uuid not null references public.discovery_promotion_runs(id)
                        on delete cascade,
    stage               text not null,
    status              text not null,
    actor               text not null default 'system'
                        check (actor in ('system', 'operator')),
    attempt             integer not null default 1,
    artifact            jsonb not null default '{}'::jsonb,
    error_summary       text,
    created_at          timestamptz not null default now()
);

create index if not exists discovery_promotion_events_run_idx
    on public.discovery_promotion_events (promotion_run_id, created_at);

alter table public.discovery_promotion_runs enable row level security;
alter table public.discovery_promotion_events enable row level security;

comment on table public.discovery_promotion_runs is
  'Estado técnico de uma promoção aprovada da Descoberta; separado do estado editorial da fila.';
comment on table public.discovery_promotion_events is
  'Auditoria append-only por etapa/retry da promoção da Descoberta.';
