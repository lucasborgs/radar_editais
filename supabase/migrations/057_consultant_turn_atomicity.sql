-- Persiste estado e resposta idempotente do Consultor numa única transação.
-- 052 já está aplicada nos ambientes promovidos; esta migration acrescenta a
-- garantia sem reescrever histórico.
create or replace function public.save_consultant_turn(
  p_session_id uuid,
  p_workspace_id uuid,
  p_state jsonb,
  p_revision integer,
  p_expected_revision integer,
  p_idempotency_key text,
  p_response jsonb
)
returns table(outcome text, response jsonb)
language plpgsql
security invoker
set search_path = public, pg_temp
as $$
declare
  existing_response jsonb;
begin
  perform pg_advisory_xact_lock(
    hashtextextended(p_workspace_id::text || ':' || p_idempotency_key, 0)
  );

  select consultant_turns.response into existing_response
    from public.consultant_turns
   where workspace_id = p_workspace_id
     and idempotency_key = p_idempotency_key;
  if found then
    outcome := 'replayed';
    response := existing_response;
    return next;
    return;
  end if;

  update public.consultant_sessions
     set state = p_state,
         revision = p_revision,
         updated_at = now()
   where id = p_session_id
     and workspace_id = p_workspace_id
     and revision = p_expected_revision;

  if not found then
    outcome := 'conflict';
    response := null;
    return next;
    return;
  end if;

  insert into public.consultant_turns (
    session_id, workspace_id, idempotency_key, response, revision
  ) values (
    p_session_id, p_workspace_id, p_idempotency_key, p_response, p_revision
  );

  outcome := 'saved';
  response := p_response;
  return next;
end;
$$;

comment on function public.save_consultant_turn is
  'Persiste state/revision e replay idempotente do Consultor atomicamente.';

grant execute on function public.save_consultant_turn(uuid, uuid, jsonb, integer, integer, text, jsonb)
  to authenticated;
