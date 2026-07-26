# RT03-T02 — Relatório

## Resultado

Migration aditiva `043_source_runs.sql`, repositório `source_runs.py` com
`start_run`/`finish_run` idempotente e best-effort, e testes de unidade
completos.

## Tabela nova: `source_runs`

| Campo | Tipo | Restrições |
|---|---|---|
| `id` | `uuid PK` | `gen_random_uuid()` |
| `batch_id` | `uuid NOT NULL` | Compartilhado pela rodada |
| `source_key` | `text NOT NULL` | Chave do canal |
| `mode` | `text NOT NULL` | Modalidade congelada |
| `status` | `text NOT NULL` | CHECK: running/succeeded/partial/failed/skipped |
| `started_at` | `timestamptz NOT NULL` | `now()` |
| `completed_at` | `timestamptz` | Nullable |
| `records_observed` | `integer` | Nullable |
| `records_emitted` | `integer` | Nullable |
| `records_staged` | `integer` | Nullable |
| `error_count` | `integer NOT NULL` | CHECK >= 0, default 0 |
| `reason_code` | `text` | Nullable |
| `metrics` | `jsonb NOT NULL` | `'{}'` |
| UNIQUE | `(batch_id, source_key)` | Uma run por canal por rodada |

RLS habilitada sem policy de usuário final (padrão `pipeline_errors`/`discovery_promotion_runs`).

## Colunas aditivas em `discovered_opportunities`

- `discovery_run_id uuid` — nullable, sem FK
- `discovery_channel text` — CHECK valores canônicos quando presente
- `query_family text` — CHECK valores canônicos quando presente
- `origin_domain text` — nullable

Colunas editoriais (`status`, `raw`, `reject_reason`, etc.) e RLS inalterados.

## Arquivos alterados (T02)

- `supabase/migrations/043_source_runs.sql` (novo)
- `src/radar/core/services/source_runs.py` (novo)
- `tests/unit/test_source_runs.py` (novo)
- `docs/execution/radar-data-trust/reports/03-source-coverage/RT03-T02-report.md` (este)

## Testes e validações

- `ENVIRONMENT=test pytest -q tests/unit/test_source_runs.py`: **27 passed**
- `ruff check src/radar/core/services/source_runs.py tests/unit/test_source_runs.py`: **All checks passed**
- `git diff --check`: **sem whitespace errors**
- Migration validada estruturalmente (12 testes: tabela, PK, constraints, RLS, colunas nullable, sem policy de usuário, sem alteração em colunas existentes)

## Idempotência e best-effort

- `start_run`: upsert com `on_conflict`; retorna UUID existente se já há run terminal ou running; `None` em falha do DB
- `finish_run`: não regride estado terminal; `False` se ignorada; loga erro sem relançar
- `_REASON_CODES` canônicos: `no_credentials`, `weekend_skip`, `timeout`, `parse_error`, `provider_error`, `empty_result`, `unknown`
- Sem persistência de query, URL, conteúdo, traceback, prompt, resposta ou segredo

## Divergências e limitações

- Migration não foi aplicada ao Postgres local (`psql` indisponível no ambiente); validação estrutural via análise SQL
- `discovery_run_id` não possui FK para `source_runs(id)` — aditivo simples, T04 pode adicionar se necessário
- `reason_code` não possui CHECK no SQL (validação em Python é suficiente para best-effort)

## Ambiente

- Worktree isolado em `/private/tmp/radar-editais-rt03-t01-t02`
- `ENVIRONMENT=test` em toda execução
- Sem `.env`, produção, rede, Tavily, DOU, LLM, merge ou push
- RT03-T03 **não foi iniciada**
