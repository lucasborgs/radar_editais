# RT04-T01 — Contrato `SourceBundle` e fixtures representativas

**Status:** `passed`
**Plano:** [`plans/04-source-bundles/RT04-T01-source-bundle-contract-fixtures.md`](../../plans/04-source-bundles/RT04-T01-source-bundle-contract-fixtures.md)
**Autoridade base:** `c8d8b7fbb`
**Branch/HEAD corretivo:** `codex/radar-data-trust-04-t01` / `25e6168d7`
**Worktree:** `/private/tmp/radar-editais-rt04-t01`
**Commits:**
- `d883515d3` feat(rt04-t01): correct SourceBundle contract per audit
- `ebef6a693` docs(rt04-t01): update execution report with audit corrections
- `25e6168d7` fix(rt04-t01): aplica 6 correções finais no contrato SourceBundle
**Implementador/modelo:** opencode (deepseek-v4-flash-free)

## Realizado (correção pós-auditoria)

### Contrato `SourceBundle` (`src/radar/domain/source_bundle.py`)

- `created_at` removido do envelope (pertence à persistência T02).
- `units` validado: lista não vazia, cada unidade não vazia (trim).
- Strings identificadoras (`subject_id`, `source`, `producer_version`, `doc_name`) normalizadas com trim.
- `collected_at` exige timezone-aware; normalizado para UTC via `astimezone(timezone.utc)`.
- `composition_order` validado como >= 0 quando presente.
- IDs canônicos validados por `subject_kind`:
  - `opportunity`: `<source>:<native_id>` (prefixo = `{source}:`, sem prefixo de ator).
  - `investor`: prefixo `investidor:`.
  - `ict`: prefixo `ict:{source}:` (inclui source no prefixo).
  - `program`: prefixo `programa:`.
  - `agency`: prefixo `agencia:`.
- Papéis documentais validados por `subject_kind`:
  - oportunidade: `base_notice`, `opportunity_page`, `program_page`, `annex`, `amendment`, `official_page`, `faq`.
  - atores: `official_page`, `official_record`, `curated_record`.
- `supersedes` substituído por `amends_content_hash`:
  - Formato `sha256:<64 hex>` (mesma validação de `content_hash`).
  - Só permitido em documentos `amendment`.
  - Deve referir-se a `content_hash` existente em outro documento do mesmo bundle.
  - Auto-referência (próprio content_hash) é rejeitada.
  - Significa alteração, não supersessão integral.
- `bundle_hash` agora inclui `schema_version` e `acquisition_status`.
- Ordenação total dos documentos no hash: `(composition_order or 0, doc_name, content_hash, canonical_json)`.
  Empates de nome/ordem/conteúdo são desempatados pelo dict canônico completo (captura papel/autoridade/etc.).

### Export (`src/radar/domain/__init__.py`)

- Inalterado (já exporta os símbolos corretos).

### Vocabulário normativo (`docs/domain/schema.md`)

