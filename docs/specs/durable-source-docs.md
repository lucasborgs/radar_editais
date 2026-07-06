# Documento Canônico durável (robustez contra disco efêmero)

Status: implementado 2026-06-27. Migration 032.

## Problema-raiz

O filesystem do worker não é fonte de verdade durável: todo rebuild de imagem (deploy de código
OU mudança de env var) apaga `data/bronze/` e os PDFs FINEP (`FINEP_PDFS_DIR`).

O `chunk_edital` (lazy — spec [lazy-chunking.md](lazy-chunking.md)) lê o
conteúdo-fonte via adapters, que leem o **disco**:

- `finep` → PDFs de `FINEP_PDFS_DIR/<id>/` (pdfplumber per-página)
- `web` → HTML cru de `bronze/web_raw/*.json`
- `fapesp`/`fapesc` → `texto_cru` de `bronze/<src>_raw/*.json`

Resultado: após qualquer redeploy, o conteúdo some → `chunk_edital` roda, o
adapter não acha fonte e produz **0 chunks** (job "succeeded" com 0). Já mordeu
2×: 31 editais web perdidos e, após a troca de `OPENAI_API_KEY` do worker, os
PDFs FINEP wipados → lazy-chunking produzindo 0.

## Insight

O texto **já é extraído eager** no momento do scrape (FINEP grava `pdf_texts` no
download; FAPESP/FAPESC gravam `texto_cru`; web guarda `html`). O que é *lazy* é
só o caro (structurer-LLM + embedding). O problema **não é re-buscar a fonte** —
é que o texto extraído é gravado **só no disco efêmero**.

Logo: persistir o **Documento Canônico** (§12.3 — o contrato agnóstico de fonte:
`[{doc_name, units}]`) num seam durável, **gravado no scrape (disco fresco)**,
elimina a dependência de disco no chunk. Não adiciona custo de extração (já é
eager) nem fere o lazy (LLM/embed seguem on-demand).

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

### Escrita (proativa, no scrape)

`run_daily_etl_task` (cron 03:00), após scrape + build KG, chama
`source_docs.persist_all_current()`: itera os editais do index e faz upsert do
Documento Canônico de cada um (via `adapter.to_documents`, com o disco fresco).
Custo: pdfplumber/html-clean para o catálogo vigente — barato (já era extraído),
sem LLM.

### Leitura (durável-primeiro, no chunk)

`_build_chunks_for_edital` lê `source_docs.load(edital_id)` primeiro. Só cai no
`adapter.to_documents` (disco) se o durável faltar — e, nesse caso, **backfilla**
o durável (self-healing para editais pré-feature ou quando o disco está fresco).
Disco vira **cache/fallback**, não dependência.

### Invariantes preservadas

- **Lazy chunking**: structurer-LLM + embedding seguem on-demand. Só a extração
  (já eager) é persistida.
- **Silver cache** (`structurer._source_hash`): hash é content-based sobre o
  Documento Canônico — o durável guarda exatamente o que o adapter produz, então
  o hash bate e o cache silver continua válido.
- **Gate de `content_hash`** (`chunk_edital_task`): inalterado.
- **Freshness**: o cron diário re-scrapeia → re-persiste → o gate re-chunka só o
  que mudou.

## Backfill em prod

O durável só popula após um scrape com disco fresco. Após deploy:
- automático: o cron 03:00 (scrape → build → `persist_all_current`); ou
- manual: `python -m core.kg.source_docs` (roda `persist_all_current` agora).

Antes disso, o read-path faz backfill on-demand quando o disco ainda tem o
bronze (ex.: logo após um scrape manual).
