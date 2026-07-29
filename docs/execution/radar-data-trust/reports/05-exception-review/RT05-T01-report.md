# RT05-T01 — Relatório de Implementação

## Dados da execução

| Campo | Valor |
|---|---|
| **Base** | `9b86c3e70` (merge spec: radar-data-trust-05-exception-review) |
| **Branch** | `codex/radar-data-trust-05-t01` |
| **Worktree** | `/private/tmp/radar-editais-rt05-t01` |
| **Data** | 2026-07-29 |

## Commits

| Tipo | Hash | Descrição |
|---|---|---|
| **Funcional** | `7a6abbe9b` | `feat(rt05-t01): temporal exception contract, fixture, and tests` |
| **Documental** | `fa048494c` | `docs(schema): reconcile deadline rule to >= hoje (RT05-T01)` |

## Arquivos alterados/criados

| Arquivo | Ação | Linhas |
|---|---|---|
| `src/radar/domain/data_quality.py` | criado | ~150 |
| `src/radar/domain/__init__.py` | modificado | +18 |
| `tests/fixtures/data_quality/__init__.py` | criado | 0 |
| `tests/fixtures/data_quality/finep_eureka.py` | criado | ~30 |
| `tests/unit/test_temporal_exception_contract.py` | criado | ~470 |
| `docs/domain/schema.md` | modificado | +2/-2 |

## Contrato implementado

**`src/radar/domain/data_quality.py`:**

- **Enums:** `TemporalMode` (fixed, continuous, unknown), `ValidityState` (active, closed, needs_review), `IssueCode` (6 códigos da spec)
- **Modelos estritos (`extra="forbid"`):** `TemporalEvaluation`, `DataQualityException`, `DataQualityReview`
- **Função determinística:** `evaluate_temporal()` com `as_of` injetado e parâmetros explícitos (nunca `date.today()`)
- **Regras §4.1 implementadas:**
  - prazo futuro ou igual a `as_of` → fixed/active
  - prazo vencido → fixed/closed
  - ausência de prazo + evidência contínua → continuous/active
  - status encerrado sem prazo → unknown/closed
  - status aberto sem prazo e sem evidência → unknown/needs_review (`temporal_status_without_basis`)
  - conflito prazo/status → needs_review (`temporal_status_conflict`)
  - dia do encerramento (`deadline == as_of`) permanece ativo
- **Reutiliza:** `EvidenceRef`, `ReviewInfo` dos contratos RT01/RT04
- **Sem:** score, confiança, taxonomia aberta, `date.today()`, banco, API, frontend

## Testes

**47 testes** em `tests/unit/test_temporal_exception_contract.py`:

| Grupo | Testes | Cobertura |
|---|---|---|
| EnumValues | 3 | Valores canônicos de TemporalMode, ValidityState, IssueCode |
| TemporalEvaluation | 5 | Construção, round-trip, invariantes (issue_code + description) |
| EvaluateTemporal | 9 | Prazo futuro, hoje, vencido; contínuo comprovado; fechado sem prazo; Finep/Eureka; sem status |
| Conflicts | 2 | Conflito deadline futuro + status fechado; deadline passado + status aberto |
| ContinuousWithoutEvidence | 3 | Rejeição de continuidade sem evidência |
| DataQualityException | 8 | Construção, validações, campos vazios, extra=forbid |
| DataQualityReview | 10 | Decisões, corrected_value obrigatório, justificativa, extra=forbid |
| SchemaVersion | 2 | Constante fixa em 1 |
| NoScoreOrConfidence | 3 | Nenhum modelo expõe confidence/score |
| DataQualitySchemaVersionFixed | 3 | schema_version Literal[1] |

**Resultados:** `183 passed in 0.31s` (47 novos + 136 existentes de provenance/source_bundles)

## Linting

```
ruff check → All checks passed! (3 arquivos)
git diff --check → (sem saída = sem whitespace errors)
```

## Fixture Finep/Eureka

`tests/fixtures/data_quality/finep_eureka.py` — fixture sanitizada:
- publicação 31/01/2024
- `status=ABERTA`, `deadline=None`, `continuous_evidence=None`
- sem HTML integral, sem rede
- **resultado esperado:** `unknown/needs_review/temporal_status_without_basis`
- Teste `test_open_without_deadline_needs_review_via_fixture` confirma

## Decisões de implementação

1. **Conflito tem precedência:** implementado como early-return antes das regras simples
2. **deadline == as_of:** tratado como `is_future_deadline` (>=), garantindo dia ativo
3. **Continuidade:** exige `EvidenceRef` não-None; ausência nunca basta
4. **Closed status values:** conjunto default fechado (`encerrada`, `resultado_divulgado`, `fechada`, `closed`, `finished`), mas sobrescritível via parâmetro
5. **validators:** `model_validator(mode="after")` para validações cross-campo (Pydantic v2)

## Limitações

1. `_eod_sao_paulo` foi removida (não utilizada) — a comparação `deadline >= as_of` com `date` já trata o dia ativo sem precisar de timezone; timezone seria necessário se o prazo tiver horário explícito (fora do escopo T01)
2. O contrato `DataQualityException` e `DataQualityReview` são modelos de domínio puros; `input_fingerprint`, `detected_at`, `last_observed_at` permanecem opcionais até T02 (persistência)
3. `DataQualityReview.decision` usa Literal, não um enum — mantendo simplicidade; pode migrar para enum se T02 precisar

## Não implementado (RT05-T02 em diante)

- ❌ Persistência (tabelas, repositório)
- ❌ Detector integrado (shadow)
- ❌ Projeção revisada
- ❌ API administrativa
- ❌ Frontend
- ❌ Consumidores (match_v3, gold)
- ❌ Migration, banco, LLM, rede, OCR, visão

## Verificação de invariantes

- ✅ `FactProvenance`, `EvidenceRef`, `ReviewInfo` reutilizados; não criados contratos paralelos
- ✅ `gold.py`, `match_v3.py`, consumidores não alterados
- ✅ Sem migration, banco, API, frontend, fila, detector integrado, LLM, OCR, visão, rede, `.env`, produção ou backfill
- ✅ RT05-T02 não iniciada
- ✅ Sem merge, push, rede, credenciais ou produção

## Auditoria Codex: pendente
