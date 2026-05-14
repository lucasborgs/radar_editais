-- 002_enable_extensions.sql
--
-- Enables the Postgres extensions required by the Radar de Editais stack.
--
-- - `vector` (pgvector): backs the RAG embedding storage referenced by
--   ADR-001 decisions A3 (vector store) and B3 (retrieval pipeline). Without
--   this, embedding columns and similarity operators (<->, <=>, <#>) are
--   unavailable.
-- - `pgcrypto`: provides `gen_random_uuid()` so primary keys and other
--   identifiers can be generated server-side instead of in application code.
--
-- This migration is idempotent: `IF NOT EXISTS` lets us re-run it safely on
-- both Supabase Cloud (production) and the Supabase CLI local stack (M4).

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pgcrypto;
