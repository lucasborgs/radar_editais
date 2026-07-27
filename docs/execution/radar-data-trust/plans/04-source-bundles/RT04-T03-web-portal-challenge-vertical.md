# RT04-T03 — Vertical Web: portal/programa e desafio promovido

## Objetivo

Fazer o fan-out Web preservar, no candidato-filho, o conteúdo já coletado do
hub e levá-lo ao `evidence_package`/staging. Só após promoção, materializar um
bundle `complete` com `program_page` contextual do snapshot e
`opportunity_page` do desafio. A projeção continua em `edital_source_docs`;
gate humano, adapter, silver e gold continuam os atuais.

## Dependências e pouso

Depende de T01–T02. Pousa primeiro em `_expand_hub()`/fila de
`opportunity_discovery`, depois em `discovery_evidence`, staging e
`canonical_documents_from_evidence()`/`materialize_approved_evidence()`.
Nenhum URL adicional será buscado.

## Arquivos prováveis

- `src/radar/core/services/discovery_materializer.py`;
- `src/radar/core/ingestion/opportunity_discovery.py` e
  `src/radar/core/services/discovery_evidence.py`;
- `src/radar/pipeline/adapters/web.py`, se metadata compatível precisar passar;
- `src/radar/core/kg/source_docs.py`, `source_bundles.py` e testes de promoção;
- `tests/fixtures/source_bundles/`, `tests/unit/test_opportunity_discovery_cache.py`
  e/ou `tests/unit/test_source_coverage_discovery.py`.

## Passos delimitados

1. Alterar o retorno/atributo interno de `_expand_hub()` para transportar a cada
   filho uma referência e snapshot sanitizado do hub já obtido por
   `_fetch_and_parse`: URL canônica, texto limitado por cap explícito, hash
   SHA-256 e estado `loaded`/`empty`. Não chamar `_page_text`, busca ou fetch
   adicional para obter esse contexto.
2. Estender `build_evidence_package()` de forma compatível para persistir esse
   contexto como página relacionada de programa, e fazer a linha de staging
   receber o pacote no `raw` já existente. Filho sem contexto recuperável segue
   como desafio isolado; não inventar portal.
3. Na promoção, materializar `opportunity_page` da evidência do filho e
   `program_page` contextual somente do snapshot de hub carregado pelo filho.
   Fazer append best-effort do bundle e projeção compatível em `source_docs`;
   erro de histórico não bloqueia bronze/promoção/chunking/ingestão.
4. Preservar `web:<url_hash>`, dedup, channel/family, relevância, status de
   staging, gate humano, promoção e `subordinado_a`; operador fica metadado,
   nunca `agencia`. Precedência formal fica T06.

## Testes proporcionais

- hub+filho com snapshot capado/hashado, filho sem conteúdo de hub, desafio
  isolado, `partial`, falha best-effort e recoleta;
- compatibilidade do formato atual de `evidence_package`/staging e ausência de
  fetch adicional; doubles locais, testes direcionados, `ruff` e diff check.

## Pare

Hub sem conteúdo recuperável, evidência que não distinga portal/desafio,
necessidade de fetch/crawler, novo ator ou alteração do contrato público de
promoção exige parar o item. Preservar o filho sem `program_page`, não fabricar
contexto.

## Não objetivos

Sem canal novo, auto-promoção, revisão, LLM, ranking/escrita, crawler ou agência
corporativa.

## Relatório esperado

`reports/04-source-bundles/RT04-T03-report.md`: papéis/IDs de fixture,
bundle/projeção/idempotência, falha best-effort, commit/base, testes e ambiente.
