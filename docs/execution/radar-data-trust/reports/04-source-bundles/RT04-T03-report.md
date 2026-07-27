# RT04-T03 — Relatório parcial

**Status:** T03-A concluída; T03-B pendente

## Escopo executado

Esta entrega implementa somente a propagação do snapshot do portal Web já
coletado por `_expand_hub()` até o `evidence_package` e o `raw` da staging.
Nenhuma materialização pós-promoção foi implementada.

## Base e branch

- Base obrigatória: `3ae6e62e5`
- Branch: `codex/radar-data-trust-04-t03a`
- Worktree: `/private/tmp/radar-editais-rt04-t03a`
- Commit: registrado no handoff final desta entrega

## Decisões

- `_expand_hub()` continua usando exatamente um `_fetch_and_parse()` e retorna
  cada filho acompanhado de um dicionário interno com URL canônica, texto
  sanitizado, cap explícito de `20_000` caracteres, SHA-256 do texto armazenado
  e estado `loaded`/`empty`.
- O contrato público de `SearchHit` não foi alterado. O loop aceita também o
  retorno antigo `SearchHit` para manter doubles/callers existentes compatíveis.
- Snapshot `loaded` vira item em `related_pages`, com papel `program_page` e
  autoridade `contextual`. Snapshot `empty` não cria item.
- `documents` permanece reservado aos documentos já consumidos pelo fluxo atual;
  o materializador legado não lê `related_pages`. A criação formal do
  `program_page` fica para T03-B.
- O desafio continua sendo a página principal. Candidatos isolados e pacotes
  legados permanecem sem `documents` adicionais.
- O caminho opcional Crawl4AI parte do registro que já contém o snapshot, então
  não apaga o contexto preservado.
- `_stage_records()` não ganhou campos nem schema novo: o pacote existente
  continua sendo enviado dentro de `raw`.

## Arquivos

- `src/radar/core/ingestion/opportunity_discovery.py`
- `src/radar/core/services/discovery_evidence.py`
- `tests/unit/test_rt04_t03a_hub_evidence.py`
- `docs/execution/radar-data-trust/reports/04-source-bundles/RT04-T03-report.md`

## Validação

- `PYTHONPATH=src pytest -q tests/unit/test_rt04_t03a_hub_evidence.py tests/unit/test_discovery_evidence.py tests/unit/test_opportunity_discovery_cache.py tests/unit/test_source_coverage_discovery.py tests/unit/test_relevance_staging.py`
  - `85 passed`
- Ruff nos três arquivos Python alterados: aprovado
- `git diff --check`: aprovado

Os testes cobrem cap/hash, compartilhamento entre filhos, snapshot vazio,
desafio isolado, compatibilidade do pacote legado, preservação no caminho real
do Crawl4AI, ausência de fetch adicional do portal, retenção do pacote em `raw`
e não-consumo de `related_pages` pelo materializador legado.

## Limitações e pendências

- A materialização pós-promoção de `program_page`/`opportunity_page`, o bundle
  versionado e a projeção em `source_docs` permanecem para T03-B.
- Não foram tocados promoção, `discovery_materializer.py`, `source_bundles.py`,
  `source_docs.py`, API, frontend, migrations, T04 ou tarefas posteriores.

## Auditoria Codex

**T03-A aprovada em 2026-07-27.** Validação independente: 86 testes
direcionados, Ruff e `git diff --check` limpos. O caminho real do Crawl4AI
preserva `related_pages`, não há fetch adicional do portal e o materializador
legado não consome esse contexto antes da T03-B.
