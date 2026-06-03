# Spec — Descoberta de Oportunidades de Inovação (item 2.2)

> **Objetivo:** capturar editais/chamadas/desafios de fomento que surgem espalhados pelo Brasil (FAPs estaduais, ministérios, SEBRAE, fundações, programas pontuais) — fontes desconhecidas e de formato arbitrário — via descoberta web diária, e canalizá-los para o KG **sob revisão humana**.
> **Base:** branch a criar a partir de `ict-mapping`/`main`. **Data:** 2026-06-03. Reusa `core/web_search.py` (DeepResearch Fase A).
> **Pré-leitura:** [spec_deepresearch.md](spec_deepresearch.md) (infra de busca), WIKI.md §10 (adicionar fonte), §12.3/§12.4 (Documento Canônico / Source Adapter), §5.9 (tema vocab), §5.10.

## Princípio que comanda tudo: não automatizar o KG

O KG é a **memória semântica global compartilhada** — fonte da verdade do produto.
Deixar um agente escrever nele a partir da web aberta viola "humans decide" e
arrisca poluir a base para todos os usuários. Logo: **descoberta e triagem podem
ser agênticas; a entrada no KG é humana-no-loop.** Nada entra no KG sem aprovação.

## A premissa que NÃO é verdade (e o desenho assume isso)

Na discussão inicial assumiu-se "a camada de extração é agnóstica". **Não é.** Os
extractors atuais são por-fonte (FINEP Liferay API, FAPESP HTML). Extrair edital
estruturado de página arbitrária e desconhecida é problema de LLM de verdade, com
precisão variável. O desenho trata isso explicitamente: extração é **assistida +
revisada**, e fontes que se provam recorrentes **graduam** para extractor
determinístico (volta ao modelo do `SCRAPER_REGISTRY` / Source Adapter §12.4).

## Decisões travadas

| # | Tópico | Decisão |
|---|--------|---------|
| 1 | Faseamento | **A:** descoberta+triagem (sem KG) → **B:** extração humana-no-loop → **C:** recorrência+graduação |
| 2 | Staging | Candidatos vivem numa tabela **`discovered_opportunities`** (Supabase), **fora do KG** |
| 3 | Reuso | `web_search` (Tavily) + `run_agent` para descoberta; ingestão final via **bronze → build_knowledge_graph** (reaproveita pme_filter, §5.9, edital_id) |
| 4 | Gate do KG | Nada entra no KG sem **aprovação humana** de candidato extraído |
| 5 | Keywords/alvos | Vocabulário de busca no **doc** (tunável sem deploy), não no `.py` |

---

## Fase A — Descoberta + triagem (agêntica, zero escrita no KG)

### Problema
Não sabemos quais fontes existem nem quando publicam. Busca manual diária é
inviável.

