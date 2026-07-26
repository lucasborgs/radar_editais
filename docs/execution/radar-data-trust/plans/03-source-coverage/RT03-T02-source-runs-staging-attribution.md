# RT03-T02 — `source_runs` e atribuição nullable no staging

## Objetivo

Criar a única tabela nova (`source_runs`) e os quatro campos nullable de
atribuição em `discovered_opportunities`. Preparar persistência idempotente e
best-effort; ainda não instrumentar ETL/Descoberta nem expor API.

## Arquivos prováveis

- `supabase/migrations/043_source_runs.sql` (novo; confirmar numeração);
- `src/radar/core/services/source_runs.py` (novo, contrato/repositório);
- `tests/unit/test_source_runs.py` e teste local de migration/RLS quando
  necessário.

## Passos

1. Criar `source_runs` com IDs UUID, canal/mode congelados, estados restritos,
   timestamps UTC, contadores nullable, `error_count`, razão curta, `metrics`
   JSON não sensível, índice de leitura e unicidade por `(batch_id, source_key)`.
2. Na mesma migration, adicionar a `discovered_opportunities` apenas
   `discovery_run_id`, `discovery_channel`, `query_family` e `origin_domain`,
   todos nullable. Criar checks para valores de canal/família somente quando
   presentes; não tocar em `status`, `raw`, dedup, promoção ou RLS existente.
3. Habilitar RLS sem policy em `source_runs`, como `pipeline_errors`. Implementar
   `start_run`/`finish_run` idempotentes: retry não duplica nem regride estado
   terminal; falha do DB é logada e não relançada.
4. Validar contadores não negativos, reasons canônicas e métricas sanitizadas.
   `discovery_run_id` referencia a run sem exigir atribuição em dados legados.

## Invariantes

- Migration é aditiva/reexecutável; não existe backfill de run ou atribuição.
- `mode` é congelado do registry; saúde será derivada em leitura, não gravada.
- Sem query completa, URL/path, conteúdo, traceback, prompt, resposta ou segredo.
- Persistência indisponível não altera aquisição, staging ou alertas.

## Testes direcionados

- schema/RLS/checks/defaults, staging legado todo `null` e referência válida;
- início/fim/retry/transição inválida de runs e falha best-effort do cliente;
- `ENVIRONMENT=test pytest -q tests/unit/test_source_runs.py`, teste local de
  migration se aplicável, `ruff check` no escopo e `git diff --check`.

## Pare

Pare se migration alterar policy/semântica existente, exigir backfill, tornar
atribuição obrigatória ou precisar guardar diagnóstico sensível. Não aplicar
migration remota, Cloud ou rede.

## Entrega e ambiente hermético

Entregar migration, repositório/testes e relatório `RT03-T02-*.md` com RLS,
compatibilidade legada e idempotência. Confirmar `ENVIRONMENT=test`, fake/DB
local isolado, sem `.env`, produção, rede, Tavily, DOU ou LLM.
