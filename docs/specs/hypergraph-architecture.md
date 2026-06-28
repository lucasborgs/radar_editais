# Spec — Arquitetura Hipergrado

Status: **proposta** · 2026-06-28 · escopo: substituir match por schema fixo por match baseado em hipergrafos N-ários extraídos via Hyper-Extract; eliminar wiki_pages como camada intermediária de LLM; elegibilidade como raciocínio LLM, não pré-filtro determinístico

---

## Motivação

O sistema atual compara formulários: a empresa preenche campos estruturados (`tipo_entidade`, `trl`, `one_liner`) e o edital é comprimido num schema fixo (`mechanism`, `trl_range`, `key_requirements`). Isso funciona para matches óbvios mas quebra a filosofia original do produto — identificar compatibilidade entre **trajetórias**, não entre formulários.

Consequência concreta: uma empresa que desenvolve IA para triagem industrial nunca aparece em editais de agronegócio, porque `themes: ["agropecuária"]` não tem sobreposição léxica com `descricao: "indústria"`. A compatibilidade real (sensor + IA embarcada + eficiência operacional como princípio domain-agnostic) não é representada em nenhum dos dois lados.

Além disso, para o mesmo corpus de documentos de um edital o sistema executa hoje três processos distintos:
1. Extração LLM para wiki_page (schema fixo)
2. Chunking + embedding para RAG (WritingSession)
3. (proposto) Hyper-Extract para hipergrado

Esta spec elimina o processo 1. As responsabilidades do processo 1 são redistribuídas: RAG cobre WritingSession; metadados ETL cobrem display; elegibilidade vira raciocínio do LLM sobre o subgrafo — sem extração intermediária.

---

## Premissas de negócio adotadas

- **Match tem dois trilhos:** "óbvio" (empresa e edital falam a mesma linguagem — priorizado) e "descoberta" (compatibilidade de trajetória não-óbvia — explicada).
- **Elegibilidade é raciocínio LLM, não pré-filtro determinístico:** constraints de elegibilidade (região, TRL, porte, mecanismo) são nós `Requisito` e `Exclusão` no hipergrado. O subgrafo passado ao LLM sempre inclui esses nós — o LLM raciocina sobre elegibilidade junto com ressonância, sem estágio separado.
- **Empresa começa com site + documentos (Option A):** o hipergrado da empresa é construído a partir do website e documentos opcionais (pitch, relatórios). O enriquecimento progressivo pelo uso (Option C) é débito planejado.
- **Completude comunicada ao usuário:** o sistema informa quando o corpus da empresa é ralo e incentiva adição de documentos.
- **WritingSession não muda funcionalmente:** já usa RAG como fonte principal; a remoção da wiki_page não afeta o fluxo de redação.

---

## Visão geral da nova arquitetura

```
Corpus edital (PDFs + web)
  │
  ├─► ETL metadata    →  title, deadline, status, value, fonte
  ├─► Hyper-Extract   →  hypergraphs/{id}.json + FAISS global
  └─► RAG pipeline    →  edital_chunks (inalterado)

Corpus empresa (site + docs)
  │
  └─► Hyper-Extract   →  company_hypergraphs (Supabase, por workspace)
        └─► completude score → feedback ao usuário

Match
  │
  ├─► KNN (FAISS)      →  eixo "obviedade" (sobreposição de nós)
  └─► LLM subgraph     →  elegibilidade + ressonância + justificativa
        subgrafo = KNN(empresa, ecossistema) + todos Requisito/Exclusão/Entidade do edital

ExploreAgent
  │
  ├─► metadata/{id}.json         →  perguntas factuais (prazo, valor, status)
  ├─► get_node_neighborhood()    →  perguntas semânticas sobre o ecossistema
  └─► company_hypergraph         →  contexto inicial do workspace

WritingSession
  └─► RAG (inalterado)
```

---

## Modelo de dados

