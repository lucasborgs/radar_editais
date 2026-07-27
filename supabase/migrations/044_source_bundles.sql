-- 044: source_bundles — histórico append-only de SourceBundle (RT04-T02)
--
-- Tabela service-role-only: RLS habilitada sem policies de usuário final.
-- Acesso exclusivo via service role (pipelines/jobs/scripts), nunca via
-- cliente autenticado anônimo. Espelha o padrão de edital_source_docs (032)
-- e source_runs (043).
--
-- A constraint UNIQUE (subject_kind, subject_id, bundle_hash) torna a
-- escrita idempotente: recoleta do mesmo conteúdo material não duplica a
-- versão. É append-only no runtime normal — não atualizar nem apagar linhas.
--
-- O índice parcial (subject_kind, subject_id, collected_at DESC, created_at DESC)
-- com status='complete' permite ler eficientemente o último bundle completo
-- de cada sujeito.

create table if not exists public.source_bundles (
    id                  uuid        primary key default gen_random_uuid(),
    subject_kind        text        not null,
    subject_id          text        not null,
    source              text        not null,
    bundle_hash         text        not null,
    bundle              jsonb       not null,
    acquisition_status  text        not null
                        check (acquisition_status in ('complete', 'partial')),
    collected_at        timestamptz not null,
    created_at          timestamptz not null default now(),

    -- Append-only idempotente: mesmo conteúdo material não gera nova versão
    unique (subject_kind, subject_id, bundle_hash)
);

comment on table public.source_bundles is
    'Histórico append-only de SourceBundle. '
    'Service-role apenas; RLS sem policy de usuário final. '
    'UNIQUE (subject_kind, subject_id, bundle_hash) garante idempotência.';

comment on column public.source_bundles.id is
    'UUID gerado pelo banco na inserção. Identificador interno estável.';

comment on column public.source_bundles.subject_kind is
    'Tipo do sujeito: opportunity, investor, ict, program ou agency.';

comment on column public.source_bundles.subject_id is
    'Identidade canônica do sujeito '
    '(ex.: "fapesc:37-2026" para oportunidade, "ict:exemplo:lab" para ICT).';

comment on column public.source_bundles.source is
    'Fonte produtora do pacote (ex.: fapesc, web, finep).';

comment on column public.source_bundles.bundle_hash is
    'SHA-256 determinístico do envelope material '
    '(schema_version, subject_kind, subject_id, source, '
    'acquisition_status, documentos e metadados). '
    'Exclui collected_at e producer_version.';

comment on column public.source_bundles.bundle is
    'JSONB conforme contrato SourceBundle (§5 da spec). '
    'Inclui schema_version, subject_kind, subject_id, source, '
    'collected_at, producer_version, acquisition_status e documents[].';

comment on column public.source_bundles.acquisition_status is
    'complete = pacote integral; partial = diagnóstico (não substitui '
    'a última versão complete na leitura corrente).';

comment on column public.source_bundles.collected_at is
    'Timestamp declarado pelo produtor (quando a coleta ocorreu). '
    'Difere de created_at (timestamp de persistência).';

comment on column public.source_bundles.created_at is
    'Timestamp de persistência no banco. Preenchido automaticamente '
    'pelo default now().';

-- Índice para leitura do último bundle complete por sujeito
create index if not exists source_bundles_complete_last_idx
    on public.source_bundles (subject_kind, subject_id, collected_at desc, created_at desc)
    where acquisition_status = 'complete';

-- Índice para lookup por hash (diagnóstico / consistência)
create index if not exists source_bundles_hash_idx
    on public.source_bundles (bundle_hash);

-- RLS habilitada sem policies = apenas service role (bypassa RLS) acessa.
-- Mesmo padrão de edital_source_docs (032), source_runs (043) e
-- discovery_promotion_runs (038).
alter table public.source_bundles enable row level security;
