-- 049: deep_research como discovery_channel da staging (RT03-T04,
--      spec discovery-deep-research.md)
--
-- Aditivo e idempotente. O canal Deep Research pousa achados no MESMO staging
-- `discovered_opportunities` (status=pending) com `discovery_channel =
-- 'deep_research'`, preservando o gate humano e a promoção canônica existente.
-- Aqui apenas estendemos o CHECK da coluna (migration 043) com o novo canal.
-- Sem backfill; registros legados permanecem NULL.

alter table public.discovered_opportunities
    drop constraint if exists discovered_opportunities_discovery_channel_check;

alter table public.discovered_opportunities
    add constraint discovered_opportunities_discovery_channel_check
    check (discovery_channel is null or discovery_channel in (
        'finep', 'fapesp', 'fapesc', 'web_curated',
        'open_search', 'dou', 'hub_expansion', 'deep_research'
    ));

comment on column public.discovered_opportunities.discovery_channel is
    'Canal que descobriu a oportunidade: scraper dedicado, web_curated, '
    'open_search, dou, hub_expansion ou deep_research. '
    'NULL em registros legados ou quando indisponível.';
