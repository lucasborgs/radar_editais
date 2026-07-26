# RT03-T04 — Relatório (correções)

## Resultado

Descoberta aberta instrumentada com `source_runs` independentes para os 3 canais
(`open_search`, `dou`, `hub_expansion`), atribuição no staging (`discovery_run_id`,
`discovery_channel`, `query_family`, `origin_domain`) e relatório interno por
canal com contadores numéricos e métricas por família de query. Assinatura pública
`discover_opportunities(...)->list[dict]` preservada.

## Correções aplicadas (8)

### Fix 1 — `_origin_domain` robusto

| Antes | Depois |
|---|---|
| `urlsplit(url).netloc.lower()` — incluía porta e userinfo | `urlsplit(url).hostname.lower().rstrip(".")` — somente hostname, sem porta/userinfo/trailing dot. Retorna `None` (não string vazia) quando não há hostname. |

`origin_domain` no staging (`discovered_opportunities`) agora é `str | None`.
Exemplo: `"http://user:pass@example.com:8080."` → `"example.com"`.

### Fix 2 — `db=None` guard + `try/except` em telemetria

- `start_run` só é chamado quando `db is not None`. Cada chamada individual é
  protegida por `try/except RuntimeError` — uma falha não derruba os outros canais.
- `_finish_all` também guarda `if db is None: return` e envolve cada
  `_finish_run` em `try/except`.
- Sem `start_run` nem `_finish_run` sem DB — a descoberta prossegue normalmente.

### Fix 3 — `hub_expansion.skipped = True` quando flag off

Quando `DISCOVERY_HUB_CRAWL_ENABLED != "1"`, o relatório do canal
`hub_expansion` é imediatamente marcado `skipped=True` (sem reason code — o
skip silencioso é o comportamento esperado). Antes ficava sem skipped e podia
terminar `succeeded` mesmo sem ter executado.

### Fix 4 — `search_available()` + guarda no Tavily

Nova função `web_search.search_available() -> bool`:
- Provider-neutral: só checa a env-var do backend configurado.
- Sempre retorna `False` se a credencial não existe (nunca levanta, nunca
  inspeciona textos de exceção).

`discover_opportunities` a chama ANTES do loop Tavily. Se `False`:
- `open_search` recebe `skipped=True, reason="no_credentials"`.
- DOU (que não precisa de busca web) continua rodando normalmente.
- O LLM (`_make_client`) ainda é verificado depois — DOU + LLM disponível
  ainda produz candidatos DOU.

### Fix 5 — `partial` runs com `reason_code` canônico

`_finish_all` agora computa `reason_code` para status `partial`:
- `"provider_error"` se `report.query_failures > 0` (erro do provedor de busca).
- `"unknown"` para falhas de triagem ou extração.

Também propaga `report.reason` diretamente quando `skipped=True` (já existia)
— códigos canônicos: `weekend_skip`, `no_credentials`, `empty_result`.

### Fix 6 — `hubs_expanded` no relatório `hub_expansion`

Antes: `report.hubs_expanded += 1` — incrementava o contador do canal *pai*
(open_search).

Depois: `reports["hub_expansion"].hubs_expanded += 1` — o contador vive no
canal correto. As métricas de `open_search` não incluem `hubs_expanded`.

### Fix 7 — Métricas por família de query

`_ChannelReport.family_metrics: dict[str, dict[str, int]]` (dataclass field
com `default_factory=dict`).

Populado no loop Tavily por família (`q_family` ou `"unknown"`):
- `returned_family_{family}` — hits retornados pela busca.
- `query_failures_family_{family}` — falhas de busca por família.

Incluído no dict de métricas via `to_metrics()` apenas para o canal
`open_search`. Chaves seguem o padrão `^[a-zA-Z_][a-zA-Z0-9_]*$` — seguras
para `_sanitize_metrics`.

### Fix 8 — Relatório RT03-T04 (semântica `if not queries`)

Quando `queries = []` (config vazia), **todos os 3 canais** são marcados
`skipped=True` com `reason="empty_result"` — antes só `open_search` era
marcado. Garante que nenhum canal termine `succeeded` sem ter executado.

