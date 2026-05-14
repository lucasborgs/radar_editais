-- 008: metadata por chunk pra filtro no retrieval
--
-- Adiciona coluna JSONB nullable a `edital_chunks`. Coluna é populada pelo
-- chunker (`core.chunker._detect_metadata`) e contém flags de tipo de
-- conteúdo: contem_tabela, contem_data, contem_valor_financeiro,
-- contem_elegibilidade, contem_criterios.
--
-- Forward-compatible: rows existentes recebem `{}` como default. Vão ser
-- backfilladas com flags reais no próximo reindex (idempotente — task faz
-- DELETE+INSERT por edital_id).
--
-- Uso típico no retrieval:
--   WHERE edital_id = $1 AND metadata->>'contem_data' = 'true'
--
-- Índice GIN cobre a busca por chaves/valores arbitrários no JSONB.

alter table public.edital_chunks
  add column if not exists metadata jsonb not null default '{}'::jsonb;

create index if not exists edital_chunks_metadata_idx
  on public.edital_chunks using gin (metadata);

comment on column public.edital_chunks.metadata is
  'Flags de tipo de conteúdo (contem_tabela, contem_data, contem_valor_financeiro, contem_elegibilidade, contem_criterios). Populadas por core.chunker._detect_metadata. Vide core/chunker.py.';