### Lado ecossistema

```
data/knowledge_graph/
  metadata/{id}.json        # ETL: title, deadline, status, value, fonte, pub_date
  hypergraphs/{id}.json     # Hyper-Extract: {nodes: [...], edges: [...]}
  faiss/                    # Índice global: embeddings de todos os nós do ecossistema
                            # (editais + programas + ICTs + investidores)
```

`metadata/{id}.json` substitui os campos de display da wiki_page atual. Não exige LLM.

`hypergraphs/{id}.json` — schema de nós e arestas (v2, ver seção Hyper-Extract).

### Lado empresa

```sql
CREATE TABLE company_hypergraphs (
  workspace_id  UUID PRIMARY KEY REFERENCES workspaces(id),
  nodes         JSONB    NOT NULL DEFAULT '[]',
  edges         JSONB    NOT NULL DEFAULT '[]',
  completude    FLOAT    NOT NULL DEFAULT 0,
  corpus_urls   TEXT[]   NOT NULL DEFAULT '{}',
  n_docs        INT      NOT NULL DEFAULT 0,
  updated_at    TIMESTAMPTZ DEFAULT NOW()
);
```

### Chunks RAG (inalterado)

```
Supabase: edital_chunks  →  sem alteração
```

---

## Schema Hyper-Extract (produção)

Derivado do experimento v2 (33 docs, 2.326 nós, 83% hiperedges 3+ membros) com ajustes de design.

**Nós — 12 tipos (Literal Pydantic):**
```
Fonte, Edital, Programa, ICT, Investidor,
Tema, Tecnologia, Aplicação,
Mecanismo, Requisito, Exclusão, Entidade
```

| Tipo | Papel no match |
|---|---|
| `Fonte` | FINEP, FAPESP, FAPESC — atribuição e agrupamento |
| `Edital` | A chamada pública como entidade (conecta tudo) |
| `Programa` | PIPE, PAPPE, FNDCT — conexão transversal entre editais |
| `ICT` | Institutos, universidades — parceria obrigatória ou oportunidade |
| `Investidor` | FIPs, VCs — trilho investidor |
| `Tema` | Domínio de aplicação (saúde, agronegócio, manufatura 4.0) |
| `Tecnologia` | Capacidade técnica (IA, IoT, visão computacional) |
| `Aplicação` | **Caso de uso específico** (triagem, classificação de grãos, diagnóstico) — ponto de cruzamento cross-domain |
| `Mecanismo` | Subvenção, crédito, investimento |
| `Requisito` | Constraints de elegibilidade (TRL, porte, idade, região) |
| `Exclusão` | Exclusões explícitas |
| `Entidade` | Tipos de organização elegíveis (empresa, startup, ICT, universidade) |

`Aplicação` é o tipo central para matches cross-domain: "triagem industrial" e "classificação de grãos" são a mesma operação em domínios diferentes — os embeddings ficam próximos no espaço vetorial, gerando o match não-óbvio.

**Arestas — 10 tipos (Literal Pydantic, N-ários):**
```
financia, exige, abrange_tema, aplica_em,
destina_a, exclui, parceria_com, pertence_a,
viabiliza, resolve
```

| Aresta | Semântica |
|---|---|
| `financia` | Fonte/Edital → Entidade (quem é financiado) |
| `exige` | Edital/Programa → Requisito (constraint de elegibilidade) |
| `abrange_tema` | Edital/Programa → Tema/Tecnologia (cobertura temática) |
| `aplica_em` | Tecnologia → Aplicação (como a tecnologia é usada) |
| `destina_a` | Edital → Entidade (quem pode aplicar) |
| `exclui` | Edital → Exclusão (exclusão explícita) |
| `parceria_com` | Edital → ICT/Investidor (parceria obrigatória ou oportunidade) |
| `pertence_a` | Edital → Programa → Fonte (hierarquia) |
| `viabiliza` | ICT/Tecnologia → Aplicação/Tema (capacidade habilita resultado) |
| `resolve` | Tecnologia/Aplicação → Desafio/Tema (ponte problema-solução) |

