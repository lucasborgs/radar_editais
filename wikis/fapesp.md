# FAPESP — Schema específico

Estende [WIKI.md](../WIKI.md). Só documenta o que diverge do schema global.

---

## 1. Identidade

```yaml
source:
  key: fapesp
  display_name: FAPESP
  graph_tag: fapesp
```

---

## 2. Coleta

```yaml
scraper:
  module: pipeline/extractors/fapesp.py
  portal: "https://fapesp.br/chamadas/"

paths:
  bronze_dir: bronze_data/fapesp_raw
  bronze_glob: "*.json"
```

Bronze é JSON com array de chamadas. Dedup por `url` (primeiro arquivo lido em
ordem alfabética vence; dentro do arquivo, primeira ocorrência vence).

**Estratégia de extração (L1):** `html_body` — o texto autoritativo do edital
vive no HTML da página individual (`/{id}/{slug}`), não em PDFs. Anexos PDF
são formulários/declarações e podem ser ignorados na síntese.

---

## 3. Mapeamento bronze → schema comum

Campos do JSON bronze FAPESP e correspondência ao schema do índice (§4 WIKI.md).

```yaml
bronze_mapping:
  url:           link              # https://fapesp.br/{id} → id derivado abaixo
  titulo:        title
  data_limite:   deadline          # bronze emite ISO (yyyy-mm-dd); convertido p/ dd/mm/yyyy
  status:        status            # "ABERTA" | "FLUXO_CONTINUO" → normalizado §7.2
  modalidades:   modalidade        # string única, usada pelo filtro PME (programa)
  areas:         themes_raw        # canonicalizado em themes via domain/vocabulary
  texto_cru:     html_body         # consumido pelo Source Adapter como unit única
  fluxo_continuo: flag             # interno; influência na vigência (sem prazo ≠ encerrado)
```

Derivados:

```yaml
derived_fields:
  id: "regex /(\\d+)$/ no path da URL  # ex: https://fapesp.br/18064/slug → '18064'"
  fonte_recurso: '["FAPESP"]'  # FAPESP é própria fonte; sem split bronze
  publico_alvo: '[]'  # FAPESP bronze não emite — inferido por LLM ou em key_facts
```

---

## 4. Conteúdo (sem PDFs)

Diferente do FINEP (`silver_data/finep/pdfs/{id}/*.pdf`), FAPESP não baixa PDFs
para o silver — o texto normativo do edital já vem inline em `texto_cru` no
bronze. Adapter retorna 1 entrada de Documento Canônico (§12.3) por chamada:

```yaml
canonical_doc:
  shape: '[{"doc_name": "pagina-chamada", "units": [html_body_text]}]'
```

Anexos PDF (`https://fapesp.br/files/upload/{id}/*.pdf`) ficam fora da síntese
da wiki page e do RAG por enquanto — são formulários/templates. Se um dia
provarem ter conteúdo normativo único, basta o scraper baixar e o adapter
incluir como `doc_name` adicional.

---

## 5. Metadados enviados ao prompt

Mesma lista do FINEP (§5 wikis/finep.md), aplicada aos campos canônicos:

```yaml
metadata_to_llm:
  - title
  - status
  - deadline
  - themes
  - publico_alvo
  - fonte_recurso
  - link
```

---

## 6. Prompt de extração

Usa o prompt global (§8.1 WIKI.md) sem overrides.

---

## 7. Grafo Obsidian

```yaml
graph_overrides:
  node_base_tag: fapesp
  subfolder_default: radar-editais
  folders:
    editais:      editais
    temas:        temas
    fontes:       fontes
    publico:      publicos
    mecanismos:   mecanismos
    subprogramas: subprogramas
    trl:          trl
  home_title: "📡 Radar de Editais — FAPESP"
```

---

## 8. Gotchas específicos

- **Bronze acumula duplicatas.** Re-scans do portal sem dedup ativo gravaram
  vários arquivos em sequência (`fapesp_scan_*.json`) cada um contendo as mesmas
  ~50 chamadas. Adapter dedup por `url` normalizada. O bronze histórico
  (pré-2026-05-29) tem ~50 entries com ~22 únicos.
- **Encoding misto entre páginas.** A listagem `/chamadas/` chega em ISO-8859-1;
  páginas individuais em UTF-8. Detectar via Content-Type, nunca assumir.
- **HTML entities duplo-escape.** Páginas individuais vêm com `&amp;ccedil;`
  no lugar de `ç` — passar por `html.unescape()` duas vezes ou regex
  pre-processor antes de gravar em `texto_cru`.
- **Meta-refresh em vez de 301.** `/{id}` retorna `<meta http-equiv="refresh">`
  para `/{id}/{slug}`. Scraper deve seguir o meta-refresh; alternativa é gerar
  o slug a partir do título e fazer fetch direto na URL completa.
- **`publico_alvo` ausente.** FAPESP não declara público-alvo como campo
  estruturado nem na listagem nem na página individual. Filtro PME apoia-se
  em `modalidade` (PIPE, Auxílio Regular etc.) — vocabulário em
  `wikis/_pme_filter.md` precisa cobrir aliases FAPESP-específicos.
- **`data_limite` em ISO.** Bronze emite `yyyy-mm-dd`; o schema canônico
  (§4.1 WIKI.md) exige `dd/mm/yyyy`. `wiki_schema.iso_to_br_date` converte.
- **`fluxo_continuo == True` significa "sem prazo fixo" (programa contínuo).**
  Não confundir com "encerrado". §7.1 WIKI.md trata sem prazo + status ABERTA
  como vigente.
