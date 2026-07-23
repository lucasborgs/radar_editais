# RT00-T06 — Métricas diagnósticas

**Status:** `completed`
**Plano:** [`RT00-T06-diagnostic-metrics.md`](../../plans/00-relevance/RT00-T06-diagnostic-metrics.md)
**Branch/base:** `codex/radar-data-trust-00-t06` / `d31ecfbfe`

## Commits

| Commit | Assunto |
|---|---|
| `(hash_t06_1)` | feat: métricas diagnósticas T06 — recall, precisão, taxa, agreement, por kind, por código, audit IDs |
| `(hash_t06_2)` | docs: relatório RT00-T06 com correções de operational_miss, X em excl_, testes |

## Resumo

A T06 complementa a instrumentação diagnóstica da suíte `relevance_shadow` com
7 novos `run_evaluators`. Nenhum threshold, gate, harness paralelo ou alteração
produtiva foi introduzida. A suíte permanece `diagnostic`, versão `"2"`.

## Correções aplicadas

Após auditoria, foram aplicadas 3 correções estruturais:

### 1. Erros operacionais nas métricas por código

A versão inicial ignorava completamente casos com erro operacional em
`run_eval_metrics_by_code`. Agora o código contabiliza:

- `support_expected`: **todas** as ocorrências esperadas, inclusive casos com erro;
- `support_evaluable_expected`: ocorrências esperadas em casos sem erro;
- `support_predicted`: ocorrências preditas (apenas casos sem erro);
- `tp`: true positives (código esperado e predito, sem erro);
- `fp`: false positives (código predito mas não esperado, sem erro);
- `semantic_fn`: código esperado, caso avaliável, mas não predito;
- `operational_miss`: código esperado em caso com erro operacional;
- `semantic_recall` = TP / (TP + semantic_fn);
- `end_to_end_recall` = TP / support_expected;
- `precision` = TP / (TP + FP).

Sem suporte, retorna None.

### 2. X1–X8 não duplicados em `conf_` e `excl_`

O contrato de oportunidade `out_of_scope` exige que `exclusion_codes` apareçam
também em `reason_codes`. A versão inicial criava métricas tanto em `conf_X*`
quanto em `excl_X*`. Agora:

- `conf_`: apenas R1–R5 e reason codes confirmados de atores (INV_*, ICT_*,
  PRG_*, AGY_*). Códigos que também estão em `exclusion_codes` são removidos.
- `excl_`: exclusivamente X1–X8 de `exclusion_codes`.
- `fail_`: exclusivamente `failed_codes` de atores.

Um X1 gera apenas `code_excl_X1_ACADEMIC_ONLY_*`, nunca `code_conf_X1_*`.

### 3. IDs auditáveis de operational miss

Adicionado `audit_code_operational_misses` com case_ids de casos com erro
que possuíam códigos esperados. Nenhum conteúdo documental é incluído.

## Métricas adicionadas

### In-scope recall

| Métrica | Fórmula |
|---|---|
| `total_in_scope_goldens` | contagem de `expected.decision == "in_scope"` |
| `in_scope_true_positives` | golden in_scope & pred in_scope (sem erro) |
| `in_scope_semantic_false_negatives` | golden in_scope & pred != in_scope (sem erro) |
| `in_scope_operational_errors` | golden in_scope & erro operacional |
| `in_scope_semantic_recall` | TP / (TP + FN) — apenas avaliáveis |
| `in_scope_end_to_end_recall` | TP / total_in_scope_goldens |

### Out-of-scope precision

| Métrica | Fórmula |
|---|---|
| `out_of_scope_total_predictions` | total predições out_of_scope |
| `out_of_scope_true_positives` | golden & pred out_of_scope |
| `out_of_scope_false_positives` | pred out_of_scope, golden != out_of_scope |
| `out_of_scope_support` | casos avaliáveis |
| `out_of_scope_precision` | TP / (TP + FP); None sem suporte |

### Needs review rate

| Métrica | Fórmula |
|---|---|
| `needs_review_total_predictions` | total predições needs_review |
| `needs_review_support` | casos avaliáveis |
| `needs_review_operational_errors` | erros operacionais |
| `needs_review_rate` | needs_review / suporte; None sem suporte |
| `needs_review_{kind}` | distribuição por kind |

### Decision agreement