**Ajustes em relação ao v2 (pendências antes da produção):**
- Remover `Subprograma`, `Outro`, `Empresa`, `Desafio`, `Região` do schema Pydantic
- Adicionar `Aplicação` e renomear `Empresa` → `Entidade`
- Renomear `aplica_para` → `aplica_em`; remover `regulamenta`; adicionar `resolve`
- Iterar `DOMAIN_PROMPT`: exemplos concretos de `Aplicação`; instrução explícita para `Edital` como nó ("a chamada pública em si é um nó do tipo Edital")
- Dedup case-insensitive: `node_key_extractor=lambda x: x.name.lower().strip()`
- Gate antes de Sprint 1: `Outro` < 5% (removido do schema, LLM deve forçar tipo adequado)

---

## Componentes alterados

### 1. ETL pipeline (`etl_process.py` + `build_knowledge_graph.py`)

**Remove:**
- Função de geração de wiki_page (chamada LLM com schema fixo)
- Exportação para `data/knowledge_graph/wiki/*.json`

**Adiciona:**

```python
# após normalização silver, por edital:
def run_hyper_extract(corpus: str, edital_id: str) -> HypergraphResult:
    # wrapper de core/retrieval/hyper_extractor.py
    # salva em data/knowledge_graph/hypergraphs/{edital_id}.json
```

**Input do Hyper-Extract por edital:**
Função `_edital_text(ch)` já existente em `build_knowledge_graph.py` — coleta `titulo + descricao + texto_cru + pdf_texts` como string plana. Reuse direto.

**Ordenação no `run_daily_etl_task`:**
```
1. scrape → bronze
2. normalize → silver + metadata/{id}.json
3. run_hyper_extract → hypergraphs/{id}.json
4. rebuild_faiss_index (global, incremental se possível)
5. chunk_edital_task (RAG — inalterado, já é procrastinate task)
```

O passo 3 substitui a antiga geração de wiki_pages.

### 2. Match pipeline (`hybrid_match_service.py`)

**Remove:**
- Stage 1 temático (sobreposição de keywords)
- Stage 2 (LLM sobre wiki_page + perfil)
- Carregamento de MATCH_FIELDS da wiki_page

**Adiciona:**

**KNN — eixo "obviedade":**
```python
def knn_score(company_nodes: list[Node], faiss_index: FAISSIndex, k: int = 10) -> dict[str, float]:
    # para cada nó da empresa: top-k vizinhos no ecossistema
    # score por edital = |nós do edital em resultados| / |total nós do edital|
    # output: {edital_id: score_0_a_1}
```

**Construção do subgrafo — dois componentes obrigatórios:**
```python
def build_subgraph(edital_id: str, knn_nodes: list[Node]) -> Subgraph:
    # 1. nós semânticos: resultado do KNN + edges de 1 hop
    # 2. nós de elegibilidade: TODOS os nós do edital com tipo in
    #    [Requisito, Exclusão, Entidade] + suas edges imediatas
    # garante que o LLM sempre veja as constraints, mesmo sem sobreposição semântica
```

**LLM subgraph — elegibilidade + ressonância:**
```python
def llm_subgraph_score(
    company_subgraph: Subgraph,
    edital_subgraph: Subgraph,
) -> SubgraphResult:
    # prompt instrui explicitamente:
    # 1. avaliar elegibilidade (Requisito/Exclusão/Entidade) antes da ressonância
    # 2. retornar: elegivel (bool), score 0-10, justificativa, cluster_semantico
    # 3. se inelegível: indicar o que falta para se qualificar
```

**Dois trilhos de resultado:**

