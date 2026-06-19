-- 026: embedding_gemma — coluna de shadow embedding para bake-off (2026-06-16)
--
-- O retriever usa RETRIEVAL_EMBEDDING_COLUMN (env) para escolher qual coluna
-- de embedding usar no braço dense. "embedding_gemma" (768d, Gemma-PT-BR via
-- sentence-transformers) foi promovido como default pós-bake-off tier-1 PT.
--
-- "embedding" (1536d, OpenAI text-embedding-3-large) permanece como fallback
-- via env RETRIEVAL_EMBEDDING_COLUMN=embedding. Ambas as colunas coexistem;
-- o script reindex_shadow_gemma.py popula embedding_gemma no subset do golden.
--
-- Idempotente: add column if not exists + create index if not exists.

alter table public.edital_chunks
  add column if not exists embedding_gemma vector(768);

create index if not exists edital_chunks_embedding_gemma_idx
  on public.edital_chunks using hnsw (embedding_gemma vector_cosine_ops);

comment on column public.edital_chunks.embedding_gemma is
  'Embedding 768d (tardellirs/embeddinggemma-pt-br via sentence-transformers). Shadow column do bake-off PT-BR; default do RETRIEVAL_EMBEDDING_COLUMN pós 2026-06-16.';
