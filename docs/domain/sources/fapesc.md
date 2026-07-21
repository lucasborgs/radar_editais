# FAPESC — Schema específico

Estende [docs/domain/schema.md](../schema.md). Só documenta o que diverge do schema global.
A listagem é WordPress (como FAPESP), mas o edital normativo vive num **PDF
anexo** (como FINEP) — estratégia `pdf`, não `html_body`. Ver §8 (gotchas).

---

## 1. Identidade

```yaml
source:
  key: fapesc
  display_name: FAPESC
  graph_tag: fapesc
```

---

## 2. Coleta

```yaml
scraper:
  module: radar.pipeline.extractors.fapesc
  portal: "https://fapesc.sc.gov.br/chamadas-abertas/"

paths:
  bronze_dir: bronze_data/fapesc_raw
  bronze_glob: "*.json"
```

WordPress server-side. A listagem `/chamadas-abertas/` traz SÓ as chamadas
abertas; cada uma é um post com URL própria por slug. Dedup por `url` (snapshot
mais recente vence; dentro do arquivo, primeira ocorrência vence).

**Estratégia de extração (L1):** `pdf` — o post WordPress só traz um RESUMO
(~600 chars, Elementor); o texto normativo vive num PDF anexo (âncora "ACESSE O
EDITAL COMPLETO", sob `/wp-content/uploads/AAAA/MM/`). O scraper baixa esse PDF
e extrai o texto inline (`pdfplumber`), gravando-o em `texto_cru` — o adapter
não muda. Skip-list (§skip_keywords) descarta anexos não-normativos (resultados,
manuais, código de conduta).

```yaml
skip_keywords:
  - plano-de-integridade   # boilerplate institucional (em toda página)
  - codigo_conduta         # idem
  - resultado              # peças de andamento (admissibilidade, mérito)
  - manual                 # manuais do SIGFAPESC
  - passo-a-passo          # tutoriais de submissão
  - cadastr                # cadastramento no SIGFAPESC
  - sigfapesc
```

---

## 3. Mapeamento bronze → schema comum

```yaml
bronze_mapping:
  native_id:     id                # número-ano do edital (ex.: 37-2026) → fapesc:37-2026
  titulo:        title
  data_limite:   deadline          # dd/mm/yyyy | None (best-effort, ver gotchas)
  status:        status            # ABERTA | FLUXO_CONTINUO → normalizado §7.2
  areas:         themes_raw        # None no MVP (FAPESC não estrutura áreas no HTML)
  texto_cru:     body              # texto do PDF do edital; consumido como units
  fluxo_continuo: flag             # sem prazo ≠ encerrado
```

Derivados:

```yaml
derived_fields:
  fonte_recurso: '["FAPESC"]'   # FAPESC é própria fonte; sem split bronze
  publico_alvo: '[]'            # não emitido — inferido por LLM ou em key_facts
```

---

## 4. Conteúdo (PDF do edital)

O scraper baixa o PDF do edital (achado pela âncora "ACESSE O EDITAL COMPLETO")
e extrai o texto inline (`pdfplumber`), gravando em `texto_cru` — não há etapa
silver de PDF separada como na FINEP; a extração mora no scraper. O adapter
retorna 1 entrada de Documento Canônico (§12.3), fatiada em units a partir do
`texto_cru` (seja PDF ou, em fallback, o resumo HTML):

```yaml
canonical_doc:
  shape: '[{"doc_name": nome, "units": split_into_units(texto), "metadata": autoridade}]'
```

Campos extras no bronze para rastreio: `edital_pdf_url` (PDF-base escolhido),
`documentos_normativos` (edital-base + retificações/erratas, com URL e texto) e
`content_source` (`pdf` | `html`). Retificações e erratas compõem o edital-base
em ordem cronológica; não são descartadas nem tratadas isoladamente.

---

## 5. Metadados enviados ao prompt

Mesma lista do FAPESP (§5 docs/domain/sources/fapesp.md): title, status, deadline, themes,
publico_alvo, fonte_recurso, link.

---

## 6. Prompt de extração

Usa o prompt global (§8.1 docs/domain/schema.md) sem overrides.

---

## 7. Grafo Obsidian

```yaml
graph_overrides:
  node_base_tag: fapesc
  subfolder_default: radar-editais
  folders:
    editais:      editais
    temas:        temas
    fontes:       fontes
    publico:      publicos
    mecanismos:   mecanismos
    subprogramas: subprogramas
    trl:          trl
  home_title: "📡 Radar de Editais — FAPESC"
```

---

## 8. Gotchas específicos

- **`native_id` é número-ano, não numérico puro.** A URL é um slug WordPress
  (`/edital-de-chamada-publica-fapesc-n-o-37-2026-.../`); o scraper extrai
  `37-2026` via regex `n-?o-(\d+)-(\d{4})`. Fallback: último segmento do slug.
  Editais de secretarias parceiras (FAPESC/SEPLAN, FAPESC/SEA) seguem o mesmo
  padrão de número-ano.
- **O edital está no PDF, não no HTML (achado do piloto 2026-06-18).** O post
  WordPress só monta um resumo (~600 chars, Elementor); raspar o HTML pegava
  boilerplate (cookies) em vez do normativo. O scraper acha o PDF do edital pela
  ÂNCORA "ACESSE O EDITAL COMPLETO" (a palavra "edital" no texto do link é o
  sinal estável — o NOME do arquivo varia: `CP-04_2026.pdf`,
  `Edital-FAPESC-016_2026.pdf`, `Processo-FAPESC-…pdf`). Fallback: PDF
  não-boilerplate mais recente sob `/wp-content/uploads/AAAA/MM/`. Se nenhum PDF
  extrair, cai no resumo HTML (`content_source=html`) — não perde a chamada.
- **`data_limite` é best-effort.** O prazo vive dentro do PDF do edital; o regex
  de prazo agora roda sobre o `texto_cru` (= texto do PDF), captura datas em
  contexto de submissão/encerramento ("até …", "prazo …", "submissão …") e pega
  a MAIS DISTANTE (rerratificações empurram pra frente). Sem data → `None`; a
  normalização (§7.1 docs/domain/schema.md) trata sem-prazo + ABERTA como vigente.
- **Retificações/erratas são normativas.** O scraper preserva o edital-base e
  todas as emendas anexadas à página. O Documento Canônico marca a família e a
  data de cada peça; o conteúdo vigente é a composição cronológica. Resultado,
  manual e demais peças de andamento continuam fora pela skip-list.
- **Só chamadas ABERTAS.** A fonte varre `/chamadas-abertas/`; encerradas vivem
  em `/category/chamadas-encerradas/` e NÃO são coletadas (status sempre ABERTA,
  salvo "fluxo contínuo" detectado no texto).
- **Áreas/modalidades não estruturadas.** Diferente do FAPESP (modalidade no
  HTML), FAPESC não expõe área/modalidade como campo — `themes_raw`/`modalidade`
  ficam vazios; o tema é inferido pela LLM na síntese a partir de `texto_cru`.
- **WordPress sem paginação na listagem.** As ~16 chamadas abertas cabem numa
  página; se um dia paginar, o scraper precisará seguir `?paged=N`.