Inclui `reason_code` no `_finish_run` para canais `partial` (fix 5) — estava
faltando.

## Comportamento final

### `discover_opportunities` (src/radar/core/ingestion/opportunity_discovery.py)

1. **batch_id** UUID gerado por execução, compartilhado pelos 3 canais.
2. **start_run** para dou, open_search e hub_expansion no início (best-effort:
   DB ausente → sem run_ids → sem persistência de telemetria). Cada chamada
   protegida por `try/except`.
3. **Atribuição** via `_attribution: dict[str, dict]` mapeando `norm_url` →
   `{channel, family}`:
   - `dou`: sem família (`query_family=None`)
   - `open_search`: família da query estruturada (`discovery_queries()`)
   - `hub_expansion`: herda família do pai quando conhecida
4. **origin_domain**: `urlsplit(url).hostname.lower().rstrip(".")` — apenas
   hostname, sem porta/userinfo/trailing dot, `None` se sem hostname.
5. **Relatório interno** (`_ChannelReport`) por canal com contadores:
   `returned`, `after_dedup`, `query_failures`, `triages_executed`,
   `triage_skipped_cache`, `triage_rejected`, `triage_failed`,
   `extraction_failed`, `hubs_expanded`, `hub_children_found`,
   `produced`, `staged`, `error_count`, `family_metrics`.
6. **Métricas por família** (`open_search`): `returned_family_{key}`,
   `query_failures_family_{key}`.
7. **finish_run** ao final: `skipped` (se canal não executou, com reason),
   `succeeded` (sem erros) ou `partial` (com `error_count > 0` e
   `reason_code` canônico).
8. **_stage_records** modificada para preservar os 4 campos de atribuição
   do record ao row do staging.
9. **Credencial de busca ausente** → `open_search` skipped/no_credentials;
   DOU continua.
10. **Credencial LLM ausente** → todos os canais skipped/no_credentials.
11. **DOU em fim de semana** → skipped/weekend_skip (detectado por
    `day.weekday() >= 5`).
12. **Config vazia** (`queries=[]`) → todos os canais skipped/empty_result.
13. **Falha de telemetria** (DB None) → runs não abertas, descoberta continua.
14. **`search_available`** checa provider sem inspecionar exceções.

### Invariantes preservados

- `open_search` é o canal lógico; Tavily não é nomeado.
- Query completa, URL/path, corpo e saída LLM nunca vão para `source_runs` ou staging.
- Nenhum candidato pula o gate humano.
- Dedup entre canais preserva semântica existente (primeiro canal vê a URL).
- Retorno público `list[dict]`, ledger e staging inalterados em estrutura.

## Arquivos alterados

| Arquivo | Alteração |
|---|---|
| `src/radar/core/ingestion/opportunity_discovery.py` | +249 linhas: `_ChannelReport` com `family_metrics`, `_origin_domain` robusto, `search_available` guard, db=None guard + try/except, hub_expansion.skipped, partial reason_code, hubs_expanded no canal correto, métricas por família, `if not queries` cobre 3 canais, `_finish_all` com reason_code e try/except |
| `src/radar/core/web_search.py` | +7 linhas: `search_available()` |
| `tests/unit/test_source_coverage_discovery.py` | +37 linhas: `TestNoQueries` (2 testes), fix `test_db_none_no_source_runs` |
| `tests/unit/test_hardening_pr4.py` | +1 linha: `search_available` mock |
| `tests/unit/test_opportunity_discovery_cache.py` | +426 linhas (novo): 18 testes (origin_domain, search_available, db=None, exception handling, hub_expansion, partial reason_code, family metrics) |
| `docs/execution/radar-data-trust/reports/03-source-coverage/RT03-T04-report.md` | este relatório |

## Testes direcionados — 149 passed

