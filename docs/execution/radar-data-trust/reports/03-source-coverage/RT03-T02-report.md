# RT03-T02 — Relatório

## Resultado

Migration aditiva `043_source_runs.sql`, repositório `source_runs.py` com
`start_run`/`finish_run` idempotente e best-effort, e testes de unidade
completos. `start_run` usa **select → insert** (nunca upsert nem update
destrutivo). `finish_run` é **atômico**: usa `WHERE status NOT IN (terminal)`
para impedir regressão sem race condition.

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
| `records_observed` | `integer` | CHECK >= 0 |
| `records_emitted` | `integer` | CHECK >= 0 |
| `records_staged` | `integer` | CHECK >= 0 |
| `error_count` | `integer NOT NULL` | CHECK >= 0, default 0 |
| `reason_code` | `text` | Nullable |
| `metrics` | `jsonb NOT NULL` | `'{}'` |
| UNIQUE | `(batch_id, source_key)` | Uma run por canal por rodada |

CHECKs: `records_observed >= 0`, `records_emitted >= 0`, `records_staged >= 0`
(saem do checkpoint "contadores validados", nunca negativos).

RLS habilitada sem policy de usuário final (padrão
`pipeline_errors`/`discovery_promotion_runs`).

## Colunas aditivas em `discovered_opportunities`

- `discovery_run_id uuid` — nullable, **FK references source_runs(id) ON DELETE SET NULL**
- `discovery_channel text` — CHECK valores canônicos quando presente
- `query_family text` — CHECK valores canônicos quando presente
- `origin_domain text` — nullable

Colunas editoriais (`status`, `raw`, `reject_reason`, etc.) e RLS inalterados.

## Arquivos alterados (T02)

- `supabase/migrations/043_source_runs.sql` (FK adicionada)
- `src/radar/core/services/source_runs.py` (select→insert, finish atômico,
  `_validate_counters`, `_normalize_reason`, `_sanitize_metrics`)
- `tests/unit/test_source_runs.py`
- `docs/execution/radar-data-trust/reports/03-source-coverage/RT03-T02-report.md` (este)

## Comportamento

### start_run

1. `SELECT id, status WHERE batch_id = ? AND source_key = ?`
2. Se encontrou → retorna `id` (qualquer status). **Nunca chama insert nem update.**
3. Se não encontrou → `INSERT` com status `running`.
4. Falha do DB → loga, retorna `None`.

### finish_run

1. Valida `status` terminal (succeeded/partial/failed/skipped).
2. `_validate_counters` — rejeita contadores negativos pre-DB.
3. `_normalize_reason` — reason_code não canônico é omitido (logado, não persistido).
4. `_sanitize_metrics` — só chaves `^[a-zA-Z_][a-zA-Z0-9_]*$` e valores
   numéricos finitos >= 0; rejeita strings, bool, dicts, listas, NaN, infinito.
5. `UPDATE ... WHERE id = ? AND status NOT IN (terminal)`. Se 0 linhas
   afetadas → False (já terminal ou inexistente).
6. Falha do DB → loga, retorna `False`.

### Reason codes canônicos

`no_credentials`, `weekend_skip`, `timeout`, `parse_error`, `provider_error`,
`empty_result`, `unknown`. Qualquer outro é ignorado.

## Testes e validações

- `ENVIRONMENT=test pytest -q tests/unit/test_source_runs.py`: **39 passed**
- `ruff check src/radar/core/services/source_runs.py tests/unit/test_source_runs.py`: **All checks passed**
- `git diff --check`: **sem whitespace errors**
- Migration validada estruturalmente (5 testes: FK, CHECKs, sem RLS policy)
- Cobertura comportamental: start (conflito, terminal, partial, DB failure),
  finish (atômico, terminal guard, status inválido), contadores negativos,
  reason normalization, sanitização de metrics (12 sub-testes)

## Divergências e limitações

- Migration não aplicada ao Postgres local (`psql` indisponível no ambiente);
  validação estrutural via análise SQL.
- Em uma corrida rara entre `select` e `insert` em `start_run`, o escritor
  perdedor pode retornar `None` (falha de unicidade no DB), mas nunca
  sobrescreve a run existente e um retry recupera o ID correto.
- `reason_code` não possui CHECK no SQL (validação em Python é suficiente para
  best-effort e evita falsos positivos em dados legados).

## Validação independente

- 81 testes direcionados (42 T01 + 39 T02)
- Suíte completa: **1469 passed, 77 skipped**
- Ruff limpo
- `git diff --check` limpo
- Auditoria Codex: **aprovada em 2026-07-26**

## Ambiente

- Worktree isolado em `/private/tmp/radar-editais-rt03-t01-t02`
- Commit: `f78385b6f`
- `ENVIRONMENT=test` em toda execução
- Sem `.env`, produção, rede, Tavily, DOU, LLM, merge ou push
- RT03-T03 **não foi iniciada**
