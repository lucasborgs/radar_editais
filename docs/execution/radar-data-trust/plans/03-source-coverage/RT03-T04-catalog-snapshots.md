# RT03-T04 — Snapshot determinístico dos catálogos versionados

## Objetivo

Inspecionar localmente os três artefatos que o gold já consome e registrar
snapshots em `source_runs`: investidores, programas e o último snapshot
EMBRAPII. A inspeção observa arquivos; não chama o gold nem reclassifica dados.

## Arquivos prováveis

- `src/radar/core/services/source_catalog_snapshots.py` (novo);
- `src/radar/core/tasks.py` (uma chamada diária após T03, reutilizando seu
  batch);
- `tests/unit/test_source_coverage_catalogs.py` (novo);
- `tests/fixtures/source_coverage/` (novos JSONs mínimos, se necessários).

## Passos

1. Implementar inspector puro parametrizado por definição do registry, que usa
   os mesmos caminhos/seleção atuais do gold: `SILVER_DIR/investidores.json`,
   `SILVER_DIR/programas.json` e o último
   `BRONZE_DIR/ict_raw/embrapii_*.json` ordenado.
2. Calcular SHA-256 do conteúdo, caminho lógico (nunca caminho/URL sensível),
   quantidade de registros e, quando o campo existir, contagens com/sem
   `verificado_em`. Registrar menor/maior data somente entre datas ISO válidas;
   ausência ou valor inválido fica explícito como ausência/contador, sem correção.
3. Abrir/finalizar um run `versioned_catalog` por artefato com a mesma rodada
   diária. O conteúdo de `metrics` é limitado a hash, caminho lógico e
   contadores. Arquivo ausente ou JSON inválido produz falha categórica sem
   interromper o ETL; não há SLA/estado healthy/stale para catálogo.
4. Depois de T03 aterrado, ligar o inspector ao cron uma única vez,
   compartilhando seu batch. Coordenar o único bloco em `tasks.py` conforme o
   README, sem duplicar chamadas ou tornar `ingest_all` pré-requisito.

## Invariantes

- A seleção EMBRAPII é exatamente a do gold; não criar catálogo paralelo.
- Nenhum registro é enriquecido, reordenado, regravado ou reingerido.
- Hash é do artefato local observado; não se fabrica `verificado_em`, data ou
  completude. Falta de denominador/data vira `null` na leitura posterior.

## Testes direcionados

- uma fixture por catálogo: hash/contagem determinísticos, verificação presente,
  ausente e data inválida/ausente;
- seleção determinística do último EMBRAPII, arquivo faltante e JSON inválido;
- persistência best-effort não derruba o chamador;
- `ENVIRONMENT=test pytest -q tests/unit/test_source_coverage_catalogs.py`,
  `ruff check` no escopo e `git diff --check`.

## Pare

Pare se o snapshot requer rede, LLM, ingestão gold, modificação do artefato ou
uma política de SLA para catálogo. Pare também se os caminhos escolhidos não
forem os consumidos por `gold.py`; isso é contradição de runtime a reportar.

## Entrega e ambiente hermético

Entregar inspector, fixtures/testes e eventual chamada diária coordenada,
acompanhados de relatório `RT03-T04-*.md` com hashes apenas de fixtures e limites
de frescor. Confirmar `ENVIRONMENT=test`, `tmp_path`/fixtures locais, sem `.env`,
rede, banco remoto, LLM, Supabase Cloud ou reingestão.
