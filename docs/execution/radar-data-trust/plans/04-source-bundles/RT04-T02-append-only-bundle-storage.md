# RT04-T02 — Persistência append-only e idempotente de bundles

## Objetivo

Criar a única tabela nova para versões de `SourceBundle` e o repositório
service-role-only que grava idempotentemente e lê a última versão `complete`.
Ainda não conectar produtores, composição ou consumidores.

## Dependências e pouso

Depende de T01. Usa o contrato puro e convive com `edital_source_docs`, que
continua a projeção atual. Revalidar a numeração de migration antes do pouso.

## Arquivos prováveis

- `supabase/migrations/044_source_bundles.sql` (número revalidável);
- `src/radar/domain/source_bundle.py` (contrato de T01) e
  `src/radar/core/kg/source_bundles.py` (novo; somente repositório);
- `tests/unit/test_source_bundles.py` e teste local de migration/RLS, se necessário.

## Passos delimitados

1. Criar uma única tabela com UUID, sujeito/fonte, hash, JSONB, status,
   `collected_at`, `created_at`, unicidade e índice para último `complete`.
2. Habilitar RLS sem policy de usuário final, como `edital_source_docs`; sem
   endpoint nem acesso de cliente.
3. Implementar append validado, sem update/delete e idempotente pelo hash. O
   leitor seleciona a última versão `complete`; `partial` só diagnostica.
4. Tornar falha de persistência categorizável para produtor futuro, sem mudar o
   comportamento presente de `source_docs.save()`.

## Testes proporcionais

- migration aditiva/reexecutável, RLS/restrições, repetição, versão material
  nova e `partial` posterior sem substituir `complete`;
- fake ou banco local isolado, testes direcionados, `ruff check` e diff check.

## Pare

Segunda tabela, trigger que reescreve histórico, backfill, policy de usuário,
documento fora de JSONB ou migration remota invalidam a task. Ordem impossível
de `complete` exige decisão de método.

## Não objetivos

Sem produtor, adapter, `source_docs` alterado, composição, proveniência, API,
métrica, revisão ou rede.

## Relatório esperado

`reports/04-source-bundles/RT04-T02-report.md`: migration/RLS, idempotência,
seleção `complete`, legado, commit/base, testes e ambiente hermético.
