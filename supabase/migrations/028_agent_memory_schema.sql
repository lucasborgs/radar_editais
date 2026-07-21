-- 028: schema dedicado `agent_memory` para a maquinaria que bypassa RLS (2026-06-19)
--
-- Etapa 5 da migração LangGraph (docs/historical/langgraph-migration.md). O LangGraph
-- PostgresStore (memória cross-session, projeção dos reflection_insights) e o
-- AsyncPostgresSaver da Etapa 3 (checkpointer) escrevem via conexão DIRETA
-- (DATABASE_URL, role postgres → bypassa RLS). O isolamento multi-tenant vem do
-- namespace (workspace_id) / thread_id, NÃO de RLS.
--
-- DECISÃO (substitui o band-aid da migration 027): em vez de ligar RLS + revogar
-- anon/authenticated tabela-a-tabela em `public`, essas tabelas passam a viver num
-- schema dedicado `agent_memory`. O PostgREST do Supabase só serve os schemas
-- listados em config.toml (`public, storage, graphql_public`) — um schema fora
-- dessa lista é INVISÍVEL pela REST API por construção, sem precisar de RLS/revoke.
-- Defesa em profundidade mais limpa: a superfície nem existe pro PostgREST.
--
-- As tabelas em si (checkpoints*, store, store_vectors, *_migrations) seguem sendo
-- criadas em RUNTIME por `.setup()` do Saver/Store (não por migration — evita drift
-- com a versão do langgraph). O runtime conecta com search_path=agent_memory,... e
-- também roda `CREATE SCHEMA IF NOT EXISTS agent_memory` defensivamente, então esta
-- migration é o caminho documentado/Supabase mas não é a única garantia.
--
-- Idempotente. Pré-launch (sem dados reais): as tabelas órfãs do checkpointer que a
-- Etapa 3 criou em `public` (trancadas pela 027) são dropadas — o runtime recria em
-- agent_memory no próximo setup(). Guardado por IF EXISTS (no-op em deploy novo).

create schema if not exists agent_memory;

-- Limpa as tabelas do checkpointer que viviam em public (Etapa 3). Pré-launch: sem
-- dados a preservar. O runtime as recria em agent_memory. A 027 (RLS/revoke nessas
-- tabelas) fica obsoleta — não há mais tabela de checkpointer em public.
drop table if exists public.checkpoint_writes;
drop table if exists public.checkpoint_blobs;
drop table if exists public.checkpoints;
drop table if exists public.checkpoint_migrations;