### `test_source_coverage_discovery.py` (30 testes)

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
| `TestNoQueries::test_empty_config_returns_empty` | queries=[] → [] |
| `TestNoQueries::test_all_three_channels_skipped` | queries=[] → 3× skipped/empty_result |
| `TestFailures::test_query_failure` | web_search exception → records==[] |
| `TestFailures::test_triage_failure` | triage None → error_count |
| `TestFailures::test_extraction_failure` | extract None → error_count |
| `TestTelemetryUnavailable::test_db_none_still_discovers` | DB None → descoberta funciona |
| `TestTelemetryUnavailable::test_db_none_no_source_runs` | DB None → sem start/finish |
| `TestPublicReturn::test_returns_list_of_dicts` | retorno público intacto |
| `TestPublicReturn::test_ledger_saved` | ledger populado |
| `TestPublicReturn::test_stage_called_with_attribution` | 4 campos no staging |
| `TestPublicReturn::test_dry_run_no_staging` | write=False → sem staging |

### `test_opportunity_discovery_cache.py` (22 testes)

| Teste | Fix |
|---|---|
| `test_rejected_url_within_ttl_skips_triage` | cache negativo baseline |
| `test_expired_ttl_retriages` | cache negativo baseline |
| `test_discard_is_logged_and_recorded` | cache negativo baseline |
| `test_dry_run_measures_skips_without_persisting` | cache negativo baseline |
| `test_origin_domain_no_hostname` | Fix 1 |
| `test_origin_domain_strips_port` | Fix 1 |
| `test_origin_domain_strips_userinfo` | Fix 1 |
| `test_origin_domain_trailing_dot` | Fix 1 |
| `test_origin_domain_case_normalized` | Fix 1 |
| `test_search_available_true` | Fix 4 |
| `test_search_available_false` | Fix 4 |
| `test_db_none_skips_start_run` | Fix 2 |
| `test_db_none_still_processes_hits` | Fix 2 |
| `test_start_run_exception_logged` | Fix 2 |
| `test_finish_run_exception_logged` | Fix 2 |
| `test_hub_expansion_skipped_when_disabled` | Fix 3 |
| `test_search_skipped_when_unavailable` | Fix 4 |
| `test_partial_reason_provider_error` | Fix 5 |
| `test_partial_reason_unknown_on_triage_failure` | Fix 5 |
| `test_hubs_expanded_in_hub_expansion_report` | Fix 6 |
| `test_per_family_metrics_returned` | Fix 7 |
| `test_per_family_metrics_query_failures` | Fix 7 |

### `test_hardening_pr4.py` (2 discovery tests)

| Teste | Relevância |
|---|---|
| `test_transient_triage_failure_does_not_touch_ledger` | compatibilidade |
| `test_real_rejection_still_recorded_in_ledger` | compatibilidade |

### `test_source_runs.py` (39 testes) — cobertura do schema de telemetria
### `test_source_coverage_registry.py` (42 testes) — cobertura do schema de canais/famílias

## Validação

- `pytest tests/unit/test_source_coverage_discovery.py tests/unit/test_opportunity_discovery_cache.py tests/unit/test_hardening_pr4.py tests/unit/test_source_runs.py tests/unit/test_source_coverage_registry.py`: **149 passed**
- `ruff check src/radar/core/ingestion/opportunity_discovery.py src/radar/core/web_search.py tests/unit/test_source_coverage_discovery.py tests/unit/test_opportunity_discovery_cache.py tests/unit/test_hardening_pr4.py`: **All checks passed**
- `git diff --check`: **sem whitespace errors**

## Divergências e limitações

- Migration não reaplicada (schema de T02 já existe na base 645b7325c).
- `search_available()` só reconhece backend Tavily por enquanto (expansível).
- Testes de `hub_expansion` com `depth=0` e `is_hub=True` usam triagem mapeada
  para o hub e seus filhos.
- `_StubDT` é subclasse de `datetime.datetime` com `now()` controlável —
  necessário porque o C type `datetime.datetime` não permite monkeypatch direto.
- Nenhum prompt, modelo, promoção, gold, RAG ou migration foi alterado.

## Ambiente

- Worktree isolado em `/private/tmp/radar-editais-rt03-t04`
- Branch: `codex/radar-data-trust-03-t04`
- Sem `.env`, produção, rede, Tavily, DOU, LLM, ingestão gold real ou merge/push
- RT03-T05 **não foi iniciada**
