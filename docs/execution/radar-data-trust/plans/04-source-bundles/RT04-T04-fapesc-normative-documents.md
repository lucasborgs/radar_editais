# RT04-T04 — Documentos normativos: FAPESC primeiro

## Objetivo

Versionar edital-base e retificações/erratas que FAPESC já coleta, mantendo
histórico e projeção factual compatível. Estender a fonte normativa só onde
outro adapter já provar papel/autoridade; nunca forçar mapeamento artificial.

## Dependências e pouso

Depende de T01–T02. Pousa em `documentos_normativos` do extractor/adaptador
FAPESC, repositório de T02 e `source_docs`; demais adapters são revalidados um
por um.

## Arquivos prováveis

- `src/radar/pipeline/extractors/fapesc.py` e `adapters/fapesc.py`;
- `src/radar/core/kg/source_docs.py`, `source_bundles.py` e pontos de
  `src/radar/core/tasks.py` que já salvam o Documento Canônico;
- `docs/domain/sources/fapesc.md`, fixtures/testes FAPESC existentes.

## Passos delimitados

1. Traduzir marcadores existentes (`edital-base`, `emenda`) em `base_notice` e
   `amendment`, com hash/URL/estado aprovado. Não promover `vigente` legado a
   estado novo nem inferir vínculo por nome/download.
2. Declarar ordem/vínculo somente quando o produtor já o conhece; caso contrário
   preservar ambos e deixar ambiguidade para T06.
3. No caminho real de `source_docs.save`, fazer append best-effort do bundle e
   manter structurer/silver/gold/chunking compatíveis.
4. Documentar quais outros adapters foram revalidados e ficaram não aplicáveis.

## Testes proporcionais

- base, base+emenda, recoleta e emenda sem ordem/vínculo; texto/PDF local;
- projeção compatível e falha best-effort; testes direcionados, `ruff` e diff check.

## Pare

Retificação não demonstravelmente ligada, emendas incompatíveis sem ordem,
interpretação jurídica/LLM ou papel inventado invalida só esse item.

## Não objetivos

Sem download recursivo, OCR/visão, catálogo inteiro, FINEP/FAPESP artificial,
composição por campo (T06) ou revisão (Spec 05).

## Relatório esperado

`reports/04-source-bundles/RT04-T04-report.md`: fixture/mapeamento FAPESC,
fontes não aplicáveis, projeção, ambiguidades, commit/base, testes e ambiente.