| Trilho | Critério | Apresentação |
|--------|----------|--------------|
| `obvio` | KNN score ≥ threshold_obvio AND elegível | Lista principal, ranqueada por score combinado |
| `descoberta` | KNN score < threshold_obvio AND ressonância ≥ threshold_res AND elegível | Seção "Descobertas", com justificativa obrigatória |
| excluído | ambos abaixo dos thresholds OU inelegível | Não exibido |

Thresholds: definidos empiricamente na suíte de eval `matching` antes de ir a produção.

**Score final:**
```
score_final = 0.5 * knn_score + 0.5 * (llm_ressonancia / 10)
```

Pesos ajustáveis via `matching_weights` (mesmo mecanismo atual).

### 3. ExploreAgent (`core/services/explore_agent.py`)

**Remove:**
- wiki_page como fonte de contexto
- Carregamento de `wiki_store` para perguntas factuais
- Tool `search_edital_trechos` (RAG) — ExploreAgent não usa RAG

**Adiciona:**
- Contexto inicial: `company_hypergraph` do workspace (quando disponível)
- Nova tool: `get_node_neighborhood(node_name, depth=1)` — retorna nós e arestas vizinhas do hipergrado via FAISS
- Nova tool: `get_edital_metadata(edital_id)` — lê `metadata/{id}.json` para perguntas factuais

**Três rotas:**

| Rota | Pergunta exemplo | Fonte |
|---|---|---|
| Factual metadata | "Qual o prazo do FINEP 783?" | `metadata/{id}.json` — sem LLM |
| Semântica | "Quais tecnologias o FINEP 783 cobre?" | `get_node_neighborhood("FINEP 783")` → nós Tecnologia via aresta `abrange_tema` |
| Descoberta | "Onde minha empresa tem oportunidade real?" | KNN empresa vs. ecossistema → subgrafo → LLM |

### 4. WritingSession (`core/services/writing_session.py`)

**Sem mudança funcional.**

O contexto estruturado da wiki_page que hoje é passado na abertura do thread (seções da proposta, key_facts) passa a ser recuperado via RAG no primeiro turno. Avaliar latência do primeiro turno em testes antes de remover o fallback.

---

## Componente novo: Company Corpus Pipeline

### Endpoints

```
POST /profile/corpus          # enfileira build_company_hypergraph_task
GET  /profile/corpus/status   # polling: {status, completude, n_nos, corpus_urls}
```

### Task `build_company_hypergraph_task(workspace_id)`

```
1. Busca workspace → pega url_site + documentos já enviados
2. Scrape via pipeline/adapters/web.py (WebScraper)
3. Parse documentos (reusa lógica de /profile/extract-from-document)
4. Concatena corpus
5. Roda Hyper-Extract → company_hypergraph
6. Calcula completude_score
7. Salva em company_hypergraphs
8. Notifica frontend (status polling ou SSE)
```

### Completude score

```python
def completude_score(n_nos: int, n_docs: int) -> float:
    return min(1.0, (n_nos / 50) * 0.6 + (n_docs / 3) * 0.4)
```

| Faixa | Mensagem ao usuário |
|-------|---------------------|
| < 0.3 | "Perfil ralo — adicione documentos para descobertas não-óbvias" |
| 0.3–0.7 | "Perfil básico — pitch deck ou relatório técnico enriquece os resultados" |
| > 0.7 | "Perfil rico" |

---

## Componentes removidos

| Componente | Ação |
|---|---|
| `data/knowledge_graph/wiki/` | Deletar após Sprint 1 passar a suíte `matching` |
| `core/kg/wiki_schema.py` | Remover |
| `pipeline/etl_process.py` — função de wiki_page | Remover função de síntese LLM |
| `HybridMatchService` Stage 1 (determinístico + temático) | Substituir por KNN |
| `HybridMatchService` Stage 2 | Substituir por LLM subgraph |
| `MATCH_FIELDS` no índice | Remover — não há mais campos estruturados de match |
| `search_edital_trechos` no ExploreAgent | Remover — ExploreAgent não usa RAG |

