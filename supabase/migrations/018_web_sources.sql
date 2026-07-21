-- 018 — fonte web genérica: seed list de URLs (web_sources)
--
-- A fonte `web` (docs/domain/schema.md §12.4, strategy=html_clean) não tem listagem
-- estruturada como FINEP/FAPESP. As URLs a indexar são curadas: vivem nesta
-- tabela operacional (dado mutável por deploy/UI, não schema). O WebScraper
-- (pipeline/extractors/web.py) lê as linhas `active` via service-role, busca
-- cada URL, e grava HTML cru em bronze_data/web_raw/. Cada URL vira um edital
-- `web:<url_hash>` (1 URL = 1 edital).
--
-- Idempotente: pode rodar mais de uma vez sem efeito colateral.

create table if not exists public.web_sources (
    id          uuid primary key default gen_random_uuid(),
    url         text not null unique,
    label       text,
    active      boolean not null default true,
    created_at  timestamptz not null default now()
);

comment on table public.web_sources is
  'Seed list curada de URLs para a fonte web genérica (L0). Lida pelo WebScraper via service-role.';

-- RLS: escrita só via service-role (sem policy de insert/update/delete →
-- anon/authenticated não escrevem; o worker usa get_supabase_service que
-- bypassa RLS). Leitura liberada a authenticated para uma futura UI de
-- gestão de URLs (espelha o padrão de edital_chunks_read_authenticated).
alter table public.web_sources enable row level security;

drop policy if exists web_sources_read_authenticated on public.web_sources;
create policy web_sources_read_authenticated
  on public.web_sources
  for select
  to authenticated
  using (true);