### Design
Task diária (procrastinate, ao lado de `run_daily_etl` em [core/tasks.py](../core/tasks.py)):
1. **Descoberta:** para cada query do vocabulário (§novo no doc — ex.: "edital
   inovação 2026 FAP", "chamada fomento PME", "desafio de inovação aberto"),
   chama `web_search` (Tavily). Acumula candidatos (URL, título, snippet).
2. **Triagem (agente):** para cada candidato, um `run_agent`/1-shot classifica:
   *é uma oportunidade de fomento real e vigente?* (s/n) + extrai sinais leves
   (agência/fonte aparente, prazo se visível). Reusa `web_search`/`fetch_url`.
3. **Dedup:** contra o KG (`index.json` — URL/título) e contra
   `discovered_opportunities` já vistos.
4. **Persistência em staging:** candidatos aprovados na triagem entram em
   `discovered_opportunities` com `status='pending'`. **Não tocam o KG.**

Vocabulário de busca: bloco no doc (`wikis/_discovery.md` ou WIKI.md §) com
queries + (opcional) lista de domínios-alvo (FAPs, ministérios). Tunável.

### Arquivos
`supabase/migrations/016_discovered_opportunities.sql` (tabela: id, url, title,
snippet, detected_source, status, dedup_key, first_seen_at, raw payload),
`core/opportunity_discovery.py` (descoberta+triagem), `core/tasks.py` (task
`discover_opportunities`), doc do vocabulário, `backend/api.py` (GET fila).

### Critérios de aceitação
- Task roda, busca, triа, deduplica e grava candidatos em staging.
- **Nenhuma escrita no KG/index.json** nesta fase (teste por ausência).
- Dedup: candidato já no KG ou já visto não duplica.
- Vocabulário de queries vem do doc.

---

## Fase B — Extração humana-no-loop → KG

### Problema
Candidato confirmado precisa virar edital estruturado no KG — mas extração de
página arbitrária é imperfeita e o KG não aceita lixo.

### Design
1. **Extração assistida:** para um candidato `pending`, `fetch_url` + LLM extrai
   os campos do schema comum (title, deadline, themes→§5.9, publico, etc.). Saída
   vai para `status='extracted'` com os campos propostos — **não** para o KG.
2. **Revisão humana:** UI lista candidatos extraídos; o humano corrige/aprova ou
   rejeita. Aprovar = **ação de decisão** (humans decide).
3. **Ingestão:** ao aprovar, o edital entra no **bronze** como uma fonte de
   descoberta (`source` = agência detectada, ou genérico `web`), com `edital_id`
   prefixado ([core/edital_id.py](../core/edital_id.py)). `build_knowledge_graph`
   o processa como qualquer outro — passa por **pme_filter**, canonicalização de
   tema (§5.9) e ganha `requires_ict_partner` (§5.10) de graça.

Isso é o ponto-chave: a entrada no KG **reusa todo o pipeline existente**; a
descoberta só alimenta o bronze, com um humano no meio.

### Arquivos
`core/opportunity_extraction.py` (LLM → campos), `core/tasks.py` (task
`extract_opportunity`), `backend/api.py` (POST aprovar/rejeitar → bronze),
frontend (tela de revisão), possível `wikis/web.md` (bronze_mapping da fonte
genérica).

### Critérios de aceitação
- Candidato aprovado vira entry no `index.json` via bronze→build (não por escrita
  direta).
- Rejeição não deixa resíduo no KG.
- Edital ingerido respeita schema (passa no `test_wiki_schema_consistency`).

---

## Fase C — Recorrência + graduação

### Design
- **Recorrência:** agendar `discover_opportunities` diária (cron procrastinate,
  como `run_daily_etl`).
- **Graduação:** fonte que aparece com regularidade e formato estável (ex.: uma
  FAP estadual específica) ganha **extractor determinístico** dedicado e entra no
  `SCRAPER_REGISTRY` / Source Adapter (§12.4) — sai do funil agêntico, ruidoso e
  caro, para o pipeline confiável. O agente de descoberta fica para a cauda longa.

### Critérios de aceitação
- Crawl diário agendado, idempotente (dedup robusto entre execuções).
- Documentado o processo de graduar uma fonte recorrente para extractor próprio.

---

## Decisões a confirmar (antes de implementar)

- **Staging: tabela Supabase vs arquivo** em `bronze_data/discovery/`. Recomendo
  tabela (UI de revisão, status, dedup por query SQL).
- **`source` dos descobertos:** genérico `web` vs slug por agência detectada.
  Recomendo por-agência quando detectável (alimenta a graduação da Fase C), com
  fallback `web`.
- **Modelo de triagem/extração:** Gemini (já usado no ETL) vs OpenAI. Triagem é
  alto volume (barato); extração pede modelo capaz.

## Riscos

- **Precisão/recall da descoberta:** web aberta traz muito ruído. Mitigação:
  triagem agêntica + gate humano; medir taxa de aprovação e ajustar queries.
- **Extração de página arbitrária é imperfeita** (a premissa falsa). Mitigação:
  humano-no-loop sempre; graduar fontes estáveis para extractor próprio.
- **Poluição do KG** (o risco maior): **eliminado por design** — só entra via
  aprovação humana + pipeline de bronze com pme_filter.
- **Custo do crawl diário LLM:** cap de queries/candidatos por execução; triagem
  com modelo barato; `fetch_url`/extração só em candidatos confirmados.
- **Dedup cross-execução:** `dedup_key` estável (URL normalizada + título) para o
  crawl diário não re-enfileirar o mesmo candidato.

## Faseamento (resumo)

| Fase | Entrega | Gate |
|------|---------|------|
| **A** | Descoberta+triagem → staging (`discovered_opportunities`) | feed de candidatos para revisão; **zero KG** |
| **B** | Extração assistida + aprovação humana → bronze → KG | candidato aprovado vira edital no índice |
| **C** | Cron diário + graduação de fontes recorrentes | crawl idempotente; processo de graduação documentado |
