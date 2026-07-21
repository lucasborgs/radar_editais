-- 027: trava as tabelas do checkpointer LangGraph contra o PostgREST (2026-06-19)
--
-- A Etapa 3 da migração LangGraph (docs/historical/langgraph-migration.md) adiciona um
-- checkpointer Postgres à WritingSession. As tabelas (checkpoints, checkpoint_blobs,
-- checkpoint_writes, checkpoint_migrations) são criadas em runtime pelo
-- AsyncPostgresSaver.setup() (ver scripts/setup_checkpointer.py) — NÃO por migration,
-- para não divergir do schema da versão do langgraph.
--
-- PROBLEMA DE SEGURANÇA: o checkpointer escreve via conexão DIRETA (DATABASE_URL,
-- superuser → bypassa RLS). O isolamento multi-tenant passa a depender 100% do
-- namespacing do thread_id ("{workspace_id}:{session_id}:{turn_index}"). Mas, estando
-- em `public`, essas tabelas ficariam LEGÍVEIS via PostgREST por qualquer usuário
-- `authenticated` — que poderia ler o blob de checkpoint de OUTRO workspace pela REST
-- API, furando o isolamento. Defesa em profundidade: negar acesso aos papéis do
-- PostgREST (anon/authenticated) e ligar RLS sem policies (deny-all aos não-bypass).
--
-- NÃO usamos FORCE RLS: a conexão legítima do checkpointer é superuser/bypass e deve
-- continuar escrevendo. ENABLE (sem policy) já bloqueia anon/authenticated.
--
-- Idempotente + guardado por existência: roda APÓS o setup() ter criado as tabelas
-- (no-op se ainda não existirem; re-aplicar é seguro).

do $$
declare
  t text;
begin
  foreach t in array array[
    'checkpoints', 'checkpoint_blobs', 'checkpoint_writes', 'checkpoint_migrations'
  ]
  loop
    if exists (
      select 1 from information_schema.tables
      where table_schema = 'public' and table_name = t
    ) then
      execute format('alter table public.%I enable row level security', t);
      execute format('revoke all on public.%I from anon, authenticated', t);
    end if;
  end loop;
end $$;
