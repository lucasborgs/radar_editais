-- 048: writing_turn_idempotency — políticas RLS para o usuário autenticado.
--
-- A tabela inicial (045) foi criada com RLS habilitada SEM policies
-- (service-role-only), mas os routers acessam `writing_turn_idempotency` via o
-- cliente user-scoped (JWT), não o service-role. Consequência em produção:
-- `_check_idempotency` nunca bate (SELECT retorna vazio) e `_record_idempotency`
-- falha silenciosamente (INSERT negado por RLS) — a proteção contra turnos
-- duplicados é inócua fora do DEMO_MODE.
--
-- Aqui habilitamos INSERT/SELECT para o usuário dono do workspace da sessão,
-- seguindo o mesmo escopo da policy de writing_sessions/004 (workspace do
-- auth.uid()). O usuário só acessa rows cujo session_id pertence ao próprio
-- workspace.

alter table public.writing_turn_idempotency enable row level security;

drop policy if exists "writing_turn_idempotency_insert_own" on public.writing_turn_idempotency;
create policy "writing_turn_idempotency_insert_own"
  on public.writing_turn_idempotency
  for insert to authenticated
  with check (
    session_id::uuid in (
      select ws.id from public.writing_sessions ws
      where ws.workspace_id in (
        select id from public.workspaces where user_id = auth.uid()
      )
    )
  );

drop policy if exists "writing_turn_idempotency_select_own" on public.writing_turn_idempotency;
create policy "writing_turn_idempotency_select_own"
  on public.writing_turn_idempotency
  for select to authenticated
  using (
    session_id::uuid in (
      select ws.id from public.writing_sessions ws
      where ws.workspace_id in (
        select id from public.workspaces where user_id = auth.uid()
      )
    )
  );