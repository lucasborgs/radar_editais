# RT05-T02 — Persistência e Repositório da Fila de Exceções

**Data:** 2026-07-29
**Branch:** `codex/radar-data-trust-05-t02`
**Base:** `89a909935` (tip of T01)
**Commits:**
- `f38bd1b80` feat(dq): migration 046 — data_quality_exceptions + reviews
- `c6ee6a435` feat(dq): repository — open_or_observe, review, queries
- `04a2f5031` test(dq): repository — 39 tests, migration structure, invariants
- `a78566a75` docs(dq): T01 aprovada (2026-07-29) + T02 report
- `4c6c4b558` fix(dq): corrige T02 — revisão de implementação
- `06e8c4900` docs(dq): corrige T02 report — remove alegações falsas, mark pendente

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
| `data_quality_reviews` | Decisão humanas append-only, com review_id textual único |

Estrutura:
- `data_quality_exceptions`: chave única
  `(subject_kind, subject_id, field_path, issue_code, input_fingerprint)`;
  `input_fingerprint text not null` com CHECK `btrim(...) <> ''`;
  CHECK para status
- `data_quality_reviews`: `review_id text not null unique`; FK para
  `data_quality_exceptions(id)` com `on delete restrict`; CHECK para decision
- Trigger `trg_reviews_append_only` (BEFORE UPDATE OR DELETE) que levanta
  exceção — garante append-only no banco sem tabela adicional;
  `DROP TRIGGER IF EXISTS` antes de `CREATE TRIGGER` para reexecutabilidade
- RLS ativado (service-role apenas, sem policies user-facing)
- Índices: subject_idx, open_idx (exceptions); exception_idx, created_at_idx
  (reviews)

### 2. Repositório — `src/radar/core/services/data_quality_exceptions.py`

| Função | Comportamento |
|---|---|
| `open_or_observe_exception` | Rejeita input_fingerprint vazio; reobserva fingerprint existente (update last_observed_at) ou insere novo; **nunca** supersede antes da inserção confirmada; após insert ou reobservação, supersede abertas do mesmo grupo com fingerprint diferente; reobservação de resolved/superseded **não** dispara supersede |
| `list_exceptions` | Lista com filtros opcionais (subject_kind, subject_id, status) |
| `get_exception` | Leitura por ID |
| `append_review` | Idempotente por review_id via `_review_payload_matches`; rejeita review_id vazio; nunca atualiza registro existente; caminho normal e 23505 (race) comparam 8 campos materiais (schema_version, exception_id, decision, corrected_value, justification, evidence_refs, actor_id, reviewed_at) — `reviewed_at` normalizado por `_normalize_ts` (parse ISO, naive→UTC, aware→UTC, Z aceito, inválido→diferente) — idêntico → True, qualquer diferença → DataQualityStorageError |
| `get_current_review_projection` | Última revisão (mais recente created_at) reconstruída como `DataQualityReview` com review_id preservado |

Tratamento de erros:
- `DataQualityStorageError` — exceção sanitizada (sem traceback, URL, password, secret)
- `ValueError` para input_fingerprint ou review_id inválidos
- Supabase ausente → degradação graciosa (False / None / [])
- 23505 (unique violation) tratado como idempotente na exceção

### 3. Testes — `tests/unit/test_data_quality_exceptions.py`

64 testes em 14 grupos, usando `FakeSupabase` determinístico (sem banco real):

| Grupo | Count | O quê |
|---|---|---|
| `TestMigrationStructure` | 9 | SQL tem 2 tables, review_id, FK, trigger, RLS, sem policies |
| `TestSupabaseAbsent` | 5 | Degradação graciosa: False/None/[] sem credenciais |
| `TestInputFingerprintRequired` | 3 | Rejeita None/vazio/whitespace |
| `TestOpenOrObserve` | 7 | Insert, mesma fp, fp nova, supersessão, grupos distintos, órfãos |
| `TestInsertFailureNoSupersede` | 2 | Unique violation ok; erro real não supersede anterior |
| `TestReviewId` | 5 | Preservado, retry idempotente, múltiplos, vazio rejeitado, model valida |
| `TestReviewPayloadMatches` | 8 | _review_payload_matches: identical, different decision/exception_id/actor_id; normalização timestamps: naive vs aware, offset vs Z, instantes diferentes, inválido |
| `TestAppendReviewCollision` | 4 | Colisão sequencial: decision, exception_id, actor_id, reviewed_at diferentes |
| `TestSourceUrlRemoved` | 6 | source_url ausente do payload de exception e review, persistido, helper |
| `TestListExceptions` | 2 | Filtro por subject_kind e status |
| `TestErrorSemantics` | 3 | Erro sanitizado, categórico, não-bool |
| `TestReobserveResolvedSuperseded` | 4 | A→B→A mantém B open; resolved/superseded não supersede; last_observed_at atualizado |
| `TestAppendReviewRace` | 3 | 23505 race: mesmo payload → idempotente; diferente → erro; no record → erro |
| `TestMigrationExecutability` | 3 | DROP TRIGGER IF EXISTS; CHECK constraint; sem default '' no input_fingerprint |

