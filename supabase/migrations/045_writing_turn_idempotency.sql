-- 045: writing_turn_idempotency — idempotência para POST /writing/turn e /writing/turn/stream
--
-- Evita duplicação de turnos quando o cliente retenta uma requisição que já
-- foi processada pelo servidor (timeout de resposta, queda de conexão, etc).
-- O frontend gera um idempotency_key (UUID v4) por tentativa distinta de
-- turno; retentativas reusam a mesma chave.
--
-- Linhas expiradas (> 24h) são limpas por um VACUUM ou cron periódico.
-- A tabela é service-role-only: RLS habilitada sem policies de usuário final.

create table if not exists public.writing_turn_idempotency (
    idempotency_key     text        primary key,
    session_id          text        not null,
    response_json       jsonb       not null,
    created_at          timestamptz not null default now()
);

comment on table public.writing_turn_idempotency is
    'Idempotência para turnos de escrita. '
    'Service-role apenas; RLS sem policy de usuário final. '
    'Linhas expiradas (> 24h) devem ser limpas periodicamente.';

comment on column public.writing_turn_idempotency.idempotency_key is
    'UUID v4 gerado pelo frontend por tentativa distinta de turno. '
    'Retentativas reusam a mesma chave.';

comment on column public.writing_turn_idempotency.session_id is
    'ID da sessão de escrita à qual o turno pertence.';

comment on column public.writing_turn_idempotency.response_json is
    'Resposta JSON completa do turno, para replay em retentativas.';

comment on column public.writing_turn_idempotency.created_at is
    'Timestamp de criação. Usado para purga de linhas expiradas.';

-- Índice para purga eficiente de registros antigos
create index if not exists writing_turn_idempotency_created_at_idx
    on public.writing_turn_idempotency (created_at);

-- RLS habilitada sem policies = apenas service role (bypassa RLS) acessa.
alter table public.writing_turn_idempotency enable row level security;