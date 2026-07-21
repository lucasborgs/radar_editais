# Avaliação: Crawl4AI como extrator universal de oportunidades

**Status:** exploratório concluído — hipótese de extrator universal refutada · **Data:** 2026-07-15

## Contexto

O Discovery pipeline atual (`core/opportunity_discovery.py`) extrai dados de oportunidades via:

1. **Tavily web search** → URLs candidatas
2. **Triage LLM** → "é oportunidade?"
3. **Fetch página** → `_fetch_and_parse()` (BeautifulSoup, HTML estático, 12k chars)
4. **Extração LLM** → `_extract()` (1 chamada gpt-4o-mini, 6k chars do page text)
5. **Staging** → `discovered_opportunities` (status=pending, gate humano)

### Problemas conhecidos

| Problema | Impacto |
|---|---|
| Extração rasa (2-3 frases de descrição) | Gold recebe silver pobre → match_chunks fracos |
| Sem download de PDFs | Regulamento oficial não entra no pipeline |
| Sem identificação de seções | Perde estrutura (quem participa, cronograma, requisitos) |
| Sem JS rendering | Páginas SPA (React, Vue) são extraídas vazias |
| Gate humano limitado | Só aceita 1 link de PDF; múltiplos PDFs + HTML rico são perdidos |

### Fontes atuais

| Fonte | Tipo de extrator hoje |
|---|---|
| FINEP | Scraper dedicado (`pipeline/extractors/finep_api.py` + `pipeline/adapters/finep.py`) |
| FAPESP | Scraper dedicado (`pipeline/extractors/fapesp.py` + `pipeline/adapters/fapesp.py`) |
| FAPESC | Scraper dedicado (`pipeline/extractors/fapesc.py` + `pipeline/adapters/fapesc.py`) |
| FAPEMIG | **Nenhum** — só entra via Discovery (Tavily + extração rasa) |
| Programas | Curados manualmente em `data/silver/programas.json` |
| Demais fontes web | Discovery genérico (Tavily + extração rasa) |

### Hipótese

Crawl4AI (v0.9.1, OSS Apache 2.0) pode substituir **todos os extratores atuais** — dedicados e genéricos — com uma única chamada por URL, entregando:

- Markdown limpo (seções preservadas)
- Extração estruturada via LLM (schema definido)
- Detecção de PDFs
- JS rendering via Playwright
- Zero necessidade de scrapers por fonte

---

## Objetivo do teste

Verificar, com dados reais, se Crawl4AI extrai oportunidades com qualidade **igual ou superior** aos extratores atuais, em **todas as categorias de fonte**.

---

## URLs de teste (6)

| # | Nome | URL | Tipo fonte | Extrator atual |
|---|---|---|---|---|
| 1 | FAPEMIG Sede Compete Minas | `https://fapemig.br/oportunidades/chamadas-e-editais/fapemig-sede-compete-minas` | Discovery puro | `_extract()` LLM |
| 2 | FINEP Mais Inovação | `https://www.finep.gov.br/e/chamada-publica/222684/755376` | Scraper dedicado | API Liferay + pdfplumber |
| 3 | FAPESP Auxílio Inovação Regular | `https://fapesp.br/18067` | Scraper dedicado | Scraper API + adapter |
| 4 | FAPESC Chamada 37/2026 | `https://fapesc.sc.gov.br/edital-de-chamada-publica-fapesc-n-o-37-2026-programa-de-ciencia-tecnologia-e-inovacao-para-apoio-aos-grupos-de-pesquisa-da-udesc` | Scraper dedicado | Scraper WordPress + adapter |
| 5 | Programa Centelha | `https://programacentelha.com.br` | Catálogo curado | Curadoria manual |
| 6 | CONFAP Horizon Europe (pending) | `https://confap.org.br/pt/editais/49/horizon-europe` | Discovery pendente | `_extract()` LLM |

---

## Metodologia

### Setup

- **Branch:** `test/crawl4ai-eval` (a partir de `main`, sem tocar código produtivo)
- **Dependência:** `crawl4ai` (Apache 2.0) instalado no venv do projeto
- **Script:** `scripts/eval_crawl4ai.py` — standalone, sem importar módulos do projeto

### Pipeline de extração por URL

```
1. AsyncWebCrawler.arun(url)
   ├── Playwright renderiza a página (JS incluso)
   ├── Gera markdown limpo (raw + fit)
   └── Identifica links para PDFs

2. LLMExtractionStrategy (gpt-4o-mini, temp=0)
   └── Aplica schema de extração sobre o conteúdo

3. Salva resultado em JSON
```

