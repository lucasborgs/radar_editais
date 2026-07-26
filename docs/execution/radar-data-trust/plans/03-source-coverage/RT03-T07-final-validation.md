# RT03-T07 — Baseline local, reconciliação e fechamento

## Objetivo

Fechar a spec com um baseline local reproduzível e documentação coerente com o
runtime entregue, sem transformar telemetria nova em alegação de cobertura ou
executar coletores reais.

## Arquivos prováveis

- `docs/execution/radar-data-trust/reports/03-source-coverage/README.md` e um
  relatório por task (novos, criados/consolidados no fechamento);
- `docs/specs/radar-data-trust-03-source-coverage.md` e
  `docs/specs/radar-data-trust.md` (status/tabela, somente após sucesso);
- `AGENTS.md` e documentação operacional de fontes, apenas se o runtime final
  exigir reconciliação factual;
- correções estritamente necessárias de testes/código já tocados por T01–T06.

## Passos

1. Revisar diffs, registry, migration, escritores, agregador, endpoint e painel
   contra os §§5–12 da spec. Buscar referências/SQL/imports para confirmar que
   não há segunda lista normativa, consumidor oculto, policy de usuário ou
   caminho de escrita pela UI.
2. Rodar baseline local controlado com fixtures: registry completo, uma rodada
   de cada modalidade, tabela vazia e estados derivados. Registrar valores como
   observação de fixture, não como cobertura de produção.
3. Consolidar relatório com canais declarados, dados/denominadores disponíveis,
   ambiguidades, limitações, falhas pré-existentes comparadas à branch-base e
   pendências futuras. Incluir um relatório por task e commits/evidências.
4. Só então reconciliar status da spec 03, tabela da spec-mãe e docs de runtime
   efetivamente afetados. Não copiar o mesmo estado para documentos paralelos;
   registry permanece a autoridade de canais.

## Invariantes

- Sem backfill fictício, deploy, `supabase db push`, acesso a Cloud, cron,
  worker, Tavily, DOU ou LLM real.
- Sem nova suíte de eval, threshold, alerta/pager, SLA, fonte, query ou mudança
  de relevância/gold/RAG/ranking.
- Só marcar a spec vigente se migration/RLS, best-effort, estados conservadores,
  auth e fallback da UI estiverem demonstrados localmente.

## Testes direcionados e fechamento

- todos os testes RT03 e `ruff check` sobre Python versionado/escopo alterado;
- `pytest -q` completo, comparando qualquer falha com a branch-base;
- `cd frontend && npx tsc --noEmit` e `cd frontend && npm run lint`;
- inspeção local da migration e RLS, mais `git diff --check`;
- nenhum `python -m radar.core.eval ... --publish`, nenhuma rede e nenhum teste
  que leia `.env` de produção.

## Pare

Não marque a spec vigente com regressão não explicada, migration/RLS não
verificada, métrica fabricada, estado não conservador, API/UI que muta dados,
vazamento sensível ou evidência de acesso externo. Contradição entre spec e
runtime deve ser reportada e resolvida documentalmente antes do fechamento.

## Entrega e ambiente hermético

Entregar o relatório consolidado, relatórios por task, documentação reconciliada
e evidências de todos os gates. O relatório final deve declarar explicitamente:
`ENVIRONMENT=test`; fixtures, `tmp_path`, fakes ou Supabase local isolado;
nenhum `.env` produtivo, credencial, rede, produção, Cloud, LLM, Tavily, DOU,
deploy ou migration remota foi usado.
