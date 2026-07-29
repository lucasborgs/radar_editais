# Execução — Radar Data Trust

**Spec-mãe:** [`../../specs/radar-data-trust.md`](../../specs/radar-data-trust.md)
**Status:** execução incremental · **Início:** 2026-07-22

Esta pasta contém somente planos e relatórios executivos da família
`radar-data-trust-*`.

## Estrutura

```text
plans/
  00-relevance/    tasks da spec 00
  01-provenance/   tasks da spec 01
  02-quality-gates/
  03-source-coverage/
  04-source-bundles/
  05-exception-review/
reports/
  00-relevance/    evidências e consolidação da spec 00
  01-provenance/   evidências e consolidação da spec 01
  02-quality-gates/
  03-source-coverage/
  04-source-bundles/
  05-exception-review/
```

## Política pré-beta

- tarefas pequenas e uma responsabilidade por commit;
- testes direcionados por task;
- uma fixture representativa por origem no primeiro ciclo;
- suíte completa somente no fechamento de cada spec filha;
- eval externa apenas quando comportamento de IA mudar;
- migrations, RLS, idempotência e risco de perda de dados continuam
  obrigatoriamente validados; e
- nenhuma dúvida de produto é resolvida por inferência do implementador.

## Fluxo

1. proprietário aprova a spec e o plano;
2. implementador executa uma task em branch delimitada;
3. implementador preenche o relatório correspondente;
4. Codex audita diff, comportamento e evidências;
5. divergência volta à spec/plano antes de novo código;
6. ao terminar todas as tasks, o relatório consolidado reconcilia spec e
   runtime.

## Estado

| Spec | Planos | Relatório | Estado |
|---|---|---|---|
| [`00 — relevância`](../../specs/radar-data-trust-00-relevance-contract.md) | [`plans/00-relevance/`](plans/00-relevance/) | [`reports/00-relevance/`](reports/00-relevance/) | reconciliada |
| [`01 — proveniência`](../../specs/radar-data-trust-01-provenance.md) | [`plans/01-provenance/`](plans/01-provenance/) | [`reports/01-provenance/`](reports/01-provenance/) | reconciliada |
| [`02 — quality gates`](../../specs/radar-data-trust-02-quality-gates.md) | [`plans/02-quality-gates/`](plans/02-quality-gates/) | [`reports/02-quality-gates/`](reports/02-quality-gates/) | reconciliada |
| [`03 — source coverage`](../../specs/radar-data-trust-03-source-coverage.md) | [`plans/03-source-coverage/`](plans/03-source-coverage/) | [`reports/03-source-coverage/`](reports/03-source-coverage/) | reconciliada |
| [`04 — source bundles`](../../specs/radar-data-trust-04-source-bundles.md) | [`plans/04-source-bundles/`](plans/04-source-bundles/) | [`reports/04-source-bundles/`](reports/04-source-bundles/) | reconciliada |
| [`05 — exception review`](../../specs/radar-data-trust-05-exception-review.md) | [`plans/05-exception-review/`](plans/05-exception-review/) | [`reports/05-exception-review/`](reports/05-exception-review/) | reconciliada localmente |
