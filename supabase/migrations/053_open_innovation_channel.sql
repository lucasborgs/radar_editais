-- SCV1-T04: o canal Deep Research também alimenta a staging web, sem publicar
-- fatos no catálogo. O caminho aberto lê esta fila com estado de revisão.
alter table public.discovered_opportunities
    drop constraint if exists discovered_opportunities_discovery_channel_check;

alter table public.discovered_opportunities
    add constraint discovered_opportunities_discovery_channel_check
    check (discovery_channel is null or discovery_channel in (
        'finep', 'fapesp', 'fapesc', 'web_curated',
        'open_search', 'dou', 'hub_expansion', 'deep_research'
    ));
