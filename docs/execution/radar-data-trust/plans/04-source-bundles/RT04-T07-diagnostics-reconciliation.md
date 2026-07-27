# RT04-T07 — Métricas diagnósticas, reconciliação e fechamento

## Objetivo

Fechar a Spec 04 com métricas derivadas do histórico, validação local e
reconciliação documental. Medir não cria gate, fila, alerta ou persistência.

## Dependências e pouso

Depende de T01–T06. Pousa em read model puro, suíte `provenance`, relatórios e
docs afetadas. Só após sucesso pode atualizar status da Spec 04/mãe; jamais nas
tasks intermediárias.

## Arquivos prováveis

- `src/radar/core/services/source_bundle_metrics.py` (novo, se não houver
  leitura equivalente) e `tests/unit/test_source_bundle_metrics.py`;
- `docs/execution/radar-data-trust/reports/04-source-bundles/README.md`;
- `docs/domain/schema.md`, documentação runtime afetada e status das specs no
  fechamento bem-sucedido.

## Passos delimitados

1. Derivar sujeitos com bundle, versões/sujeito, documentos/papel, fatos críticos
   ligados à versão, conflitos/precedências e atores sem conteúdo oficial.
   Denominador ausente retorna `null`/ausência; nenhuma coluna/snapshot novo.
2. Rodar fixtures Web composto, FAPESC retificado, ator incompleto, `partial`
   posterior e legado. Registrar baseline de fixture, nunca cobertura produtiva.
3. Buscar imports/SQL para confirmar uma única tabela, o caminho
   SourceAdapter→Documento Canônico→`source_docs`→silver/gold e ausência de
   crawler, harness, LLM, API de escrita ou fonte paralela.
4. Consolidar commits, limitações, divergências e decisões das Specs 05/06. Só
   com invariantes provados, reconciliar documentação/status autoritativos.

## Testes proporcionais

- testes RT04 e métricas, `python -m radar.core.eval run provenance`;
- `ruff check` no Python alterado, `git diff --check` e `pytest -q` completo;
- sem frontend, DB remoto, deploy, worker, `.env`, rede, LLM ou `--publish`.

## Pare

Não fechar com segunda tabela, `partial` vencendo `complete`, histórico mutável,
ator sintético, precedência não comprovada, versão não recuperável, regressão
sem explicação ou acesso externo. Divergência local invalida sua task de origem.

## Não objetivos

Sem threshold/gate, dashboard/API, alerta, backfill, revisão, OCR/visão,
extração adaptativa, fonte/canal novo ou mudança de produto.

## Relatório esperado

`reports/04-source-bundles/RT04-T07-report.md` e README consolidado: baseline,
métricas/denominadores, testes/eval, commits, reconciliação/pendências e ambiente.
