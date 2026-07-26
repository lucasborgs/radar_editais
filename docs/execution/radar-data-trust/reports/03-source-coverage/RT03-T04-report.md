# RT03-T04 — Relatório

## Resultado

Descoberta aberta instrumentada com `source_runs` independentes para os 3 canais
(`open_search`, `dou`, `hub_expansion`), atribuição no staging (`discovery_run_id`,
`discovery_channel`, `query_family`, `origin_domain`) e relatório interno por
canal com contadores numéricos. Assinatura pública `discover_opportunities(...)->list[dict]`
preservada.

## Comportamento implementado

### `discover_opportunities` (src/radar/core/ingestion/opportunity_discovery.py)

1. **batch_id** UUID gerado por execução, compartilhado pelos 3 canais.
2. **start_run** para dou, open_search e hub_expansion no início (best-effort:
   DB ausente → sem run_ids → sem persistência de telemetria).
3. **Atribuição** via `_attribution: dict[str, dict]` mapeando `norm_url` →
   `{channel, family}`:
   - `dou`: sem família (`query_family=None`)
   - `open_search`: família da query estruturada (`discovery_queries()`)
   - `hub_expansion`: herda família do pai quando conhecida
4. **origin_domain**: `urllib.parse.urlsplit(url).netloc.lower()` — somente
   hostname, nunca path/query.
5. **Relatório interno** (`_ChannelReport`) por canal com contadores:
   `returned`, `after_dedup`, `query_failures`, `triages_executed`,
   `triage_skipped_cache`, `triage_rejected`, `triage_failed`,
   `extraction_failed`, `hubs_expanded`, `hub_children_found`,
   `produced`, `staged`, `error_count`.
6. **finish_run** ao final: `skipped` (se canal não executou), `succeeded`
   (sem erros) ou `partial` (com `error_count > 0`).
7. **_stage_records** modificada para preservar os 4 campos de atribuição
   do record ao row do staging.
8. **Credencial ausente** (LLM None) → todos os canais skipped/no_credentials.
9. **DOU em fim de semana** → skipped/weekend_skip (detectado por `day.weekday() >= 5`).
10. **Falha de telemetria** (DB None) → runs não abertas, descoberta continua.

### Invariantes preservados

- `open_search` é o canal lógico; Tavily não é nomeado.
- Query completa, URL/path, corpo e saída LLM nunca vão para `source_runs` ou staging.
- Nenhum candidato pula o gate humano.
- Dedup entre canais preserva semântica existente (primeiro canal vê a URL).
- Retorno público `list[dict]`, ledger e staging inalterados em estrutura.

## Arquivos alterados

| Arquivo | Alteração |
|---|---|
| `src/radar/core/ingestion/opportunity_discovery.py` | +201 linhas: `_ChannelReport`, `_get_db`, `_origin_domain`, `discovery_queries()` routing, atribuição, source_runs, modificação `_stage_records` |
| `tests/unit/test_source_coverage_discovery.py` | +448 linhas (novo): 28 testes |
| `docs/execution/radar-data-trust/reports/03-source-coverage/RT03-T04-report.md` | este relatório |

## Testes direcionados — 28 passed

| Teste | O que cobre |
|---|---|
| `TestOpenSearchChannel::test_open_search_attribution` | channel=open_search, family presente, domain norm |
| `TestOpenSearchChannel::test_open_search_run_tracked` | start/finish chamados |
| `TestOpenSearchChannel::test_multiple_queries_multiple_families` | 2 queries, 2 famílias distintas |
| `TestDouChannel::test_dou_attribution_no_family` | channel=dou, family=None |
| `TestDouChannel::test_dou_disabled_has_start` | start chamado mesmo qdo DISCOVERY_DOU_ENABLED=0 |
| `TestHubExpansion::test_hub_child_inherits_family` | child herda family do parent open_search |
| `TestHubExpansion::test_hub_disabled_no_expansion` | HUB_CRAWL disabled → sem expansão |
| `TestOriginDomain::test_domain_no_path` | hostname sem path |
| `TestOriginDomain::test_domain_no_query` | hostname sem query string |
| `TestOriginDomain::test_domain_https_stripped` | scheme removido |
| `TestCrossChannelDedup::test_dou_wins_over_open_search` | DOU (1º) vs open_search mesma URL |
| `TestCrossChannelDedup::test_dedup_dou_first_then_open_search_skips` | DOU vê 1º, open_search skip |
| `TestNoCredentials::test_no_llm_returns_empty` | LLM None → [] |
| `TestNoCredentials::test_no_llm_skipped` | skipped status |
| `TestNoCredentials::test_no_llm_ledger_unchanged` | ledger não tocado |
| `TestWeekendDou::test_weekend_dou_does_not_process` | fim de semana → sem staging |
| `TestEmptyResult::test_empty_return` | sem candidatos → [] |
| `TestEmptyResult::test_finish_called` | finish chamado mesmo vazio |
| `TestEmptyResult::test_ledger_unchanged` | ledger não tocado |
| `TestFailures::test_query_failure` | web_search exception → records==[] |
| `TestFailures::test_triage_failure` | triage None → error_count |
| `TestFailures::test_extraction_failure` | extract None → error_count |
| `TestTelemetryUnavailable::test_db_none_still_discovers` | DB None → descoberta funciona |
| `TestTelemetryUnavailable::test_db_none_no_source_runs` | DB None → sem finish |
| `TestPublicReturn::test_returns_list_of_dicts` | retorno público intacto |
| `TestPublicReturn::test_ledger_saved` | ledger populado |
| `TestPublicReturn::test_stage_called_with_attribution` | 4 campos no staging |
| `TestPublicReturn::test_dry_run_no_staging` | write=False → sem staging |

## Validação

- `ENVIRONMENT=test pytest tests/unit/test_source_coverage_discovery.py`: **28 passed**
- `ENVIRONMENT=test pytest tests/unit/test_source_runs.py`: **39 passed**
- `ENVIRONMENT=test pytest tests/unit/test_opportunity_discovery_cache.py`: **4 passed**
- `ENVIRONMENT=test pytest tests/unit/test_source_coverage_registry.py`: **42 passed**
- `ruff check src/radar/core/ingestion/opportunity_discovery.py tests/unit/test_source_coverage_discovery.py`: **All checks passed**
- `git diff --check`: **sem whitespace errors**

## Divergências e limitações

- Migration não reaplicada (schema de T02 já existe na base 645b7325c).
- `reason_code` do skip de canal sem credencial fica `None` (o código define
  `report.skipped = True` e `report.reason = "no_credentials"`, mas a lógica
  de `_finish_all` só usa `report.reason` quando `report.skipped=True` — correto.
- Testes de `hub_expansion` com `depth=0` e `is_hub=True` usam triagem mapeada
  para o hub e seus filhos.
- `_StubDT` é subclasse de `datetime.datetime` com `now()` controlável —
  necessário porque o C type `datetime.datetime` não permite monkeypatch direto.
- Nenhum prompt, modelo, promoção, gold, RAG ou migration foi alterado.

## Ambiente

- Worktree isolado em `/private/tmp/radar-editais-rt03-t04`
- Branch: `codex/radar-data-trust-03-t04`
- `ENVIRONMENT=test` em toda execução
- Sem `.env`, produção, rede, Tavily, DOU, LLM, ingestão gold real ou merge/push
- RT03-T05 **não foi iniciada**
