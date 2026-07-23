# RT00-T04 — Contrato de staging

**Status:** `completed`
**Plano:** [`RT00-T04-staging-contract.md`](../../plans/00-relevance/RT00-T04-staging-contract.md)
**Branch/base:** `codex/radar-data-trust-00-t04` / `02e2b393e`
**Base de referência:** RT00-T03 concluída em `02e2b393e`

## Commits

| Commit | Assunto |
|---|---|
| `(1º commit a ser criado)` | migration/contrato e implementação |
| `(2º commit a ser criado)` | testes e relatório |

## Schema adicionado

Migration `041_discovered_opportunities_relevance.sql` — aditiva, default-safe,
sem alterar RLS, índices, constraints editoriais ou consumidores existentes.

4 colunas adicionadas a `discovered_opportunities`:

| Coluna | Tipo | Default | Nullable | Check |
|---|---|---|---|---|
| `relevance_status` | `text` | `'unclassified'` | NOT NULL | `in ('unclassified', 'classified', 'error')` |
| `relevance_verdict` | `jsonb` | — | YES | — |
| `relevance_error` | `text` | — | YES | — |
| `relevance_classified_at` | `timestamptz` | — | YES | — |

- `unclassified`: registro ainda não processado pelo classificador (default p/ legado).
- `classified`: classificação concluída com sucesso; `relevance_verdict` preenchido.
- `error`: falha operacional; `relevance_error` contém mensagem sanitizada,
  `relevance_verdict` é `null`.

Nenhuma coluna editorial (`status`, `reject_reason`, `reviewed_at`,
`promoted_web_source_id`) foi alterada. RLS, índices e `url_hash UNIQUE`
preservados.

## Ponto exato do dual-write

A função `persist_opportunity_verdict(db, opportunity_id, result)` em
`src/radar/core/ingestion/relevance_classifier.py:660` é o único ponto de
escrita.

- Aceita o mesmo dicionário retornado por `classify_opportunity()`:
  `{"verdict": {...}}` ou `{"error": "..."}`.
- Escreve via `UPDATE discovered_opportunities SET ... WHERE id = opp_id`.
- Resultado inválido (sem `verdict` nem `error`) levanta `ValueError`.
- Não está conectada a `discover_opportunities()`, `_stage_records`, ledger,
  cache negativo, promoção, API ou shadow eval — RT00-T05 fará a fiação.

## Comportamento em falha

| Resultado do classificador | `relevance_status` | `relevance_verdict` | `relevance_error` | Efeito no candidato |
|---|---|---|---|---|
| `{"verdict": {"decision": "in_scope", ...}}` | `classified` | dict completo | `null` | Nenhum |
| `{"verdict": {"decision": "out_of_scope", ...}}` | `classified` | dict completo | `null` | Nenhum |
| `{"verdict": {"decision": "needs_review", ...}}` | `classified` | dict completo | `null` | Nenhum |
| `{"error": "timeout: ..."}` | `error` | `null` | mensagem sanitizada | Nenhum |
| `{"error": "parse_failure: ..."}` | `error` | `null` | mensagem sanitizada | Nenhum |
| `{"error": "provider_error: ..."}` | `error` | `null` | mensagem sanitizada | Nenhum |
| `{"error": "contract_violation: ..."}` | `error` | `null` | mensagem sanitizada | Nenhum |
| `{"error": "grounding_error: ..."}` | `error` | `null` | mensagem sanitizada | Nenhum |
| nunca processado (legado) | `unclassified` | `null` | `null` | Nenhum |

Falha nunca apaga o candidato, nunca altera `status` editorial e nunca fabrica
`out_of_scope`.

## Compatibilidade com registros legados

- Registros existentes recebem `relevance_status = 'unclassified'` pelo default
  da coluna — nenhum backfill é executado ou necessário.
- Nenhuma decisão humana existente (`promoted`/`rejected`) é reclassificada,
  sobrescrita ou reinterpretada.
- Cache negativo permanece vinculado ao candidato/URL/documento no
  `kg_store` (`discovery_ledger`), nunca ao órgão ou domínio.

## Promover/rejeitar e cache — nenhuma alteração

- `POST /discovered-opportunities/{id}/promote` e `reject` ignoram as colunas
  novas — continuam dependendo exclusivamente de `status` editorial.
- Nenhuma promotion run event, ledger, `promotion_runs` ou `web_sources` foi
  alterada.
