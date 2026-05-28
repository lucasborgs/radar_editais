-- 010: aprovação humana de weight_suggestions vindas do reflection_service (Gap 2)
--
-- `ReflectionService` já gera weight_suggestions em reflection_insights.evidence,
-- e a tabela matching_weights já suporta override por workspace. O que faltava
-- era o canal de aprovação: usuário revisar uma sugestão e materializá-la como
-- peso real do workspace.
--
-- Adiciona rastreabilidade explícita (qual insight gerou cada peso, quando foi
-- aprovado) — fecha o Loop C sem aplicação automática (mantém "AI drafts,
-- humans decide": pesos só mudam após confirmação humana).

alter table public.matching_weights
  add column if not exists approved_from_insight_id uuid
    references public.reflection_insights(id) on delete set null,
  add column if not exists approved_at timestamptz;

-- Acelera lookup de "esta sugestão já foi aplicada?" no endpoint
-- GET /me/weights/pending — partial index porque a maioria das rows é manual.
create index if not exists matching_weights_approved_insight_idx
  on public.matching_weights (approved_from_insight_id)
  where approved_from_insight_id is not null;

comment on column public.matching_weights.approved_from_insight_id is
  'FK para reflection_insights.id se este peso veio de aprovação de uma sugestão. NULL para pesos manuais ou globais.';
comment on column public.matching_weights.approved_at is
  'Timestamp da aprovação humana. NULL para pesos manuais (UI direta) ou globais (seed).';
