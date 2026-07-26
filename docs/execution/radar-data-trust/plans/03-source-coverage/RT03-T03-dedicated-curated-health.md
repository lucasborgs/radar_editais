# RT03-T03 — Saúde das fontes dedicadas e Web curada

## Objetivo

Instrumentar o loop real de `_run_daily_etl` para os canais `finep`, `fapesp`,
`fapesc` e `web_curated`, sem alterar payload, retry, alertas, bronze, silver ou
gold. Esta task mede o caminho determinístico; não toca Descoberta aberta.

## Arquivos prováveis

- `src/radar/core/tasks.py`;
- `src/radar/core/services/source_runs.py`;
- `tests/unit/test_etl_gold_pipeline.py` e
  `tests/unit/test_source_coverage_etl.py` (novo).

## Passos

1. Gerar um `batch_id` por `_run_daily_etl` e mapear somente as quatro entradas
   existentes do `SCRAPER_REGISTRY` às chaves documentais (o scraper `web` é
   `web_curated`). Abrir uma run antes de cada coleta.
2. Ao retorno, manter `results`/`new_count` e registrar `records_observed`.
   Lista vazia sem prova de ausência recebe sinal de resultado ambíguo; não vira
   `healthy` no leitor futuro.
3. Nos `except` já existentes, preservar `pipeline_errors`, logs e `step_errors`;
   finalizar a fonte como `failed` com razão curta/contador, sem exceção bruta.
   Não inferir `partial` do silêncio.
4. Isolar abertura/finalização de telemetria: falha de DB só loga e não muda
   scraper, alertas, total ou estágios posteriores.

## Invariantes

- Uma run por canal/batch; sucesso técnico não é prova de completude do portal.
- `web_sources` continua autoridade das URLs curadas; não é transformada em
  fonte individual nem alterada.
- Nenhuma etapa após scraping ganha dependência da telemetria.

## Testes direcionados

- sucesso com itens, vazio ambíguo, exceção tipada/genérica e telemetria falha;
- batch comum, canal correto e `pipeline_errors`/alertas preservados;
- `ENVIRONMENT=test pytest -q tests/unit/test_etl_gold_pipeline.py
  tests/unit/test_source_coverage_etl.py`, `ruff check` no escopo e
  `git diff --check`.

## Pare

Pare se payload/retorno/retry mudar, se zero for promovido a saudável, se uma
falha absorvida virar sucesso ou se for preciso coletar rede/credencial. Conflito
entre registry e `SCRAPER_REGISTRY` volta a T01.

## Entrega e ambiente hermético

Entregar instrumentação/testes e relatório `RT03-T03-*.md` com os cenários de
saúde e best-effort. Confirmar `ENVIRONMENT=test`, scrapers/DB mockados, sem
`.env`, worker, ingestão gold real, rede ou produção.
