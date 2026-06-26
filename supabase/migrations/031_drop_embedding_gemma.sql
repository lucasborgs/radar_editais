-- 031: remove o braço gemma do RAG (reverte a 026) — 2026-06-26
--
-- Decisão: o sistema volta a usar a coluna `embedding` vector(1536) com
-- OpenAI text-embedding-3-small (mais barato; ambos small/large são 1536d,
-- então a coluna não muda). O shadow `embedding_gemma` (768d, embeddinggemma-pt-br
-- via sentence-transformers) foi um braço de bake-off que vazou para o default
-- e criou um landmine: o ingest gravava 1536d numa coluna 768d. O ingest e a
-- query agora compartilham uma única constante (RETRIEVAL_EMBEDDING_COLUMN,
-- default `embedding`) — ver core/retrieval/retriever.py e core/tasks.py.
--
-- Reversível: o braço gemma pode ser recriado por um futuro bake-off; o backend
-- sentence_transformers em core/retrieval/embedder.py permanece (dormente).

drop index if exists public.edital_chunks_embedding_gemma_idx;

alter table public.edital_chunks
  drop column if exists embedding_gemma;
