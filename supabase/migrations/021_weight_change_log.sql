-- 021: weight_change_log — trilha de auditoria do auto-apply de pesos (Item 1, Sprint 1)
--
-- O fluxo manual (010) só materializa pesos após aprovação humana explícita.
-- Agora `reflect_workspace` pode AUTO-APLICAR uma weight_suggestion quando os
-- 3 guardas passam simultaneamente: confidence='high' E outcomes>=5 E |delta|<=5.
-- Tudo o que é auto-aplicado precisa ser reversível e logado — esta tabela é a
-- trilha: cada linha registra um delta aplicado (ou um revert), com o insight
-- de origem, a janela de outcomes e a confiança que justificaram a aplicação.
--
-- `reverted_at` NULL = mudança ativa; um revert preenche `reverted_at` na linha
-- original E insere uma nova linha (delta inverso) representando o próprio
-- revert. Filosofia "AI acts, Human can correct": aplica sozinho, mas deixa
-- o rastro completo pro humano desfazer.
--
-- Idempotente: pode rodar mais de uma vez sem efeito colateral.

create table if not exists public.weight_change_log (
  id               uuid primary key default gen_random_uuid(),
  workspace_id     uuid not null references public.workspaces(id) on delete cascade,
  dimension        text not null,
  old_weight       numeric not null,
  new_weight       numeric not null,
  delta            numeric not null,
  confidence       text,
  outcomes_window  int,
  rationale        text,
  insight_id       uuid references public.reflection_insights(id) on delete set null,
  applied_at       timestamptz not null default now(),
  reverted_at      timestamptz
);

create index if not exists weight_change_log_workspace_idx
  on public.weight_change_log (workspace_id, applied_at desc);

alter table public.weight_change_log enable row level security;

-- RLS por workspace, mesmo padrão das demais tabelas workspace-scoped
-- (application_log, reflection_insights): o usuário só vê/manipula linhas de
-- workspaces que possui.
drop policy if exists "weight_change_log_own" on public.weight_change_log;
create policy "weight_change_log_own" on public.weight_change_log
  for all using (
    workspace_id in (select id from public.workspaces where user_id = auth.uid())
  );

comment on table public.weight_change_log is
  'Trilha de auditoria do auto-apply de pesos (reflect_workspace). Cada linha = um delta aplicado a matching_weights ou um revert. reverted_at NULL = ativo. RLS por workspace.';
comment on column public.weight_change_log.reverted_at is
  'NULL = mudança ativa. Preenchido quando o usuário reverte; o revert também insere uma nova linha (delta inverso) com rationale "revert de <id>".';
comment on column public.weight_change_log.outcomes_window is
  'Quantos outcomes a reflexão considerou (outcomes_considered). Um dos 3 guardas do auto-apply (>=5).';
