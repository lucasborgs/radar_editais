-- 043: source_runs e atribuição nullable no staging da Descoberta (RT03-T02)
--
-- Aditivo, idempotente, sem backfill. Cria a tabela única de execução por
-- canal (`source_runs`) e adiciona 4 colunas nullable de atribuição a
-- `discovered_opportunities`. Nenhuma coluna editorial, política RLS ou
-- semântica de dedup/promoção existente é alterada.
--
-- RLS habilitada em `source_runs` sem policy de usuário final — service-role
-- escreve, API administrativa lê. Padrão idêntico a `pipeline_errors` (007)
-- e `discovery_promotion_runs` (038).
--
-- Aplicar SOMENTE no Supabase local antes do runtime instrumentado (T03+).

-- ──────────────────────────────────────────────────────────────────────────
-- 1. source_runs
-- ──────────────────────────────────────────────────────────────────────────

create table if not exists public.source_runs (
    id                uuid primary key default gen_random_uuid(),
    batch_id          uuid not null,
    source_key        text not null,
    mode              text not null,
    status            text not null default 'running'
                        check (status in (
                            'running', 'succeeded', 'partial',
                            'failed', 'skipped'
                        )),
    started_at        timestamptz not null default now(),
    completed_at      timestamptz,
    records_observed  integer
                        check (records_observed >= 0),
    records_emitted   integer
                        check (records_emitted >= 0),
    records_staged    integer
                        check (records_staged >= 0),
    error_count       integer not null default 0
                        check (error_count >= 0),
    reason_code       text,
    metrics           jsonb not null default '{}'::jsonb,
    unique (batch_id, source_key)
);

create index if not exists source_runs_batch_idx
    on public.source_runs (batch_id, source_key);

create index if not exists source_runs_channel_idx
    on public.source_runs (source_key, started_at desc);

create index if not exists source_runs_status_idx
    on public.source_runs (status, started_at desc)
    where status in ('running', 'failed');

alter table public.source_runs enable row level security;
-- Intencionalmente sem CREATE POLICY: apenas service-role escreve e
-- consulta. Espelha o padrão de pipeline_errors (007) e
-- discovery_promotion_runs (038).

comment on table public.source_runs is
    'Registro de execução por canal da Descoberta (RT03-T02). '
    'Service-role apenas; RLS sem policy de usuário final.';

comment on column public.source_runs.batch_id is
    'UUID compartilhado pela rodada de descoberta '
    '(todos os canais da mesma execução cron).';

comment on column public.source_runs.source_key is
    'Chave do canal conforme docs/domain/sources/_coverage.md '
    '(finep, fapesp, fapesc, web_curated, open_search, dou, hub_expansion).';

comment on column public.source_runs.mode is
    'Modalidade congelada do canal no momento da execução: '
    'dedicated, curated_web, open_search, official_feed ou hub.';

comment on column public.source_runs.status is
    'running=em andamento; succeeded=concluído; '
    'partial=parcial com falhas absorvidas; failed=falhou; '
    'skipped=pulado (ex. sem credencial, fim de semana).';

comment on column public.source_runs.records_observed is
    'Itens retornados pela fonte/API antes de qualquer filtro.';

comment on column public.source_runs.records_emitted is
    'Itens que atravessaram filtragem/dedup (antes do staging).';

comment on column public.source_runs.records_staged is
    'Itens efetivamente enviados a discovered_opportunities.';

comment on column public.source_runs.error_count is
    'Número de falhas observadas durante a execução. Não negativo.';

comment on column public.source_runs.reason_code is
    'Razão canônica curta quando status != succeeded '
    '(ex. no_credentials, weekend_skip, timeout).';

comment on column public.source_runs.metrics is
    'Contadores adicionais não sensíveis. '
    'NÃO persiste query, URL, conteúdo, traceback, prompt, '
    'resposta LLM ou segredo.';

-- ──────────────────────────────────────────────────────────────────────────
-- 2. discovered_opportunities — atribuição nullable
-- ──────────────────────────────────────────────────────────────────────────

alter table public.discovered_opportunities
    add column if not exists discovery_run_id uuid
        references public.source_runs(id) on delete set null;

alter table public.discovered_opportunities
    add column if not exists discovery_channel text
        check (discovery_channel is null or discovery_channel in (
            'finep', 'fapesp', 'fapesc', 'web_curated',
            'open_search', 'dou', 'hub_expansion'
        ));

alter table public.discovered_opportunities
    add column if not exists query_family text
        check (query_family is null or query_family in (
            'state_innovation_funding',
            'corporate_open_innovation',
            'startup_acceleration',
            'international_brazil_access'
        ));

alter table public.discovered_opportunities
    add column if not exists origin_domain text;

comment on column public.discovered_opportunities.discovery_run_id is
    'UUID da source_run que originou este candidato. '
    'NULL em registros legados (anteriores a RT03-T02).';

comment on column public.discovered_opportunities.discovery_channel is
    'Canal que descobriu a oportunidade. '
    'NULL em registros legados ou quando indisponível.';

comment on column public.discovered_opportunities.query_family is
    'Família de busca que gerou o candidato. '
    'NULL para DOU/hub sem família ou registros legados.';

comment on column public.discovered_opportunities.origin_domain is
    'Hostname normalizado (sem query/path) do domínio de origem. '
    'NULL em registros legados.';
