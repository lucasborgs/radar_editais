# RT03-T04 — Instrumentação multicanal da Descoberta

## Objetivo

Preservar a identidade de `open_search`, `dou` e `hub_expansion` até o staging,
medir cada canal/família e preencher a atribuição nova. O retorno público
`discover_opportunities(...)->list[dict]` continua idêntico.

## Arquivos prováveis

- `src/radar/core/ingestion/opportunity_discovery.py`;
- `src/radar/core/ingestion/dou_feeder.py` (somente razão interna canônica, se
  indispensável);
- `src/radar/core/services/source_runs.py`;
- `tests/unit/test_source_coverage_discovery.py` (novo), com reuso de
  `tests/unit/test_opportunity_discovery_cache.py`.

## Passos

1. Criar relatório interno por rodada/canal que carrega canal, família e
   contadores: retornados, dedup, falhas por query, triagens, cache, rejeições,
   falhas de triagem/extração, filhos de hub, produzidos e enviados ao staging.
2. Associar cada hit ao canal lógico antes de uni-los. `open_search` recebe a
   família declarada, DOU fica sem família quando não houver query e filho de
   hub preserva família do pai somente quando tecnicamente conhecida; caso
   contrário é `null`. Atribuir ao record apenas `discovery_run_id`, canal,
   família e hostname normalizado de URL.
3. Abrir/finalizar `source_runs` separados para `open_search`, `dou` e
   `hub_expansion`. Credencial ausente é `skipped`; DOU em fim de semana pode
   ser `skipped`; zero indistinguível é sinal ambíguo. Falha observável por
   query/candidato é contador e `partial`, sem exceção bruta.
4. Fazer `_stage_records` aceitar a atribuição interna sem mudar dedup, raw,
   relevância, status, ledger ou promoção. Telemetria falha de modo best-effort.

## Invariantes

- `open_search` não é Tavily; trocar provider futuro não altera atribuição
  pública. DOU e hub não viram adapters/gold separados.
- Query completa, URL/path, corpo e saída LLM nunca vão para `source_runs` ou
  colunas de staging; somente família e domínio normalizado.
- Nenhum candidato pula o gate humano e a assinatura/semântica pública da
  Descoberta permanece compatível.

## Testes direcionados

- atribuição independente Tavily/open_search, DOU e filho de hub; dedup entre
  canais; família presente/nula e domínio sem path/query;
- sem credencial, fim de semana, zero ambíguo, falha de query/triagem/extração;
- retorno público, ledger, staging e promoção inalterados;
- `ENVIRONMENT=test pytest -q tests/unit/test_source_coverage_discovery.py
  tests/unit/test_opportunity_discovery_cache.py`, `ruff check` no escopo e
  `git diff --check`.

## Pare

Pare se for preciso mudar assinatura pública, dedup/status editorial, prompt ou
modelo, se a atribuição exigir inventar origem/família, ou se teste chamar busca,
DOU/LLM/rede reais. Reportar contagem não atribuível em vez de fabricá-la.

## Entrega e ambiente hermético

Entregar relatório interno, persistência de atribuição e testes, com relatório
`RT03-T04-*.md` contendo denominadores/ambiguidades. Confirmar
`ENVIRONMENT=test`, mocks de busca/DOU/LLM/DB e ausência de `.env`, rede,
produção ou worker.
