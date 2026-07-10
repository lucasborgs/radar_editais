-- 036: schema gold v3 (Fase 0 da spec docs/specs/v3-unified.md) — 2026-07-09
--
-- Dissociação Match / KG / RAG. Introduz 4 tabelas NOVAS que passam a ser a
-- source-of-truth do catálogo e do match v3. É PURAMENTE ADITIVO: o match v2
-- (hypergraph_match + hyperextract, blobs em kg_artifacts) continua intocado —
-- nenhuma tabela existente é alterada aqui.
--
--   entities              — nós do catálogo (edital/programa/investidor/ict/agencia)
--   entity_relationships  — 4 arestas determinísticas (operado_por, subordinado_a,
--                           exige_parceria_com, credenciada_por)
--   match_chunks          — superfície pública do match (embed cru do silver)
--   company_chunks        — lado empresa (dado de tenant, RLS por workspace_id)
--
-- entities/entity_relationships/match_chunks são CATÁLOGO PÚBLICO (sem dado de
-- tenant): RLS ligada com leitura para `authenticated` (mesmo padrão de
-- edital_chunks, migration 004) — escrita só via service-role (ingest gold.py).
-- company_chunks carrega dado do tenant: RLS "own" por workspace_id (padrão
-- writing_sessions/reflection_insights). Ver §5 da spec.
--
-- Aplicar SOMENTE no Supabase local nesta fase (supabase migration up). O push
-- para o remoto é decisão de deploy separada.

-- ────────────────────────────────────────────────────────────
-- 1. entities — nós do catálogo (§5.1)
-- ────────────────────────────────────────────────────────────
create table if not exists public.entities (
    id                uuid primary key default gen_random_uuid(),
    kind              text not null check (kind in ('edital','programa','investidor','ict','agencia')),
    -- supertipo derivado do kind (oportunidade × ator) — sempre consistente.
    type              text generated always as (
                        case when kind in ('edital','programa') then 'oportunidade' else 'ator' end
                      ) stored,
    source            text not null,   -- finep, fapesp, fapesc, web, embrapii, curadoria
    native_id         text not null,   -- id original bronze/silver ("finep:589", "investidor:indicator-capital")
    name              text not null,
    description       text not null default '',   -- texto silver/curado (~2000 chars)
    mecanismo         text check (mecanismo in ('subvencao','bolsa','parceria_pd','premio','equity')),
    formato           text,            -- edital_periodico | fluxo_continuo | credenciamento
    setores           text[] not null default '{}',
    tecnologias_tags  text[] not null default '{}',
    status            text,            -- aberta | encerrada | ativa | inativa (NULL se n/a)
    deadline          date,            -- NULL = fluxo contínuo / sem prazo
    uf                text,            -- display/constraint (NULL = nacional)
    ticket_min        numeric,         -- display (cards)
    ticket_max        numeric,         -- display (cards)
    constraints       jsonb not null default '[]',   -- vocabulário §4.4, avaliado por eligibility.py
    requisitos_texto  text[] not null default '{}',  -- resíduo não-estruturado (informa sem gate)
    curated           bool not null default false,
    verificado_em     date,
    metadata          jsonb not null default '{}',   -- campos por kind (estagio_alvo/contact/cadencia/…)
    embedding         vector(1536),    -- embed da description (busca semântica + trilha investidor)
    updated_at        timestamptz not null default now(),
    unique (source, native_id)
);

-- Validação de setores (1-3 itens, vocabulário fechado) é feita em APLICAÇÃO no
-- ingest — não em CHECK: `array_length` passa por semântica de NULL e dá falsa
-- segurança (ver §5.1 da spec).

create index if not exists idx_entities_kind on public.entities(kind);
create index if not exists idx_entities_status_deadline on public.entities(status, deadline);
create index if not exists idx_entities_setores on public.entities using gin(setores);
create index if not exists idx_entities_tags on public.entities using gin(tecnologias_tags);
-- HNSW para a busca semântica do explore/catálogo + trilha investidor (Stage 2/§8).
-- Build instantâneo em tabela vazia; a coluna é nullable mas o ingest garante NOT NULL.
create index if not exists idx_entities_embedding
  on public.entities using hnsw (embedding vector_cosine_ops);

alter table public.entities enable row level security;
-- Catálogo público: leitura para authenticated; escrita só via service-role.
create policy "entities_read_authenticated" on public.entities
  for select to authenticated using (true);


-- ────────────────────────────────────────────────────────────
-- 2. entity_relationships — arestas determinísticas (§5.2)
-- ────────────────────────────────────────────────────────────
create table if not exists public.entity_relationships (
    id          uuid primary key default gen_random_uuid(),
    source_id   uuid not null references public.entities(id) on delete cascade,
    target_id   uuid not null references public.entities(id) on delete cascade,
    type        text not null check (type in ('operado_por','subordinado_a','exige_parceria_com','credenciada_por')),
    properties  jsonb not null default '{}',
    unique (source_id, target_id, type)
);
create index if not exists idx_er_source on public.entity_relationships(source_id);
create index if not exists idx_er_target on public.entity_relationships(target_id);

alter table public.entity_relationships enable row level security;
create policy "entity_relationships_read_authenticated" on public.entity_relationships
  for select to authenticated using (true);


-- ────────────────────────────────────────────────────────────
-- 3. match_chunks — superfície do match (corpus público, §5.3)
-- ────────────────────────────────────────────────────────────
create table if not exists public.match_chunks (
    id            uuid primary key default gen_random_uuid(),
    entity_id     uuid not null references public.entities(id) on delete cascade,
    idx           int not null,
    section_path  text[] not null default '{}',
    kind          text,             -- kind do bloco silver (heading/paragraph/…)
    text          text not null,
    embedding     vector(1536) not null,   -- embed CRU do silver (sem contextual retrieval — §5.3)
    unique (entity_id, idx)
);
create index if not exists idx_match_chunks_entity on public.match_chunks(entity_id);
create index if not exists idx_match_chunks_embedding
  on public.match_chunks using hnsw (embedding vector_cosine_ops);

alter table public.match_chunks enable row level security;
create policy "match_chunks_read_authenticated" on public.match_chunks
  for select to authenticated using (true);


-- ────────────────────────────────────────────────────────────
-- 4. company_chunks — lado empresa (dado de tenant, §5.4)
--    População é da Fase 2; nasce VAZIA mas com RLS desde já (critério de
--    aceite: leak-test durável, tests/test_company_chunks_rls.py).
-- ────────────────────────────────────────────────────────────
create table if not exists public.company_chunks (
    id            uuid primary key default gen_random_uuid(),
    workspace_id  uuid not null references public.workspaces(id) on delete cascade,
    origin        text not null check (origin in ('profile','library_doc','hyde')),
    doc_id        uuid,             -- FK lógica p/ content_library quando origin=library_doc
    text          text not null,
    embedding     vector(1536) not null,
    updated_at    timestamptz not null default now()
);
create index if not exists idx_company_chunks_workspace on public.company_chunks(workspace_id);
create index if not exists idx_company_chunks_embedding
  on public.company_chunks using hnsw (embedding vector_cosine_ops);

alter table public.company_chunks enable row level security;
-- RLS "own" por workspace_id (mesmo padrão de writing_sessions/reflection_insights):
-- o tenant só lê/escreve linhas dos workspaces que possui.
create policy "company_chunks_own" on public.company_chunks
  for all
  using (
    workspace_id in (select id from public.workspaces where user_id = auth.uid())
  )
  with check (
    workspace_id in (select id from public.workspaces where user_id = auth.uid())
  );
