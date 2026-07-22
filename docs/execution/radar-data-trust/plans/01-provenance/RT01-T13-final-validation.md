# RT01-T13 — Fechamento e reconciliação

**Objetivo:** validar o conjunto T01–T12 e reconciliar spec, runtime e
limitações pré-beta.

## Validação

- ruff sobre Python versionado;
- pytest completo contra baseline da branch-base;
- frontend lint/typecheck se tocado;
- migrations/RLS e equivalência por origem;
- Docker app/worker se wiring ou runtime de container mudou;
- evals afetadas, sem inventar thresholds novos.

## Entrega

- relatório consolidado em `reports/01-provenance/README.md`;
- um relatório por task executada;
- matriz de fontes: validada, parcial, legacy ou bloqueada;
- pendências encaminhadas às specs 02–05;
- documentação autoritativa reconciliada.

## Pare

Não marque a spec vigente com regressão não explicada, migration não testada,
consumidor quebrado ou campo novo apresentado como confiável sem evidência.
