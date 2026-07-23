# RT00-T07 — Fechamento e reconciliação

**Objetivo:** provar que a spec 00 foi implementada sem regressão do fluxo atual.

## Escopo

- revisar todos os diffs T01–T06;
- confirmar gate humano, cache e promoção;
- consolidar métricas e limitações;
- reconciliar schema, arquitetura, runbook e status da spec.

## Validação

- ruff sobre Python versionado;
- pytest completo contra baseline da branch-base;
- frontend lint/typecheck se tocado;
- run diagnóstica de relevância;
- teste manual mínimo de pending → promote/reject.

## Entrega

- relatório consolidado em `reports/00-relevance/README.md`;
- um relatório por task;
- lista explícita de pendências para Data Trust 02/03.

## Pare

Não marque a spec vigente com divergência não explicada, caso omitido do
manifesto ou regressão não presente na branch-base.
