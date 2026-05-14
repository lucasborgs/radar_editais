-- ============================================================
-- Migration 005 — Fase 2 #15: embeddings em content_items
--
-- Adiciona coluna VECTOR(1536) para text-embedding-3-large + HNSW index.
-- A coluna é nullable (item criado vazio; preenchido async pelo procrastinate
-- task embed_content_task). Retrieval multi-critério (Fase 2 #16) usa esta
-- coluna para o componente de relevância (cosine similarity).
-- ============================================================

alter table public.content_items
  add column if not exists embedding vector(1536);

create index if not exists content_items_embedding_idx
  on public.content_items using hnsw (embedding vector_cosine_ops);
