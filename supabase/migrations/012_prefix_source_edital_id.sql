-- Fase 1 multi-fonte (WIKI.md §12): prefixa edital_id com `{source}:` em todas
-- as tabelas que armazenam referência file-based ao KG. IDs nativos das fontes
-- (FAPESP 18064, FINEP 782, BNDES slugs) colidem entre si — prefixo elimina
-- ambiguidade.
--
-- Hard cut: hoje só há dado FINEP em produção local. Prefixa retroativamente
-- com 'finep:'. Filtro `WHERE NOT LIKE '%:%'` torna a migration idempotente
-- (re-rodar não duplica `finep:finep:782`).
--
-- Tabelas afetadas:
--   - writing_sessions (FK opcional pro KG)
--   - edital_chunks    (unique edital_id + chunk_index)
--   - application_log  (unique workspace_id + edital_id)
--   - pipeline_errors  (edital_id nullable)

update public.writing_sessions
   set edital_id = 'finep:' || edital_id
 where edital_id is not null
   and edital_id not like '%:%';

update public.edital_chunks
   set edital_id = 'finep:' || edital_id
 where edital_id not like '%:%';

update public.application_log
   set edital_id = 'finep:' || edital_id
 where edital_id not like '%:%';

update public.pipeline_errors
   set edital_id = 'finep:' || edital_id
 where edital_id is not null
   and edital_id not like '%:%';
