# RT05-T09 — Diagnóstico, reconciliação e fechamento

## Objetivo

Fechar a Spec 05 medindo a fila e reconciliando contrato, runtime e docs.
Medição informa a Spec 06; não cria gate, SLA, alerta ou autoaprendizado.

## Dependências

RT05-T01 a T08.

## Arquivos prováveis

- `src/radar/core/services/data_quality_metrics.py` (novo, somente se leitura
  não couber no repositório) e teste unitário;
- `docs/execution/radar-data-trust/reports/05-exception-review/README.md`;
- `docs/domain/schema.md`, `docs/architecture.md`, docs operacionais e status
  da Spec 05/mãe, somente após sucesso.

## Passos

1. Derivar em leitura exceções abertas/resolvidas por código/fonte/campo, idade,
   tempo de revisão, reaberturas, decisões e casos impedidos de ativos.
   Denominador ausente é `null`, nunca sucesso/zero.
2. Rodar fixtures, incluindo Finep/Eureka, e confirmar idempotência, reabertura,
   auth, enforcement e incerteza. Registrar fixture, não estoque produtivo.
3. Buscar caminhos temporais para provar que `deadline=null` não significa mais
   continuidade e que logs/payloads não expõem conteúdo sensível.
4. Consolidar commits, limitações e sinais para Spec 06. Só então atualizar
   status da spec e spec-mãe.

## Invariantes

- Métricas são diagnósticas/read-only; sem threshold, gate, prioridade, alerta,
  retry, promoção ou modelo.
- Revisão humana não vira golden, prompt ou treinamento automaticamente.
- Sem produção, `.env`, rede, LLM, worker, cron, deploy ou migration remota.

## Testes mínimos

- Fila vazia, aberta/resolvida, reaberta e denominador ausente.
- `pytest -q` completo contra branch-base, `ruff check` e `git diff --check`.
- `cd frontend && npx tsc --noEmit` e `cd frontend && npm run lint`.
- Reusar suíte `provenance`, sem harness/threshold/gate novo.

## Critérios de aceite

- Schema e runtime descrevem mesma regra.
- Finep/Eureka está demonstrado como não ativo até revisão válida.
- Relatório consolidado registra limitações e sinais para Spec 06.

## Proibições

Sem backfill, coleta externa, operador automático, OCR/visão, LLM, harness,
gate, dashboard adicional, alerta, merge ou push.

## Pare se

Houver regressão sem baseline, divergência schema/runtime, fila sem RLS,
vazamento, `needs_review` entrando no match ativo ou métrica dependente de
produção. Corrigir origem antes de marcar a spec vigente.
