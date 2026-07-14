# Spec — v3: Dissociação Match / KG / RAG

Status: **supersedida** por [`v3-unified.md`](v3-unified.md) · 2026-07-07 ·
registro da proposta inicial, não descrição do runtime atual.

---

## Motivação

O hypergraph atual (hipergrados v2) tenta servir 4 funções simultaneamente:

| Função | Como o hypergraph faz hoje | Problema |
|--------|---------------------------|----------|
| **Match** | Embedding de conceitos extraídos (tema/tecnologia/aplicacao) vs embedding do perfil | Conceitos genéricos inflam score; extração LLM perde informação do texto original |
| **Navegação (agente)** | BFS cross-source sobre nós e arestas | Funciona, mas o BFS é caro e não-determinístico |
| **Catálogo** | Leitura de `hypergraphs/ict.json` etc. | Impoverished: perde campos dos curados |
| **Modelo canônico (extraction)** | LLM força texto do edital a caber em 3 tipos de nó + 11 arestas | Frágil; custo de LLM; conceitos genéricos |

A tensão principal: **match precisa de fidelidade ao texto original** (perder o mínimo de informação); **KG precisa de abstração navegável** (generalizar para conectar entidades). O hypergraph tenta fazer os dois com o mesmo artefato e falha em ambos.

### Diagnóstico dos curados vs hypergraph

Análise das fontes curadas mostrou que o hypergraph **descarta informação** em relação ao dado original:

| Fonte | Dado curado | Hypergraph | Perda |
|-------|-------------|------------|-------|
| EMBRAPII (ICT) | `about` ~2000 chars + `areas_raw` | descrição ~1 linha | ~95% do texto |
| Investidor | `tese` + `tese_keywords` + `setores` | descrição ~105 chars | `tese_keywords`, `setores`, `anti_tese` |
| Programa | `descricao` (~134 chars, LLM) | fields similares | — (ambos frágeis) |
| Edital FINEP | `descricao` 1372 + `raw_html` 4016 + `pdf_texts` | conceitos extraídos | Texto original inteiro |

O match atual roda sobre embedding de conceitos extraídos — que são uma **perda em relação ao hypergraph**, que é uma **perda em relação ao curado**, que é uma **perda em relação ao HTML original**. Quatro camadas de perda.

---

## Objetivos

1. **Match usa texto real, não conceitos extraídos** — cosseno sobre silver chunks + reranker
2. **KG vira navegação pura** — entidades + relacionamentos para o agente explorar
3. **Filtros determinísticos antes do match** — WHERE clause barra o impossível (UF, prazo, estágio, ticket)
4. **Extrair só o necessário** — LLM leve para campos determinísticos ausentes; sem conceitos abstratos
5. **Hyper-Extract mantido para KG** — mas sua saída não alimenta o score de match

---

## Premissas adotadas

- **Match é chunk-to-chunk (MaxSim):** documentos da empresa não são colapsados em um vetor médio. São chunkados independentemente (mesmo pipeline do silver). O RAG calcula MaxSim real: `max_{chunk_empresa} max_{chunk_oportunidade} cosine(e, o)`. Se um único chunk técnico da empresa der match 0.85 com um parágrafo do edital, o edital é retornado.
- **Hyper-Extract continua:** extrai entidades e arestas do silver chunks para o KG. Não é removido.
- **Silver é a camada universal de texto parseado:** bronze → parser agnóstico → silver (chunks). Vale para editais, ICTs, investidores, programas.
- **Gold é nova:** metadados determinísticos extraídos sem LLM (quando a fonte fornece) ou com LLM leve (campos ausentes).
- **WritingSession não muda:** já usa RAG sobre silver chunks. Continua igual.
- **Memória do agente (PostgresStore + checkpointer) é ortogonal:** a migração não afeta estados de memória cross-session.
- **Programas e investidores.json:** não são scraped (são hardcoded + LLM). Por ora, `about` (ICT), `tese` (investidor) e `descricao` (programa) viram textos no silver. Scrapers oficiais são itens futuros.
- **Entity resolution cross-source** continua: Hyper-Extract já resolve `(type, name)` entre hipergrados.

