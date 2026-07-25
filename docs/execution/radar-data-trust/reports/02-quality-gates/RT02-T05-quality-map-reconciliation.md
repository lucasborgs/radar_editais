# RT02-T05 — Fechamento, mapa de qualidade e reconciliação

**Status:** concluída; auditoria Codex aprovada
**Plano:** [`RT02-T05-quality-map-reconciliation.md`](../../plans/02-quality-gates/RT02-T05-quality-map-reconciliation.md)
**Branch/base:** `codex/radar-data-trust-02-completion` / `37f34a74d112b441b91279d058209f127ce1e1d9`
**Correção auditada:** `641b6e648` (sinais observados e fail-closed antes de callbacks)
**Auditoria Codex:** aprovada em 2026-07-24

## Realizado

- Consolidado o mapa de camadas, classificações, tamanho de golden, execução e
  limites de representatividade em [`README.md`](README.md).
- Reconciliadas as listas de suítes e suas classificações em `AGENTS.md` e
  `docs/specs/evaluation-operations.md`.
- Corrigida a fotografia documental: `writing` é `diagnostic` (agora explícito
  no runtime, sem mudança efetiva); `writing_v2` permanece `experimental`.
- Atualizados o status da spec 02 e a tabela da spec-mãe; após a auditoria
  final, ambos foram marcados como vigentes.

## Validação local hermética

- Testes direcionados de provenance, e2e_health, harness e golden: **71 passed**.
- `ruff check` sobre todo Python versionado: **verde**.
- `provenance` executada duas vezes com agregados idênticos: exact 2/6,
  document_only 2/6, unresolved 2/6 e faithfulness 4/4 entre candidatos
  resolvidos. O golden não observa state/producer, portanto não fabrica
  completude por campo.
- `e2e_health` observa um único fato real capturado do gold: state presente e
  producer completo (`kind`/`name`/`version`) em 1.0. É amostra E2E, não
  cobertura dos campos críticos.
- `e2e_health` executada duas vezes com agregados idênticos: os sinais do
  caminho em 1.0 e `operational_error=0.0`.
- `pytest -q` completo: **1384 passed, 77 skipped, 3 failed**. As três falhas
  não são regressões desta spec: os quatro arquivos envolvidos
  (`entity_catalog`, `chunker` e seus testes) não diferem da base
  `37f34a74d`. A falha de catálogo espera o conjunto antigo de chaves sem
  `provenance`; as duas falhas de chunker pressupõem uma contagem de tokens que
  o ambiente atual não reproduz. Não foram alteradas para não introduzir
  workaround fora do escopo.

## Limitações e decisões

- `provenance` é baseline comportamental diagnóstico de seis casos; não prova
  representatividade, não tem threshold e não autoriza gate.
- `e2e_health` cobre um único caminho mínimo; é smoke diagnóstico, não matriz
  E2E.
- Casos `conflicting` e retificação continuam encaminhados à spec 04, que será
  dona de precedência documental.
- O incidente anterior de produção está contido. O harness agora recusa,
  antes de qualquer prereq, carregamento ou task, ambiente production/staging
  e alvos remotos em ambiente local/test/desconhecido. Nenhuma execução desta
  task usou rede, credenciais, LLM, banco remoto ou produção.

## Não aplicável

Frontend, Docker, worker, migrations, consumidores de matching/RAG/Explore e
escrita não foram alterados.
