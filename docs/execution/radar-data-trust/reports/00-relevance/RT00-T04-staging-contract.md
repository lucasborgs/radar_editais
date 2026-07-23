# RT00-T04 — Contrato de staging

**Status:** `completed` (após correção de auditoria)
**Plano:** [`RT00-T04-staging-contract.md`](../../plans/00-relevance/RT00-T04-staging-contract.md)
**Branch/base:** `codex/radar-data-trust-00-t04` / `02e2b393e`
**Base de referência:** RT00-T03 concluída em `02e2b393e`

## Commits

| Commit | Assunto |
|---|---|
| `ba59f569e` | (original) migration/contrato e implementação inicial |
| `408547e02` | (original) testes e relatório iniciais (continha `assert True`) |
| `(corretivo 1)` | feat: dual-write real na descoberta + validação de fronteira |
| `(corretivo 2)` | test: testes comportamentais + relatório corrigido |

## Correções aplicadas após auditoria Codex

### 1. Dual-write real na Descoberta

A classificação v1 roda em shadow dentro de `_row_with_relevance()`, chamada por
`_stage_records()` a cada registro extraído, antes do upsert em
`discovered_opportunities`.

Ponto exato do dual-write:

```
_extract(hit) → record dict
  ↓
_row_with_relevance(record)
  ├─ classify_opportunity(material)           ← LLM v1 shadow
  ├─ validate_opportunity_result(result)      ← validação de fronteira
  └─ row["relevance_*"] = ...                ← adiciona ao upsert
  ↓
_stage_records → upsert(rows, ...)            ← atômico com status=pending
```

A classificação usa `classify_opportunity()` de `relevance_classifier.py` —
exatamente o mesmo registrado no harness. Não há classificador paralelo.

A linha de staging carrega as colunas de relevância **juntas** no upsert,
dispensando UPDATE posterior por UUID. O upsert usa `ignore_duplicates=True`,
portanto registros já existentes não são reclassificados (idempotente).

### 2. Validação de fronteira

Nova função `validate_opportunity_result(result)` em `relevance_classifier.py`:

- **Sucesso (`"verdict"`):**
  - `RelevanceVerdict.model_validate()` — rejeita campos extra (`extra=forbid`),
    código inválido, `in_scope` sem todos R1-R5, `out_of_scope` sem exclusão.
  - `_check_output_evidence_contract()` — verifica correspondência exata entre
    `reason_codes`/`exclusion_codes` e entradas de `evidence`.
  - `model_dump(mode="json")` — serializa para JSON puro (tipos básicos).
- **Erro (`"error"`):**
  - Apenas categorias sanitizadas conhecidas são aceitas:
    `parse_failure:`, `timeout:`, `provider_error:`, `contract_violation:`,
    `grounding_error:`.
  - Mensagem bruta do provedor nunca é persistida.
- Resultado inválido (sem `verdict` nem `error`) → `ValueError`.

### 3. Preservação de resultado bem-sucedido

- `ignore_duplicates=True` no upsert impede que uma execução posterior com erro
  sobrescreva um `classified` já persistido.
- Erro transitório nunca apaga ou rebaixa um veredicto anterior.
- Nenhuma reclassificação ou backfill é introduzida nesta task.

### 4. Testes comportamentais

Todos os `assert True` foram removidos. 26 testes direcionados agora executam
caminhos reais com mocks:

| Teste | O que comprova |
|---|---|
| `test_valid_in_scope` | in_scope com R1-R5 + evidência é aceito |
| `test_valid_out_of_scope` | out_of_scope com exclusão + evidência é aceito |
| `test_valid_needs_review` | needs_review com missing_information é aceito |
| `test_valid_error_known_category` | 5 categorias sanitizadas conhecidas são aceitas |
| `test_invalid_result_no_keys_raises` | dict vazio → ValueError |
| `test_invalid_unknown_error_category_raises` | erro bruto → ValueError |
| `test_in_scope_incomplete_rejected` | in_scope sem R1-R5 → rejeitado |
| `test_evidence_code_mismatch_rejected` | evidence sem reason_codes → rejeitado |
| `test_serialize_mode_json` | model_dump(mode=json) produz tipos básicos |
| `test_classified_in_scope` | _row_with_relevance → classified + pending |
| `test_classified_out_of_scope` | _row_with_relevance → classified + out_of_scope |
| `test_classified_needs_review` | _row_with_relevance → classified + needs_review |
| `test_error_graceful_pending_preserved` | erro v1 → pending + relevance_status=error |
| `test_unexpected_exception_never_blocks_staging` | exceção → pending + error sanitizado |
| `test_no_material_skips_classification` | sem texto → sem classificação |
| `test_editorial_columns_untouched` | colunas editoriais não são escritas |
| `test_records_have_relevance_on_upsert` | _stage_records inclui relevance_* no upsert |
| `test_relevance_error_still_upserts_pending` | erro não impede upsert |
| `test_ignore_duplicates_preserves_classified` | ignore_duplicates=True protege classified |
| `test_no_gold_table_written` | apenas discovered_opportunities é escrita |
| `test_migration_041_default_is_unclassified` | SQL da migration lida e verificada |
| `test_legacy_records_unclassified_by_default` | contrato: default 'unclassified' |
| `test_promote_not_called_by_staging` | _stage_records não contém "promote" |
| `test_reject_not_called_by_staging` | _stage_records não contém "reject" |
| `test_cache_not_modified_by_relevance` | _row_with_relevance não chama cache |
| `test_write_false_skips_staging` | write=False → _stage_records não chamado |

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