---

## Arquitetura

```
                              ┌──────────────────────────────────────────┐
                              │              Bronze                      │
                              │  (raw scraped: HTML, PDF, JSON, API)     │
                              └────────────────┬─────────────────────────┘
                                               │ parser agnóstico
                                               ▼
                              ┌──────────────────────────────────────────┐
                              │              Silver                      │
                              │  (chunks estruturados com section_path)   │
                              │                                          │
                              │  Editais: structured_docs/{fonte}/{id}   │
                              │  ICTs: about + areas_raw (~2000 chars)   │
                              │  Investidores: tese + keywords (~300c)   │
                              │  Programas: descricao (~134 chars)       │
                              └──┬──────────────────────┬────────────────┘
                                 │                      │
                    ┌────────────┤                      ├────────────┐
                    ▼            ▼                      ▼            ▼
           ┌──────────────┐  ┌──────┐         ┌──────────────┐  ┌──────┐
           │ Gold         │  │ RAG  │         │ Gold         │  │ RAG  │
           │ (metadados   │  │      │         │ (metadados   │  │      │
           │  + filtros)  │  │      │         │  + filtros)  │  │      │
           └──────┬───────┘  └──────┘         └──────┬───────┘  └──────┘
                  ▼                                   ▼
           ┌──────────────┐                  ┌──────────────────┐
           │ Tabela       │                  │ Hyper-Extract    │
           │ entities     │                  │ → KG entidades   │
           │ + rels (SQL) │                  │   + arestas      │
           └──────────────┘                  └──────────────────┘
```

### Match: funil de 2 estágios

```
Perfil empresa (UF, estágio, faturamento, descrição textual + docs upados)
         │
         ▼
┌──────────────────────────────────────────────────────────────────┐
│ Stage 1 — Filtros determinísticos (Gold / SQL)                   │
│                                                                  │
│ SELECT e.* FROM entities e WHERE e.deadline >= now()             │
│   AND e.status IN ('aberta','ativo')                             │
│   AND (e.uf IS NULL OR e.uf = empresa.uf)                        │
│   AND e.estagio_alvo && empresa.estagio                          │
│   AND (e.ticket_max IS NULL OR e.ticket_max >= empresa.faturamento) │
│                                                                  │
│ Resultado: ~30-40 candidatos (vs 150+ sem filtro)                │
         │
         ▼
┌──────────────────────────────────────────────────────────────────┐
│ Stage 2 — Ranking semântico (RAG / pgvector)                     │
│                                                                  │
│ 1. Chunk documentos da empresa (mesmo pipeline do silver)        │
│ 2. pgvector MaxSim: `max_{chunk_empresa} cosine(chunk, silver)` │
│ 3. Reranker cross-encoder (opcional)                             │
│ 4. LLM verdict final sobre os chunks reais                       │
│                                                                  │
│ Resultado: top 5-10 ordenados por relevância                     │
└──────────────────────────────────────────────────────────────────┘
```

### KG topológico: tabelas

#### `entities`

| Campo | Tipo | Origem | Exemplo |
|-------|------|--------|---------|
| `id` | UUID | gerado | |
| `type` | enum | gold | `edital`, `ict`, `investidor`, `programa`, `investimento` |
| `name` | text | bronze | "CERTI" |
| `source` | text | bronze | `finep`, `embrapii`, `fapesp`, `curadoria` |
| `source_id` | text | bronze | "783", "ator:certi" |
| `description` | text | silver | silver text (~2000 chars) |
| `metadata` | jsonb | gold | `{"tese_keywords":["deep-tech"], "setores":["multissetorial"]}` |
| `status` | enum | gold | `aberta`, `encerrada`, `ativa`, `inativa` |
| `deadline` | date | gold | |
| `uf` | text | gold | "SP" |
| `ticket_min` | numeric | gold | 50000 |
| `ticket_max` | numeric | gold | 130000 |
| `estagio_alvo` | text[] | gold | `["seed", "serie-a"]` |
| `trl` | text[] | gold | |
| `curated` | bool | gold | true se fonte verificada |

#### `entity_relationships`

