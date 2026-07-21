# Repository polish

> **Registro histórico:** escopo concluído da revisão conservadora executada em
> 2026-07-14. Não autoriza trabalho atual.

## Objetivo

Executar uma revisão conservadora de qualidade pré-feedback externo para que o
repositório descreva com precisão o sistema em produção e não exponha resíduos
históricos comprovadamente sem uso.

## Escopo

- conferir documentação, manifests, runtime, deploy, CI e implementação atual;
- corrigir referências comprovadamente obsoletas ou contraditórias;
- remover arquivos, duplicações ou dependências somente quando a ausência de uso
  estiver demonstrada por busca de referências e validação do caminho afetado;
- melhorar arestas de organização estritamente quando prejudicarem a leitura do
  sistema existente, sem alterar contratos ou comportamento.

## Exclusões

Não fazem parte deste trabalho: funcionalidades, redesign, roadmap, levantamento
de gaps, refatoração por preferência, alteração de regras de domínio fora de
`docs/domain/schema.md`/`docs/domain/sources/`, mudanças de API, schema ou migration, e os artefatos locais de
avaliação `docs/historical/crawl4ai-eval.md`, `scripts/eval_crawl4ai*`,
`scripts/eval_comparison_scrapers*` e `scripts/eval_discovery_pipeline*`.

## Critérios objetivos para remoção

Um arquivo ou dependência só pode ser removido quando: (1) não houver referência
em código, testes, configuração, CI, deploy ou documentação operacional; (2) não
for ponto de entrada, contrato público, artefato de dados ou compatibilidade; e
(3) a validação proporcional do consumidor afetado permanecer verde. Em caso de
dúvida, o item será mantido e registrado como “mantido por incerteza”, com a
razão.

## Plano de validação

- revisar o diff e executar `git diff --check`;
- executar `ruff check .` se houver alteração Python;
- executar `cd frontend && npx tsc --noEmit` se houver alteração de frontend ou
  dependências JavaScript;
- executar testes direcionados para qualquer código alterado;
- validar instalação, build ou teste correspondente se uma dependência for
  removida;
- confirmar que o working tree final não incluiu os artefatos locais excluídos
  deste escopo.
