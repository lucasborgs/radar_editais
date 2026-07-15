-- Identidade persistida do banco. CLIs mutantes comparam esta sentinela ao
-- ENVIRONMENT antes de escrever, impedindo troca acidental entre ambientes.
create table if not exists public.environment_metadata (
    id boolean primary key default true check (id),
    environment text not null check (environment in ('local', 'test', 'staging', 'production')),
    project_ref text not null,
    schema_version text not null default '040',
    dataset_version text not null default 'unseeded',
    updated_at timestamptz not null default now()
);

alter table public.environment_metadata enable row level security;
revoke all on table public.environment_metadata from anon, authenticated;
grant select, insert, update, delete on table public.environment_metadata to service_role;

comment on table public.environment_metadata is
    'Sentinela singleton de identidade do ambiente; nunca contém credenciais.';
