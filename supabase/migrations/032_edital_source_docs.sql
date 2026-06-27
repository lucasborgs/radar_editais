-- 032: Documento Canônico (§12.3) durável por edital — robustez contra disco
-- efêmero do worker (2026-06-27). Spec: docs/specs/durable-source-docs.md
--
-- O FS do worker no Railway é EFÊMERO: cada redeploy apaga data/bronze + os PDFs
-- FINEP. Como o chunk_edital (lazy) lê o conteúdo-fonte via adapters que leem o
-- disco, todo redeploy fazia o chunking produzir 0 chunks. Esta tabela persiste
-- o Documento Canônico (o contrato agnóstico §12.3 — [{doc_name, units}]),
-- gravado no scrape (disco fresco); o chunk_edital passa a ler daqui e o disco
-- vira só cache/fallback. Escrita via core.kg.source_docs.save; leitura via
-- core.kg.source_docs.load. Espelha o seam de kg_artifacts (016).

create table if not exists public.edital_source_docs (
    edital_id     text        primary key,   -- prefixado: 'finep:782'
    source        text        not null,      -- 'finep'|'fapesp'|'fapesc'|'web'
    canonical_doc jsonb       not null,       -- [{doc_name, units:[...]}]  (§12.3)
    content_hash  text,                       -- md5 do canonical_doc (observabilidade)
    updated_at    timestamptz not null default now()
);

comment on table public.edital_source_docs is
  'Documento Canônico (§12.3) durável por edital. Escrito no scrape via core.kg.source_docs.save; lido pelo chunk_edital via core.kg.source_docs.load. Robustez contra o disco efêmero do worker (data/bronze some a cada redeploy).';

-- Dado público não-tenant. Acesso apenas via service role (worker); RLS
-- habilitada sem policies = nega anon/authenticated. service role bypassa RLS.
alter table public.edital_source_docs enable row level security;

-- Mantém updated_at coerente em cada upsert (rastreia quando o doc foi publicado).
create or replace function public.set_edital_source_docs_updated_at()
returns trigger language plpgsql as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

drop trigger if exists trg_edital_source_docs_updated_at on public.edital_source_docs;
create trigger trg_edital_source_docs_updated_at
  before update on public.edital_source_docs
  for each row execute function public.set_edital_source_docs_updated_at();