- Cache negativo continua por candidato/URL/documento (`discovery_ledger`),
  nunca por instituição — nenhuma conversão automática de cache para reason
  codes v1 foi introduzida.

## Rollback lógico

- Desativar o produtor novo (não conectar na RT00-T05) é suficiente para
  reverter o comportamento — os dados já gravados permanecem sem efeito.
- Remover a migration requer apenas `DROP` das 4 colunas, sem afetar consumidores
  existentes (nenhum consumer lê estas colunas ainda).

## Testes executados

```bash
PYTHONPATH=src .venv/bin/pytest -q \
  tests/unit/test_relevance_staging.py \
  tests/unit/test_relevance.py \
  tests/unit/test_relevance_shadow.py \
  tests/unit/test_relevance_goldens.py \
  tests/unit/test_discovery_promotion.py \
  tests/unit/test_opportunity_discovery_cache.py
# 285 passed
```

### Testes específicos da RT00-T04 (`test_relevance_staging.py`, 18 testes)

| Teste | O que comprova |
|---|---|
| `test_persist_in_scope` | `in_scope` → `relevance_status='classified'`, decisão preservada |
| `test_persist_out_of_scope` | `out_of_scope` → `relevance_status='classified'`, decisão preservada |
| `test_persist_needs_review` | `needs_review` → `relevance_status='classified'`, decisão preservada |
| `test_persist_error` | erro → `relevance_status='error'`, `relevance_verdict=null` |
| `test_error_never_deletes_record` | erro não altera `status`, `reject_reason` ou colunas editoriais |
| `test_idempotent_write_same_verdict` | mesma escrita duas vezes não falha |
| `test_idempotent_verdict_then_error` | sobrescrita classified→error é segura |
| `test_does_not_alter_editorial_status` | nenhuma coluna editorial é tocada |
| `test_invalid_result_raises_value_error` | dict sem `verdict`/`error` → `ValueError` |
| `test_empty_dict_raises_value_error` | `{}` → `ValueError` |
| `test_sets_classified_at_timestamp` | timestamp ISO8601 é gravado |
| `test_persist_error_sets_null_verdict` | erro → `relevance_verdict=null`, mensagem no `relevance_error` |
| `test_correct_table_and_filter` | UPDATE na tabela certa com filtro `id = opp_id` |
| `test_execute_is_called` | `.execute()` é chamado |
| `test_default_relevance_status_is_unclassified` | default SQL da migration |
| `test_no_relevance_columns_in_legacy_select` | migration aditiva, colunas antigas intactas |
| `test_promote_does_not_check_relevance` | promote/reject ignoram colunas novas |
| `test_reject_does_not_check_relevance` | rejeição humana não afetada |

### Ruff

```bash
.venv/bin/ruff check src/radar/core/ingestion/relevance_classifier.py \
  tests/unit/test_relevance_staging.py
# All checks passed
```

### `git diff --check`

Limpo — sem espaços brancos, tabs ou caracteres não ASCII.

## Validação da migration local

Supabase local disponível e executando. Migration `041` aplicada com sucesso via
`supabase db push --local`. Verificado via `information_schema.columns`:

- `relevance_status` presente, `NOT NULL`, default `'unclassified'`, check constraint ativo.
- `relevance_verdict` presente, nullable, tipo `jsonb`.
- `relevance_error` presente, nullable, tipo `text`.
- `relevance_classified_at` presente, nullable, tipo `timestamptz`.
- Constraints editoriais (`status` check, `extraction_quality` check) preservadas.

A migration não altera RLS — as policies existentes continuam vigentes.

## Divergências

Nenhuma divergência entre spec e runtime foi encontrada. O método da task é
válido para o estado atual do código.

## Pendências

- Nenhuma. A fiação do produtor (chamar `persist_opportunity_verdict` no pipeline
  real) pertence à RT00-T05.

## Confirmação

- [x] RT00-T05 não foi iniciada.
- [x] Nenhuma alteração em prompts, taxonomias, goldens ou labels da T03.
- [x] Nenhuma escrita em `entities`, `entity_relationships` ou `match_chunks`.
- [x] Nenhuma alteração no gate humano, promote/reject ou cache negativo.
- [x] Shadow eval continua sem conexão com staging.
- [x] Migration aditiva, default-safe, rollback lógico possível.
