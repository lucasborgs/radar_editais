# RT03-T06A — Backend: endpoint `GET /source-coverage`

**Data:** 2026-07-26
**Branch:** `codex/radar-data-trust-03-t06a`
**Base:** `25a9bf3a5` (main, merge `codex/radar-data-trust-03-t05`)
**Worktree:** `/private/tmp/radar-editais-rt03-t06a`
**Auditoria Codex:** pendente

---

## Arquivos alterados

| Arquivo | Tipo |
|---|---|
| `src/radar/api/routers/source_coverage.py` | criado |
| `src/radar/api/app.py` | alterado (2 linhas) |
| `tests/unit/test_source_coverage_api.py` | criado |
| `docs/execution/radar-data-trust/reports/03-source-coverage/RT03-T06A-backend.md` | alterado |

## Commits

1. `ee1f1d5fd` feat(rt03-t06a): add GET /source-coverage administrative endpoint
2. `(pending)` fix(rt03-t06a): auditoria — projeções, sanitização de erro, logs

## O que foi feito

### Router (`src/radar/api/routers/source_coverage.py`)

- Endpoint `GET /source-coverage` protegido por `AdminUserId` (fail-closed).
- Lê `source_runs` (projeção exata: 7 campos, sem `*`) e `discovered_opportunities` (6 campos, sem `id`) via service-role.
- Obtém canais e famílias pelos loaders autoritativos (`coverage_channels()`, `query_families()`).
- Chama `compute_source_coverage()` — sem duplicar suas regras.
- Injeta explicitamente apenas as flags dos canais gated via `_build_env_from_channels()`.
- `get_supabase_service()` incluso no mesmo bloco try/except que as queries → falha na construção também retorna 503 sanitizado.
- Erros sanitizados: `_sanitize_error` registra apenas `type(exc).__name__` (nunca `str(exc)`, `exc_info` ou traceback); resposta nunca contém DSN, query, URL sensível.
- Resposta inclui `generated_at`, `channels` (saúde), `runs`, `channel_funnel`, `family_funnel`, `gaps`, `emerging_domains`, `limitations`.
- `limitations` lista 5 textos canônicos sobre denominadores e impossibilidade de garantir cobertura total.
- Tabela vazia: canais `enabled_by_default=true` → `unknown`; canais gated sem flag → `disabled`.

### Response models

```python
SourceCoverageResponse
├── generated_at: str
├── channels: list[ChannelHealthOut]   # {source_key, health}
├── runs: dict[str, ChannelRunMetricsOut]
├── channel_funnel: dict[str, EditorialFunnelOut]
├── family_funnel: dict[str, FamilyFunnelOut]
├── gaps: list[CoverageGapOut]
├── emerging_domains: list[EmergingDomainOut]
└── limitations: list[str]
```

### App wiring (`src/radar/api/app.py`)

- Import: `from radar.api.routers.source_coverage import router as source_coverage_router`
- Registro: `app.include_router(source_coverage_router)` (antes do `discovered_router`)

## Testes (`tests/unit/test_source_coverage_api.py`)

81 testes passando (incluindo os 60 existentes de `test_source_coverage_metrics.py` e `test_admin_gate.py`):

| Categoria | Testes |
|---|---|
| Auth gate | non-admin → 403; admin → 200 |
| Estrutura do payload | campos top-level, 7 canais, dados representativos, domínios emergentes |
| Tabelas vazias | unknown/disabled, sem zeros fabricados, gaps enabled_no_run |
| Denominador ausente | yield_rate null, emitted null, last_attempt/success null |
| Canal gated | DISCOVERY_DOU_ENABLED=0 → disabled; =1 sem runs → unknown |
| Sanitização de erro | 503 categórico sem DSN/query/traceback; payload sem SELECT/URL/traceback |
| **Projeções** | source_runs com 7 campos sem `*`; discovered sem `id` |
| **Falha get_supabase_service** | 503 sanitizado; log só contém RuntimeError, não o segredo |
| Sem escrita | apenas .table().select() chamado; insert/update/delete/upsert nunca |
| Wiring | router registrado no app, gate de admin, apenas GET, response_model |

## Limitações conhecidas

- Frontend (T06-B) **não foi iniciado** — apenas backend.
- Nenhuma migration, cache, coluna ou persistência derivada.
- Nenhuma alteração em promoção, rejeição, relevância, registry, flags ou execução de fontes.
- Nenhum endpoint de edição, retry, crawler ou criação de scraper.

## Invariantes verificadas

- [x] Autorização obrigatória via `AdminUserId`, fail-closed.
- [x] Endpoint estritamente read-only.
- [x] Nenhuma migration, cache, coluna ou persistência derivada.
- [x] Nenhuma alteração em promoção/rejeição/relevância/registry/flags/fontes.
- [x] Nenhum endpoint de escrita/retry/crawler/scraper.
- [x] Não toca em `frontend/`.
- [x] Não acessa `.env`, produção, Supabase Cloud, rede, Tavily, DOU ou LLM.
- [x] Usa `ENVIRONMENT=test` e DB fake/mocks.
- [x] Sem novas dependências ou camadas genéricas.