Nenhuma coluna editorial (`status`, `reject_reason`, `reviewed_at`,
`promoted_web_source_id`) foi alterada.

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
| exceção inesperada | `error` | `null` | `provider_error: falha inesperada` | Nenhum |
| nunca processado (legado) | `unclassified` | `null` | `null` | Nenhum |

Falha nunca apaga o candidato, nunca altera `status` editorial e nunca fabrica
`out_of_scope`.

## Compatibilidade com registros legados

- Registros existentes recebem `relevance_status = 'unclassified'` pelo default
  da coluna — nenhum backfill.
- Nenhuma decisão humana existente é reclassificada ou sobrescrita.
- Cache negativo permanece no `kg_store` (`discovery_ledger`), nunca no
  `discovered_opportunities` nem convertido a reason codes v1.

## Promover/rejeitar e cache — nenhuma alteração

- `POST /discovered-opportunities/{id}/promote` e `reject` ignoram as colunas
  novas — dependem exclusivamente de `status` editorial.
- `_row_with_relevance` não chama `_record_rejection`, não toca o ledger, não
  altera o cache negativo.
- Cache negativo continua por candidato/URL/documento, nunca por instituição.

## Rollback lógico

- Desativar a chamada a `_row_with_relevance` em `_stage_records` é suficiente
  para reverter o comportamento produtivo — dados já gravados permanecem sem
  efeito.
- Remover a migration requer apenas `DROP` das 4 colunas.

## Testes executados

```bash
PYTHONPATH=src .venv/bin/pytest -q \
  tests/unit/test_relevance_staging.py \
  tests/unit/test_relevance.py \
  tests/unit/test_relevance_shadow.py \
  tests/unit/test_relevance_goldens.py \
  tests/unit/test_discovery_promotion.py \
  tests/unit/test_opportunity_discovery_cache.py \
  tests/unit/test_hardening_pr4.py
# 309 passed
```

### Ruff

```bash
.venv/bin/ruff check src/radar/core/ingestion/relevance_classifier.py \
  src/radar/core/ingestion/opportunity_discovery.py \
  tests/unit/test_relevance_staging.py
# All checks passed
```

### `git diff --check`

Limpo.

## Validação da migration local

Migration `041` reaplicada via `supabase db push --local`. Verificado via
`information_schema.columns`:

- `relevance_status`: `text`, `NOT NULL`, default `'unclassified'`, check: `('unclassified', 'classified', 'error')`
- `relevance_verdict`: `jsonb`, nullable
- `relevance_error`: `text`, nullable
- `relevance_classified_at`: `timestamptz`, nullable

Constraints editoriais e RLS preservados.

## Divergências encontradas na auditoria

1. **Implementação inicial sem dual-write real**: a versão original (`ba59f569e`)
   adicionava `persist_opportunity_verdict()` como função isolada, sem conectá-la
   à descoberta. A classificação v1 nunca era chamada no fluxo produtivo.
   **Corrigido:** `_row_with_relevance()` em `opportunity_discovery.py` chama o
   classificador a cada registro antes do upsert.

2. **Ausência de validação de fronteira**: a versão original não validava o
   resultado contra `RelevanceVerdict` nem verificava categorias de erro
   conhecidas antes de persistir.
   **Corrigido:** `validate_opportunity_result()` em `relevance_classifier.py`
   aplica as 4 validações (model, evidence, in_scope completeness, error prefix).

3. **Testes com `assert True`**: 4 testes eram placeholders sem verificação real.
   **Corrigido:** todos os 26 testes executam asserções comportamentais.

## Confirmação final

- [x] Falha v1 não bloqueia staging
- [x] promote/reject continuam humanos
- [x] cache e gold não mudaram
- [x] RT00-T05 não foi iniciada
- [x] Nenhuma alteração em prompts, taxonomias, goldens ou labels da T03
- [x] Shadow eval continua sem escrita em staging
- [x] Migration aditiva, default-safe, rollback lógico possível