---

## Plano de implementação

### Sprint 0 — Edital hypergraph (2-3 dias) · sem mudança user-facing

- [ ] Criar `core/retrieval/hyper_extractor.py`: wrapper Hyper-Extract com schema de produção (12 nós, 10 arestas, Literal Pydantic, dedup case-insensitive)
- [ ] Iterar `DOMAIN_PROMPT`: exemplos de `Aplicação`; instrução explícita para `Edital` como nó; remover tipos depreciados
- [ ] Adicionar step ao ETL: Hyper-Extract por edital → `hypergraphs/{id}.json`
- [ ] Gerar `metadata/{id}.json` por edital
- [ ] Gate: rodar Hyper-Extract no corpus completo; verificar `Outro` < 5% e `Edital` > 2% dos nós
- [ ] Rodar hipergrado em paralelo à wiki_page (não remover nada ainda)

### Sprint 1 — Match com hipergrado (3 dias + eval)

- [ ] Substituir `hybrid_match_service.py`: implementar KNN via FAISS global
- [ ] Implementar `build_subgraph()`: KNN nodes + todos Requisito/Exclusão/Entidade do edital
- [ ] Implementar LLM subgraph: prompt com instrução explícita de elegibilidade-antes-de-ressonância
- [ ] Definir thresholds dos dois trilhos
- [ ] Rodar suíte `matching` — gate obrigatório antes de remover Stage 1/2 antigo
- [ ] Se eval passa: remover Stage 1, Stage 2 antigos e `MATCH_FIELDS`

### Sprint 2 — Company corpus (4-5 dias)

- [ ] Migration Supabase: tabela `company_hypergraphs`
- [ ] Endpoint `POST /profile/corpus` + task `build_company_hypergraph_task`
- [ ] Completude score + UI feedback (banner / barra de progresso no perfil)
- [ ] Integrar `company_hypergraph` ao match pipeline

### Sprint 3 — ExploreAgent + cleanup (3 dias)

- [ ] Adicionar tool `get_node_neighborhood()` ao ExploreAgent
- [ ] Adicionar tool `get_edital_metadata()` ao ExploreAgent
- [ ] Remover tool `search_edital_trechos` do ExploreAgent
- [ ] Remover wiki_page do contexto do ExploreAgent
- [ ] Remover `data/knowledge_graph/wiki/`
- [ ] Remover `core/kg/wiki_schema.py`
- [ ] Avaliar latência do primeiro turno da WritingSession sem wiki_page

---

## Riscos e decisões em aberto

| Item | Risco | Mitigação |
|---|---|---|
| Elegibilidade via LLM | LLM pode não capturar todas as constraints se o prompt não for explícito | Instrução explícita no prompt: "avalie Requisito/Exclusão/Entidade antes de qualquer score"; suite de eval com casos regionalizados e TRL-restrito |
| `Aplicação` subpopulado | Novo tipo sem exemplos no v2 — LLM pode classificar incorretamente | Adicionar 5+ exemplos concretos no DOMAIN_PROMPT; gate: `Aplicação` > 5% dos nós |
| WritingSession sem wiki_page | Primeiro turno mais lento (RAG substitui síntese pronta) | Medir antes de remover; se latência > 3s, pré-fetch no `writing-start` |
| Investidor com 20 nós | Match fraco no trilho investidor | Enriquecer corpus (portfólio + tese) antes de incluir no FAISS |
| FAISS global com 2K+ nós | Escala com novos editais | Começar global; particionar por fonte se p99 latência > 200ms |
| Thresholds dos dois trilhos | Sem dados históricos para calibrar | Definir empiricamente com 10-20 exemplos anotados antes de produção |
| `matching_weights` no DB | Pesos atuais calibrados para Stage1/Stage2, não para KNN/ressonância | Recalibrar após primeira rodada de eval |
