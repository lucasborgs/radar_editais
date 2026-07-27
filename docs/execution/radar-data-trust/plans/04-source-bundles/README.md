# Plano executável — Radar Data Trust 04 (Pacotes documentais versionados)

**Spec:** [`../../../../specs/radar-data-trust-04-source-bundles.md`](../../../../specs/radar-data-trust-04-source-bundles.md)  
**Spec-mãe:** [`../../../../specs/radar-data-trust.md`](../../../../specs/radar-data-trust.md)  
**Status:** concluído

## Resultado

Versionar material documental de oportunidades e atores sem deslocar o data
plane existente. A única persistência nova é o histórico append-only de
`SourceBundle`; `edital_source_docs` continua sendo a projeção compatível que
alimenta structurer, silver, gold e chunks. Ausência documental permanece
`unknown`; ambiguidade documental permanece `conflicting`.

Não há crawler universal, OCR/visão, nova LLM, backfill integral, fila de
revisão (Spec 05), extração adaptativa (Spec 06), mudança de ranking/RAG ou
fonte de verdade paralela.

## Ordem e dependências

| Task | Plano | Resultado | Depende de |
|---|---|---|---|
| `RT04-T01` | [`source-bundle-contract-fixtures.md`](RT04-T01-source-bundle-contract-fixtures.md) | contrato puro e fixtures representativas | Spec 04 aprovada |
| `RT04-T02` | [`append-only-bundle-storage.md`](RT04-T02-append-only-bundle-storage.md) | única tabela nova e repositório idempotente | T01 |
| `RT04-T03` | [`web-portal-challenge-vertical.md`](RT04-T03-web-portal-challenge-vertical.md) | Web: snapshot sanitizado de hub + desafio promovido | T01–T02 |
| `RT04-T04` | [`fapesc-normative-documents.md`](RT04-T04-fapesc-normative-documents.md) | base/anexo/retificação em fontes normativas aplicáveis | T01–T02 |
| `RT04-T05` | [`actor-source-bundles.md`](RT04-T05-actor-source-bundles.md) | fontes conhecidas de ICTs, investidores, programas e agências | T01–T02 |
| `RT04-T06` | [`composition-conflict-provenance.md`](RT04-T06-composition-conflict-provenance.md) | composição, conflito e referências de proveniência | T03–T05 |
| `RT04-T07` | [`diagnostics-reconciliation.md`](RT04-T07-diagnostics-reconciliation.md) | métricas diagnósticas, fechamento e reconciliação | T01–T06 |

## Ondas e pousos serializados

- **Onda A:** T01, depois T02. Tipos, normalização e a migration são o
  contrato comum; nenhum produtor escreve bundle antes disso.
- **Onda B:** T03, T04 e T05 podem ser implementadas em paralelo após T02,
  mas pousam serialmente em `source_docs`/repositório de bundles e no contrato
  de proveniência. T03 é a única task da materialização Web e preserva no
  candidato-filho apenas o snapshot sanitizado do hub já coletado no fan-out;
  T04 é dona de FAPESC e de adapters normativos realmente aplicáveis; T05 é
  dona dos catálogos de atores.
- **Onda C:** T06A compõe/projeta e preserva `conflicting`; T06B, depois dela,
  adiciona as referências bundle→proveniência/chunks. As duas são pousos curtos
  da mesma T06 e não iniciam antes das três fixtures estáveis.
- **Onda D:** T07 só mede, reconcilia e fecha; não cria tabela, API ou revisão.

Pontos compartilhados a aterrar em série: `docs/domain/schema.md`,
`src/radar/core/kg/source_docs.py`, o repositório de bundles e
`src/radar/domain/provenance.py`. Revalidar a numeração da migration: hoje a
última é `043`.

## Invariantes transversais

- Reutilizar `SourceAdapter`, Documento Canônico, `source_docs`, silver/gold,
  `FactProvenance`/`EvidenceRef` e a suíte `provenance`; sem produtor, harness
  ou documento paralelo.
- No máximo uma tabela service-role-only, append-only e idempotente por
  `(subject_kind, subject_id, bundle_hash)`. `partial` é diagnóstico e nunca
  substitui a última versão material `complete`.
- Hashes novos são SHA-256 determinísticos; MD5 legado é só compatibilidade.
- Metadado e claims document-scoped explícitos decidem precedência; T06 não
  cria parser, diff textual ou LLM. Sem claims separados, preservar o estado
  atual/`unknown`; `conflicting` exige dois valores incompatíveis sustentados
  por documentos autorizados e sem precedência.
- Empresa operadora não vira `agencia` nem novo kind; conteúdo ausente de ator
  fica `unknown`, sem chunks/RAG sintéticos.
- Contexto de hub só existe quando o fan-out já o coletou: é snapshot sanitizado
  com cap de texto e hash, carregado ao filho/evidence package/staging sem
  recrawl, sem mudar triagem, relevância ou promoção.
- Testes são herméticos: `ENVIRONMENT=test`, fixtures/doubles locais, sem
  `.env`, rede, produção, credenciais, worker, LLM ou migration remota.

## Gate proporcional e relatórios

- Por task: testes direcionados, `ruff check` no Python alterado e
  `git diff --check`; uma fixture por caso, sem matriz cartesiana.
- T02 valida schema/RLS e reexecução com fake/banco local. T06 roda a suíte
  `provenance` diagnóstica, sem threshold/gate. A suíte Python completa é T07.
- Cada task produz, em commit documental separado quando necessário,
  `docs/execution/radar-data-trust/reports/04-source-bundles/RT04-T0N-report.md`
  com base/commit, arquivos, testes, fixtures, limitações e ambiente hermético.
  T07 cria/atualiza o README consolidado do diretório.

Commits são separados por task; não fazer merge ou push. Divergência invalida
somente a task afetada; parar o fluxo inteiro apenas se método/contrato for
questionado ou uma decisão de produto for necessária.
