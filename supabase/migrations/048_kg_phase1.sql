-- 048: projeção da Fase 1 do grafo (schema kg_phase1) — 2026-08-01
--
-- Projeção DURÁVEL, reconstruível e ISOLADA da Fase 1 determinística do grafo
-- (validada na spike `kg-structure-aware`, SPEC §8; ver
-- docs/execution/kg-phase1-production/). Reprojeta o gold
-- (public.entities + public.entity_relationships) em um schema próprio, PRONTO
-- para consumo futuro pelo Explorar — sem tocar em nada do gold, match, RAG ou
-- da Fase 2 (extração LLM — fora do escopo desta etapa).
--
-- Modelo de GERAÇÕES com troca atômica:
--   * cada build grava uma NOVA geração (nós/qualidade/arestas/comunidades
--     indexados por generation_id) dentro de UMA transação;
--   * a geração só vira `is_current=true` no MESMO commit do build — leitores
--     nunca observam uma geração incompleta;
--   * falha → rollback → a última geração saudável permanece corrente (um
--     registro `failed` fica no ledger, best-effort, fora da transação do build).
--
-- Arestas carregam `origin` (CHECK fechado) — a ORIGEM LÓGICA preservada:
--   phase1_deterministic  fatos copiados do gold (setores/tags/estágio/UF/
--                         mecanismo/TRL)
--   phase1_structural     cópia das relações estruturais do gold
--                         (entity_relationships: operado_por/subordinado_a/
--                         credenciada_por)
--   phase1_similarity     similar_a = cosseno dos embeddings (DERIVADA; NÃO é
--                         fato documental)
--   phase1_tech_bridge    potencial_parceria = heurística Jaccard de tecnologia
--                         (DERIVADA; NÃO é fato documental)
--
-- Aplicar SOMENTE no Supabase local nesta fase (supabase migration up). O push
-- para o remoto é decisão de deploy separada (mesma postura da migration 036).

create schema if not exists kg_phase1;

-- ────────────────────────────────────────────────────────────
-- 1. generations — ledger das projeções + ponteiro da corrente
-- ────────────────────────────────────────────────────────────
create table if not exists kg_phase1.generations (
    id            bigint generated always as identity primary key,
    status        text not null check (status in ('building', 'healthy', 'failed')),
    is_current    boolean not null default false,
    build_version text not null,
    source_hash   text not null default '',   -- hash determinístico do gold lido
    counts        jsonb not null default '{}',
    error         text not null default '',   -- sempre SANITIZADO (sem conteúdo/URL/payload)
    started_at    timestamptz not null default now(),
    finished_at   timestamptz
);
-- Exatamente UMA geração corrente — a única observável pelos leitores.
create unique index if not exists kg_phase1_generations_one_current
    on kg_phase1.generations (is_current) where is_current;

-- ────────────────────────────────────────────────────────────
-- 2. nodes — substâncias (espelho do gold; id determinístico)
-- ────────────────────────────────────────────────────────────
create table if not exists kg_phase1.nodes (
    generation_id bigint not null references kg_phase1.generations(id) on delete cascade,
    id            text not null,          -- <kind>:<native_id> (ex.: edital:finep:589)
    kind          text not null,
    native_id     text not null,
    name          text not null,
    description   text not null default '',
    embedding     vector(1536),           -- cópia do embedding do gold (fonte do similar_a)
    primary key (generation_id, id)
);

-- ────────────────────────────────────────────────────────────
-- 3. quality_nodes — acidentes materializados
--    (setor/tecnologia/estagio/uf/mecanismo/faixa_trl)
-- ────────────────────────────────────────────────────────────
create table if not exists kg_phase1.quality_nodes (
    generation_id bigint not null references kg_phase1.generations(id) on delete cascade,
    id            text not null,          -- <family>:<value> (ex.: setor:agro)
    family        text not null,
    value         text not null,
    primary key (generation_id, id)
);

-- ────────────────────────────────────────────────────────────
-- 4. edges — arestas tipadas com peso e ORIGEM LÓGICA
-- ────────────────────────────────────────────────────────────
-- source_id/target_id referenciam nodes OU quality_nodes (alvo polimórfico) —
-- por isso sem FK de borda, como na spike. `type` SEM CHECK (vocabulário
-- governado em aplicação); `origin` com CHECK fechado nas 4 origens.
create table if not exists kg_phase1.edges (
    generation_id bigint not null references kg_phase1.generations(id) on delete cascade,
    id            bigint generated always as identity primary key,
    source_id     text not null,
    target_id     text not null,
    type          text not null,
    weight        double precision not null default 1.0,
    properties    jsonb not null default '{}',
    origin        text not null check (origin in (
                      'phase1_deterministic', 'phase1_structural',
                      'phase1_similarity', 'phase1_tech_bridge'
                  )),
    unique (generation_id, source_id, target_id, type)
);
create index if not exists kg_phase1_idx_edges_source on kg_phase1.edges(generation_id, source_id);
create index if not exists kg_phase1_idx_edges_target on kg_phase1.edges(generation_id, target_id);
create index if not exists kg_phase1_idx_edges_type   on kg_phase1.edges(generation_id, type);

-- ────────────────────────────────────────────────────────────
-- 5. communities — saída do Louvain (por geração)
-- ────────────────────────────────────────────────────────────
create table if not exists kg_phase1.communities (
    generation_id bigint not null references kg_phase1.generations(id) on delete cascade,
    community_id  text not null,          -- com_<idx>
    node_id       text not null,
    primary key (generation_id, community_id, node_id)
);
create index if not exists kg_phase1_idx_communities_cid
    on kg_phase1.communities(generation_id, community_id);

-- ────────────────────────────────────────────────────────────
-- RLS — projeção é catálogo público DERIVADO: leitura para `authenticated`
-- (mesmo padrão do gold, migration 036); escrita só via service-role/backend
-- (por default a RLS nega tudo fora do SELECT autorizado).
-- ────────────────────────────────────────────────────────────
alter table kg_phase1.generations   enable row level security;
alter table kg_phase1.nodes         enable row level security;
alter table kg_phase1.quality_nodes enable row level security;
alter table kg_phase1.edges         enable row level security;
alter table kg_phase1.communities   enable row level security;

drop policy if exists "generations_read_authenticated" on kg_phase1.generations;
create policy "generations_read_authenticated" on kg_phase1.generations
    for select to authenticated using (true);
drop policy if exists "nodes_read_authenticated" on kg_phase1.nodes;
create policy "nodes_read_authenticated" on kg_phase1.nodes
    for select to authenticated using (true);
drop policy if exists "quality_nodes_read_authenticated" on kg_phase1.quality_nodes;
create policy "quality_nodes_read_authenticated" on kg_phase1.quality_nodes
    for select to authenticated using (true);
drop policy if exists "edges_read_authenticated" on kg_phase1.edges;
create policy "edges_read_authenticated" on kg_phase1.edges
    for select to authenticated using (true);
drop policy if exists "communities_read_authenticated" on kg_phase1.communities;
create policy "communities_read_authenticated" on kg_phase1.communities
    for select to authenticated using (true);
