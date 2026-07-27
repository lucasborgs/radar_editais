# RT04-T03 — Relatório final

**Status:** T03 concluída

## Escopo executado

T03-A preserva o snapshot do portal no candidato-filho, em `related_pages`,
até o `evidence_package` e a staging. T03-B materializa essa evidência somente
após promoção humana, sem rede, recrawl ou LLM.

## Base, branch e commits

- Base obrigatória T03-B: `f5425a0ad`
- Branch: `codex/radar-data-trust-04-t03b`
- Worktree: `/private/tmp/radar-editais-rt04-t03b`
- T03-A integrada na base: `f5425a0ad`
- T03-B: `2157efd7e` (`feat(data-trust): materialize web evidence bundle`)
- Correção residual T03-B: `fix(data-trust): preserve evidence timestamps`

## Implementação

- A página específica carregada gera `opportunity_page` com autoridade `active`.
- Cada item carregado de `related_pages` gera `program_page` com autoridade
  `contextual`.
- O sujeito mantém `web:<url_hash>` e os hashes documentais são recalculados
  no formato SHA-256 do contrato `SourceBundle`.
- `complete` exige a página específica carregada; contexto sem página específica
  gera `partial`; ausência de documento carregado não fabrica bundle.
- `source_bundles.save()` é usado de forma append-only/idempotente. `partial`
  não substitui `complete`, pois o repositório existente seleciona somente o
  último `complete` na leitura.
- `BundleStorageError` é tratado como best-effort: bronze, `source_docs`,
  promoção e jobs existentes continuam preservados.
- Evidência sem `collected_at` válido não fabrica horário de coleta: o bundle é
  omitido com aviso sanitizado, mantendo bronze e a projeção compatível.
- A persistência append-only do bundle é tentada antes de `source_docs`; em
  `BundleStorageError`, o log expõe somente a categoria da falha e a projeção
  continua sendo gravada.
- A projeção compatível em `source_docs` inclui a página do desafio e o contexto
  carregado. Documentos auxiliares sem papel declarado não são promovidos a
  papel normativo no bundle.

## Arquivos alterados

- `src/radar/core/services/discovery_materializer.py`
- `tests/unit/test_rt04_t03a_hub_evidence.py`
- `tests/unit/test_rt04_t03b_materialization.py`
- `docs/execution/radar-data-trust/reports/04-source-bundles/RT04-T03-report.md`

Não foram alterados API, migrations, frontend, gate humano, deduplicação,
consumers downstream, T04 ou qualquer etapa posterior.

## Validação

- Testes direcionados e regressões relacionadas: `143 passed`
- Ruff nos arquivos Python alterados: aprovado
- `git diff --check`: aprovado

A suíte cobre portal + desafio (`complete`), desafio isolado, contexto sem
página específica (`partial`), ausência documental, falha de
`BundleStorageError`, idempotência por `bundle_hash`, projeção em `source_docs`
e ausência de fetch adicional, inclusive timestamp ausente/inválido, ordem
bundle→projeção e sanitização do log de armazenamento.
