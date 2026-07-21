# FINEP — Schema específico

Estende [docs/domain/schema.md](../schema.md). Só documenta o que diverge do schema global.

---

## 1. Identidade

```yaml
source:
  key: finep
  display_name: FINEP
  graph_tag: finep   # tag base de todo nó deste vault
```

---

## 2. Coleta

```yaml
scraper:
  module: radar.pipeline.extractors.finep
  portal: "http://www.finep.gov.br/chamadas-publicas"

paths:
  bronze_dir: bronze_data/finep_raw
  pdfs_dir:   silver_data/finep/pdfs/{edital_id}
  bronze_glob: "*.json"
```

Bronze é JSON com lista de chamadas. Dedup por `link` (primeiro arquivo lido em ordem alfabética vence).

---

## 3. Mapeamento bronze → schema comum

Campos do JSON bronze FINEP e sua correspondência no schema do índice (§4 do docs/domain/schema.md). Não há necessidade de adaptador — scraper já emite nos nomes esperados.

```yaml
bronze_mapping:
  chamada_id:       id
  titulo:           title
  status:           status        # normalizado depois (§7.2 docs/domain/schema.md)
  prazo_envio:      deadline
  data_publicacao:  pub_date
  link:             link
  tema:             themes_raw    # split por [;,|], canonicalizado em themes via radar.domain.vocabulary
  publico_alvo:     publico_alvo  # split por [;,|]
  fonte_recurso:    fonte_recurso # split por [;,] + normalização para siglas canônicas (§5.4 docs/domain/schema.md)
```

Campo derivado:

```yaml
derived_fields:
  n_pdfs: "len(glob('{pdfs_dir}/*.pdf'))"
```

---

## 4. PDFs

Cada edital tem um diretório `silver_data/finep/pdfs/{id}/` com PDFs baixados.

### 4.1 Fonte dos PDFs (prioridade)

1. Diretório físico `pdfs_dir`.
2. Fallback: campo `pdf_texts` embutido no JSON bronze mais recente (dict `{nome: texto}`).

### 4.2 PDFs a ignorar

Documentos auxiliares sem conteúdo normativo. Match case-insensitive por substring no nome do arquivo (sem extensão).

```yaml
skip_keywords:
  - minuta
  - declaracao
  - carta_de_manifestacao
  - apresentacao
  - resultado
  - oficio
  - telas
  - guia
  - orientacoes_para_apresentacao
  - orientacoes_para_despesas
  - relatorio_parcial
  - ebook
  - agravo
  - aviso
```

> **`agravo`** descarta peças judiciais (ex.: `Decisão_TRF1_-Agravo_de_instrumento.pdf`) que o portal anexa à chamada — conteúdo não-normativo e ruído no RAG. `faq` e `tabela_com_requisitos` foram REMOVIDOS da lista: medição com golden de proveniência independente (NotebookLM, 2026-06-15) mostrou que o FAQ oficial é o alvo de retrieval mais alinhado a perguntas de fundador (estilo P&R), e descartá-lo derrubava o recall. A skip-list filtra o **não-normativo-e-inútil**, não o **não-normativo-mas-útil-pra-RAG**.
>
> **`telas`/`guia`** descartam screenshots do sistema FAP (`Telas_do_FAP.pdf`) e guias de preenchimento (`Guia_Rápido.pdf`) — auxiliares de UI, não-normativos. **Forma de escrita:** keywords são substring **sem acento e sem gap de token** — `telas` (não `telas_fap`, que não casa `Telas_do_FAP`) e termos sem acento (o filename é acentuado; o match no adapter é exato e não-dobrado, então `declaracao` só pega `Declaração` se o consumidor dobrar acento — ver `src/radar/core/retrieval/hyper_extractor._deaccent`).

### 4.3 Autoridade e versões

`Regulamento`, `Edital` e seus documentos rerratificados pertencem a famílias
normativas versionadas. O adapter deve preservar todas as versões no Documento
Canônico com `family`, `revision`, `published_at`, `source_url` e
`authority_state`, mas somente a versão de maior revisão/data fica `vigente` e
segue para Silver, gold e `edital_chunks`. As anteriores ficam `superseded`
para auditoria.

O reconhecimento não depende da palavra `edital` no filename: nomes reais como
`Regulamento_Conhecimento_Brasil` e `3_Rerratificacao` pertencem à mesma família.
FAQ é uma família auxiliar própria; sua versão mais recente pode complementar,
mas nunca prevalece sobre regulamento vigente em conflito. Data e revisão
extraídas do filename são sinais determinísticos; em empate, a data oficial da
URL/bronze deve prevalecer quando disponível.

