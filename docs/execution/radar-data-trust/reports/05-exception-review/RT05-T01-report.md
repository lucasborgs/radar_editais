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
| **Funcional (original)** | `7a6abbe9b` | `feat(rt05-t01): temporal exception contract, fixture, and tests` |
| **Documental** | `fa048494c` | `docs(schema): reconcile deadline rule to >= hoje (RT05-T01)` |
| **Relatório** | `519f3085f` | `docs(rt05-t01): implementation report` |
| **Funcional (corretivo)** | `388aae58b` | `fix(rt05-t01): corrige classificacao de status, invariantes e reuso de tipos` |

## Arquivos alterados/criados (total)

| Arquivo | Ação | Linhas |
|---|---|---|
| `src/radar/domain/data_quality.py` | criado | ~160 |
| `src/radar/domain/__init__.py` | modificado | +18 |
| `tests/fixtures/data_quality/__init__.py` | criado | 0 |
| `tests/fixtures/data_quality/finep_eureka.py` | criado | ~20 |
| `tests/unit/test_temporal_exception_contract.py` | criado | ~610 |
| `docs/domain/schema.md` | modificado | +2/-2 |
| `docs/execution/radar-data-trust/reports/05-exception-review/RT05-T01-report.md` | criado | este |

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
  - status ausente/Desconhecido/arbitrário sem prazo → unknown/needs_review (`critical_fact_missing`)
- **Reutiliza:** `EvidenceRef`, `ReviewInfo`, `SubjectKind`, `FactState` dos contratos RT01/RT04
- **Sem:** score, confiança, taxonomia aberta, `date.today()`, banco, API, frontend

## Correções aplicadas (commit `388aae58b`)

### 1. Classificação explícita de status

- Removido `closed_status_values` da API pública de `evaluate_temporal()`
- Conjuntos canônicos internos: `_OPEN_STATUSES = {"aberta"}`, `_CLOSED_STATUSES = {"encerrada", "resultado_divulgado", "fechada", "closed", "finished"}`
- Status ausente/vazio, `Desconhecido` ou qualquer valor arbitrário não é classificado nem como aberto nem como fechado
- Sem prazo e sem status aberto/fechado → `unknown/needs_review` + `CRITICAL_FACT_MISSING`
- `ABERTA` sem prazo permanece `unknown/needs_review` + `TEMPORAL_STATUS_WITHOUT_BASIS`
- Prazo presente com status neutro → segue regra do prazo sem conflito
- Fixture `finep_eureka` atualizada (removido `closed_status_values`)

### 2. Invariantes de `TemporalEvaluation`

- `active` ou `closed` com `issue_code` → rejeitado
- `active` ou `closed` com `issue_description` → rejeitado
- `needs_review` sem `issue_code` → rejeitado
- `needs_review` sem `issue_description` → rejeitado
- `issue_code` sem descrição e vice-versa → coberto pelos acima

### 3. Invariantes de `DataQualityReview`

- `confirm_continuous` sem `evidence_refs` → rejeitado
- `correct` sem `corrected_value` → rejeitado
- `correct` sem `evidence_refs` → rejeitado
- `corrected_value` vazio ou whitespace → rejeitado
- `corrected_value` em `confirm`, `mark_unknown` ou `confirm_continuous` → rejeitado

### 4. Reuso de contratos

- `DataQualityException.subject_kind` agora é `SubjectKind` (enum canônico)
- `DataQualityException.produced_state` agora é `FactState` (enum canônico)
- Valores inválidos em ambos são rejeitados pelo Pydantic

## Testes

**~68 testes** em `tests/unit/test_temporal_exception_contract.py`:

| Grupo | Testes | Cobertura |
|---|---|---|
| EnumValues | 3 | Valores canônicos |
| TemporalEvaluationInvariants | 11 | active/closed sem issue; needs_review com issue; extra=forbid; roundtrip |
| EvaluateTemporal | 9 | Prazo futuro, hoje, vencido; contínuo; fechado sem prazo; Finep/Eureka; sem status → CRITICAL |
| Desconhecido | 3 | Neutro: +prazo passado, +prazo futuro, sem prazo → CRITICAL |
| ArbitraryStatusNotOpen | 4 | Valor arbitrário não classificado como aberto |
| Conflicts | 2 | deadline futuro + fechado; deadline passado + aberto |
| ContinuousWithoutEvidence | 3 | Ausência de evidência não produz continuous |
| DataQualityException | 9 | SubjectKind, FactState, inválidos, roundtrip |
| DataQualityReview | 14 | Todas as decisões; corrected_value obrigatório/vedado; evidence obrigatória |
| SchemaVersion | 1 | Constante fixa em 1 |
| NoScoreOrConfidence | 3 | Nenhum modelo expõe confidence/score |
| DataQualitySchemaVersionFixed | 3 | schema_version Literal[1] |

**Resultados:** `204 passed in 0.34s` (68 novos + 136 existentes)

## Validação

```
pytest:   204 passed in 0.34s
ruff:     All checks passed! (3 arquivos)
git diff --check 9b86c3e70..HEAD: (sem saída)
```

## Fixture Finep/Eureka

`tests/fixtures/data_quality/finep_eureka.py` — fixture sanitizada:
- publicação 31/01/2024, `status=ABERTA`, `deadline=None`, sem evidência contínua
- sem HTML integral, sem rede
- **resultado:** `unknown/needs_review/temporal_status_without_basis`
- Teste `test_finep_eureka_fixture` confirma

## Decisões de implementação

1. **Conflito tem precedência:** early-return antes das regras simples
2. **deadline == as_of:** tratado como `is_future_deadline` (>=), garantindo dia ativo
3. **Continuidade:** exige `EvidenceRef` não-None; ausência nunca basta
4. **Status canônico interno:** apenas `aberta` é aberto; `Desconhecido`/arbitrário é neutro
5. **Validators:** `model_validator(mode="after")` para validações cross-campo (Pydantic v2)
6. **Reuso:** `SubjectKind` e `FactState` em `DataQualityException` — sem enum paralelo

## Limitações

1. `_eod_sao_paulo` removida (não utilizada) — `deadline >= as_of` com `date` já trata o dia ativo
2. `DataQualityReview.decision` usa Literal, não um enum — pode migrar se T02 precisar
3. A validação de que a evidência da revisão pertence à exceção é de T04 (escopo)

## Não implementado (RT05-T02 em diante)

- ❌ Persistência (tabelas, repositório)
- ❌ Detector integrado (shadow)
- ❌ Projeção revisada
- ❌ API administrativa
- ❌ Frontend
- ❌ Consumidores (match_v3, gold)
- ❌ Migration, banco, LLM, rede, OCR, visão

## Verificação de invariantes

- ✅ `FactProvenance`, `EvidenceRef`, `ReviewInfo`, `SubjectKind`, `FactState` reutilizados
- ✅ Nenhum enum paralelo ou taxonomia alternativa criada
- ✅ `gold.py`, `match_v3.py`, `writing.py` e consumidores não alterados
- ✅ Sem migration, banco, API, frontend, fila, detector integrado, LLM, OCR, visão, rede, `.env`, produção ou backfill
- ✅ RT05-T02 não iniciada
- ✅ Sem merge, push, rede, credenciais ou produção

## Auditoria Codex: pendente
