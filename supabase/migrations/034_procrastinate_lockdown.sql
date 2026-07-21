-- 034: trava a maquinaria do procrastinate contra o PostgREST (2026-07-02)
--
-- FURO P0 (leak-test pré-beta, docs/historical/pre-beta-verification.md — Frente 1/S1):
-- o schema do procrastinate (migration 003, gerado pela lib) vive em `public` e,
-- por default do Supabase, `anon` e `authenticated` recebem GRANT ALL nas tabelas
-- + EXECUTE em todas as funções. Como `public` é servido pelo PostgREST, a fila
-- inteira ficava exposta pela REST API:
--
--   • SELECT procrastinate_jobs → vaza `args` (workspace_id, edital_id, payloads)
--     de TODOS os tenants. Cross-tenant read com a anon key, sem nem autenticar.
--   • DELETE/UPDATE procrastinate_jobs → cancela/corrompe jobs de outros tenants
--     (integridade + DoS da fila).
--   • EXECUTE procrastinate_defer_jobs_v1 / _cancel_job_v1 / _fetch_job_v2 / … →
--     enfileirar jobs arbitrários, cancelar, ou roubar jobs — controle total da
--     fila a partir de um chamador anônimo.
--
-- Reproduzido ao vivo no Supabase local: `curl` com a anon key leu `args` e
-- deletou um job (HTTP 204). Ver tests/integration/test_tenant_isolation.py::TestProcrastinate.
--
-- DEFESA (mesma filosofia da migration 027 p/ o checkpointer — band-aid de
-- REVOKE + RLS, já que relocar o schema do procrastinate é invasivo e arriscado
-- pré-beta): negar todo acesso de anon/authenticated a tabelas, sequences e
-- funções `procrastinate*`, e ligar RLS sem policy nas tabelas (deny-all aos
-- papéis não-bypass). O worker/backend conecta via DATABASE_URL como role
-- `postgres` (dono das tabelas, BYPASSRLS) — REVOKE e RLS não o afetam. O
-- service_role (BYPASSRLS) também segue livre.
--
-- Dinâmico (varre `procrastinate%`) para sobreviver a bumps da lib que adicionem
-- novos objetos. Idempotente: REVOKE/ENABLE RLS são no-op ao re-aplicar.

do $$
declare
  obj record;
begin
  -- Tabelas: RLS deny-all + revoke de anon/authenticated.
  for obj in
    select c.relname
    from pg_class c
    join pg_namespace n on n.oid = c.relnamespace
    where n.nspname = 'public'
      and c.relkind = 'r'
      and c.relname like 'procrastinate%'
  loop
    execute format('alter table public.%I enable row level security', obj.relname);
    execute format('revoke all on table public.%I from anon, authenticated', obj.relname);
  end loop;

  -- Sequences: revoke (impede nextval/currval via PostgREST).
  for obj in
    select c.relname
    from pg_class c
    join pg_namespace n on n.oid = c.relnamespace
    where n.nspname = 'public'
      and c.relkind = 'S'
      and c.relname like 'procrastinate%'
  loop
    execute format('revoke all on sequence public.%I from anon, authenticated', obj.relname);
  end loop;

  -- Funções/procedures: revoke EXECUTE (fecha o controle de fila via RPC).
  for obj in
    select p.oid,
           p.proname,
           pg_get_function_identity_arguments(p.oid) as args,
           p.prokind
    from pg_proc p
    join pg_namespace n on n.oid = p.pronamespace
    where n.nspname = 'public'
      and p.proname like 'procrastinate%'
  loop
    if obj.prokind = 'p' then
      execute format(
        'revoke all on procedure public.%I(%s) from anon, authenticated',
        obj.proname, obj.args
      );
    else
      execute format(
        'revoke all on function public.%I(%s) from anon, authenticated',
        obj.proname, obj.args
      );
    end if;
  end loop;
end $$;
