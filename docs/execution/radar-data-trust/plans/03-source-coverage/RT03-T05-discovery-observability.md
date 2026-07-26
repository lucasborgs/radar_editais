# RT03-T05 — Métricas aditivas da Descoberta Tavily/DOU

## Objetivo

Medir Tavily e DOU como canais distintos dentro da Descoberta, sem alterar a
assinatura pública `discover_opportunities(...)->list[dict]`, o gate humano, o
ledger ou a classificação de relevância.

## Arquivos prováveis

- `src/radar/core/ingestion/opportunity_discovery.py`;
- `src/radar/core/ingestion/dou_feeder.py` (somente se expuser razão canônica
  interna sem mudar sua API pública);
- `src/radar/core/services/source_discovery_observability.py` (novo, relatório
  interno que consome o repositório de T02);
- `tests/unit/test_source_coverage_discovery.py` (novo), com reaproveitamento
  de `tests/unit/test_opportunity_discovery_cache.py` quando útil.

## Passos

1. Introduzir relatório interno/aditivo, por execução e por canal, que acompanha
   candidatos retornados, dedup, triagens executadas/puladas, rejeições, falhas
   de triagem/extração, registros produzidos e staging. O wrapper público ainda
   devolve apenas a lista atual.
2. Preservar a origem interna de cada candidato até a contagem final, sem gravar
   query, URL ou conteúdo em `source_runs`. Dedup entre canais deve ser contado
   de forma explícita, não atribuído silenciosamente ao canal vencedor.
3. Abrir/finalizar runs separados para `tavily` e `dou`. Ausência de credencial
   vira `skipped` com razão canônica; DOU de fim de semana pode ser `skipped`.
   Em dia útil, retorno vazio/impossível de distinguir de indisponibilidade fica
   `result_ambiguous`/estado público `unknown`, nunca saudável.
4. Capturar falhas por query/candidato como contadores e, quando a rodada ainda
   produz resultado, estado `partial`; não guardar exceção bruta. Falha da
   telemetria continua best-effort e não altera staging/ledger.
5. Não adicionar query, fonte, flag ou chamada de rede; mocks devem exercitar os
   caminhos já existentes. A decisão editorial posterior fica para o read model
   de T06, por consulta a `discovered_opportunities`.

## Invariantes

- DOU e Tavily continuam produtores de candidatos, não novos adapters nem
  fontes gold; promoção humana continua obrigatória.
- Sem credencial não equivale a zero/sucesso; lista vazia não prova ausência de
  oportunidade.
- Nenhum `raw`, URL, query, texto, prompt, resposta ou chave cruza para a nova
  tabela/API.

## Testes direcionados

- Tavily e DOU separados, dedup entre eles e staging atribuído ao canal correto;
- Tavily sem chave, DOU desligado/sem credencial/fim de semana e zero ambíguo;
- falha por query, por triagem e por extração como contadores/partial quando
  observável; retorno público, ledger e gate editorial inalterados;
- `ENVIRONMENT=test pytest -q tests/unit/test_source_coverage_discovery.py
  tests/unit/test_opportunity_discovery_cache.py`, `ruff check` no escopo e
  `git diff --check`.

## Pare

Pare se a separação exigir mudar `SearchHit`/o retorno público de forma
incompatível, persistir detalhes sensíveis, trocar prompt/modelo, chamar Tavily,
DOU ou LLM reais, ou inferir que uma lista vazia é saudável. Reportar qualquer
contagem que não possa ser atribuída honestamente em vez de inventá-la.

## Entrega e ambiente hermético

Entregar relatório interno, instrumentação, testes e relatório `RT03-T05-*.md`
com denominadores/ambiguidades explícitos. Confirmar `ENVIRONMENT=test`, mocks
de busca/DOU/LLM/DB e ausência de `.env`, rede, produção, worker ou credenciais.
