# RT03-T02 — `source_runs`: migration e persistência aditiva

## Objetivo

Disponibilizar a única persistência nova da spec: uma linha de execução por
canal/rodada, com início/fim idempotentes e escrita best-effort. Ainda não
instrumentar ETL, Descoberta ou catálogos.

## Arquivos prováveis

- `supabase/migrations/043_source_runs.sql` (novo; confirmar o próximo número
  antes de criar);
- `src/radar/core/services/source_coverage.py` (novo, repositório/contratos
  puros e adaptador de persistência);
- `tests/unit/test_source_runs.py` (novo);
- teste local de migration/RLS, se o padrão do repositório exigir um arquivo
  dedicado em `tests/integration/`.

## Passos

1. Criar `source_runs` com UUID de `id` e `batch_id`, chave/mode congelados,
   estados restritos (`running`, `succeeded`, `partial`, `failed`, `skipped`),
   timestamps UTC, contadores nullable, `error_count default 0`,
   `reason_code` curto e `metrics jsonb default '{}'`. Adicionar índices de
   leitura por `source_key`/tempo e de rodada; garantir no máximo uma linha por
   `(batch_id, source_key)`.
2. Habilitar RLS sem policy, como `pipeline_errors`; não alterar políticas ou
   colunas existentes. Documentar por comentários SQL que o service role escreve
   e a API de operador lê depois de `AdminUserId`.
3. Implementar contrato mínimo de `start_run`/`finish_run` que recebe IDs do
   chamador. Repetir início/fim do mesmo canal/batch não duplica nem regride uma
   linha terminal; a finalização só completa a própria linha `running`.
4. Validar status, contadores não negativos, razão apenas para
   parcial/falha/skip e métricas serializáveis/sanitizadas. Falha de cliente/DB
   é engolida com log categórico e resultado explícito para o chamador, nunca
   relançada ao produtor.
5. Usar `metrics` somente para extensões não sensíveis (por exemplo,
   `result_ambiguous` e metadados de artefato); não criar tabela de erros,
   detalhes ou uma segunda verdade de saúde.

## Invariantes

- Migration é aditiva e reexecutável; sem backfill de rodadas antigas.
- `mode` vem congelado do registry na abertura, não é recalculado da tabela.
- Traceback, URL com query, texto, prompt, resposta e segredo ficam fora de
  `source_runs`; detalhes técnicos continuam em logs/`pipeline_errors`.
- Persistência indisponível não muda o trabalho nem os alertas do ETL.

## Testes direcionados

- round-trip em fake/local DB para iniciar, finalizar, repetir e rejeitar
  transição inválida;
- schema: checks, defaults, índice/uniqueness e RLS sem policy de usuário;
- falha do cliente não propaga;
- `ENVIRONMENT=test pytest -q tests/unit/test_source_runs.py` e o teste local
  de migration, `ruff check` no escopo e `git diff --check`.

## Pare

Pare diante de alteração de RLS existente, coluna/tabela não aditiva, requisito
de backfill, necessidade de guardar diagnóstico sensível ou semântica ambígua
de idempotência. Não aplicar a migration remota nem iniciar Supabase Cloud.

## Entrega e ambiente hermético

Entregar migration, serviço mínimo, testes e relatório `RT03-T02-*.md` com
schema, RLS, transições e falha best-effort demonstrados. Confirmar
`ENVIRONMENT=test`, fixture/fake ou Supabase local isolado, sem `.env`, rede,
produção, Tavily, DOU ou LLM.
