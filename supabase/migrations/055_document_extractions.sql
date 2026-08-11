-- RT06-T01: artifacts append-only de extração adaptativa.
-- Não há tabela por claim: o envelope validado vive em JSONB.

create table if not exists public.document_extractions (
    id                uuid primary key default gen_random_uuid(),
    fingerprint       text not null unique,
    subject_id        text not null,
    asset_hash        text not null,
    bundle_hash       text,
    schema_version    int not null default 1,
    status            text not null check (status in ('complete', 'partial', 'failed', 'unavailable')),
    artifact          jsonb not null,
    created_at        timestamptz not null default now()
);

comment on table public.document_extractions is
    'RT06 extraction artifacts append-only; service-role only; one row per material fingerprint.';

create index if not exists document_extractions_subject_idx
    on public.document_extractions (subject_id, created_at desc);

alter table public.document_extractions enable row level security;

-- Sem policies: apenas service_role acessa o artefato documental.

create or replace function public.reject_document_extraction_mutations()
returns trigger language plpgsql as $$
begin
    raise exception 'document_extractions is append-only: updates and deletes are not allowed';
end;
$$;

drop trigger if exists trg_document_extractions_append_only on public.document_extractions;
create trigger trg_document_extractions_append_only
    before update or delete on public.document_extractions
    for each row execute function public.reject_document_extraction_mutations();
