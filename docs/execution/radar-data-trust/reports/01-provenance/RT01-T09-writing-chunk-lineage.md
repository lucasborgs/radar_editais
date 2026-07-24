# RT01-T09 — Linhagem dos chunks de escrita

**Status:** `passed`
**Plano:** [`plans/01-provenance/RT01-T09-writing-chunk-lineage.md`](../../plans/01-provenance/RT01-T09-writing-chunk-lineage.md)
**Branch/commit-base:** `codex/radar-data-trust-01-t09` / base `5f35a0459`
**Commits:** commit único desta branch (`git log codex/radar-data-trust-01-t09`)
**Implementador/modelo:** deepseek (opencode), worktree isolado

## Realizado

Cada chunk gravado em `edital_chunks.metadata` pelo caminho de indexação
(`chunk_edital_task` / `_build_chunks_for_edital`, `src/radar/core/tasks.py`)
passa a carregar linhagem aditiva, sem tocar `text`, `source_file`,
`page_range` nem o marcador de idempotência existente:

- `"canonical_content_hash": "md5:<hex>"` — hash de
  `source_docs.canonical_hash(active)`, onde `active` é o Documento Canônico
  (`CanonicalDoc`) **realmente usado** no chunking desta run (já filtrado por
  `source_docs.active_documents`, mesmo objeto que alimenta
  `build_or_load_structured_doc`/`chunk_from_blocks`). Calculado uma vez em
  `_build_chunks_for_edital`, logo após `chunk_from_blocks`, e replicado no
  `metadata` de cada chunk do lote — o CanonicalDoc está sempre em mãos nesse
  ponto (a função já retornou `[]` mais cedo se `documents` estivesse vazio),
  então a condição de parada "sem CanonicalDoc → não fabricar hash" nunca é
  atingida no caminho normal.
- `"chunker_version": CHUNKER_VERSION` — constante nova
  (`src/radar/core/retrieval/chunker.py`, valor `"1"`) ao lado de
  `TARGET_TOKENS`/`OVERLAP_TOKENS`/etc.
- `"context_version": {"model": <modelo efetivo>}` — presente **somente**
  quando `contextual_retrieval.is_enabled()` é `True` no momento do index;
  ausente (chave nem existe) quando desativada. `<modelo efetivo>` vem de um
  accessor público novo e mínimo, `contextual_retrieval.effective_model()`,
  que só expõe a constante privada `_MODEL` já existente — nenhuma mudança de
  comportamento em `is_enabled()`/`contextualize_chunks()`.

### Onde cada campo é escrito

`_build_chunks_for_edital` (linha ~504-524) grava `canonical_content_hash` e
`chunker_version` no `metadata` de cada chunk retornado — isso cobre tanto o
caminho normal de `chunk_edital_task` quanto qualquer outro caller futuro de
`_build_chunks_for_edital`.

`chunk_edital_task` lê `contextual_retrieval.is_enabled()` **uma vez**, antes
de chamar `contextualize_chunks` (que reavalia a mesma condição
internamente — env não muda no meio da task, então as duas leituras são
consistentes por construção, sem duplicar a lógica de decisão). Uma função
local `_row_metadata(c)` funde o `metadata` do chunk (já com
`canonical_content_hash`/`chunker_version`) com `context_version` quando
aplicável, na construção de `rows`. O marcador de conclusão (`chunk_index=0`,
gravado só após o último batch) passou a derivar de `rows[0]["metadata"]` em
vez de `chunks[0].get("metadata")` — antes da mudança isso já era equivalente
(mesmo dict), mas agora `rows[0]["metadata"]` é a única versão que também
carrega `context_version`; usar a fonte antiga teria perdido esse campo no
marcador (regressão sutil que o teste (a)/(b) abaixo teria pego).

### Arquivos modificados

- `src/radar/core/retrieval/chunker.py` — só a constante `CHUNKER_VERSION`
  nova, ao lado das demais constantes de config. Nenhuma mudança de
  `TARGET_TOKENS`/`OVERLAP_TOKENS`/`MIN_TOKENS`/`MAX_TOKENS` nem do algoritmo
  de `chunk_from_blocks`.
- `src/radar/core/contextual_retrieval.py` — só a função nova
  `effective_model()` (accessor público de `_MODEL`). `is_enabled()` e
  `contextualize_chunks()` intocadas.
