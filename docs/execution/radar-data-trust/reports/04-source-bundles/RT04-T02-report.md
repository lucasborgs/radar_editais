# RT04-T02 — Relatório de Implementação

**Task:** Persistência append-only e idempotente de SourceBundle
**Spec:** [`specs/radar-data-trust-04-source-bundles.md`](../../../../specs/radar-data-trust-04-source-bundles.md)
**Plano:** [`plans/04-source-bundles/RT04-T02-append-only-bundle-storage.md`](../../plans/04-source-bundles/RT04-T02-append-only-bundle-storage.md)
**Branch:** `codex/radar-data-trust-04-t02`
**Base:** `4fe003b5a` (RT04-T01)
**Data:** 2026-07-27

---

## Arquivos criados

| Arquivo | Função |
|---|---|
| `supabase/migrations/044_source_bundles.sql` | Migration aditiva, idempotente: tabela `source_bundles` com UUID, sujeito, hash, JSONB, status, timestamps, UNIQUE `(subject_kind, subject_id, bundle_hash)`, RLS sem policies, 2 índices |
| `src/radar/core/kg/source_bundles.py` | Repositório service-role-only: `save(bundle) -> bool` e `load(subject_kind, subject_id) -> SourceBundle \| None` |
| `tests/unit/test_source_bundles_repo.py` | 31 testes de unidade do repositório (stubs, sem DB real) |

Arquivos **não** alterados (preservados da T01): `src/radar/domain/source_bundle.py`, `tests/unit/test_source_bundles.py`, `tests/fixtures/source_bundles/fixtures.py`.

## Decisões de implementação

### Migration (044)

- **Tabela única** `public.source_bundles` com as colunas exatas da spec §6: `id` (UUID PK, `gen_random_uuid()`), `subject_kind`, `subject_id`, `source`, `bundle_hash`, `bundle` (JSONB), `acquisition_status` (CHECK `complete|partial`), `collected_at` (timestamptz), `created_at` (timestamptz, default `now()`).
- **UNIQUE** `(subject_kind, subject_id, bundle_hash)` — constraint única que garante idempotência no banco, sem upsert ou lógica de aplicação.
- **RLS** habilitada, **sem policies** de usuário final — padrão idêntico a `edital_source_docs` (032), `source_runs` (043) e `discovery_promotion_runs` (038). Acesso exclusivo via service role.
- **Índices:** (1) parcial `source_bundles_complete_last_idx` em `(subject_kind, subject_id, collected_at DESC, created_at DESC) WHERE acquisition_status = 'complete'` — leitura eficiente do último `complete`; (2) `source_bundles_hash_idx` em `(bundle_hash)` para lookup diagnóstico.
- **Sem trigger** de `updated_at`: a tabela é append-only, sem updates.
- **Sem `id` no índice parcial**: a query de leitura usa `id DESC` como tiebreaker na ORDER BY, mas o índice já filtra pelo prefixo mais seletivo; a varredura do índice é suficiente.

### Repositório (`source_bundles.py`)

- **`save(bundle: SourceBundle) -> bool`:**
  - Usa `bundle.compute_bundle_hash()` — produtor canônico do domínio — e serializa o envelope completo em JSONB.
  - Insere via `supabase.table("source_bundles").insert(payload).execute()` — sem upsert, sem on_conflict.
  - Detecta duplicata pelo código `23505` (unique_violation) do `APIError` do PostgREST — retorna `True` (idempotente).
  - Erros com outro código (`500`, etc.) ou exceções inesperadas retornam `False` — nunca silenciados como sucesso.
  - Sem Supabase configurado → no-op `False` (degrada gracioso, como `source_docs.save`).

- **`load(subject_kind: str, subject_id: str) -> SourceBundle | None`:**
  - Filtra `acquisition_status='complete'`, ordena por `collected_at DESC, created_at DESC, id DESC` (desempate determinístico via UUID PK), LIMIT 1.
  - Bundles `partial` NUNCA são retornados — mesmo que sejam posteriores.
  - Desserializa o JSONB como `SourceBundle.model_validate()` — round-trip completo do contrato.
  - Falhas de leitura retornam `None` com log.

### Padrão reutilizado

- `_pg_configured()` — mesmo padrão de `source_docs.py`.
- `get_supabase_service()` — singleton do db infra.
- `APIError` do `postgrest.exceptions` — já disponível nas dependências.

## Testes

### T01 (herdados) — 70 testes, todos verdes

Contrato: enums vs YAML, construção, hash stabilité, hash mutabilidade, fixtures, rejeições de validação.

### T02 (novos) — 31 testes

| Classe | Testes | O que cobre |
|---|---|---|
| `TestNoSupabase` | 2 | save no-op False, load None sem Supabase |
| `TestSaveFirstInsert` | 3 | estrutura do payload, campos obrigatórios, bundle_hash |
| `TestIdempotentRepeat` | 2 | duplicata 23505 → True; sujeitos diferentes não conflitam |
| `TestMaterialChange` | 2 | conteúdo diferente altera hash; status altera hash |
| `TestRealErrors` | 3 | APIError 500 → False; exceção inesperada → False; APIError sem code → False |
| `TestLoad` | 5 | complete rows; empty; missing key; query structure (eq/order/select/limit); erro de leitura |
| `TestPartialVsComplete` | 2 | hashes diferentes; load só retorna complete |
| `TestRoundTrip` | 3 | envelope preservado; campos opcionais; bundle_hash/created_at ausentes no modelo |
| `TestMigrationContract` | 5 | migration existe; UNIQUE; RLS; índices; IF NOT EXISTS |
| `TestRequiredColumns` | 2 | 7 colunas obrigatórias no insert; created_at é default do banco |

### Validação

- `ruff check src/radar/core/kg/source_bundles.py` — All checks passed
- `ruff check tests/unit/test_source_bundles_repo.py` — All checks passed
- `git diff --check` — sem espaços em branco ou conflitos
- `pytest tests/unit/test_source_bundles.py tests/unit/test_source_bundles_repo.py` — **101 passed**

## Ambiente

- **Worktree:** `/private/tmp/radar-editais-rt04-t02`
- **Sem rede, produção, `.env` ou credenciais**: todos os testes usam stubs/fakes locais.
- **Migration validada estruturalmente** (testes de contrato no arquivo SQL), **não aplicada** localmente (sem Supabase local disponível sem credenciais).
- **Package reinstalado** como editable para refletir o worktree.

## Limitações

1. **RT04-T03 não foi iniciada.** Nenhum produtor, adapter, `source_docs`, composição, API, frontend ou pipeline foi alterado.
2. **Migration não aplicada em DB real.** Validada apenas por inspeção e testes de contrato; aplicação local exigiria `supabase start` com Supabase CLI configurado.
3. **Sem testes de integração contra Postgres real.** Proporcional ao pré-beta; a suíte `provenance` poderá cobrir isso em T06/T07.
4. **`edital_source_docs` permanece intacto.** A projeção compatível continua sendo a fonte de verdade do runtime existente.

## Commits

```
commit 1: implementação (migration + repositório + testes)
commit 2: relatório (este documento)
```

Nenhum merge ou push foi realizado.