| Métrica | Fórmula |
|---|---|
| `decision_correct` | pred == expected (sem erro) |
| `decision_divergent` | pred != expected (sem erro) |
| `decision_operational_errors` | erros operacionais |
| `decision_support` | total de casos avaliáveis |
| `decision_accuracy_rate` | correct / support; None sem suporte |

### Por kind

Cada kind com suporte produz:

| Métrica | Fórmula |
|---|---|
| `kind_{k}_in_scope_recall` | semantic recall; None sem in_scope avaliável |
| `kind_{k}_in_scope_e2e_recall` | end-to-end recall; None sem in_scope golden |
| `kind_{k}_fn_count` | FN contados |
| `kind_{k}_in_scope_operational_errors` | erros em golden in_scope |
| `kind_{k}_out_of_scope_precision` | TP/(TP+FP); None sem pred |
| `kind_{k}_needs_review_rate` | nr / avaliáveis; None sem suporte |
| `kind_{k}_decision_agreement` | correct / support |
| `kind_{k}_operational_errors` | total de erros |

### Por código (com namespace)

Namespace = `conf_`, `excl_` ou `fail_`.

| Métrica | Fórmula |
|---|---|
| `code_{ns}_{code}_support_expected` | todas as ocorrências esperadas (inclusive erro) |
| `code_{ns}_{code}_support_evaluable_expected` | ocorrências esperadas em casos sem erro |
| `code_{ns}_{code}_support_predicted` | ocorrências preditas |
| `code_{ns}_{code}_tp` | verdadeiros positivos |
| `code_{ns}_{code}_fp` | falsos positivos |
| `code_{ns}_{code}_semantic_fn` | FN em casos avaliáveis |
| `code_{ns}_{code}_operational_miss` | código esperado em caso com erro |
| `code_{ns}_{code}_precision` | TP / (TP + FP); None sem pred |
| `code_{ns}_{code}_semantic_recall` | TP / (TP + semantic_fn); None sem avaliáveis |
| `code_{ns}_{code}_end_to_end_recall` | TP / support_expected; None sem expected |

### IDs auditáveis

| Métrica | Conteúdo |
|---|---|
| `audit_divergences` | case_ids divergência de decisão |
| `audit_false_negatives` | case_ids de FN |
| `audit_false_positives_oos` | case_ids de FP out_of_scope |
| `audit_operational_errors` | case_ids de erro operacional |
| `audit_code_divergences` | case_ids divergência de codeset |
| `audit_code_operational_misses` | case_ids de erro com código esperado |

Nenhum conteúdo documental, apenas case_ids.

## Diferença entre recall semântico e end-to-end

**Semantic recall:** TP / (TP + semantic_fn). Exclui erros operacionais do
denominador. Mede a qualidade do classificador sobre os casos que produziram
resposta.

**End-to-end recall:** TP / support_expected. Erro operacional conta como
oportunidade não recuperada. Mede a eficácia do pipeline total.

Exemplo — código R1 em 2 goldens: caso A corretamente predito, caso B com
timeout:
- semantic_fn = 0, operational_miss = 1
- semantic_recall = 1 / (1 + 0) = 1.0
- end_to_end_recall = 1 / 2 = 0.5

## Tratamento de None

- Métrica sem suporte retorna `value: None`, nunca 0 nem 1.
- Códigos sem referência no golden nem na predição: não geram métrica.
- Kinds sem casos: não geram métricas.
- Namespace sem códigos: nenhuma métrica emitida.
- Erro operacional nunca infla precision nem recall: semantic_recall fica None
  quando não há casos avaliáveis; end_to_end_recall captura a perda.

## Arquivos alterados

| Arquivo | Alteração |
|---|---|
| `src/radar/core/eval/relevance_shadow.py` | 7 novos `run_evaluators`; versão `"2"` |
| `tests/unit/test_relevance_shadow.py` | 31 novos testes em 8 classes T06 |
| `docs/execution/radar-data-trust/reports/00-relevance/RT00-T06-diagnostic-metrics.md` | este relatório |

## Evaluators adicionados

| Função | Métricas |
|---|---|
| `run_eval_in_scope_recall` | recall semântico e end-to-end |
| `run_eval_out_of_scope_precision` | precisão out_of_scope |
| `run_eval_needs_review_rate` | taxa e distribuição needs_review |
| `run_eval_decision_agreement` | contagens de concordância |
| `run_eval_metrics_by_kind_t06` | todas as métricas por kind |
| `run_eval_metrics_by_code` | precision/recall por código e namespace |
| `run_eval_audit_ids` | case_ids auditáveis |

