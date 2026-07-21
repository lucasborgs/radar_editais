# Documento Canônico durável (robustez contra disco efêmero)

**Status:** vigente · **Data:** 2026-06-27 · **Migration:** 032

## Problema-raiz

O filesystem do worker não é fonte de verdade durável: todo rebuild de imagem (deploy de código
OU mudança de env var) apaga `data/bronze/` e os PDFs FINEP (`FINEP_PDFS_DIR`).

O `chunk_edital` (aquecido diariamente e também garantido sob demanda; ver
[data-plane-convergence.md](data-plane-convergence.md)) lê o conteúdo-fonte via
adapters quando o bronze fresco está disponível e usa o seam durável como
fallback de redeploy:

- `finep` → PDFs de `FINEP_PDFS_DIR/<id>/` (pdfplumber per-página)
- `web` → HTML cru de `bronze/web_raw/*.json`
- `fapesp`/`fapesc` → `texto_cru` de `bronze/<src>_raw/*.json`

Resultado: após qualquer redeploy, o conteúdo some → `chunk_edital` roda, o
adapter não acha fonte e produz **0 chunks** (job "succeeded" com 0). Já mordeu
2×: 31 editais web perdidos e, após a troca de `OPENAI_API_KEY` do worker, os
PDFs FINEP removidos → chunking produzindo 0.

## Insight

O texto **já é extraído** no momento do scrape (FINEP grava `pdf_texts` no
download; FAPESP/FAPESC gravam `texto_cru`; web guarda `html`). O processamento
caro (contextualização e embedding) é idempotente e pode ocorrer no aquecimento
ou sob demanda. O problema **não é re-buscar a fonte** —
é que o texto extraído é gravado **só no disco efêmero**.

Logo: persistir o **Documento Canônico** (§12.3 — o contrato agnóstico de fonte:
`[{doc_name, units, metadata?}]`) num seam durável, **gravado no scrape (disco fresco)**,
elimina a dependência de disco no chunk. Não adiciona custo de extração.

Descartado: **re-fetch on-demand** (re-baixar PDF/URL no chunk) — exigiria
tornar `pdf_urls`/`edital_pdf_url` duráveis E realocar o fetch dos scrapers, e
adicionaria dependência de rede (gov.br) no instante da escrita + modo de falha
de URL morta. Seu único ganho (versão mais nova no chunk) já é coberto pelo cron
diário + gate de `content_hash`.

## Design

### Seam durável: tabela `edital_source_docs`

```
edital_id     text  primary key   -- prefixado: 'finep:782'
source        text  not null
canonical_doc jsonb not null      -- [{doc_name, units:[...]}]  (§12.3)
content_hash  text                 -- md5 do canonical_doc (observabilidade)
updated_at    timestamptz
```

RLS habilitada sem policies (service-role only) — espelha `kg_artifacts` (016).
Postgres-only: sem Supabase é no-op (dev local usa o fallback de disco, que não
é efêmero).

### Escrita (proativa, antes do Silver)

`run_daily_etl_task` (cron 03:00), após o scrape, faz adapter/autoridade e grava
o Documento Canônico antes de materializar Silver e gold. A ordem impede que
uma versão durável antiga sobreviva por mais uma run. `persist_all_current()`
permanece depois do gold apenas como rede de segurança para itens já catalogados
que não foram enumerados no bronze da run.

### Leitura (fresco-primeiro com fallback durável)

`_build_chunks_for_edital` consulta primeiro o adapter. Se houver bronze fresco,
salva esse resultado e o usa na mesma run; se não houver, lê
`source_docs.load(edital_id)`. Assim o disco não é dependência após redeploy, mas
uma coleta nova sempre prevalece sobre o snapshot persistido anterior.

### Invariantes preservadas

- **Chunking híbrido**: o mesmo produtor idempotente atende ao aquecimento diário
  e ao ensure/prefetch sob demanda. Só a extração de origem é persistida aqui.
- **Silver cache** (`structurer._source_hash`): hash inclui conteúdo e metadata
  de autoridade; mudança de revisão invalida o cache mesmo com texto idêntico.
- **Gate de `content_hash`** (`chunk_edital_task`): inalterado.
- **Freshness**: o cron diário re-scrapeia → re-persiste → o gate re-chunka só o
  que mudou.

## Backfill em prod

O durável só popula após um scrape com disco fresco. Após deploy:
- automático: o cron 03:00 (scrape → build → `persist_all_current`); ou
- manual: `python -m radar.core.kg.source_docs` (roda `persist_all_current` agora).

Antes disso, o read-path faz backfill on-demand quando o disco ainda tem o
bronze (ex.: logo após um scrape manual).
