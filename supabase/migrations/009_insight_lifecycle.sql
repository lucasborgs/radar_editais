-- 009: lifecycle de invalidação em reflection_insights (Gap 3b)
--
-- Hoje `reflection_insights.active` é boolean default true, mas nada nunca
-- atualiza pra false — insights antigos contaminam contextos atuais sem
-- expiração. Esta migration adiciona o lifecycle explícito:
--
--   deactivated_at           timestamp da invalidação (NULL = ativo)
--   deactivated_by_insight_id qual insight novo superou este (audit)
--   deactivation_reason       texto explicativo (ex: "TRL da empresa subiu...")
--   confidence                low|medium|high, promovido de evidence JSONB
--                             para coluna real (usado em mitigação de bias
--                             auto-consistente em Gap 3a)
--
-- A coluna `active` legacy é mantida e sincronizada via trigger — código
-- consumidor antigo continua funcionando, mas a fonte da verdade é
-- `deactivated_at`. A coluna `active` será removida em migration futura
-- quando nenhum leitor depender dela.

alter table public.reflection_insights
  add column if not exists deactivated_at timestamptz,
  add column if not exists deactivated_by_insight_id uuid
    references public.reflection_insights(id) on delete set null,
  add column if not exists deactivation_reason text,
  add column if not exists confidence text
    check (confidence in ('low', 'medium', 'high'));

-- Backfill confidence a partir do evidence JSONB (level 2 carrega lá hoje).
update public.reflection_insights
   set confidence = evidence->>'confidence'
 where confidence is null
   and evidence ? 'confidence'
   and evidence->>'confidence' in ('low', 'medium', 'high');

-- Backfill consistency: rows que já estavam active=false ganham
-- deactivated_at retroativo (now() — sem timestamp histórico disponível).
update public.reflection_insights
   set deactivated_at = now(),
       deactivation_reason = 'backfill: row tinha active=false antes da migration 009'
 where active = false
   and deactivated_at is null;

-- Trigger: mantém `active` derivado de `deactivated_at` para compatibilidade
-- com código consumidor antigo. INSERTs com active=true continuam ok; ninguém
-- precisa setar active explicitamente.
create or replace function public.sync_reflection_insight_active() returns trigger as $$
begin
  new.active = (new.deactivated_at is null);
  return new;
end;
$$ language plpgsql;

drop trigger if exists reflection_insights_sync_active on public.reflection_insights;
create trigger reflection_insights_sync_active
  before insert or update on public.reflection_insights
  for each row execute function public.sync_reflection_insight_active();

-- Novo índice: load_active_insights filtra por deactivated_at IS NULL.
-- Partial index pra cobrir o caso comum (insights ativos).
create index if not exists reflection_insights_lifecycle_idx
  on public.reflection_insights (workspace_id, level desc, created_at desc)
  where deactivated_at is null;

comment on column public.reflection_insights.deactivated_at is
  'Timestamp de invalidação do insight (NULL = ativo). Setado por Gap 3a (full mode reflection) quando um insight novo supera este.';
comment on column public.reflection_insights.deactivated_by_insight_id is
  'FK para o insight novo que superou este. Audit trail.';
comment on column public.reflection_insights.deactivation_reason is
  'Justificativa do LLM ao desativar o insight. Ex: "TRL da empresa subiu de 4-5 para 7-8 nos últimos 18 meses".';
comment on column public.reflection_insights.confidence is
  'Promovido de evidence JSONB para coluna real. Usado em Gap 3a para mitigar bias auto-consistente (deactivation requer confidence >= confidence do insight invalidado).';
