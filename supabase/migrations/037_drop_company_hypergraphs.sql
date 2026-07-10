-- 037: drop de company_hypergraphs (Fase 2 do v3 — spec docs/specs/v3-unified.md)
--
-- O lado empresa do match deixou de ser o hipergrado extraído por LLM
-- (company_hypergraphs, migration 033) e passou a ser texto real chunkado em
-- `company_chunks` (migration 036, RLS "own"). O caminho de código inteiro
-- (core/services/company_corpus.py, run_hyper_extract_company, task
-- build_company_hypergraph, endpoints /profile/corpus*) foi deletado nesta
-- fase — a tabela fica órfã e é removida. Dado é derivado/reconstituível
-- (nunca foi fonte de verdade), então o drop não perde nada de usuário.
--
-- ATENÇÃO deploy: o remoto tem drift pendente na migration 033 — reparar o
-- drift ANTES de aplicar esta (senão o histórico do CLI diverge de novo).

drop table if exists public.company_hypergraphs;
