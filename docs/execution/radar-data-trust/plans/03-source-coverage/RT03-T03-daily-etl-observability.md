# RT03-T03 — Instrumentação conservadora do ETL diário

## Objetivo

Instrumentar o loop real de `_run_daily_etl` para abrir e concluir um
`source_run` por scraper do `SCRAPER_REGISTRY`, preservando integralmente o
payload, erros em `pipeline_errors`, alertas e etapas posteriores.

## Arquivos prováveis

- `src/radar/core/tasks.py`;
- `src/radar/core/services/source_coverage.py`;
- `tests/unit/test_etl_gold_pipeline.py` e/ou
  `tests/unit/test_source_coverage_etl.py` (novo).

## Passos

1. Gerar um `batch_id` por chamada de `_run_daily_etl`; para cada chave do
   registry, resolver a definição documental e abrir telemetria antes de
   instanciar/executar o scraper.
2. Ao retorno, conservar `results` e `new_count` como hoje, registrar
   `records_observed` e finalizar. Resultado vazio de produtor sem prova de
   ausência deve carregar sinal não sensível de ambiguidade para que a leitura
   futura não o converta em `healthy`.
3. Nos dois `except` existentes, manter `log_pipeline_error`, log e `step_errors`;
   finalizar apenas aquele run como `failed`, com `error_count`/`reason_code`
   canônicos, sem traceback. Não inferir `partial` do silêncio: só usar esse
   estado se o scraper futuro expuser falhas parciais confiáveis.
4. Isolar cada chamada de telemetria: falhar ao abrir/finalizar registra log
   local e deixa o scraper, `pipeline_errors`, alertas e total exatamente como
   estavam. Não alterar os demais estágios do cron.
5. Não antecipar snapshot de catálogo, Descoberta ou read model nesta task. T04
   acrescentará a chamada única de seu inspector após este loop, reutilizando o
   batch da rodada sem duplicar telemetria.

## Invariantes

- Há no máximo um run por `(batch_id, source_key)` e os quatro source keys são
  os do registry normativo/operacional compatível.
- Sucesso técnico não alega completude de portal; `0` ambíguo permanece
  distinguível.
- O retorno e os efeitos de `scraper.extract`, bronze, silver, gold, Documento
  Canônico e Obsidian não mudam.

## Testes direcionados

- scraper bem-sucedido com registros, vazio ambíguo e exceção tipada/genérica;
- falha de `start_run` e `finish_run` não derruba a execução nem remove
  `pipeline_errors`/alertas esperados;
- batch comum e uma finalização por canal;
- `ENVIRONMENT=test pytest -q tests/unit/test_etl_gold_pipeline.py
  tests/unit/test_source_coverage_etl.py`, `ruff check` no escopo e
  `git diff --check`.

## Pare

Pare se a instrumentação alterar o payload de scraper, transformar falha
absorvida em sucesso, exigir rede/credencial ou precisar deduzir parcial de
lista vazia. Divergência entre registry documental e `SCRAPER_REGISTRY` volta
para T01/governança.

## Entrega e ambiente hermético

Entregar somente a instrumentação e seus testes, com relatório `RT03-T03-*.md`
contendo os três cenários e a prova de best-effort. Confirmar `ENVIRONMENT=test`,
mocks de scraper/DB, sem `.env`, `ingest_all` real, rede, produção ou worker.
