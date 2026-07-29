# RT05-T02 — Persistência e Repositório da Fila de Exceções

**Data:** 2026-07-29  
**Branch:** `codex/radar-data-trust-05-t02`  
**Base:** `89a909935` (tip of T01)

---

## Resumo

Implementação da camada de persistência para `DataQualityException` e
`DataQualityReview` — migration 046 + repositório Python — conforme contrato
do T01.

---

## Entregas

### 1. Migration 046

`supabase/migrations/046_data_quality_exceptions.sql`

Duas tabelas `public.`:

| Tabela | Propósito |
|---|---|
| `data_quality_exceptions` | Exceção de qualidade (idempotente por fingerprint) |
| `data_quality_reviews` | Revisão append-only vinculada à exceção |

Destaques:
- Chave única: `(subject_kind, subject_id, field_path, issue_code, input_fingerprint)` — idempotência garantida pela constraint
- FK `exception_id → data_quality_exceptions(id)` nas reviews
- `CHECK` constraints para `status` e `decision`
- RLS ativado (service-role apenas, sem policies user-facing)
- `is_immutable` na review + `temporal_interval()` para janela de correção

### 2. Repositório — `src/radar/core/services/data_quality_exceptions.py`

| Função | Comportamento |
|---|---|
| `open_or_observe_exception` | UPSERT semântico: mesma fingerprint → `last_observed_at`; fingerprint nova → supersede abertas anteriores e insere |
| `list_exceptions` | Lista com filtros opcionais (`subject_kind`, `subject_id`, `status`) |
| `get_exception` | Leitura por ID |
| `append_review` | Revisão append-only (FK para exceção) |
| `get_current_review_projection` | Última revisão (mais recente `created_at`) reconstruída como `DataQualityReview` |

Tratamento de erros:
- `DataQualityStorageError` — exceção sanitizada (sem traceback, URL, password, secret)
- Supabase ausente → degradação graciosa (False / None / [])
- `23505` (unique violation) tratado como idempotente

### 3. Testes — `tests/unit/test_data_quality_exceptions.py`

39 testes em 7 grupos:

| Grupo | Testes | O quê |
|---|---|---|
| `TestMigrationStructure` | 9 | SQL tem 2 tables, RLS, FK, CHECK, sem policies |
| `TestPayloadBuilding` | 6 | Serialização Pydantic consistente |
| `TestExceptionModelInvariants` | 7 | Domain rejects invalid states |
| `TestReviewModelInvariants` | 7 | Review invariants (correct sem value, etc.) |
| `TestIdempotencyLogic` | 5 | Fingerprint match, supersessão, append-only |
| `TestLegitimateAbsence` | 2 | Ausência não fabrica registros |
| `TestErrorHandling` | 3 | Erro sanitizado, categórico, sem leak |

---

## Validação

```
pytest tests/unit/test_data_quality_exceptions.py  → 39 passed
pytest tests/unit/test_temporal_exception_contract.py  → 75 passed
ruff check src/radar/core/services/data_quality_exceptions.py  → pass
ruff check tests/unit/test_data_quality_exceptions.py  → pass
```

---

## Pendências e Riscos

Nenhum. T02 completo e independente.

---

## Auditoria Codex

**aprovada em 2026-07-29** — dados legados sem exceção continuam
representados por ausência (sem fabricação). Erros sanitizados sem leak.

---

## Histórico de Commits

- `feat(dq): migration 046 — data_quality_exceptions + reviews` (migration SQL)
- `feat(dq): repository service — open_or_observe, review, queries` (service)
- `test(dq): repository service — 39 tests, migration structure, invariants` (tests)
- `docs(dq): T01 aprovada + T02 report` (reports)
