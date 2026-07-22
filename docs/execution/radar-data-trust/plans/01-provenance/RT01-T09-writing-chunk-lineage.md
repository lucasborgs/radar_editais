# RT01-T09 — Linhagem dos chunks de escrita

**Objetivo:** preservar arquivo, página e versão nos chunks existentes sem
alterar retrieval ou exigir chunks de atores.

## Entrega

- hashes/versões em `edital_chunks.metadata`;
- texto-fonte continua distinto do contexto sintético usado no embedding;
- reindex idempotente.

## Validação

- uma fixture de edital reindexada;
- texto, `source_file` e `page_range` preservados;
- teste direcionado do worker/retrieval.

## Pare

Pare se for necessário mudar política de chunk, modelo de embedding ou ranking;
essas mudanças exigem avaliação independente.
