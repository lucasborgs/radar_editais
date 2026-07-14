-- 033: Hipergrado da EMPRESA durável por workspace (Sprint 2 — Company corpus).
-- Spec: docs/specs/hypergraph-architecture.md (§ Lado empresa).
--
-- Hoje o match (find_matching_editais) re-extrai os nós da empresa do texto do
-- perfil a cada sessão (cache efêmero por processo). Esta tabela persiste o
-- hipergrado da empresa (extraído do corpus: perfil + content_items) por workspace,
-- para o match reusar nós duráveis e mais ricos — e para dar feedback de completude.
-- Escrita/leitura via core.kg.kg_store.{save,load}_company_hypergraph.

create table if not exists public.company_hypergraphs (
    workspace_id uuid        primary key references public.workspaces(id) on delete cascade,
    nodes        jsonb       not null default '[]'::jsonb,
    edges        jsonb       not null default '[]'::jsonb,
    completude   float       not null default 0,   -- 0..1 (ver company_corpus.completude_score)
    corpus_urls  text[]      not null default '{}', -- proveniência do corpus extraído
    n_docs       int         not null default 0,    -- nº de documentos no corpus
    updated_at   timestamptz not null default now()
);

comment on table public.company_hypergraphs is
    'Hipergrado da empresa (lado DEMANDA do match) por workspace, extraído do corpus. Sprint 2.';

alter table public.company_hypergraphs enable row level security;

-- RLS: o dono do workspace lê o próprio hipergrado (a escrita é via service-role na
-- task, que bypassa RLS — mesma postura de content_items/enrich).
drop policy if exists "company_hypergraph_select_own" on public.company_hypergraphs;
create policy "company_hypergraph_select_own" on public.company_hypergraphs
    for select using (
        workspace_id in (select id from public.workspaces where user_id = auth.uid())
    );
