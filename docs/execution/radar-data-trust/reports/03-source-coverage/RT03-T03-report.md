# RT03-T03 — Relatório

## Resultado

ETL diário instrumentado com telemetria `source_runs` para os 4 canais de
fontes dedicadas e Web curada. Um `batch_id` UUID é gerado por execução de
`_run_daily_etl`, compartilhado pelos canais `finep` (dedicated), `fapesp`
(dedicated), `fapesc` (dedicated) e `web_curated` (curated_web, mapeado do
scraper `web`). A telemetria é best-effort e não altera payload, retry,
alertas, bronze, silver, gold nem tratamento de erros existente.

## SCRAPER_REGISTRY validado

Confirmado em `src/radar/pipeline/extractors/__init__.py`:

| Chave | Classe | display_name |
|---|---|---|
| `finep` | `FINEPScraper` | FINEP |
| `fapesp` | `FAPESPScraper` | FAPESP |
| `fapesc` | `FAPESCScraper` | FAPESC |
| `web` | `WebScraper` | Web |

As 4 entradas correspondem exatamente aos produtores previstos na spec. Pare
não acionado — registry compatível.

## Comportamento implementado

### `_run_daily_etl` (src/radar/core/tasks.py)

1. Gera `batch_id = str(uuid.uuid4())` no início da execução.
2. Obtém cliente DB (`get_supabase_service`) com tratamento best-effort: falha
   → `db = None` e telemetria silenciosamente desabilitada.
3. Para cada fonte em `SCRAPER_REGISTRY` mapeada por `_SOURCE_RUN_MAP`:
   - **Antes da coleta:** `start_run(db, batch_id, source_key, mode)`.
   - **Sucesso:** `finish_run(..., status="succeeded", records_observed=new_count)`.
   - **PipelineError:** `finish_run(..., status="failed", reason_code=mapped)`.
   - **Exception genérica:** `finish_run(..., status="failed", reason_code=mapped)`.
4. `start_run`/`finish_run` são chamados via `asyncio.to_thread` (API síncrona
   do supabase-py). Falhas são logadas e não interrompem o pipeline.

### Invariantes

- Uma `source_run` por canal por batch.
- `records_observed` = `len(results)` ou 0 se `None`/vazio — zero não vira
  prova de saúde ou completude (o leitor futuro deve tratar como ambíguo).
- `reason_code` mapeado de `PipelineError.category`: `timeout`, `parse_error`,
  `schema_violation → parse_error`, `llm_refusal → provider_error`,
  `duplicate → empty_result`, `unknown → unknown`.
- `status = "failed"` para qualquer exceção, nunca `"partial"`.
- `error_count = 1` em falha, `0` em sucesso.
- Telemetria nunca altera `step_errors`, `pipeline_errors`, `send_alert`,
  `total_new` ou etapas pós-scraping.

## Arquivos alterados

- `src/radar/core/tasks.py` (+103 linhas):
  - `import uuid` (módulo)
  - `_SOURCE_RUN_MAP` — mapeamento scraper → source_key/mode
  - `_PIPELINE_CATEGORY_TO_REASON` — mapeamento category → reason_code
  - Instrumentação em `_run_daily_etl`: batch_id, DB client, start_run/finish_run
- `tests/unit/test_source_coverage_etl.py` (+378 linhas, novo):
  - 22 testes direcionados

## Testes direcionados — 22 passed

| Teste | O que cobre |
|---|---|
| `TestSuccessWithItems.test_finish_run_called_for_each_source` | 4 start_run, 4 finish_run |
| `TestSuccessWithItems.test_all_succeeded_status` | status=succeeded |
| `TestSuccessWithItems.test_records_observed_matches_item_count` | contagem por fonte |
| `TestSuccessWithItems.test_error_count_zero_on_success` | error_count=0 |
| `TestEmptyResult.test_records_observed_zero` | lista vazia → records_observed=0 |
| `TestEmptyResult.test_status_succeeded_not_healthy_inference` | vazio é sucesso técnico, não `healthy` |
| `TestPipelineError.test_failed_status` | PipelineError → status=failed |
| `TestPipelineError.test_reason_code_from_category` | reason_code mapeado da category |
| `TestPipelineError.test_error_count_one` | error_count=1 |
| `TestPipelineError.test_records_observed_zero_on_failure` | records_observed=0 |
| `TestGenericException.test_failed_status` | exceção genérica → failed |
| `TestGenericException.test_reason_code_unknown` | reason_code = unknown |
| `TestTelemetryFailure.test_start_run_none_does_not_break_etl` | start_run None → sem finish |
| `TestTelemetryFailure.test_start_run_exception_logged_and_continues` | exceção no start_run |
| `TestTelemetryFailure.test_db_unavailable_skips_all_telemetry` | DB indisponível → sem telemetria |
| `TestSameBatchId.test_all_channels_share_batch_id` | mesmo batch_id para 4 canais |
| `TestWebMapping.test_web_uses_web_curated_source_key` | web_curated + curated_web |
| `TestWebMapping.test_finep_fapesp_fapesc_dedicated_mode` | 3 fontes com mode=dedicated |
| `TestPreservation.test_pipeline_errors_still_logged_on_failure` | PipelineError persiste |
| `TestPreservation.test_step_errors_still_accumulated` | step_errors + send_alert |
| `TestPreservation.test_telemetry_does_not_affect_pipeline_result` | pós-scraping intacto |
| `TestNoInferredPartial.test_pipeline_error_is_failed_not_partial` | nunca partial |

## Validação

- `ENVIRONMENT=test pytest tests/unit/test_source_coverage_etl.py`: **22 passed**
- `ENVIRONMENT=test pytest tests/unit/test_etl_gold_pipeline.py`: **3 passed**
- `ENVIRONMENT=test pytest tests/unit/test_source_runs.py`: **39 passed**
- `ruff check src/radar/core/tasks.py tests/unit/test_source_coverage_etl.py`: **All checks passed**
- `git diff --check`: **sem whitespace errors**
- Suíte completa: **1504 passed, 64 skipped** (baseline: 1504 passed, 64 skipped)
- Nenhum teste novo quebrou, nenhum existente regrediu

## Divergências e limitações

- Migration T02 não aplicada no banco local; `start_run`/`finish_run` validados
  por mocks.
- `reason_code` não possui CHECK no SQL (já documentado em T02).
- O leitor de saúde, API administrativa e UI não foram implementados (escopo
  da task).
- Descoberta aberta (Tavily, DOU, hub_expansion) não foi instrumentada (T04).
- Telemetria testada com mocks — comportamento real depende de `source_runs`
  existir no schema.

## Ambiente

- Worktree isolado em `/private/tmp/radar-editais-rt03-t03`
- Branch: `codex/radar-data-trust-03-t03`
- `ENVIRONMENT=test` em toda execução
- Sem `.env`, produção, rede, Tavily, DOU, LLM, ingestão gold real ou
  merge/push
- Scrapers mockados; DB mockado
- RT03-T04 **não foi iniciada**
