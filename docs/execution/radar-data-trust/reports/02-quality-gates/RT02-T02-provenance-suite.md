# RT02-T02 — Suíte `provenance` diagnóstica

**Status:** `passed` (implementação; auditoria da governança pendente)
**Plano:** [`plans/02-quality-gates/RT02-T02-provenance-suite.md`](../../plans/02-quality-gates/RT02-T02-provenance-suite.md)
**Branch/commit-base:** `codex/radar-data-trust-02-completion` / base `37f34a74d`
**Commits:** `8b9640a1df01f332492c1879db45f1a0b7562d36`,
`641b6e648` (correção auditada de sinais observados)
**Implementador/modelo:** claude-sonnet, worktree isolado
**Auditoria Codex:** aprovada em 2026-07-24

## Realizado

- `src/radar/core/eval/provenance.py` (NOVO) — `Suite` diagnóstica que roda
  `radar.core.kg.evidence_resolver.resolve_quote()` sobre o golden RT02-T01
  (6 casos representativos) e agrega locator e faithfulness observáveis.
- Registrada em `src/radar/core/eval/registry.py` — uma linha (import + dict entry).
- `tests/unit/test_eval_provenance.py` — 13 testes de contrato.

## Sinais produzidos

Execução local (ENVIRONMENT=test, fallback local):

| Sinal | Valor |
|---|---|
| `mean_locator_exact` | 0.3333 (2/6) |
| `mean_locator_document_only` | 0.3333 (2/6) |
| `mean_locator_unresolved` | 0.3333 (2/6) |
| `mean_faithfulness_verbatim` | 1.0000 (4/4 — somente casos com candidato resolvido) |
| `aggregate_signals` | 1.0000 |

Os casos absent/legacy não têm candidato resolvido e ficam fora do denominador
de faithfulness. A checagem compara o quote retornado contra o texto dos blocos
candidatos reais; não considera o quote copiado no `EvidenceRef` como prova.

O golden de locator não carrega `FactProvenance` produzido pelo gold, então a
suíte não publica métricas de state, producer ou completude por campo crítico.
Esses dois sinais são observados no único fato real e hermético de
`e2e_health`; são amostra E2E, não cobertura dos seis casos.

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
  - contagens concretas de locator e denominador de faithfulness (4/4);
  - `critical_field=null` preservado no golden sem fabricar denominador de
    completude;
  - caso legacy não mascarado (unresolved + missing_hash);
  - determinismo entre duas rodadas;
  - nenhum threshold/gate.

Todos verdes em 0.48s.

## Confirmações

- **Sem LLM:** a suíte não faz nenhuma chamada externa.
- **Sem DB:** consome apenas o golden JSON (fixture).
- **Sem rede:** módulo puro, sem I/O de rede.
- **Sem credenciais:** não lê `.env`, `DATABASE_URL` ou chaves de API.
