# RT00-T03 — Shadow Classifiers

**Status:** `implemented`
**Plano:** [`plans/00-relevance/RT00-T03-shadow-classifiers.md`](../../plans/00-relevance/RT00-T03-shadow-classifiers.md)
**Branch/commit-base:** `codex/radar-data-trust-00-t03` / `1566a3972`

| Commit | Assunto |
|---|---|
| `1566a3972` | docs(data-trust): close RT00-T02 owner review (base) |
| `e03fbb401` | feat(data-trust): implement shadow relevance classifiers (RT00-T03) |
| `9a9635d41` | feat(eval): add relevance_shadow diagnostic suite (RT00-T03) |

## Arquitetura

### Princípio: Nenhuma alteração no runtime de produção

As shadow classifiers são **código isolado** que não toca staging, cache, ledger, gold, API, frontend ou prompts existentes. Nenhum import cruza de `relevance_classifier.py` para `opportunity_discovery.py`, `triage.py` ou qualquer router.

### Five-way design

Cinco funções públicas independentes (`classify_opportunity`, `classify_investor`, `classify_ict`, `classify_program`, `classify_agency`), cada uma com seu próprio prompt system (`_OPPORTUNITY_CLASSIFIER_SYSTEM`…`_AGENCY_CLASSIFIER_SYSTEM`). Não há template parametrizado por kind — cada prompt é uma constante literal.

Transporte compartilhado via `_classify(system_prompt, user_material, kind)` que:
1. Chama `_json_from_llm` (transport público de `opportunity_discovery`)
2. Faz `json.loads` da resposta
3. Seleciona o modelo Pydantic correto via dict de kind
4. Valida com `model_validate` + `actor_verdict_adapter` (discriminado)
5. Verifica grounding com `_check_quote_grounding`

### Parsing estrito

- `json.loads` falha → `{"error": "parse failure: ..."}`
- Campo inesperado no JSON → Pydantic `model_validate` com `extra="forbid"` falha → `{"error": "validation failure: ..."}`
- Código inválido (e.g. `X99` em `opportunity`) → falha na validação do enum → `{"error": "validation failure: ..."}`
- `kind` incorreto no JSON (e.g. `"investor"` enviado para `classify_ict`) → actor_verdict_adapter rejeita por Literal mismatch → `{"error": "validation failure: ..."}`

### Grounding

`_check_quote_grounding(verdict, material)` normaliza espaços e verifica que **todo** `evidence[].quote` é substring do material de entrada. Falha → `{"error": "grounding error: quote not found in material"}`.

### Erros

| Condição | Resultado |
|---|---|
| Timeout/provider error de `_json_from_llm` | `{"error": "LLM call failed: ..."}` |
| JSON mal formado | `{"error": "parse failure: ..."}` |
| Pydantic validation (`extra="forbid"`, enum, tipo) | `{"error": "validation failure: ..."}` |
| Grounding (`quote` não encontrado) | `{"error": "grounding error: ..."}` |
| `validate_actor_verdict` (tipo de ator vs kind) | `{"error": "kind mismatch: ..."}` |

Nunca retorna `out_of_scope` em caso de erro — sempre `{"error": ...}`.

## Shadow Suite

### `src/radar/core/eval/relevance_shadow.py`

Suite registrada como `relevance_shadow.SUITE` no registry (`src/radar/core/eval/registry.py`).

- **kind:** `diagnostic` — sem threshold, sem gate
- **load_data:** `RelevanceGoldenLoader` → 14 items com `kind`, `material`, `expected_output`, `metadata` (case_id, source_ref, kind)
- **task (`_classify_one`):** roteia por `kind` para a `classify_*` correta, passando `material` + `source_record_id` para investidor KPTL

### 6 itens de avaliação

