-- 046: data_quality_exceptions — fila de exceções e revisões (RT05-T02)
--
-- Duas tabelas service-role-only:
--   1. data_quality_exceptions: fila materializada e idempotente;
--   2. data_quality_reviews: decisões humanas append-only.
--
-- Chave lógica da exceção:
--   (subject_kind, subject_id, field_path, issue_code, input_fingerprint)
-- Mesmo fingerprint → atualiza só last_observed_at.
-- Fingerprint novo → insere, depois marca anteriores como superseded.
-- Revisões são append-only: triggers rejeitam UPDATE e DELETE.
-- review_id textual único preserva a identidade externa da decisão.

-- Tabela 1: data_quality_exceptions

create table if not exists public.data_quality_exceptions (
    id                  uuid        primary key default gen_random_uuid(),
    schema_version      int         not null default 1,
    subject_kind        text        not null,
    subject_id          text        not null,
    field_path          text        not null,
    issue_code          text        not null,
    produced_state      text,
    produced_value      text,
    evidence_refs       jsonb       not null default '[]'::jsonb,
    bundle_hash         text,
    producer_version    text,
    input_fingerprint   text        not null
                        check (btrim(input_fingerprint) <> ''),
    status              text        not null default 'open'
                        check (status in ('open', 'resolved', 'superseded')),
    detected_at         timestamptz not null default now(),
    last_observed_at    timestamptz not null default now(),
    created_at          timestamptz not null default now(),

    -- Idempotência: mesmo fingerprint não duplica
    unique (subject_kind, subject_id, field_path, issue_code, input_fingerprint)
);

comment on table public.data_quality_exceptions is
    'Fila de exceções de qualidade de dados. '
    'Service-role apenas; RLS sem policy de usuário final. '
    'UNIQUE (subject_kind, subject_id, field_path, issue_code, input_fingerprint) '
    'garante idempotência.';

comment on column public.data_quality_exceptions.id is
    'UUID gerado pelo banco. Identificador interno estável.';

comment on column public.data_quality_exceptions.schema_version is
    'Versão do contrato (1 = data_quality.py).';

comment on column public.data_quality_exceptions.subject_kind is
    'Tipo do sujeito: opportunity, investor, ict, program ou agency.';

comment on column public.data_quality_exceptions.subject_id is
    'Identidade canônica do sujeito.';

comment on column public.data_quality_exceptions.field_path is
    'Caminho do campo que gerou a exceção (ex.: deadline, status).';

comment on column public.data_quality_exceptions.issue_code is
    'Código da exceção: fact_conflict, critical_fact_missing, '
    'validation_failed, evidence_unresolved, '
    'temporal_status_without_basis, temporal_status_conflict.';

comment on column public.data_quality_exceptions.produced_state is
    'Estado factual produzido (FactState: stated, inferred, absent, '
    'conflicting, unknown). Opcional.';

comment on column public.data_quality_exceptions.produced_value is
    'Valor produzido que gerou a exceção. Opcional.';

comment on column public.data_quality_exceptions.evidence_refs is
    'JSONB: lista de EvidenceRef serializados validados. '
    'Nunca contém documentos integrais ou URLs arbitrárias.';

comment on column public.data_quality_exceptions.bundle_hash is
    'Hash do SourceBundle aplicável, quando disponível.';

comment on column public.data_quality_exceptions.producer_version is
    'Versão do produtor/validador que gerou a exceção.';

comment on column public.data_quality_exceptions.input_fingerprint is
    'Derivado das entradas materiais. Fingerprint novo = '
    'nova versão material, marca a anterior como superseded.';

comment on column public.data_quality_exceptions.status is
    'open = pendente; resolved = revisado; '
    'superseded = substituído por fingerprint mais recente.';

comment on column public.data_quality_exceptions.detected_at is
    'Timestamp da primeira detecção.';

comment on column public.data_quality_exceptions.last_observed_at is
    'Timestamp da última observação (atualizado em reobservação idempotente).';

comment on column public.data_quality_exceptions.created_at is
    'Timestamp de criação do registro no banco.';

-- Índice para lookup por sujeito (listar exceções de uma oportunidade)
create index if not exists data_quality_exceptions_subject_idx
    on public.data_quality_exceptions (subject_kind, subject_id);

-- Índice para listagem de exceções abertas (fila administrativa)
create index if not exists data_quality_exceptions_open_idx
    on public.data_quality_exceptions (status)
    where status = 'open';

alter table public.data_quality_exceptions enable row level security;

-- Tabela 2: data_quality_reviews

create table if not exists public.data_quality_reviews (
    id                  uuid        primary key default gen_random_uuid(),
    review_id           text        not null unique,
    schema_version      int         not null default 1,
    exception_id        uuid        not null
                        references public.data_quality_exceptions(id)
                        on delete restrict,
    decision            text        not null
                        check (decision in (
                            'confirm', 'correct', 'mark_unknown',
                            'confirm_continuous'
                        )),
    corrected_value     text,
    justification       text        not null,
    evidence_refs       jsonb       not null default '[]'::jsonb,
    actor_id            text        not null,
    reviewed_at         timestamptz not null default now(),
    created_at          timestamptz not null default now()
);

comment on table public.data_quality_reviews is
    'Decisões humanas append-only sobre exceções. '
    'Nunca atualizar ou remover registros. '
    'Service-role apenas; RLS sem policy de usuário final.';

comment on column public.data_quality_reviews.id is
    'UUID gerado pelo banco. Identificador interno estável.';

comment on column public.data_quality_reviews.review_id is
    'Identificador textual único da revisão. '
    'Usado para idempotência de append_review.';

comment on column public.data_quality_reviews.schema_version is
    'Versão do contrato (1 = data_quality.py).';

comment on column public.data_quality_reviews.exception_id is
    'FK para data_quality_exceptions.id. '
    'Restrict: não permite remover exceção com revisões.';

comment on column public.data_quality_reviews.decision is
    'Decisão: confirm, correct, mark_unknown ou confirm_continuous.';

comment on column public.data_quality_reviews.corrected_value is
    'Valor corrigido (obrigatório quando decision=correct).';

comment on column public.data_quality_reviews.justification is
    'Justificativa curta da revisão (max 2000 caracteres).';

comment on column public.data_quality_reviews.evidence_refs is
    'JSONB: lista de EvidenceRef que sustentam a decisão. '
    'source_url é removido antes da persistência.';

comment on column public.data_quality_reviews.actor_id is
    'Identidade do revisor administrativo. '
    'Vem da autenticação, nunca de payload público.';

comment on column public.data_quality_reviews.reviewed_at is
    'Timestamp da decisão.';

comment on column public.data_quality_reviews.created_at is
    'Timestamp de criação do registro no banco.';

-- Índice para lookup de revisões por exceção
create index if not exists data_quality_reviews_exception_idx
    on public.data_quality_reviews (exception_id);

-- Índice para ordenação cronológica de revisões
create index if not exists data_quality_reviews_created_at_idx
    on public.data_quality_reviews (exception_id, created_at);

alter table public.data_quality_reviews enable row level security;

-- Trigger: rejeita UPDATE e DELETE em data_quality_reviews

create or replace function public.reject_review_mutations()
returns trigger as $$
begin
    raise exception 'data_quality_reviews is append-only: updates and deletes are not allowed';
end;
$$ language plpgsql;

drop trigger if exists trg_reviews_append_only on public.data_quality_reviews;

create trigger trg_reviews_append_only
    before update or delete on public.data_quality_reviews
    for each row execute function public.reject_review_mutations();