### 4. EvidenceRef sanitization

`_evidence_refs_payload()` serializa `EvidenceRef.model_dump(mode="json")` e remove
a chave `source_url` antes de persistir. Round-trip: `source_url` é None na
leitura.

---

## Validação

```
ENVIRONMENT=test PYTHONPATH=src pytest -q \
  tests/unit/test_data_quality_exceptions.py \
  tests/unit/test_temporal_exception_contract.py \
  tests/unit/test_provenance.py

  → 203 passed (64 + 75 + 64)

ruff check src/radar/core/services/data_quality_exceptions.py  → pass
ruff check tests/unit/test_data_quality_exceptions.py          → pass
git diff --check 89a909935..HEAD                               → pass
```

---

## Pendências e Limitações

1. O `FakeSupabase` não implementa transações — o teste de "insert sem
   supersede" simula erro via flag imperativa. Em produção, a atomicidade
   depende do Postgres (insert + update não são atômicos na mesma conexão
   sem transação explícita). O contrato de consistência eventual é aceito
   por design.

2. A remoção de `source_url` é feita no Python, não no banco — uma inserção
   direta no Postgres (fora do repositório) pode conter o campo. A migration
   não tem trigger de sanitização.

3. O trigger de append-only (`reject_review_mutations`) não cobre TRUNCATE.
   TRUNCATE não é esperado no fluxo operacional.

4. O teste de condição de corrida 23505 em `append_review` usa
   `_skip_select_once` no `FakeSupabase` — um seam de teste que simula a
   invisibilidade entre transações. Em produção, a atomicidade depende do
   Postgres (READ COMMITTED + unique constraint). O cenário de "mesmo payload"
   confirma que a recuperação relê e compara corretamente; o cenário de
   "payload diferente" confirma que colisão real não é silenciada.

---

## Auditoria Codex: pendente

- Dados legados sem exceção continuam representados por ausência (sem
  fabricação) — verificado em `TestSupabaseAbsent` (degradação graciosa) e
  método `get_exception` retorna None para missing.
- Erros sanitizados sem leak — verificado em `TestErrorSemantics`.
- Round-trip de `source_url` confirmado: None após reconstrução.
- `review_id` textual preservado e idempotente — verificado em 5 testes.
- Ordem de supersessão: insert → supersede (nunca o inverso) — verificado
  em `TestInsertFailureNoSupersede`.
- Reobservação de resolved/superseded não dispara supersede — verificado em
  `TestReobserveResolvedSuperseded` (4 testes: A→B→A, resolved, superseded,
  last_observed_at).
- 23505 race em append_review com comparação de payload — verificado em
  `TestAppendReviewRace` (3 testes: mesmo payload, diferente, no record).
- Migration reexecutável: DROP TRIGGER IF EXISTS, CHECK constraint,
  sem default '' — verificado em `TestMigrationExecutability`.
- Comparador unificado `_review_payload_matches` usado no caminho normal e
  no race — verificado em `TestReviewPayloadMatches` (8 testes) e
  `TestAppendReviewCollision` (4 testes: decision, exception_id, actor_id,
  reviewed_at diferentes disparam DataQualityStorageError).
- `reviewed_at` normalizado por `_normalize_ts`: naive→UTC, aware→UTC,
  Z sufixo aceito, timestamp inválido → `False` (payload diferente) — sem
  exceção bruta. Verificado em 4 testes de normalização.

---

## Histórico de Commits (rebase)

- `feat(dq): migration 046 — data_quality_exceptions + reviews`
- `feat(dq): repository — open_or_observe, review, queries`
- `test(dq): repository — 39 tests, migration structure, invariants`
- `docs(dq): T01 aprovada + T02 report`
- `fix(dq): corrige T02 — revisão de implementação`
- `docs(dq): corrige T02 report — remove alegações falsas, mark pendente`
