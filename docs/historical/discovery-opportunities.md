# Spec — Descoberta de Oportunidades de Inovação (item 2.2)

> **Objetivo:** capturar editais/chamadas/desafios de fomento que surgem espalhados pelo Brasil (FAPs estaduais, ministérios, SEBRAE, fundações, programas pontuais) — fontes desconhecidas e de formato arbitrário — via descoberta web diária, e integrá-los ao KG para que entrem nos fluxos de match e escrita.
> **Base:** branch a criar a partir de `ict-mapping`/`main`. **Data:** 2026-06-03. Reusa `core/web_search.py` (DeepResearch Fase A).
> **Pré-leitura:** [spec_deepresearch.md](deep-research-design.md) (infra de busca), docs/domain/schema.md §10 (adicionar fonte), §12.3/§12.4 (Documento Canônico / Source Adapter), §5.9 (tema vocab), §5.10.

## Princípio: integrar o KG, com dimensão de confiança (não com gate bloqueante)

Match (KGMatchService/HybridMatch leem `index.json`) e escrita (WritingSession
ancora em `edital_chunks` de um edital do índice) **só consomem o KG**. Logo,
oportunidade que não chega ao KG é invisível ao produto — **a descoberta tem que
integrar o KG**. Um gate humano *antes* do KG viraria gargalo no volume diário e
deixaria descobertas inúteis na fila.

Resolução: a descoberta entra no KG **já**, marcada como `verificacao=provisorio`
+ proveniência (URL). Fica matchável/writable na hora, **rotulada como não
verificada**. A revisão humana é camada **não-bloqueante**: sobe a confiança
(`provisorio → verificado`) ou rejeita/remove. "Humans decide" continua — mas como
camada de confiança, não como porta que estrangula o fluxo. O KG é **honesto**
sobre o que é verificado; a poluição é mitigada por status + proveniência +
grounding, não por bloqueio.

## A premissa que NÃO é verdade (e o desenho assume isso)

Na discussão inicial assumiu-se "a camada de extração é agnóstica". **Não é.** Os
extractors atuais são por-fonte (FINEP Liferay API, FAPESP HTML). Extrair edital
estruturado de página arbitrária é problema de LLM, com precisão variável — por
isso o resultado entra **provisório** (não verdade firmada), e fontes recorrentes
**graduam** para extractor determinístico (volta ao modelo do `SCRAPER_REGISTRY`
/ Source Adapter §12.4), entrando aí como `verificado`.

## Decisões travadas

| # | Tópico | Decisão |
|---|--------|---------|
| 1 | Entrada no KG | **Provisório no KG, não-bloqueante** — `verificacao` ∈ {provisorio, verificado} + proveniência |
| 2 | Faseamento | **A:** descoberta+extração → KG provisório → **B:** verificação humana + exposição diferenciada → **C:** cron + graduação |
| 3 | Reuso | `web_search` (Tavily) + `run_agent`; ingestão via **bronze → build_knowledge_graph** (reaproveita pme_filter, §5.9, §5.10, edital_id) |
| 4 | Confiança | FINEP/FAPESP e fontes graduadas = `verificado`; web aberta = `provisorio` |
| 5 | Keywords/alvos | Vocabulário de busca no **doc** (tunável sem deploy) |

---

## Fase A — Descoberta + extração → KG provisório (agêntica)

### Problema
Não sabemos quais fontes existem nem quando publicam; e o que for achado precisa
chegar ao KG para servir a match/escrita.

