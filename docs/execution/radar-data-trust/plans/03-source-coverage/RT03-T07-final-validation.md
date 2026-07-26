# RT03-T07 — Baseline, reconciliação e fechamento

## Objetivo

Validar localmente o funil entregue e reconciliar a documentação com o runtime,
sem apresentar cobertura medida como completude da web ou executar coleta real.

## Arquivos prováveis

- `docs/execution/radar-data-trust/reports/03-source-coverage/README.md` e
  relatórios por task (novos, no fechamento);
- `docs/specs/radar-data-trust-03-source-coverage.md` e
  `docs/specs/radar-data-trust.md` (status somente após sucesso);
- documentação de runtime estritamente afetada e correções mínimas de T01–T06.

## Passos

1. Revisar registry, famílias, migration, atribuição, runs, read model, endpoint
   e UI contra §§5–13. Confirmar por busca que não existe lista paralela,
   escopo de atores, endpoint de escrita ou atributo sensível exposto.
2. Executar baseline de fixtures: sete canais, quatro famílias, staging legado,
   runs de sucesso/falha/ambiguidade, funil editorial e domínio recorrente.
   Registrar observação de fixture, nunca dado/cobertura de produção.
3. Consolidar denominadores, lacunas, limitações, falhas pré-existentes versus
   branch-base, evidências/commits e pendências para corpus retrospectivo futuro.
4. Só depois atualizar status da spec/tabela-mãe e docs realmente afetados;
   `_coverage.md`/`_discovery.md` continuam autoridades normativas.

## Invariantes

- Sem rede, Cloud, deploy, cron, worker, backfill, migration remota, Tavily,
  DOU, LLM, eval, threshold/gate de recall, alerta ou fonte automática.
- Só concluir a spec com migration/RLS, compatibilidade pública, atribuição,
  saúde conservadora, funil, auth e fallback demonstrados localmente.

## Testes direcionados e fechamento

- testes RT03, `ruff check` no Python alterado e `git diff --check`;
- `pytest -q` completo contra a branch-base;
- `cd frontend && npx tsc --noEmit` e `cd frontend && npm run lint`;
- inspeção local de migration/RLS e ausência de consumidor/ação fora do escopo.

## Pare

Não marcar vigente com regressão sem explicação, RLS/migration não verificada,
atribuição inventada, métrica sem denominador, domínio auto-promovido, UI que
muta dados, vazamento sensível ou acesso externo. Contradição volta à spec.

## Entrega e ambiente hermético

Entregar relatório consolidado, relatórios por task, docs reconciliadas e
evidências. Declarar `ENVIRONMENT=test`, fixtures/fakes ou DB local isolado e
ausência de `.env` produtivo, credencial, rede, Cloud, Tavily, DOU, LLM, deploy
ou migration remota.
