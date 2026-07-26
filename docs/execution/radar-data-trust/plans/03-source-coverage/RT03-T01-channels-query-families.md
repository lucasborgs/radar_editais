# RT03-T01 — Contrato de canais e famílias de busca

## Objetivo

Declarar os sete canais de aquisição de oportunidades e as quatro famílias de
busca estáveis. O contrato é documental e carregado pelo schema existente; ele
não executa coleta nem muda o comportamento atual da Descoberta.

## Arquivos prováveis

- `docs/domain/sources/_coverage.md` (novo, registry autoritativo);
- `docs/domain/sources/_discovery.md` (famílias/identificadores e motivo das
  queries, preservando texto configurável);
- `src/radar/core/kg/schema.py` (helpers puros, sem lista paralela);
- `tests/unit/test_source_coverage_registry.py` (novo).

## Passos

1. Declarar no YAML os canais `finep`, `fapesp`, `fapesc`, `web_curated`,
   `open_search`, `dou` e `hub_expansion`, com modo, nome, papel, intervalo
   quando periódico, e nome de flag quando aplicável. `open_search` representa
   o port lógico; não registrar Tavily como canal normativo.
2. Estruturar as quatro famílias iniciais em `_discovery.md`:
   `state_innovation_funding`, `corporate_open_innovation`,
   `startup_acceleration` e `international_brazil_access`. Cada query mantém
   texto e família; alteração futura registra motivo e permite comparação, sem
   criar canal novo.
3. Preservar compatibilidade até T04: o loader expõe entradas normalizadas com
   `query_family`, enquanto o consumidor atual de strings continua funcional
   (por projeção plana ou helper específico). Não executar busca nesta task.
4. Validar unicidade/lowercase de `source_key` e família, modos aceitos,
   intervalos/flags coerentes e que query completa não é devolvida para métricas.

## Invariantes

- Só canais de oportunidade existentes entram; catálogos de atores não são
  canais desta spec.
- Docs são a autoridade. Código lê/valida, sem duplicar canais, famílias, flags
  ou queries normativas.
- Sem segredo, valor de flag, URL parametrizada ou promessa de cobertura total.

## Testes direcionados

- registry/famílias reais e fixtures inválidas (duplicidade, casing, modo,
  intervalo/flag e família ausente);
- compatibilidade da projeção de queries existente;
- `ENVIRONMENT=test pytest -q tests/unit/test_source_coverage_registry.py`,
  `ruff check` no loader/teste e `git diff --check`.

## Pare

Pare se uma chave não corresponder ao produtor atual, se a compatibilidade de
queries exigir alterar a Descoberta agora, ou se for preciso armazenar query ou
segredo no registry. A decisão retorna à governança.

## Entrega e ambiente hermético

Entregar docs, loader e teste, com relatório `RT03-T01-*.md` listando os sete
canais e quatro famílias. Confirmar `ENVIRONMENT=test`, sem `.env`, DB, rede,
worker, Tavily, DOU, LLM ou produção.