| Campo | Tipo | Exemplo |
|-------|------|---------|
| `id` | UUID | |
| `type` | enum | `parceria_com`, `operado_por`, `financia`, `credencia`, `subsequente_de`, `coinveste_com`, `abrange_tema` |
| `from_entity_id` | UUID FK | entity id |
| `to_entity_id` | UUID FK | entity id |
| `metadata` | jsonb | descrição opcional da relação |

### LLM extração: só metadados ausentes

```
Se campo já existe na fonte estruturada (API FINEP, HTML EMBRAPII, HTML Centelha):
  → vai direto pra gold sem LLM

Se campo está ausente (prazo, UF, ticket, TRL, estágio):
  → LLM leve extrai do silver, vai pra gold

Sem conceitos abstratos. Sem temas/tecnologias/aplicações canônicas.
Só campos binariamente verificáveis (certo/errado).
```

---

## Comparação: hoje vs novo

| Aspecto | Hoje (v2) | Novo (v3) |
|---------|-----------|-----------|
| **Match** | embedding perfil → cosseno → embedding conceitos extraídos | Stage 1 SQL → Stage 2 pgvector silver chunks |
| **KG** | 3 tipos nó + 11 arestas (Hyper-Extract) serve match + navegação | KG só navegação (Hyper-Extract); match não usa KG |
| **Filtros** | BFS no hypergraph (caro, não-determinístico) | WHERE clause SQL (indexado, determinístico) |
| **Extração LLM** | conceitos abstratos + arestas (frágil) | só campos determinísticos ausentes (prazo, UF, ticket) |
| **Camadas de perda** | bronze →...→ hypergraph → match (4 camadas) | bronze → silver → match (2 camadas) |
| **Textos RAG** | só editais (WritingSession) | editais + ICTs + investidores + programas |
| **Documentos cliente** | só perfil estruturado | perfil + docs upados em chunks → MaxSim |
| **WritingSession** | inalterada | inalterada |
| **Memória (LangGraph)** | PostgresStore + checkpointer | inalterada |

---

## O que morre / O que fica / O que é novo

### Morre

- `hypergraph_match.py` → `find_matching_editais()` via embedding de conceitos (live service, não o Hyper-Extract em si)
- `hybrid_match_service.py` — serviço híbrido legado (já parcialmente substituído)
- `_is_generic_concept()` / `_GENERIC_LABELS` — sem conceitos no match, sem necessidade
- `anti_class_verdict()` — sem conceitos canônicos
- `canonicalize.py` (uso no match) — conceitos canônicos não são mais superfície de match
- `match_tools.py` → `_company_nodes()` com `hyperextract` para match (Hyper-Extract continua só para KG)
- `curated_icts.json` — substituído pelo campo `curated` em `entities` (mais geral)
- BFS cross-source como mecanismo de descoberta para match (BFS continua para navegação do agente)

### Fica (adaptado)

- **Hyper-Extract** (`hyper_extractor.py`) — continua extraindo entidades + arestas do silver para o KG. Não é removido, só muda consumidor.
- **Silver chunks** (`silver/structured_docs/`) — mesmos, mas expandidos para ICTs/investidores/programas
- **WritingSession** — inalterado
- **ExploreAgent** — navegação via KG continua (BFS), mas match agora é Stage 1 + Stage 2
- **kg_store** — vira storage de entidades relacionais (tabelas Supabase)
- **embedder** (`core/retrieval/embedder.py`) — mesmo embedder, só que agora aplicado a silver chunks em vez de conceitos
- **pgvector** — mesmo índice, só que populado com silver chunks + chunks de ICT/investidor/programa
- **chunker** (`core/retrieval/chunker.py`) — mesmo chunker, expandido para novos tipos de entidade

### Novo

- **Tabela `entities`** (Supabase) — entidades unificadas (editais + ICTs + investidores + programas)
- **Tabela `entity_relationships`** (Supabase) — relacionamentos explícitos entre entidades
- **Pipeline Gold** — extração de metadados determinísticos (LLM leve para campos ausentes)
- **Stage 1** — filtro SQL sobre `entities` antes do match semântico
- **Stage 2** — reranker cross-encoder + LLM verdict sobre chunks reais
- **Ingestão de ICTs/investidores/programas no silver** — chunking + embedding
- **Scraper de programas** (futuro) — para substituir dados LLM não verificados

