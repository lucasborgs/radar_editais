-- P0 demo readiness: durable cron run ledger and deduplicated incidents.
-- The existing source_runs table remains the channel-level ledger.
create table if not exists public.cron_runs (
    id uuid primary key default gen_random_uuid(),
    task text not null check (task in ('run_daily_etl','discover_opportunities','warm_edital_chunks')),
    scheduled_at timestamptz not null,
    started_at timestamptz not null default now(),
    completed_at timestamptz,
    status text not null default 'running'
        check (status in ('running','succeeded','partial','failed')),
    job_id text not null,
    image_version text,
    counters jsonb not null default '{}'::jsonb,
    error_summary text,
    last_step text,
    unique (task, scheduled_at)
);
create index if not exists cron_runs_task_started_idx on public.cron_runs(task, started_at desc);
create index if not exists cron_runs_status_idx on public.cron_runs(status, started_at desc);

create or replace function public.guard_cron_run_terminal_transition()
returns trigger language plpgsql as $$
begin
    if old.status in ('succeeded','partial','failed') and new.status <> old.status then
        raise exception 'cron_runs terminal status is immutable';
    end if;
    return new;
end $$;
drop trigger if exists cron_runs_terminal_guard on public.cron_runs;
create trigger cron_runs_terminal_guard before update on public.cron_runs
for each row execute function public.guard_cron_run_terminal_transition();

create table if not exists public.operational_incidents (
    id uuid primary key default gen_random_uuid(),
    fingerprint text not null unique,
    kind text not null,
    status text not null default 'open' check (status in ('open','recovered')),
    first_seen_at timestamptz not null default now(),
    last_seen_at timestamptz not null default now(),
    recovered_at timestamptz,
    details jsonb not null default '{}'::jsonb
);
create index if not exists operational_incidents_status_idx on public.operational_incidents(status, last_seen_at desc);

alter table public.cron_runs enable row level security;
alter table public.operational_incidents enable row level security;
revoke all on table public.cron_runs, public.operational_incidents from anon, authenticated;

comment on table public.cron_runs is 'Ledger autoritativo das execuções dos três CRONs operacionais.';
comment on table public.operational_incidents is 'Incidentes deduplicados e recuperação do dead-man operacional.';