- `src/radar/core/tasks.py` — só o caminho de indexação de chunks:
  `_build_chunks_for_edital` (grava a linhagem) e `chunk_edital_task` (lê
  `is_enabled`/`effective_model`, funde `context_version` nas rows e no
  marcador). Import de `CHUNKER_VERSION` adicionado à linha existente de
  `from radar.core.retrieval.chunker import ...`. Nenhuma outra task do
  arquivo tocada.

## Divergências e decisões

- nenhuma; a spec e o plano foram seguidos literalmente. Único ponto que
  exigiu julgamento: o marcador de conclusão (`marker_meta`) precisava ler de
  `rows[0]["metadata"]`, não de `chunks[0]["metadata"]`, para não perder
  `context_version` no chunk 0 — decisão documentada acima, coberta pelo
  teste `test_context_version_present_only_when_contextualization_enabled`
  (o chunk único do fixture É o chunk 0, então qualquer perda no marcador
  apareceria como falha desse teste).
- ruff `--fix` inicialmente quebrou o import agrupado
  `from radar.core.contextual_retrieval import (contextualize_chunks,
  effective_model as ..., is_enabled as ...)` em três imports de uma linha
  separados (isort). Em vez de aceitar a formatação esquisita, troquei por
  `from radar.core import contextual_retrieval` + chamadas qualificadas
  (`contextual_retrieval.is_enabled()` etc.) — mais limpo, mesmo
  comportamento, ruff limpo sem a quebra.

## Dados e migrations

Nenhuma migration. `metadata` já é `jsonb` existente em `edital_chunks`
(coluna usada hoje pelas flags `_detect_metadata` do chunker e pelo marcador
de idempotência `{content_hash, n_chunks}`).

## Validação

### 1. Suíte nova — hermética (sem rede/banco)

```
$ PYTHONPATH=src pytest -q tests/unit/test_chunk_lineage.py
.....                                                                    [100%]
5 passed in 0.64s
```

Casos cobertos (um teste por caso, conforme pedido):

- (a) `test_rows_carry_canonical_hash_and_chunker_version` — rows gravados
  carregam `canonical_content_hash` (`"md5:" + source_docs.canonical_hash`
  do CanonicalDoc do fixture, recomputado independentemente no teste) e
  `chunker_version == CHUNKER_VERSION`.
- (b) `test_context_version_present_only_when_contextualization_enabled` —
  duas rodadas no mesmo teste (contextualização off via
  `contextual_retrieval.is_enabled` stubado para `False`, depois on com
  `is_enabled`/`effective_model`/`contextualize_chunks` stubados, sem LLM
  real): `context_version` ausente no primeiro caso, presente com
  `{"model": "stub-context-model"}` no segundo.
- (c) `test_text_source_file_page_range_unchanged` — `text`, `source_file` e
  `page_range` do row batem exatamente com o bloco silver de entrada
  (linhagem é aditiva, não substitui nada).
- (d) `test_reindex_same_content_is_idempotent_and_skips_reembed` — duas
  chamadas de `chunk_edital_task` (sem `force`) sobre o mesmo conteúdo: a
  segunda não chama `embed_texts` (gate `_index_is_current` intacto) e as
  rows finais são idênticas às da primeira rodada.
- (e) `test_hash_changes_when_canonical_doc_changes` — dois CanonicalDocs
  distintos (units diferentes) produzem `canonical_content_hash` diferentes.

`chunk_edital_task` é exercitada fim-a-fim (não só unidades isoladas): stub
de `get_adapter`, `build_or_load_structured_doc`, `embed_texts` e
`get_supabase_service` (client fake em memória reimplementando a chain
`table().select/insert/update/delete().eq().execute()` do supabase-py, com
store persistente entre chamadas — necessário pro caso (d) de reindex).
`SUPABASE_URL`/`SUPABASE_SERVICE_KEY` são removidas do env do teste e
`radar.core.infra.db.get_supabase_service` (o singleton real, `lru_cache`)
é stubado para lançar se chamado — cobre também a chamada interna de
`mark_by_edital` (observabilidade, best-effort) sem depender de estado de
cache entre testes. Nenhuma rede, banco ou LLM real em nenhum caso.

### 2. Suíte unitária completa — 100% verde

```
$ PYTHONPATH=src pytest -q tests/unit
1285 passed, 2 skipped, 4 warnings in 12.66s
```

(2 skips pré-existentes, não relacionados a esta task.)

### 3. Gate de equivalência do gold (T02) — intacto