Exceções em que o portal não inclui a data no filename ficam versionadas no
manifesto abaixo, auditado contra a página oficial da chamada:

```yaml
document_authority_overrides:
  "3ª_rerratificação_-_Regulamento_rerratificado.pdf":
    published_at: "2026-02-09"
    revision: 3
  "3ª_Rerratificação_do_Anexo_1.pdf":
    published_at: "2026-02-09"
    revision: 3
```

---

## 5. Metadados enviados ao prompt

Campos do edital expostos à LLM como `{metadata}` no prompt (§8.1 docs/domain/schema.md). Serializados como JSON com indent=2, ensure_ascii=False.

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

Usa o prompt global (§8.1 docs/domain/schema.md) sem overrides.

---

## 7. Grafo Obsidian

```yaml
graph_overrides:
  node_base_tag: finep
  subfolder_default: radar-editais
  folders:
    editais:      editais
    temas:        temas
    fontes:       fontes
    publico:      publicos
    mecanismos:   mecanismos
    subprogramas: subprogramas
    trl:          trl
  home_title: "📡 Radar de Editais — FINEP"
```

Estrutura de pastas gerada no vault:

```
{vault}/{subfolder}/
├── HOME.md
├── editais/{id}.md
├── temas/{slug}.md
├── fontes/{slug}.md
├── publicos/{slug}.md
├── mecanismos/{slug}.md
├── subprogramas/{slug}.md
└── trl/{slug}.md
```

Limpeza: antes de re-exportar, todos os `.md` das 7 subpastas são deletados.

---

## 8. Gotchas específicos

- **Status do portal é pouco confiável.** Muitos editais aparecem como "Desconhecido" mesmo vigentes. Regra de normalização (§7.2 docs/domain/schema.md) recupera isso via prazo futuro.
- **`chamada_id`** nem sempre vem preenchido. Fallbacks: regex `/chamadapublica/(\d+)` no link, depois último segmento do link.
- **`fonte_recurso` mistura quatro dimensões numa string.** O portal FINEP emite strings como `"FNDCT – Subvenção Econômica ; BNDES"`, `"FINEP/FNDCT"`, `"CT-Infra"` ou `"Petrobras – Cláusula de PD&I, conforme Resolução nº 918/2023 da ANP."`. O normalizador aplica três extractors em cascata sobre a mesma string bruta (após split por `[;|]`):
  1. Fontes canônicas (§5.4 docs/domain/schema.md) — `FNDCT`, `BNDES`, `Petrobras` etc.
  2. Subprogramas (§5.6 docs/domain/schema.md) — `CT-Infra`, `MOVER` etc.
  3. Drop-list de modalidades (§5.7 docs/domain/schema.md) — `subvenção`, `reembolsável`, `recursos próprios` etc.

  Cada extractor pode emitir múltiplas matches da mesma string (sem early-break no regex). Fragmentos que não casam com nenhum são descartados silenciosamente. Contexto regulatório não-financiador (ex.: ANP em editais de Cláusula PD&I da Petrobras) não vira nó do grafo — fica apenas em `key_facts` / `key_requirements` da wiki page, extraído pela LLM dos PDFs.
- **`publico_alvo`** pode vir com qualificadores específicos (ex.: `"ICT (Pública ou Privada) credenciada na ANP"`). Normalizador colapsa para canônico (§5.5 docs/domain/schema.md); detalhe específico preserva-se em `key_requirements` da wiki page.
- **`tema`** frequentemente tem vírgula dentro do nome composto (ex.: `"Agricultura, agronegócio e saúde animal"`, `"Petróleo, gás e etanol"`). Por isso o splitter usa apenas `[;|]`, nunca vírgula. A canonicalização (em `src/radar/domain/vocabulary.py`) mapeia cada variação completa para o tema canônico.
- **Wiki pages armazenam inherited fields congelados no momento da geração** (§4.1 docs/domain/schema.md). Quando a ingestão do índice muda (nova canonicalização, novo vocabulário), as wiki pages existentes ficam defasadas em `fonte_recurso`, `publico_alvo`, `themes`, `subprogramas`. O exporter Obsidian resolve isso **mesclando** card + índice a cada export: synthesized fields vêm do card (§4.2), inherited fields + `subprogramas` vêm do índice (autoridade). Não é necessário reprocessar com `--skip-cache` só por mudanças de vocabulário.
