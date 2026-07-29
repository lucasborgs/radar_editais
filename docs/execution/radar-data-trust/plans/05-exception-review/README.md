# Plano executável — Radar Data Trust 05 (Revisão de exceções)

**Spec:** [`../../../../specs/radar-data-trust-05-exception-review.md`](../../../../specs/radar-data-trust-05-exception-review.md)
**Spec-mãe:** [`../../../../specs/radar-data-trust.md`](../../../../specs/radar-data-trust.md)
**Status:** planejado

## Resultado

Impedir que uma oportunidade pareça acionável quando sua validade não puder ser
provada. A primeira vertical é temporal: prazo, fluxo contínuo e status. O
caso Finep/Eureka (`ABERTA` sem prazo) deve resultar em `needs_review`, nunca
em oportunidade ativa.

Não há OCR, visão, LLM, crawler, backfill integral, score, workflow de equipe
nem revisão em massa de atores. A Spec 06 continua fora de escopo.

## Ordem e dependências

| Task | Plano | Resultado | Depende de |
|---|---|---|---|
| `RT05-T01` | [`temporal-exception-contract.md`](RT05-T01-temporal-exception-contract.md) | contrato puro e fixture Finep/Eureka | aprovação da Spec 05 |
| `RT05-T02` | [`exception-storage-repository.md`](RT05-T02-exception-storage-repository.md) | duas tabelas e repositório | T01 |
| `RT05-T03` | [`temporal-detector-shadow.md`](RT05-T03-temporal-detector-shadow.md) | detector idempotente em shadow | T01–T02 |
| `RT05-T04` | [`review-projection-service.md`](RT05-T04-review-projection-service.md) | revisão append-only e projeção | T01–T03 |
| `RT05-T05` | [`admin-exceptions-api.md`](RT05-T05-admin-exceptions-api.md) | API administrativa da fila | T02–T04 |
| `RT05-T06` | [`admin-exceptions-ui.md`](RT05-T06-admin-exceptions-ui.md) | aba administrativa da Descoberta | T05 |
| `RT05-T07` | [`temporal-enforcement-read-model.md`](RT05-T07-temporal-enforcement-read-model.md) | enforcement temporal e payload canônico | T04 |
| `RT05-T08` | [`consumer-validity-ux.md`](RT05-T08-consumer-validity-ux.md) | comunicação nos consumidores e UX | T06–T07 |
| `RT05-T09` | [`diagnostics-reconciliation.md`](RT05-T09-diagnostics-reconciliation.md) | diagnóstico e fechamento | T01–T08 |

## Ondas e pousos serializados

- **A:** T01, depois T02: vocabulário, regra de data e chave idempotente antes
  de qualquer gravação.
- **B:** T03 é o único autor da ligação detector→fluxo; não faz enforcement.
- **C:** T04 estabelece a decisão; T05 expõe somente a API administrativa e
  T06 é sua única UI administrativa.
- **D:** T07 é o único autor do enforcement/read model. T08 é o único autor da
  comunicação em produto e usa o payload já canônico.
- **E:** T09 fecha documentação/status após a validação completa.

As nove tasks mantêm cada superfície de risco pequena: persistência, detector,
decisão, API, UI administrativa, enforcement e UX não se misturam em uma
mega-task.

## Invariantes transversais

- `deadline >= hoje` em `America/Sao_Paulo`; prazo ausente não prova fluxo
  contínuo. `continuous` requer evidência oficial recuperável.
- `needs_review` não entra no match ativo nem é descrito como aberto por
  Explorar/Escrita; pode permanecer histórico no Ecossistema.
- A fila é única e idempotente; revisão é append-only. Nova versão material
  supersede a exceção, sem herdar override.
- Bundles, `source_docs`, gold e `FactProvenance` são autoridades; revisão não
  edita bronze, documento ou produtor histórico.
- Em shadow, falha de persistência é erro operacional e não decide validade;
  após o enforcement de T07, falha ao obter o estado temporal de item novo ou
  revalidado não concede estado ativo. Logs não carregam conteúdo bruto.
- Testes usam `ENVIRONMENT=test`, fixtures locais e relógio/dados injetados;
  sem `.env`, produção, rede, worker, LLM ou `--publish`.

## Gate e relatórios

Por task: testes direcionados, `ruff check` no escopo e `git diff --check`.
T06/T08 também executam `cd frontend && npx tsc --noEmit` e `npm run lint`.
T09 roda `pytest -q` e compara falhas com a branch-base.

Cada task cria relatório em
`docs/execution/radar-data-trust/reports/05-exception-review/`. T09 cria o
README consolidado. Não fazer merge ou push durante as tasks.