---

## Esquema de transição

### Fase 1 — Gold + tabelas (sem mudar match)

1. Criar tabelas `entities` + `entity_relationships` no Supabase
2. Pipeline gold: ler silver chunks, extrair metadados (LLM leve onde faltar)
3. Popular entidades a partir dos curados existentes (editais bronze + ICTs + investidores + programas)
4. Manter match v2 rodando em paralelo — medir cobertura de cada stage

### Fase 2 — Stage 1 (filtro SQL)

1. Conectar Stage 1 ao perfil empresa: WHERE clause sobre `entities`
2. Comparar recall do filtro vs BFS hypergraph (precisa ser igual ou melhor)
3. Feature flag: `MATCH_STAGE_1_ENABLED=true/false`

### Fase 3 — Stage 2 (RAG sobre silver)

1. Embed silver chunks de ICTs/investidores/programas no pgvector
2. Implementar Stage 2: pgvector cosseno sobre candidatos do Stage 1
3. Reranker + LLM verdict
4. Feature flag: `MATCH_ENGINE=v2|v3`

### Fase 4 — KG puro (navegação)

1. Migrar BFS do hypergraph (disco) para CTE SQL sobre `entity_relationships`
2. Adaptar `explore_tools.py` → `resolve_entity()` e `neighborhood()` usam SQL
3. Remover leitura de `hypergraphs/` para navegação (Hyper-Extract ainda escreve lá, mas vira produtor da tabela)
4. Feature flag: `KG_BACKEND=hypergraph|relational`

### Fase 5 — Limpeza

1. Remover `hypergraph_match.py` e dependências mortas
2. Remover `_GENERIC_LABELS`, `anti_class_verdict()`, `canonicalize.py` do match
3. Remover `match_tools.py` se não mais usado pelo agente
4. Remover feature flags
5. Scraper de programas (se decidido)

---

## Riscos e mitigações

| Risco | Mitigação |
|-------|-----------|
| **Programas.json é LLM puro, sem verificação** | Usar como está por ora (descricao ~134 chars); scraper posterior substitui |
| **6/17 investidores.json são ChatGPT não verificado** | Manter `verificado_em: None`; não usar metadados deles em filtros críticos |
| **Cobertura de metadados é incompleta** | LLM leve preenche lacunas; campos nulos são ignorados no WHERE (SQL IS NULL OR) |
| **Documentos grandes do cliente excedem limite do embedder** | Chunking reentrante (mesmo pipeline do silver) |
| **Regressão de recall durante transição** | Feature flags permitem A/B test, medir com golden existente |
| **Dependência de Hyper-Extract para navegação** | Hyper-Extract mantido; só a leitura do KG muda de disco → SQL |

---

## Decisões arquiteturais registradas

1. **Match não usa KG** — KG serve navegação do agente. Match usa texto real (silver).
2. **Hyper-Extract mantido** — extrai entidades e arestas do silver. Não é removido.
3. **Gold é nova camada** — metadados determinísticos extraídos sem LLM ou com LLM leve.
4. **Filtro antes de embedding** — Stage 1 SQL barra impossíveis antes do Stage 2 pgvector.
5. **Match é simétrico por MaxSim** — documentos da empresa são chunkados (mesmo pipeline silver). Match é `max_{i,j} cosine(chunk_empresa_i, chunk_oportunidade_j)`. Um parágrafo da empresa pode casar com um parágrafo do edital sem resumir a empresa inteira.
6. **Writing não muda** — RAG sobre silver chunks já funciona.
7. **Memória do agente é ortogonal** — PostgresStore + checkpointer não são afetados.
8. **Programas sem scraper** — por ora, `descricao` LLM entra como texto silver. Scraper é futuro.
9. **Feature flags em cada fase** — transição gradual sem quebrar produção.
10. **Documentos do cliente viram chunks** — projetos antigos, pitch decks, apresentações são parseados, chunkados e embeddados como vetores independentes. Não há colapso em vetor médio.
