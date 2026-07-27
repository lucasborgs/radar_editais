# RT04-T04 — Documentos normativos FAPESC

**Status:** T04 concluída

## Base e commit

- Base obrigatória: `23a7ef406`
- Branch: `codex/radar-data-trust-04-t04`
- Worktree: `/private/tmp/radar-editais-rt04-t04`
- Commit de implementação: `0057c59b5`

## Implementação

- O helper específico `build_source_bundle()` reutiliza o bronze FAPESC já
  coletado e mapeia somente `family: edital-base` para `base_notice` e
  `family: emenda` para `amendment`.
- Texto, URL e hashes são derivados dos itens reais de `documentos_normativos`.
  Nenhum `amends_content_hash` ou `composition_order` é inventado.
- O sujeito mantém `fapesc:<native_id>`.
- `data_extracao` é a origem de `collected_at`; timestamp ausente ou inválido
  não cria bundle e não usa `now()`.
- O legado `authority_state: vigente` não é convertido automaticamente em
  `active`; a autoridade permanece `contextual` até decisão posterior.
- O bundle é tentado antes de `source_docs.save()` nos três caminhos existentes:
  chunking, construção diária de silver e ingestão de edital promovido.
- `BundleStorageError` é tratado best-effort e logado somente com tipo
  sanitizado; silver, gold, chunking e a projeção canônica continuam o fluxo.
- Fallback HTML continua válido para o adapter, mas não cria bundle normativo.

## Projeção e escopo

A projeção canônica atual permaneceu compatível. `source_bundles.py`, migrations,
API, frontend e consumers downstream não foram alterados. FINEP e FAPESP foram
revalidados: seus adapters não expõem `documentos_normativos` com as famílias
FAPESC, portanto o mapeamento não é aplicável e nenhum adapter foi modificado.
Composição por campo e precedência permanecem para T06.

## Arquivos

- `src/radar/pipeline/adapters/fapesc.py`
- `src/radar/core/tasks.py`
- `docs/domain/sources/fapesc.md`
- `tests/unit/test_fapesc_source_bundle.py`

## Validação

- Regressão FAPESC/ETL/bundles/tasks/source docs: `162 passed`
- Revalidação FINEP/FAPESP: `29 passed`
- Ruff nos arquivos Python alterados: aprovado
- `git diff --check`: aprovado

Os testes cobrem edital-base, base + emenda sem vínculo/ordem inventados,
recoleta idêntica, timestamp ausente/inválido, fallback HTML, ordem bundle antes
de `source_docs`, falha sanitizada do histórico e os três caminhos de task.
