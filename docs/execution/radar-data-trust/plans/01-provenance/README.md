# Plano executável — Radar Data Trust 01

**Spec:** [`../../../../specs/radar-data-trust-01-provenance.md`](../../../../specs/radar-data-trust-01-provenance.md)
**Status:** pronto para aprovação após a spec 00

## Resultado

Propagar evidência estruturada do produtor ao gold e ao usuário, preservando
todos os caminhos atuais e sem criar RAG artificial para atores.

## Ordem

| Task | Plano | Depende de |
|---|---|---|
| `RT01-T01` | [`provenance-types.md`](RT01-T01-provenance-types.md) | spec 00/T01 |
| `RT01-T02` | [`equivalence-baseline.md`](RT01-T02-equivalence-baseline.md) | T01 |
| `RT01-T03` | [`evidence-resolver.md`](RT01-T03-evidence-resolver.md) | T01–T02 |
| `RT01-T04` | [`additive-storage.md`](RT01-T04-additive-storage.md) | T01–T03 |
| `RT01-T05` | [`finep-vertical-slice.md`](RT01-T05-finep-vertical-slice.md) | T01–T04 |
| `RT01-T06` | [`other-opportunity-sources.md`](RT01-T06-other-opportunity-sources.md) | T05 |
| `RT01-T07` | [`embrapii-icts.md`](RT01-T07-embrapii-icts.md) | T01–T04 |
| `RT01-T08` | [`curated-actors.md`](RT01-T08-curated-actors.md) | T01–T04 |
| `RT01-T09` | [`writing-chunk-lineage.md`](RT01-T09-writing-chunk-lineage.md) | T05–T06 |
| `RT01-T10` | [`api-explore.md`](RT01-T10-api-explore.md) | T05–T09 |
| `RT01-T11` | [`product-citations.md`](RT01-T11-product-citations.md) | T10 |
| `RT01-T12` | [`sample-backfill.md`](RT01-T12-sample-backfill.md) | T05–T11 |
| `RT01-T13` | [`final-validation.md`](RT01-T13-final-validation.md) | T01–T12 |

T07 e T08 podem rodar em paralelo depois da migration. T09 depende apenas das
fontes de oportunidade. Nenhuma task de ator exige chunks.

## Gate proporcional

- por task: testes direcionados + lint do escopo;
- migrations: schema local, RLS e round-trip;
- por origem: uma fixture representativa e projeção de equivalência;
- frontend somente em T11;
- suíte completa e Docker/worker apenas em T13 ou quando uma task tocar wiring
  de runtime.

O implementador não amplia escopo de produto. Dúvida de campo, precedência,
inclusão ou exposição pública volta ao proprietário.