### Schema de extração (adaptado do `_extract` atual + seções expandidas)

```python
{
    "titulo": "string",
    "prazo_envio": "string (dd/mm/yyyy ou '')",
    "publico_alvo": "string",
    "descricao": "string (2-3 frases)",
    "status": "ABERTA | ENCERRADA | ''",
    "opportunity_type": "edital | desafio | programa",
    "tema": ["string", ...],
    "tema_livre": ["string", ...],
    "secoes": {
        "resumo": "string",
        "descricao_completa": "string",
        "quem_pode_participar": "string",
        "cronograma": "string",
        "requisitos": "string",
        "categorias_financiamento": "string",
        "faq": "string",
    },
    "pdf_urls": ["string", ...],
}
```

### Termos de comparação

Para cada URL, registrar:

| Métrica | Descrição |
|---|---|
| `crawl_duration_ms` | Tempo total da chamada Crawl4AI |
| `markdown_len` | Tamanho do markdown em chars |
| `markdown_sections` | Nº de seções (cabeçalhos h1-h3) detectadas |
| `llm_extraction.titulo` | Título extraído |
| `llm_extraction.prazo_envio` | Prazo extraído vs prazo real (conferir na página) |
| `llm_extraction.publico_alvo` | Público-alvo extraído |
| `llm_extraction.secoes.*` | Seções extraídas (campos vazios = não detectado) |
| `llm_extraction.pdf_urls` | URLs de PDF encontradas |
| `llm_extraction.tema` | Temas mapeados para vocabulário canônico |
| `has_pdfs` | Boolean: páginas com PDFs foram detectadas? |
| `extraction_quality` | high (≥500 chars descricao + ≥3 seções) / medium / low |
| `js_rendered` | Boolean: página precisou de JS? (estimado) |

### Saída esperada

- `scripts/eval_crawl4ai_results.json` — resultados completos
- Resumo no stdout ao final da execução

---

## Critérios de aprovação

O Crawl4AI será considerado **viável para substituição** se, para ≥5 das 6 URLs:

1. **Título** extraído corretamente (match com o título real da página)
2. **Descrição** ≥500 chars (vs ~100-200 chars do `_extract` atual)
3. **Ao menos 3 seções** preenchidas em `secoes` (vs 0 do Discovery atual)
4. **PDFs detectados** quando houver (FINEP, FAPESC tipicamente têm)
5. **Tempo médio** ≤15s por URL
6. **Sem quebras** (exceções, timeouts, conteúdo vazio)

---

## Cronograma

| Passo | Duração |
|---|---|
| Criar branch + script | 30 min |
| Instalar dependências (1x) | 10 min |
| Executar teste (6 URLs) | ~5 min |
| Analisar resultados | 15 min |
| **Total** | **~1h** |

---

## Nota: Cenário A — Pipeline determinístico de multi-extração

> **Alternativa ao Crawl4AI, caso o teste mostre limitações.**

Se Crawl4AI não atender (ex.: conflito de dependências, latência alta,
custo de chamadas LLM extras, ou qualidade insuficiente), o plano B é
implementar um **multi-extractor determinístico** dentro do próprio
projeto, **sem agente ReAct** — apenas um pipeline fixo de 6 passos:

```
1. fetch_page(URL)                     → HTML completo        ← requests + bs4
2. extrair_secoes(HTML) → LLM temp=0   → [section, ...]       ← schema fixo
3. encontrar_pdfs(HTML)                 → [pdf_urls]           ← regex determinístico
4. download_pdfs(pdf_urls)              → [pdf_bytes]          ← safe_get
5. extrair_pdf_text(pdf_bytes)          → [pdf_text]           ← pdfplumber (já existe)
6. merge(secoes, pdf_texts)             → record rico          ← dict merge
```

**Características:**

- **Determinístico** — não há agente decidindo o que fazer; todos os 6
  passos executam em sequência fixa. O LLM entra apenas no passo 2,
  com temperature=0 e schema JSON rígido (mesmo risco de
  não-determinismo do `_extract` atual).
- **Sem novas dependências** — reusa `requests`, `beautifulsoup4`,
  `pdfplumber` e `core/web/fetch.py` já existentes.
- **Código novo:** ~150 linhas em `core/opportunity_discovery.py` ou
  módulo separado `core/discovery_extractor.py`.
- **Mesmo padrão de saída** que o Crawl4AI testaria: seções
  estruturadas + PDFs + campos extraídos.
