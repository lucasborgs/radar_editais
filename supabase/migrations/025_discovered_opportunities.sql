-- 025: discovered_opportunities — staging da torneira de descoberta (Parte C)
--
-- A torneira (core/ingestion/opportunity_discovery) deixa de escrever direto no KG
-- (web_raw/ provisorio → chunk → índice). Em vez disso, cada achado pousa AQUI
-- como `pending`. Um humano revê a fila e PROMOVE o que vale: a promoção insere
-- a URL em `web_sources` (migration 018), e o WebScraper a trata como fonte
-- curada (HTML cru → chunk → KG). Link morto / notícia rasa / duplicata morrem
-- na fila sem nunca tocar o RAG. É o gate humano da fonte web ("a IA mostra, o
-- humano decide").
--
-- GLOBAL, não workspace-scoped: a torneira é cron de sistema (sem usuário). RLS
-- espelha web_sources — leitura a authenticated (futura UI de revisão), escrita
-- só via service-role (o worker e os endpoints de promoção/reject).
--
-- Dedup: url_hash UNIQUE (mesma identidade `web:<url_hash>`). A torneira já
-- mantém o ledger (core: discovery_ledger) que evita re-triar URLs vistas, então
-- itens pending/rejeitados não voltam à fila em runs futuras.
--
-- Idempotente: pode rodar mais de uma vez sem efeito colateral.

create table if not exists public.discovered_opportunities (
    id                      uuid primary key default gen_random_uuid(),
    url                     text not null,
    url_hash                text not null unique,
    title                   text,
    agency                  text,
    fonte                   text,
    descricao               text,
    prazo_envio             text,
    publico_alvo            text,
    tema                    text,
    opportunity_type        text,
    raw                     jsonb not null default '{}'::jsonb,
    status                  text not null default 'pending'
                              check (status in ('pending', 'promoted', 'rejected')),
    reject_reason           text,
    created_at              timestamptz not null default now(),
    reviewed_at             timestamptz,
    promoted_web_source_id  uuid references public.web_sources(id) on delete set null
);

create index if not exists discovered_opportunities_queue_idx
  on public.discovered_opportunities (status, created_at desc);

alter table public.discovered_opportunities enable row level security;

-- Leitura liberada a authenticated (UI de revisão futura); escrita só
-- service-role (sem policy de insert/update/delete → o worker e os endpoints
-- usam get_supabase_service, que bypassa RLS). Espelha web_sources.
drop policy if exists discovered_opportunities_read_authenticated on public.discovered_opportunities;
create policy discovered_opportunities_read_authenticated
  on public.discovered_opportunities
  for select
  to authenticated
  using (true);

comment on table public.discovered_opportunities is
  'Staging da torneira de descoberta (Parte C). Achados chegam pending; humano promove → web_sources (vira fonte curada) ou rejeita. Nada entra no KG sem promoção. GLOBAL, RLS espelha web_sources.';
comment on column public.discovered_opportunities.status is
  'pending = na fila; promoted = virou web_source; rejected = descartado pelo humano.';
comment on column public.discovered_opportunities.promoted_web_source_id is
  'web_sources.id criado/casado na promoção. NULL enquanto pending/rejected.';
comment on column public.discovered_opportunities.raw is
  'Registro bruto do _extract da torneira (inclui texto_cru), para promoção/debug.';