### Design
Task diária (procrastinate, ao lado de `run_daily_etl` em [core/tasks.py](../../core/tasks.py)):
1. **Descoberta:** para cada query do vocabulário (novo bloco no doc — ex.: "edital
   inovação 2026 FAP", "chamada fomento PME", "desafio de inovação aberto"),
   `web_search` (Tavily) → candidatos (URL, título, snippet).
2. **Triagem (agente):** classifica *é oportunidade de fomento real e vigente?*
   (s/n) + agência aparente. Reusa `web_search`/`fetch_url`.
3. **Dedup:** contra o KG (`index.json` por URL/título) e um **ledger de descoberta**
   (URLs já ingeridas) — para o crawl diário não re-extrair o mesmo (extração é cara).
4. **Extração assistida:** `fetch_url` + LLM extrai os campos do schema comum.
5. **Ingestão provisória:** grava no **bronze** como fonte de descoberta
   (`source` = agência detectada, ou `web`), com `verificacao=provisorio` +
   `source_url`. `build_knowledge_graph` o processa como qualquer fonte — passa por
   **pme_filter**, canonicalização de tema (§5.9), `requires_ict_partner` (§5.10),
   `edital_id` prefixado. **Resultado: edital provisório vivo no KG.**

### Schema (docs/domain/schema.md)
- Novo campo do edital `verificacao` ∈ {`provisorio`, `verificado`} (§5.11 novo).
  Default `verificado` para FINEP/FAPESP (fontes confiáveis); `provisorio` para
  descobertas. Propriedade/tag, não nó (§6.1.1). Index-derived (não nos campos
  herdados das wiki pages — evita regerar wiki pages).
- Proveniência: reusa `link` do edital (a URL da fonte).

### Arquivos
`supabase/migrations/016_discovery_ledger.sql` (ledger de dedup: url, dedup_key,
first_seen_at, ingested_edital_id), `core/ingestion/opportunity_discovery.py` (descoberta +
triagem + extração), `core/tasks.py` (task `discover_opportunities`), doc do
vocabulário + docs/domain/schema.md §5.11, `pipeline/build_knowledge_graph.py` (carregar
`verificacao` do bronze), `docs/domain/sources/web.md` (bronze_mapping da fonte genérica).

### Critérios de aceitação
- Task descobre → tria → extrai → ingere editais `provisorio` no `index.json`.
- Editais FINEP/FAPESP permanecem `verificado` (default não quebra o existente).
- Dedup: URL já ingerida não re-extrai nem duplica.
- Provisório passa no `test_wiki_schema_consistency` (campo válido).

---

## Fase B — Verificação humana (não-bloqueante) + exposição diferenciada

### Problema
Provisório é útil mas não confiável; o produto precisa ser honesto sobre isso e
dar ao humano o controle de confiança.

### Design
- **Match:** editais `provisorio` aparecem **rotulados** ("descoberta não
  verificada — confira a fonte"), possivelmente em balde separado ou ranqueados
  abaixo dos verificados.
- **Escrita:** writable, mas o agente/UI avisa que a fonte é não-verificada; o
  grounding anti-fabricação ([spec_robustez](robustez-match-escrita.md))
  continua valendo. **Caveat:** provisório extraído só de snippet tem texto pobre
  → grounding fraco; quando possível, puxar o documento real do edital para os
  `edital_chunks` (chunk_edital), senão a escrita fica rasa.
- **Verificação:** UI lista provisórios; humano **verifica** (`→ verificado`) ou
  **rejeita** (remove do bronze/KG). Não-bloqueante: o provisório já estava vivo.

### Arquivos
`backend/api.py` (GET provisórios, POST verificar/rejeitar), `core/kg_match_service.py`
+ `core/hybrid_match_service.py` (expor/ranquear por `verificacao`),
`core/writing_session.py` (aviso de fonte não-verificada), frontend (rótulo + tela
de verificação).

### Critérios de aceitação
- Match distingue provisório de verificado (rótulo/bucket).
- Verificar muda `verificacao` sem re-ingestão; rejeitar remove do KG.
- Escrita sobre provisório emite aviso; grounding inalterado.

---

## Fase C — Recorrência + graduação

### Design
- **Recorrência:** agendar `discover_opportunities` diária (cron procrastinate,
  como `run_daily_etl`); idempotente via ledger de dedup.
- **Graduação:** fonte recorrente de formato estável ganha **extractor
  determinístico** dedicado, entra no `SCRAPER_REGISTRY`/Source Adapter (§12.4) e
  passa a ingerir como **`verificado`** — sai do funil agêntico (ruidoso/caro)
  para o pipeline confiável. O agente de descoberta fica para a cauda longa.

### Critérios de aceitação
- Crawl diário agendado e idempotente.
- Processo de graduar fonte recorrente → extractor próprio documentado.

---

## Decisões a confirmar (antes de implementar)

- **`source` dos descobertos:** por-agência detectada (recomendo — alimenta a
  graduação da Fase C) com fallback `web`.
- **Modelo de triagem/extração:** triagem é alto volume (modelo barato, ex.: Gemini
  já usado no ETL); extração pede modelo capaz.
- **Política de ranqueamento de provisório no match:** bucket separado vs penalidade
  no score do HybridMatch. (Afina na Fase B.)

## Riscos

- **Precisão/recall da descoberta:** web aberta é ruidosa. Mitigação: triagem
  agêntica + status provisório (não polui como verdade) + verificação humana;
  medir taxa de verificação/rejeição e ajustar queries.
- **Extração de página arbitrária imperfeita** (premissa falsa): mitigada por
  entrar provisório + graduar fontes estáveis para extractor próprio.
- **Confiança do KG:** o risco deixa de ser "lixo entra" e vira "provisório mal
  rotulado". Mitigação: rótulo claro em match/escrita + grounding + proveniência.
  **Nunca** deixar provisório se passar por verificado.
- **Escrita rasa sobre provisório de snippet:** puxar documento real para chunks
  quando possível; senão o grounding já degrada com aviso de contexto insuficiente.
- **Custo do crawl diário LLM:** cap de queries/candidatos; triagem barata;
  extração só em candidatos que passaram na triagem e não estão no ledger.

## Faseamento (resumo)

| Fase | Entrega | Confiança |
|------|---------|-----------|
| **A** | Descoberta+extração → editais `provisorio` no KG (matcháveis) | provisório, rotulado |
| **B** | Verificação humana não-bloqueante + exposição diferenciada em match/escrita | provisório → verificado |
| **C** | Cron diário + graduação de fontes recorrentes para extractor próprio | graduada → verificado |
