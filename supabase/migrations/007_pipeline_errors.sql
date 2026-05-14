-- ============================================================
-- Migration 007 — Pipeline #28: Taxonomia de falhas ETL
--
-- Registra cada falha do pipeline com categoria, contexto e traceback.
-- Permite (1) retry diferenciado por causa, (2) alerta de degradação por
-- categoria, (3) dashboard de saúde do ETL.
--
-- Tabela é escrita exclusivamente por jobs/scripts com service-role key
-- (ETL não tem contexto de usuário). RLS habilitada sem policies para
-- usuários finais — apenas operadores via service-role consultam.
-- ============================================================

create table if not exists public.pipeline_errors (
  id          bigserial primary key,
  source      text not null,                       -- "FINEP", "FAPESP", "BNDES", etc.
  edital_id   text,                                -- null se erro de listagem (antes de identificar edital)
  category    text not null check (category in (
                'timeout',          -- request lento, retry com backoff
                'parse_error',      -- HTML mudou estrutura, requer intervenção manual
                'schema_violation', -- edital fora do vocabulário autorizado (quarentena)
                'llm_refusal',      -- LLM recusou enriquecimento, skip com log
                'duplicate',        -- hash já existe no bronze, skip silencioso
                'unknown'           -- não classificada (capturado por handler genérico)
              )),
  message     text not null,
  traceback   text,
  context     jsonb not null default '{}'::jsonb,  -- url, payload, parâmetros relevantes
  occurred_at timestamptz not null default now()
);

create index if not exists pipeline_errors_lookup_idx
  on public.pipeline_errors (source, category, occurred_at desc);

create index if not exists pipeline_errors_edital_idx
  on public.pipeline_errors (edital_id) where edital_id is not null;

alter table public.pipeline_errors enable row level security;
-- Intencionalmente sem CREATE POLICY: apenas service-role escreve e consulta.