```
$ PYTHONPATH=src pytest -q tests/unit/test_gold_equivalence.py
................                                                         [100%]
16 passed in 0.50s
```

### 4. Ruff

```
$ ruff check src/radar/core/tasks.py src/radar/core/retrieval/chunker.py \
    src/radar/core/contextual_retrieval.py tests/unit/test_chunk_lineage.py
All checks passed!
```

### 5. `git diff --check`

```
$ git diff --check
(sem output — sem erros de whitespace)
```

### 6. `git diff 5f35a0459 --stat`

```
$ git diff 5f35a0459 --stat
 src/radar/core/contextual_retrieval.py |  6 ++++++
 src/radar/core/retrieval/chunker.py    |  5 +++++
 src/radar/core/tasks.py                | 32 +++++++++++++++++++++++++++-----
 3 files changed, 38 insertions(+), 5 deletions(-)
```

`tests/unit/test_chunk_lineage.py` é arquivo novo (não aparece no `--stat`
acima; incluído no commit único da task).

`git status --short` antes do commit:

```
 M src/radar/core/contextual_retrieval.py
 M src/radar/core/retrieval/chunker.py
 M src/radar/core/tasks.py
?? tests/unit/test_chunk_lineage.py
```

(sem untracked além do esperado — tudo foi para o commit único da task)

## Pendências

- Nenhuma dentro do escopo aprovado. Fora de escopo (não pedido pela task):
  `match_chunks` (§6.2 menciona `document, page, silver_block_idx,
  source_hash` para esse outro caminho — RAG de match, não de escrita) não
  foi tocado; permanece pendente de task própria se aplicável.
- Retrofit de chunks já indexados antes desta task: não têm
  `canonical_content_hash`/`chunker_version`/`context_version` até o próximo
  reindex (natural — spec §7.4 "Falta de provenance gold não impede
  retrieval de chunk legado; apenas aparece como qualidade reduzida até
  reindex/backfill", e não objetivo #2 da spec-mãe: "não exigir provenance
  retroativa perfeita para todo o catálogo").

## Auditoria Codex

**Veredito:** pendente

- **Arquivos tocados**: `src/radar/core/tasks.py` (só
  `_build_chunks_for_edital` + `chunk_edital_task`), `src/radar/core/retrieval/chunker.py`
  (só a constante `CHUNKER_VERSION`), `src/radar/core/contextual_retrieval.py`
  (só o accessor `effective_model`), `tests/unit/test_chunk_lineage.py` (novo)
- **Gates intocáveis**: `retriever.py`, `embedder.py`, `hyde.py`, reranker,
  `gold.py`, `provenance_writer.py`, `equivalence.py`, `evidence_resolver.py`,
  `tests/helpers/gold_projection.py`, `tests/unit/test_gold_equivalence.py`,
  `tests/fixtures/gold_equivalence/**`, migrations, RLS, prompts LLM — nenhum
  tocado (confirmado por `git diff --stat` acima, só 3 arquivos de produção)
- **Sem alteração de**: política de chunking (`TARGET_TOKENS`/`OVERLAP_TOKENS`/
  `MIN_TOKENS`/`MAX_TOKENS`/algoritmo de `chunk_from_blocks` intocados),
  modelo de embedding, ranking, texto armazenado (`text` continua o corpo
  original, nunca o contextualizado — provado pelo teste (c))
- **Idempotência preservada**: marcador `{content_hash, n_chunks}` do
  `chunk_index=0` continua sendo a chave do gate `_index_is_current`
  (inalterado); teste (d) prova reindex do mesmo conteúdo pula o re-embed
- **Hash**: prefixo `md5:` real (reusa `source_docs.canonical_hash`, mesma
  função pura já usada por `source_docs.save`/T07/T08 — não reimplementada);
  hash muda quando o CanonicalDoc muda (teste (e)); nunca fabricado quando o
  CanonicalDoc não está disponível (nesse caminho de código ele sempre está,
  por causa do early-return anterior — ver "Realizado")
- **Banco/rede/LLM real em teste**: nenhum — `get_supabase_service` real
  (singleton `lru_cache`) stubado para lançar se chamado; `contextualize_chunks`
  stubada no caso "on"; env sem `SUPABASE_URL`/`SUPABASE_SERVICE_KEY`/chaves
  de LLM
- **Worktree limpo**: sem untracked além dos arquivos desta task

