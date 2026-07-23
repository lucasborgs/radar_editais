-- 041 — Relevance classification columns for discovered_opportunities (RT00-T04).
--
-- Aditivo, default-safe. Registros existentes ou ainda não classificados
-- aparecem como 'unclassified'. Falha operacional preservada como 'error',
-- nunca como 'out_of_scope' nem como exclusão do candidato.
--
-- O produtor destas colunas é radar.core.ingestion.relevance_classifier;
-- nenhuma coluna editorial (status, reviewed_at, reject_reason) é alterada.
-- promote/reject continuam dependendo exclusivamente da decisão humana.

alter table public.discovered_opportunities
  add column if not exists relevance_status text
    not null default 'unclassified'
    check (relevance_status in ('unclassified', 'classified', 'error'));

alter table public.discovered_opportunities
  add column if not exists relevance_verdict jsonb;

alter table public.discovered_opportunities
  add column if not exists relevance_error text;

alter table public.discovered_opportunities
  add column if not exists relevance_classified_at timestamptz;

comment on column public.discovered_opportunities.relevance_status is
  'unclassified = não processado; classified = classificado (relevance_verdict preenchido); error = falha operacional';
comment on column public.discovered_opportunities.relevance_verdict is
  'Veredito completo: {decision, reason_codes, exclusion_codes, evidence, missing_information, classifier_version}';
comment on column public.discovered_opportunities.relevance_error is
  'Mensagem sanitizada (parse_failure, timeout, provider_error, contract_violation, grounding_error)';
comment on column public.discovered_opportunities.relevance_classified_at is
  'Timestamp da última classificação de relevância';
