-- 016: artefatos do knowledge graph como blobs JSONB (plano de dados → prod)
--
-- Os blobs do grafo (index.json / index_historico.json / icts.json) viviam
-- SÓ em arquivos locais (knowledge_graph/*.json) — gitignored e ausentes da
-- imagem de produção. Como hybrid_match, kg_match, temporal, ict_match e
-- opportunity_discovery liam esses arquivos no request-path, o matching não
-- tinha substrato em prod.
--
-- core/kg_store.py passa a ler desta tabela quando KG_STORE_BACKEND=postgres
-- e o ETL (build_knowledge_graph / build_ict_graph) faz upsert aqui via
-- kg_store.save — publicando o índice para a prod sem redeploy.

create table if not exists public.kg_artifacts (
    key        text        primary key,   -- 'index' | 'index_historico' | 'icts'
    blob       jsonb       not null,
    updated_at timestamptz not null default now()
);

comment on table public.kg_artifacts is
  'Blobs do knowledge graph. Escrito pelo ETL via core.kg_store.save; lido pelo request-path via core.kg_store.load quando KG_STORE_BACKEND=postgres.';

-- Dado público não-tenant. Acesso apenas via service role (backend); RLS
-- habilitada sem policies = nega anon/authenticated. service role bypassa RLS.
alter table public.kg_artifacts enable row level security;

-- Mantém updated_at coerente em cada upsert (rastreia quando o KG foi publicado).
create or replace function public.set_kg_artifacts_updated_at()
returns trigger language plpgsql as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

drop trigger if exists trg_kg_artifacts_updated_at on public.kg_artifacts;
create trigger trg_kg_artifacts_updated_at
  before update on public.kg_artifacts
  for each row execute function public.set_kg_artifacts_updated_at();
