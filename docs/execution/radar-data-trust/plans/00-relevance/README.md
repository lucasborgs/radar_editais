# Plano executável — Radar Data Trust 00

**Spec:** [`../../../../specs/radar-data-trust-00-relevance-contract.md`](../../../../specs/radar-data-trust-00-relevance-contract.md)
**Status:** pronto para aprovação

## Resultado

Classificar relevância com `in_scope | out_of_scope | needs_review`, reason
codes e evidência, usando contratos separados para oportunidades e atores.

## Ordem

| Task | Plano | Depende de |
|---|---|---|
| `RT00-T01` | [`domain-contract.md`](RT00-T01-domain-contract.md) | spec aprovada |
| `RT00-T02` | [`representative-goldens.md`](RT00-T02-representative-goldens.md) | T01 |
| `RT00-T03` | [`shadow-classifiers.md`](RT00-T03-shadow-classifiers.md) | T01–T02 |
| `RT00-T04` | [`staging-contract.md`](RT00-T04-staging-contract.md) | T03 |
| `RT00-T05` | [`operator-review.md`](RT00-T05-operator-review.md) | T04 |
| `RT00-T06` | [`diagnostic-metrics.md`](RT00-T06-diagnostic-metrics.md) | T02–T05 |
| `RT00-T07` | [`final-validation.md`](RT00-T07-final-validation.md) | T01–T06 |

## Gate proporcional

- por task: testes direcionados + lint dos arquivos alterados;
- se houver migration: teste local da migration e RLS;
- se mudar prompt/modelo: run diagnóstica da suíte afetada;
- no fechamento T07: suíte Python completa, frontend lint/typecheck se tocado e
  comparação com o baseline da branch-base.

Não há threshold novo nesta spec; promoção a gate pertence à Data Trust 02.
