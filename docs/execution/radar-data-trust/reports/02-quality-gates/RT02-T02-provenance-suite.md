# RT02-T02 — Suíte `provenance` diagnóstica

**Status:** `passed` (implementação; auditoria da governança pendente)
**Plano:** [`plans/02-quality-gates/RT02-T02-provenance-suite.md`](../../plans/02-quality-gates/RT02-T02-provenance-suite.md)
**Branch/commit-base:** `codex/radar-data-trust-02-completion` / base `37f34a74d`
**Commits:** `8b9640a1df01f332492c1879db45f1a0b7562d36`
**Implementador/modelo:** claude-sonnet, worktree isolado
**Auditoria Codex:** pendente

## Realizado

- `src/radar/core/eval/provenance.py` (NOVO) — `Suite` diagnóstica que roda
  `radar.core.kg.evidence_resolver.resolve_quote()` sobre o golden RT02-T01
  (6 casos representativos) e agrega os três sinais da spec §7.1.
- Registrada em `src/radar/core/eval/registry.py` — uma linha (import + dict entry).
- `tests/unit/test_eval_provenance.py` — 13 testes de contrato.

## Sinais produzidos

Execução local (ENVIRONMENT=test, fallback local):

| Sinal | Valor |
|---|---|
| `mean_locator_exact` | 0.3333 (2/6) |
| `mean_locator_document_only` | 0.3333 (2/6) |
| `mean_locator_unresolved` | 0.3333 (2/6) |
| `mean_completeness_has_state` | 1.0000 |
| `mean_completeness_has_producer` | 1.0000 |
| `mean_faithfulness_verbatim` | 0.8333 (5/6 — caso 6 legacy não tem quote) |
| `mean_critical_field_completeness` | 1.0000 |
| `aggregate_signals` | 1.0000 |

Crítica: faithfulness 0.833 não é falha — o caso 6 (legacy sem silver) não
produz `evidence_ref`, portanto não tem quote a verificar. O numerador correto
é 5/5 para casos resolúveis.

## Limitações

- Golden de proveniência é baseline comportamental do resolvedor atual, não
  verdade semântica humana abrangente.
- Seis casos não provam representatividade.
- `conflicting`/retificação encaminhados à spec 04 (nenhum produtor os emite).
- Sem threshold, sem gate, sem critérios.

## Testes

`tests/unit/test_eval_provenance.py` — 13 testes:

  - carregamento do golden (6 casos, ids);
  - classificação/criteria/version;
  - registro no registry;
  - sinais de locator, completude e faithfulness no agregado;
  - `critical_field=null` excluído do denominador de critical_field_completeness;
  - caso legacy não mascarado (unresolved + missing_hash);
  - determinismo entre duas rodadas;
  - nenhum threshold/gate.

Todos verdes em 0.48s.

## Confirmações

- **Sem LLM:** a suíte não faz nenhuma chamada externa.
- **Sem DB:** consome apenas o golden JSON (fixture).
- **Sem rede:** módulo puro, sem I/O de rede.
- **Sem credenciais:** não lê `.env`, `DATABASE_URL` ou chaves de API.
