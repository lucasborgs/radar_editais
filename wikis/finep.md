# FINEP — Schema específico

Estende [WIKI.md](../WIKI.md). Só documenta o que diverge do schema global.

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
  module: pipeline/extractors/finep.py
  portal: "http://www.finep.gov.br/chamadas-publicas"

paths:
  bronze_dir: bronze_data/finep_raw
  pdfs_dir:   silver_data/finep/pdfs/{edital_id}
  bronze_glob: "*.json"
```

Bronze é JSON com lista de chamadas. Dedup por `link` (primeiro arquivo lido em ordem alfabética vence).

---

## 3. Mapeamento bronze → schema comum

Campos do JSON bronze FINEP e sua correspondência no schema do índice (§4 do WIKI.md). Não há necessidade de adaptador — scraper já emite nos nomes esperados.

```yaml
bronze_mapping:
  chamada_id:       id
  titulo:           title
  status:           status        # normalizado depois (§7.2 WIKI.md)
  prazo_envio:      deadline
  data_publicacao:  pub_date
  link:             link
  tema:             themes_raw    # split por [;,|], canonicalizado em themes via domain.vocabulary
  publico_alvo:     publico_alvo  # split por [;,|]
  fonte_recurso:    fonte_recurso # split por [;,] + normalização para siglas canônicas (§5.4 WIKI.md)
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
  - faq
  - apresentacao
  - resultado
  - oficio
  - telas_fap
  - orientacoes_para_apresentacao
  - tabela_com_requisitos
  - orientacoes_para_despesas
  - relatorio_parcial
```

---

## 5. Metadados enviados ao prompt

Campos do edital expostos à LLM como `{metadata}` no prompt (§8.1 WIKI.md). Serializados como JSON com indent=2, ensure_ascii=False.

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
  node_base_tag: finep
  subfolder_default: radar-editais
  folders:
    editais: editais
    temas:   temas
    fontes:  fontes
    publico: publicos
  home_title: "📡 Radar de Editais — FINEP"
```

Estrutura de pastas gerada no vault:

```
{vault}/{subfolder}/
├── HOME.md
├── editais/{id}.md
├── temas/{slug}.md
├── fontes/{slug}.md
└── publicos/{slug}.md
```

Limpeza: antes de re-exportar, todos os `.md` das 4 subpastas são deletados.

---

## 8. Gotchas específicos

- **Status do portal é pouco confiável.** Muitos editais aparecem como "Desconhecido" mesmo vigentes. Regra de normalização (§7.2 WIKI.md) recupera isso via prazo futuro.
- **`chamada_id`** nem sempre vem preenchido. Fallbacks: regex `/chamadapublica/(\d+)` no link, depois último segmento do link.
- **`fonte_recurso`** frequentemente vem como string livre com múltiplas fontes concatenadas (ex.: "FNDCT – Subvenção Econômica; BNDES"). Normalização extrai siglas conhecidas e preserva strings não reconhecidas.
