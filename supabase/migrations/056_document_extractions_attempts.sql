-- RT06: o fingerprint identifica a entrada material; cada execução possui
-- uma identidade própria para preservar falhas e permitir retry explícito.
alter table if exists public.document_extractions
    add column if not exists attempt_id text;

update public.document_extractions
set attempt_id = coalesce(attempt_id, id::text)
where attempt_id is null;

alter table if exists public.document_extractions
    alter column attempt_id set not null;

alter table if exists public.document_extractions
    drop constraint if exists document_extractions_fingerprint_key;

alter table if exists public.document_extractions
    add constraint document_extractions_fingerprint_attempt_key
    unique (fingerprint, attempt_id);

create unique index if not exists document_extractions_healthy_fingerprint_key
    on public.document_extractions (fingerprint)
    where status in ('complete', 'partial');
