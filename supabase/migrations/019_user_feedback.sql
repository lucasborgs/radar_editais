-- 019 — user_feedback: canal de feedback do tester (build-in-public)
--
-- Feedback livre enviado pelo usuário autenticado (POST /feedback). `context`
-- (jsonb) carrega metadados leves do cliente (página/url/session_id) para
-- situar o relato sem PII. RLS: cada usuário insere/lê o próprio; o fundador
-- lê tudo via service role (fora do RLS).
--
-- Idempotente: pode rodar mais de uma vez sem efeito colateral.

create table if not exists public.user_feedback (
  id          bigserial primary key,
  user_id     uuid not null default auth.uid(),
  message     text not null,
  context     jsonb not null default '{}'::jsonb,
  created_at  timestamptz not null default now()
);

create index if not exists user_feedback_user_idx
  on public.user_feedback (user_id, created_at desc);

alter table public.user_feedback enable row level security;

drop policy if exists "user_feedback_own" on public.user_feedback;
create policy "user_feedback_own" on public.user_feedback
  for all
  using (user_id = auth.uid())
  with check (user_id = auth.uid());

comment on table public.user_feedback is
  'Feedback livre do tester (build-in-public). Inserido via POST /feedback. RLS por user_id; fundador lê via service role.';
