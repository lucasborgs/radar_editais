# RT04-T06 — Composição corrente, conflito e proveniência do bundle

## Objetivo

Compor conservadoramente a última versão `complete`, emitir `conflicting` sem
precedência comprovada e ligar bundle/documento usado à proveniência de fatos,
relações e chunks. Sem interpretação jurídica ou LLM.

## Dependências e pouso

Depende de T03–T05. Pousa no leitor de bundles/`source_docs`,
`EvidenceRef`/`FactProvenance`, resolvedor, gold e linhagem de chunks existentes.
Mantém provenance legado e não duplica documento no gold.

## Arquivos prováveis

- `src/radar/core/kg/source_bundles.py`, `source_docs.py`, `gold.py` e
  `evidence_resolver.py`;
- `src/radar/domain/provenance.py`, `provenance_writer.py` e `core/tasks.py`;
- testes de bundles/proveniência e golden da suíte `provenance`.

## Passos delimitados

### T06A — composição, projeção e conflito

1. Ler último `complete`, excluir só `superseded`, manter anexo/contexto e nunca
   deixar `partial` posterior substituir a projeção completa. Receber somente
   claims/campos já emitidos pelos produtores e ancorados no documento que os
   sustenta; não criar parser, text diff ou chamada LLM para descobrir o que
   uma emenda altera.
2. Resolver puramente sobre esses claims explícitos: amendment só vence o campo
   que declara; consolidado só identificado; desafio vence portal no mesmo
   campo; curado não vence oficial. Emitir `FactState.CONFLICTING` somente se
   houver dois valores incompatíveis, sustentados por documentos autorizados,
   sem precedência confiável. Sem claims separados, preservar estado atual ou
   `unknown` e registrar a limitação; não inventar conflito nem ampliar extração.

### T06B — referências aditivas de bundle

3. Depois de T06A, estender evidência aditivamente com `source_bundle_id` (ou
   referência estável), `bundle_hash` e `content_hash`; produtores T03–T05 e
   chunks apontam à versão já composta. Legado fica `unknown/legacy`.
4. Reusar a suíte `provenance`, adicionando só casos mínimos da spec; sem harness,
   threshold ou gate.

## Testes proporcionais

- base, anexo, emenda ordenada, consolidado, conflito, portal+desafio,
  ator oficial+curado, legado e `partial` posterior;
- testes unitários RT04 + `python -m radar.core.eval run provenance` local;
- `ruff check` no escopo e `git diff --check`.

## Pare

Interpretação jurídica, emendas incompatíveis sem ordem, FAQ contraditória,
ausência de claims document-scoped por campo ou referência que duplique
documento no gold retorna à decisão de produto. A ausência de claims é limitação
registrada para a Spec 06, não autorização para parser/diff/LLM ou conflito falso.

## Não objetivos

Sem fila/revisão, score/gate, LLM, OCR/visão, rede, API de histórico, matching,
ranking, escrita ou backfill integral.

## Relatório esperado

`reports/04-source-bundles/RT04-T06-report.md`: matriz de precedência,
referências emitidas, conflitos, resultado provenance, legado, commit/testes e
ambiente hermético.