Evaluators T03 mantidos inalterados: `run_eval_metrics_by_kind`,
`run_eval_divergences`.

## Testes

31 novos testes em 8 classes:

| Classe | Testes | Cobre |
|---|---|---|
| `TestT06InScopeRecall` | 5 | recall perfeito, FN semântico, erro, semântico vs e2e, sem goldens |
| `TestT06OutOfScopePrecision` | 4 | precisão, FP, None sem pred, None sem suporte |
| `TestT06NeedsReviewRate` | 4 | taxa, erros separados, distribuição, zero avaliável |
| `TestT06DecisionAgreement` | 1 | correct, divergent, error, support |
| `TestT06MetricsByKind` | 3 | kind sem suporte, kind ausente, média não esconde perda |
| `TestT06MetricsByCode` | 6 | TP/FP/FN, FP+semantic_fn, operational_miss, failed_codes, sem suporte, X só excl_ |
| `TestT06AuditIDs` | 6 | divergências, FN, FP, erro, codeset, operational miss |
| `TestT06EdgeCases` | 2 | zero casos, erro nunca infla |

Todos com fixtures pequenas (2–3 resultados), sem replicar os 14 goldens.

### Demonstração: R1 + timeout

```python
results = [
    {"output": {"verdict": {"reason_codes": ["R1_ENTERPRISE_PATH"]},
     "expected_output": {"reason_codes": ["R1_ENTERPRISE_PATH"]},
     "metadata": {"case_id": "a"}},
    {"output": {"error": "timeout"},
     "expected_output": {"reason_codes": ["R1_ENTERPRISE_PATH"]},
     "metadata": {"case_id": "b"}},
]
metrics = run_eval_metrics_by_code(results)
# support_expected = 2, evaluable_expected = 1
# tp = 1, semantic_fn = 0, operational_miss = 1
# semantic_recall = 1.0, end_to_end_recall = 0.5
```

### Demonstração: X1 só em excl_

```python
results = [{"output": {"verdict": {"reason_codes": ["X1_ACADEMIC_ONLY"],
                                   "exclusion_codes": ["X1_ACADEMIC_ONLY"]}},
            "expected_output": {"reason_codes": ["X1_ACADEMIC_ONLY"],
                                "exclusion_codes": ["X1_ACADEMIC_ONLY"]},
            "metadata": {"case_id": "a"}}]
metrics = run_eval_metrics_by_code(results)
assert "code_excl_X1_ACADEMIC_ONLY_tp" in metrics
assert not any("conf_X1" in k for k in metrics)  # nunca em conf_
```

## Validação

```text
311 passed (5 files)
All checks passed (ruff)
git diff --check  # limpo
```

## Limitações

1. **Corpus de 14 casos.** Métricas por kind e código são limitadas pela
   cobertura do golden. Kinds com 1–2 casos podem produzir métricas instáveis.

2. **Ausência de ator out_of_scope.** O golden não contém ator `out_of_scope`.
   `out_of_scope_precision` para investidor, ICT, programa e agência pode
   permanecer None.

3. **Códigos não exercitados.** Sem referência no golden nem na predição, o
   código não gera métrica — correto, mas deixa lacunas visíveis.

4. **Duplicação X1–X8 corrigida.** X* só aparece em `excl_`; nenhum leak para
   `conf_` foi identificado nos testes.

## Run externa

**Pendente.** Será executada pelo Codex após auditoria e autorização explícita:

```bash
PYTHONPATH=src python -m radar.core.eval run relevance_shadow
```

Mesmos evaluators de T03 e T06. Nenhuma alteração de prompt, modelo, golden ou
label.

## Confirmações

- [x] Nenhum threshold novo introduzido.
- [x] Nenhum Criterion bloqueante criado.
- [x] Suíte continua `diagnostic`.
- [x] Nenhum harness paralelo.
- [x] Nenhum runtime produtivo alterado.
- [x] Nenhum prompt, modelo, label ou golden alterado.
- [x] Nenhuma alteração em staging, API ou frontend.
- [x] Correção 1: operational_miss contabilizado sem descartar erro.
- [x] Correção 2: X1–X8 exclusivos em `excl_`, sem leak para `conf_`.
- [x] Correção 3: `audit_code_operational_misses` adicionado.
- [x] RT00-T07 não iniciada.
- [x] Run externa não executada.
- [x] Merge/push não realizado.
