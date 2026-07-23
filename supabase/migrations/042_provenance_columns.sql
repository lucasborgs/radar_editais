-- 042: colunas aditivas de proveniência (RT01-T04, spec docs/specs/radar-data-trust-01-provenance.md §6)
--
-- Prepara o storage gold para o dual-write de proveniência sem introduzir
-- nenhum produtor ou consumidor novo. Estritamente ADITIVA e IDEMPOTENTE
-- (`if not exists` em toda alteração):
--
--   entities.provenance              jsonb not null default '{}'
--   entity_relationships.provenance  jsonb not null default '{}'
--   match_chunks.document / page / silver_block_idx / source_hash (nullable)
--
-- NENHUM índice novo (não há leitor ainda), NENHUMA policy/grant/RLS,
-- NENHUM drop/rename/alter de coluna existente. Registros anteriores a esta
-- migration ficam com `provenance = '{}'::jsonb` e as 4 colunas novas de
-- `match_chunks` em NULL — o "legado" identificável exigido pela spec §9.1
-- (default seguro, não `state=stated` implícito).
--
-- Aplicar SOMENTE no Supabase local (RT01-T04). Push para o remoto é decisão
-- de deploy separada.

-- ────────────────────────────────────────────────────────────
-- 1. entities.provenance (spec §6.1)
-- ────────────────────────────────────────────────────────────
alter table public.entities
  add column if not exists provenance jsonb not null default '{}'::jsonb;

comment on column public.entities.provenance is
  'FactProvenance por path de fato (chave = path estável, ex. "deadline", '
  '"constraints.porte"), spec docs/specs/radar-data-trust-01-provenance.md '
  '§4.3/§6.1. Default {} = registro legado (state=unknown), não state=stated '
  'implícito. Escrita só via service-role; leitura segue as policies de '
  'entities existentes desde a migration 036.';


-- ────────────────────────────────────────────────────────────
-- 2. entity_relationships.provenance (spec §6.1)
-- ────────────────────────────────────────────────────────────
alter table public.entity_relationships
  add column if not exists provenance jsonb not null default '{}'::jsonb;

comment on column public.entity_relationships.provenance is
  'FactProvenance da própria aresta (não por path), spec '
  'docs/specs/radar-data-trust-01-provenance.md §4.3/§6.1. Default {} = '
  'relação legada sem evidência resolvida ainda. Escrita só via service-role; '
  'leitura segue as policies de entity_relationships existentes desde a '
  'migration 036.';


-- ────────────────────────────────────────────────────────────
-- 3. match_chunks — coordenadas mínimas compatíveis com silver (spec §6.2)
-- ────────────────────────────────────────────────────────────
alter table public.match_chunks
  add column if not exists document text,
  add column if not exists page int,
  add column if not exists silver_block_idx int,
  add column if not exists source_hash text;

comment on column public.match_chunks.document is
  'Nome/identificador do documento silver de origem do chunk, spec '
  'docs/specs/radar-data-trust-01-provenance.md §6.2. NULL = chunk anterior '
  'a esta migration (legado); `_replace_match_chunks` (delete+insert) zera '
  'esta coluna em re-ingest até um produtor em dual-write escrevê-la.';

comment on column public.match_chunks.page is
  'Página 1-based do bloco silver de origem, quando o documento tem unidade '
  'paginada (spec §6.2/§4.2). NULL para HTML sem página ou chunk legado.';

comment on column public.match_chunks.silver_block_idx is
  'Índice do bloco silver (`idx`) que originou o chunk, spec §6.2. NULL para '
  'chunk legado ou quando o empacotamento não preserva 1:1 o bloco de origem.';

comment on column public.match_chunks.source_hash is
  'Hash da versão silver/canônica de origem (mesma convenção de '
  'EvidenceRef.silver_source_hash/canonical_content_hash, spec §4.2/§6.2). '
  'NULL = chunk legado sem versão de origem rastreada.';
