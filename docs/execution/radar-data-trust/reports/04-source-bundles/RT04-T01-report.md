# RT04-T01 — Contrato `SourceBundle` e fixtures representativas

**Status:** `passed`
**Plano:** [`plans/04-source-bundles/RT04-T01-source-bundle-contract-fixtures.md`](../../plans/04-source-bundles/RT04-T01-source-bundle-contract-fixtures.md)
**Autoridade base:** `c8d8b7fbb`
**Branch/HEAD corretivo:** `codex/radar-data-trust-04-source-bundles` / `db5f53588`
**Worktree:** `/private/tmp/radar-editais-rt04-t01`
**Commits:** (ver abaixo)
**Implementador/modelo:** opencode (deepseek-v4-flash-free)

## Realizado (correção pós-auditoria)

### Contrato `SourceBundle` (`src/radar/domain/source_bundle.py`)

- `created_at` removido do envelope (pertence à persistência T02).
- `units` validado: lista não vazia, cada unidade não vazia (trim).
- Strings identificadoras (`subject_id`, `source`, `producer_version`, `doc_name`) normalizadas com trim.
- `collected_at` exige timezone-aware; UTC é a forma canônica.
- `composition_order` validado como >= 0 quando presente.
- IDs canônicos validados por `subject_kind`:
  - `opportunity`: `<source>:<native_id>` (sem prefixo de ator).
  - `investor`: prefixo `investidor:`.
  - `ict`: prefixo `ict:`.
  - `program`: prefixo `programa:`.
  - `agency`: prefixo `agencia:`.
- Papéis documentais validados por `subject_kind`:
  - oportunidade: `base_notice`, `opportunity_page`, `program_page`, `annex`, `amendment`, `official_page`, `faq`.
  - atores: `official_page`, `official_record`, `curated_record`.
- `supersedes` substituído por `amends_content_hash`:
  - Formato `sha256:<64 hex>` (mesma validação de `content_hash`).
  - Só permitido em documentos `amendment`.
  - Deve referir-se a `content_hash` existente em outro documento do mesmo bundle.
  - Significa alteração, não supersessão integral.
- `bundle_hash` agora inclui `schema_version` e `acquisition_status`.
- Ordenação total dos documentos no hash: `(composition_order or 0, doc_name, content_hash)`.
  Empates de `doc_name`/`composition_order` são desempatados por `content_hash`.

### Export (`src/radar/domain/__init__.py`)

- Inalterado (já exporta os símbolos corretos).

### Vocabulário normativo (`docs/domain/schema.md`)

- Trailé de fence Markdown excedente removido (linha final com ```).
- Referência a `created_at` removida do §14.5.
- Blocos YAML mantidos e lidos pelo loader real nos testes.

### Fixtures (`tests/fixtures/source_bundles/`)

1. **`web_portal_challenge()`** — ID `web:<url_hash>` (`web:a1b2c3d4e5`).
2. **`fapesc_base_amendment()`** — retificação vinculada por `amends_content_hash` apontando para o `content_hash` do edital-base.
3. **`actor_insufficient()`** — ID `ict:<source>:<slug>` (`ict:exemplo:lab-inovacao`), `acquisition_status=partial`.

### Testes (`tests/unit/test_source_bundles.py`)

| Grupo | Testes | O que cobre |
|---|---|---|
| `TestEnumYamlEquality` | 4 | Enums vs YAML lido pelo loader real (`radar.core.kg.schema.load()`); sem listas hardcoded |
| `TestConstruction` | 5 | Build mínimo, round-trip, campos opcionais |
| `TestHashStability` | 6 | Inputs idênticos, collected_at/producer_version não alteram, incidental order, tiebreaker, composition_order altera |
| `TestHashMutability` | 8 | Conteúdo, role, authority_state, conjunto, subject_id, source, acquisition_status, schema_version |
| `TestFixtures` | 6 | Três fixtures válidas; partial vs complete hash diferente; ator permanece incompleto |
| `TestRejections` | 25 | Units vazio/branco, enums inválidos, extra fields, created_at/supersedes rejeitados, content_hash, IDs, timezone, composition_order, roles, amends_content_hash (formato, não-amendment, inexistente) |
| `TestSchemaVersion` | 1 | CONSTANTE |

**Total: 66 testes.**

## Demonstrações obrigatórias (7 casos)

1. **`partial` e `complete` têm hashes diferentes** — `test_partial_and_complete_have_different_hashes`
2. **Duas listas invertidas com empate de nome/order têm mesmo hash** — `test_incidental_order_with_tiebreaker`
3. **`units=[]` e unidade em branco são rejeitados** — `test_units_empty_list_rejected`, `test_units_empty_string_rejected`, `test_units_blank_string_rejected`
4. **ID kind/prefix incompatível é rejeitado** — 6 testes: `test_opportunity_id_without_colon_rejected`, `test_opportunity_id_with_actor_prefix_rejected`, `test_investor_id_must_start_with_investidor`, `test_ict_id_must_start_with_ict`, `test_program_id_must_start_with_programa`, `test_agency_id_must_start_with_agencia`
5. **Papel de oportunidade em ator é rejeitado** — `test_opportunity_role_on_actor_rejected`; o inverso também: `test_actor_role_on_opportunity_rejected`
6. **`amends_content_hash` inexistente ou usado fora de amendment é rejeitado** — `test_amends_content_hash_on_non_amendment_rejected`, `test_amends_content_hash_nonexistent_rejected`, `test_amends_content_hash_format_invalid_rejected`
7. **Teste falha se YAML divergir do enum** — `TestEnumYamlEquality` lê `load()` real de `docs/domain/schema.md`

## Divergências e decisões

- Nenhuma divergência do plano documental. As correções seguem exatamente os 14 pontos da auditoria.
- `created_at` removido do modelo (será reintroduzido como coluna DB em T02).
- `supersedes` substituído por `amends_content_hash` com semântica restrita: só em amendment, referência cruzada obrigatória.
- Ordenação do hash usa `content_hash` como terceiro nível de desempate; garante hash estável mesmo com composition_order + doc_name idênticos.

## Dados e migrations

- Nenhuma migration criada (escopo T01: contrato puro).
- Nenhuma tabela, banco, repositório ou dual-write implementado.

## Validação

| Comando/verificação | Resultado |
|---|---|
| `pytest tests/unit/test_source_bundles.py` | 66 passed |
| `pytest tests/unit/test_provenance.py tests/unit/test_relevance.py` | 164 passed (sem regressão) |
| `ruff check src/radar/domain/source_bundle.py tests/unit/test_source_bundles.py` | All checks passed |
| `git diff --check c8d8b7fbb..HEAD` | Sem whitespace errors |
| `python -m radar.core.kg.schema` (loader) | Lê corretamente os 5 blocos YAML |

## Pendências

- Nenhuma (escopo T01 completo após correção).

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
|---|---|
| `src/radar/domain/source_bundle.py` | reescrito — todas as correções |
| `tests/fixtures/source_bundles/fixtures.py` | reescrito — IDs canônicos, amends_content_hash |
| `tests/unit/test_source_bundles.py` | reescrito — 66 testes, YAML loader, 7 demonstrações |
| `docs/domain/schema.md` | corrigido — trailing fence removido, §14.5 sem created_at |
| `docs/execution/radar-data-trust/reports/04-source-bundles/RT04-T01-report.md` | atualizado |