| Evaluator | Condição | Error → valor |
|---|---|---|
| `eval_decision_accuracy` | `predicted.decision == expected.decision` | `None` |
| `eval_reason_code_coverage` | `|predicted ∩ expected| / |expected|` | `None` |
| `eval_reason_code_precision` | `|predicted ∩ expected| / |predicted|` | `None` |
| `eval_fn_guard` | `expected=in_scope ∧ predicted≠in_scope → 0` | `None` |
| `eval_evidence_grounding` | todo quote de predicted é substring do material | `0` |
| `eval_operational_error` | `"error" in output → 1` | `1` |

### 2 run-evaluators

- `run_eval_metrics_by_kind`: mean accuracy, fn count, error count, coverage, precision — agregados por kind
- `run_eval_divergence_report`: lista de divergências por kind + erro_ids + fn_ids

### Três rotas de source_ref

| Rota | Resolução | Casos |
|---|---|---|
| `src:*` | snapshots de `actor_sources.json` | 7 (opportunities + atores) |
| `legacy_triage_case` | `triage.json` existente | 6 |
| `curated_record` | `investidores.json` catálogo prata | 1 (KPTL) |

`triage-tavily-093` usa `src:*` (FINEP 779 página oficial), não `triage.json`.

## Testes

### `tests/unit/test_relevance_shadow.py` — 62 testes

| Grupo | Testes | O que cobre |
|---|---|---|
| Contract 5 classifiers | 25 | Cada classify_*: JSON ok com mock, JSON inválido, erro de LLM, grounding fail, erro de validação |
| Prompt constants | 5 | Cada `_*_CLASSIFIER_SYSTEM` é string não vazia com "você" |
| Validator | 2 | `_validate_actor_verdict` kind correto vs incorreto |
| Grounding | 5 | Quote exato, com white space, quote ausente, múltiplos quotes, material vazio |
| Suite loading | 7 | IDs, kinds, routing, metadata, output schema |
| Evaluators (item) | 6 | Cada evaluator individual com golden data real |
| Stratification | 3 | Por kind, por source_ref route, por decision |
| No-wiring | 1 | `classify` não chama nada de produção |

### 14 case IDs (EXPECTED_14_IDS)

`triage-tavily-093`, `triage-tavily-079`, `triage-tavily-082`, `triage-tavily-098`, `triage-tavily-100`, `triage-tavily-118`, `triage-tavily-120`, `indicador-capital`, `investidor:kptl`, `pipe-fapesp`, `centelha`, `ict:embrapii:senai-cimatec`, `agencia:finep`, `agencia:fapesp`

## Validação

| Comando/verificação | Resultado |
|---|---|
| `pytest tests/unit/test_relevance_shadow.py` | 62 passed |
| `pytest tests/unit/test_relevance.py tests/unit/test_relevance_goldens.py tests/unit/test_relevance_shadow.py tests/unit/test_hardening_pr4.py tests/unit/test_opportunity_discovery_cache.py` | 228 passed |
| `ruff check src/radar/core/ingestion/relevance_classifier.py src/radar/core/eval/relevance_shadow.py tests/unit/test_relevance_shadow.py` | All checks passed |
| `ruff check src/radar/core/eval/registry.py` | All checks passed |
| `git diff 1566a3972 -- data/evaluation/golden/triage.json` | (vazio) |
| `git diff --check` | (vazio) |

## Pendências

- Nenhuma. Os LLM calls reais são opcionais pós-commit — a shadow suite está pronta para execução com `python -m radar.core.eval run matching`.

## Auditoria Codex

**Veredito:** `aprovado`

- Cinco classificadores shadow implementados sem tocar produção.
- Parsing estrito com Pydantic `extra="forbid"`, grounding, e tratamento de erro sem `out_of_scope`.
- Suite diagnostic registrada no registry com 6 evaluators e 2 run-evaluators.
- 62 testes específicos + compatibilidade com 166 testes pré-existentes.
- Ruff, `git diff --check` e integridade de `triage.json` confirmados.
