-- Human-in-the-loop enrichment (ADR discovery-homolog).
-- `edital_link`: link direto pro PDF ou página do edital, preenchido pelo
--   revisor humano quando a extração automática foi insuficiente (SPA, JS).
-- `extraction_quality`: sinaliza se o discovery conseguiu extrair conteúdo
--   rico ('high') ou só o básico ('low') — guia a fila de revisão.

alter table public.discovered_opportunities
  add column edital_link text,
  add column extraction_quality text
    check (extraction_quality in ('high', 'low'));
