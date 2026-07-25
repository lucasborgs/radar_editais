# Plano executável — Radar Data Trust 02 (Quality gates e cobertura)

**Spec:** [`../../../../specs/radar-data-trust-02-quality-gates.md`](../../../../specs/radar-data-trust-02-quality-gates.md)
**Spec-mãe:** [`../../../../specs/radar-data-trust.md`](../../../../specs/radar-data-trust.md)
**Status:** pronto para execução (spec aprovada para planejamento/implementação, 2026-07-24)

## Resultado

Produzir **medida e estrutura**, não régua: registrar as suítes que faltam
(`provenance`, `e2e_health`) como `diagnostic` no harness existente, confirmar
que cada suíte existente roda e está classificada corretamente, e documentar o
mapa por camada com baseline observado. Nenhum threshold é inventado, nenhuma
suíte vira `gate`, nenhum consumidor é alterado.

Esta spec é **estritamente diagnóstica**. A recomendação de maturidade (§7.4) é
recomendação com baseline em mãos — não decisão de promoção.

## Ordem e dependências

| Task | Plano | Resultado | Depende de |
|---|---|---|---|
| `RT02-T01` | [`provenance-golden.md`](RT02-T01-provenance-golden.md) | golden representativo de proveniência (§7.1, casos de §10.2) — só fixtures | — |
| `RT02-T02` | [`provenance-suite.md`](RT02-T02-provenance-suite.md) | suíte `provenance` diagnóstica registrada (§7.1) | T01 |
| `RT02-T03` | [`existing-suites-review.md`](RT02-T03-existing-suites-review.md) | revisão leve das suítes existentes: classificação, execução, tamanho de golden (§7.3) | — |
| `RT02-T04` | [`e2e-health-suite.md`](RT02-T04-e2e-health-suite.md) | sinal E2E `e2e_health` diagnóstico (§7.2) | — |
| `RT02-T05` | [`quality-map-reconciliation.md`](RT02-T05-quality-map-reconciliation.md) | mapa por camada + recomendação de maturidade + reconciliação (§7.4, §13) | T02, T03, T04 |

## Paralelismo seguro

- **Onda A (imediata):** `T01`, `T03` e `T04` são disjuntos em arquivos e podem
  correr em paralelo. `T01` cria um golden novo (`data/evaluation/golden/provenance/`);
  `T03` edita as suítes existentes (`triage.py`, `structurer.py`, …) e o seu
  relatório; `T04` cria um módulo `e2e_health.py` novo com fixture própria.
- **`T02` depois de `T01`** (a suíte consome o golden).
- **Ponto de pouso compartilhado:** `T02` e `T04` adicionam cada uma UMA linha
  (import + entrada no dict `SUITES`) a `src/radar/core/eval/registry.py`. Land
  sequencial ou rebase de uma linha; não é edição concorrente real do mesmo bloco.
- **Onda B (fechamento):** `T05` depois de `T02`+`T03`+`T04` — o mapa e o
  baseline observado dependem dos resultados diagnósticos das três.

## Gate proporcional (pré-beta, spec-mãe §11.5)

- **por task:** testes direcionados + `ruff` apenas no escopo alterado;
- **suítes novas (`provenance`, `e2e_health`):** ambas são determinísticas (sem
  LLM/rede) — rodar local 2x e confirmar agregado **estável entre execuções**;
- **golden:** UMA fixture representativa por caso obrigatório — não exaustivo;
- **sem threshold, sem `gate`, sem promoção:** toda suíte nova nasce
  `classification="diagnostic"`, `criteria=()`;
- **sem consumidor tocado:** matching/RAG/ranking/prompt/modelo/frontend não mudam;
- **suíte completa + Docker/worker:** só no fechamento (`T05`) — nenhuma task aqui
  toca wiring de runtime (tudo é aditivo a `registry.py` e a `data/evaluation/`).

O implementador não amplia escopo de produto nem inventa régua. Dúvida de
corpus, de "o que conta como representativo" ou de classificação de suíte
interrompe a task e volta ao proprietário/governança, sem decisão implícita.