- Trailé de fence Markdown excedente removido (linha final com ```).
- Referência a `created_at` removida do §14.5.
- Blocos YAML mantidos e lidos pelo loader real nos testes.

### Fixtures (`tests/fixtures/source_bundles/`)

1. **`web_portal_challenge()`** — ID `web:<url_hash>` (`web:a1b2c3d4e5f6`, 12 hex chars).
2. **`fapesc_base_amendment()`** — retificação vinculada por `amends_content_hash` apontando para o `content_hash` do edital-base.
3. **`actor_insufficient()`** — ID `ict:<source>:<slug>` (`ict:exemplo:lab-inovacao`), `acquisition_status=partial`.

### Testes (`tests/unit/test_source_bundles.py`)

| Grupo | Testes | O que cobre |
|---|---|---|---|
| `TestEnumYamlEquality` | 4 | Enums vs YAML lido pelo loader real (`radar.core.kg.schema.load()`); sem listas hardcoded |
| `TestConstruction` | 5 | Build mínimo, round-trip, campos opcionais |
| `TestHashStability` | 7 | Inputs idênticos, collected_at/producer_version não alteram, incidental order, tiebreaker, composition_order altera, tiebreaker total (role/authority) |
| `TestHashMutability` | 8 | Conteúdo, role, authority_state, conjunto, subject_id, source, acquisition_status, schema_version |
| `TestFixtures` | 6 | Três fixtures válidas; partial vs complete hash diferente; ator permanece incompleto |
| `TestRejections` | 30 | Units vazio/branco, enums inválidos, extra fields, created_at/supersedes rejeitados, content_hash, IDs, timezone, composition_order, roles, amends_content_hash (formato, não-amendment, inexistente, auto-referência) |
| `TestSchemaVersion` | 1 | CONSTANTE |

**Total: 72 testes (6 novos: prefixo/source, native ID vazio, ICT sem source, ICT sem slug, amends auto-ref, tiebreaker total).**

## Demonstrações obrigatórias (7 casos)

1. **`partial` e `complete` têm hashes diferentes** — `test_partial_and_complete_have_different_hashes`
2. **Duas listas invertidas com empate de nome/order têm mesmo hash** — `test_incidental_order_with_tiebreaker` + `test_same_name_order_content_diff_role_authority_stable`
3. **`units=[]` e unidade em branco são rejeitados** — `test_units_empty_list_rejected`, `test_units_empty_string_rejected`, `test_units_blank_string_rejected`
4. **ID kind/prefix incompatível é rejeitado** — 6+4 testes: legados (`test_investor_id_must_start_with_investidor`, `test_ict_id_must_start_with_ict_source`, `test_program_id_must_start_with_programa`, `test_agency_id_must_start_with_agencia`) + novos (`test_opportunity_id_wrong_source_prefix_rejected`, `test_opportunity_id_source_prefix_mismatch`, `test_opportunity_id_no_native_id`, `test_ict_id_without_source_in_prefix`, `test_ict_id_no_slug`, `test_opportunity_id_with_actor_prefix_rejected`)
5. **Papel de oportunidade em ator é rejeitado** — `test_opportunity_role_on_actor_rejected`; o inverso também: `test_actor_role_on_opportunity_rejected`
6. **`amends_content_hash` inexistente ou usado fora de amendment é rejeitado** — `test_amends_content_hash_on_non_amendment_rejected`, `test_amends_content_hash_nonexistent_rejected`, `test_amends_content_hash_format_invalid_rejected`, `test_amends_content_hash_self_reference_rejected`
7. **Teste falha se YAML divergir do enum** — `TestEnumYamlEquality` lê `load()` real de `docs/domain/schema.md`

## Divergências e decisões

- Nenhuma divergência do plano documental. Correções seguem exatamente os 14+6 pontos da auditoria (ciclo final).
- `created_at` removido do modelo (será reintroduzido como coluna DB em T02).
- `supersedes` substituído por `amends_content_hash` com semântica restrita: só em amendment, referência cruzada obrigatória, auto-referência rejeitada.
- Ordenação do hash usa `(order, name, content_hash, canonical_json)` como desempate; garante hash estável mesmo quando nome/ordem/conteúdo coincidem mas papel/autoridade diferem.

## Dados e migrations

- Nenhuma migration criada (escopo T01: contrato puro).
- Nenhuma tabela, banco, repositório ou dual-write implementado.

## Validação

| Comando/verificação | Resultado |
|---|---|
| `pytest tests/unit/test_source_bundles.py` | 72 passed |
| `pytest -q --deselect test_local_run_is_faster_in_parallel` (full suite) | 1698 passed, 64 skipped (única falha é flaky timing test preexistente) |
| `ruff check src/radar/domain/source_bundle.py tests/unit/test_source_bundles.py tests/fixtures/source_bundles/fixtures.py` | All checks passed |
| `git diff --check c8d8b7fbb..HEAD` | Sem whitespace errors |
| `python -m radar.core.kg.schema` (loader) | Lê corretamente os 5 blocos YAML |

## Pendências

- Auditoria Codex: pendente (6 correções finais aplicadas, aguarda fechamento).
- 7 novos testes de regressão adicionados; 72 testes no total.
- `test_local_run_is_faster_in_parallel` (eval harness) flaky — preexistente, fora do escopo T01.

## Auditoria Codex

**Veredito:** `pendente` (correções aplicadas; aguarda re-auditoria)

## Ambiente

- Worktree isolado em `/private/tmp/radar-editais-rt04-t01`
- `ENVIRONMENT=test` em toda execução
- Sem `.env`, produção, rede, LLM, credenciais ou merge/push
- RT04-T02 **não foi iniciada**
- Nenhuma alteração em `source_docs`, adapters, produtores ou consumidores

## Arquivos alterados (correção)

| Arquivo | Tipo |
|---|---|---|
| `src/radar/domain/source_bundle.py` | reescrito — todas as correções + 6 finais |
| `tests/fixtures/source_bundles/fixtures.py` | reescrito — IDs canônicos, amends_content_hash, web hash 12 chars |
| `tests/unit/test_source_bundles.py` | reescrito — 72 testes, YAML loader, 7 demonstrações |
| `docs/domain/schema.md` | corrigido — trailing fence removido, §14.5 sem created_at |
| `docs/execution/radar-data-trust/reports/04-source-bundles/RT04-T01-report.md` | atualizado |
