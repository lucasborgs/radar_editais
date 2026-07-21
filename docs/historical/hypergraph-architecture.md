# Spec — Arquitetura Hipergrado

Status: **histórica, substituída** por [`v3-unified.md`](../specs/v3-unified.md) ·
implementada em 2026-06-30 e aposentada pela migração gold v3.

> **Atualização pós-implementação (2026-06-30 — fechamento):** Sprints 0, 1, 2 e 3 implementados. A implementação **divergiu da spec em pontos-chave** — ver a seção [Previsto → Realizado](#previsto--realizado) abaixo. O texto original abaixo dela é o **plano**; a seção registra o que de fato foi feito e por quê.

---

## Previsto → Realizado

Registro das divergências entre o plano e a implementação, com justificativa. As linhas marcadas **PENDENTE** continuam como planejado (ainda não feitas).

| Área | Previsto (plano) | Realizado | Por quê divergiu |
|---|---|---|---|
| **Ranking do match** | path search empresa→aresta→edital, ordenado pela melhor aresta; threshold 0.80 → 0.60 | **marginsum**: `affinity = Σ(cosseno − threshold)` sobre as arestas do edital + piso `min_aggregate`; threshold de aresta **0.55** | O gate F3 mostrou que rankear pela MELHOR aresta deixa nós **boilerplate** do eco («PROPOSTA»/«prototipagem») soterrarem o match temático real (empresa de IA não achava editais de IA). Agregar evidência (somar margens) faz o edital tematicamente denso vencer o spike único. recall@8 **0.80→0.88**, ruído controle 6→3. |
| **Especificidade (IDF)** | "peso por especificidade (IDF)" como mitigação dos temas amplos | **IDF testado e REPROVADO**; o problema foi resolvido pelo marginsum | No corpus de 32 editais a document-frequency não separa: «PROPOSTA» (idf 1.10) ≈ «Aprendizado de Máquina» (1.01) — boilerplate é parafraseado diferente por edital, não acumula df. IDF-weight até **piorou** o recall (0.80→0.70). Mean-centering = só reescala. |
| **Afinidade vs elegibilidade** | o subgrafo passado ao agente inclui `Requisito`/`Exclusão`; o agente raciocina elegibilidade JUNTO com ressonância, sem estágio separado | o match por hipergrado usava **só** `Tema`/`Tecnologia`/`Aplicação` (`AFFINITY_TYPES`); esta pendência histórica foi depois substituída pelo Stage 1 de elegibilidade do match gold v3 | `Mecanismo`/`Requisito` afogavam o sinal de conteúdo no desenho antigo. O gold v3 separou afinidade e elegibilidade em dados SQL estruturados; não há pendência atual de ler hiperarestas. |
| **Embeddings/arestas do eco** | embed por nó em build-time; `rebuild_synthetic_edges → graph/synthetic_edges.json` persistido | cosseno numpy **on-demand**; embeddings do eco cacheados em `graph/ecosystem_embeddings.npz` (hash dos textos); **sem** `synthetic_edges.json` | Sem FAISS (contorna o finding do `_CanonicalEmbeddings`). As arestas sintéticas são efêmeras/recomputáveis (mudam com threshold e com o perfil) — persistir não compensa; cachear os embeddings do eco já torna iterar grátis. |
| **Storage do eco** | grafo no kg_store/Postgres | `load_ecosystem_nodes()` lê do **disco** (`HYPERGRAPHS_DIR`) | **PENDENTE** — migrar p/ kg_store (disco do worker não é fonte de verdade durável). Débito conhecido. |
| **Gate / golden** | "calibrar threshold com 10-20 pares anotados" | golden de **afinidade** (`data/evaluation/golden/matching.json`): 8 empresas sintéticas × editais relevantes (juiz LLM independente + curadoria humana), guarda-chuva = neutro, **nós congelados** (`frozen_nodes`) → gate determinístico, sem LLM de extração | O golden mede AFINIDADE (não elegibilidade, que é camada à parte). Congelar os nós isola o MOTOR da variância de extração (a extração tem suíte própria). Suíte `matching` em `core/eval` (regra: não criar harness paralelo). |
| **Company corpus (Sprint 2)** | task re-scrapeia o site + parseia documentos | **mínimo**: corpus = só o que já está no DB (`workspaces.profile` + `content_items`), **sem** re-scrape | Fatia escolhida ("durable backend mínimo"). Re-scrape do site + banner de UI são fatias adicionais (não neste PR). |
| **Storage lado-empresa** | (implícito no kg_store) | módulo dedicado `core/services/company_corpus.py` + tabela `company_hypergraphs` | Dado **per-tenant** (vizinho de `content_library`, com RLS), não ecossistema-global — não pertence ao kg_store. |
| **Remoção do legado** | Sprint 1/3 removem `hybrid_match_service`, `radar_service`, `GraphService`, `wiki_schema`, `index.json`, `search_edital_trechos` | **Concluído.** Python removido, artefatos de dados removidos do disco. Todos os 5 consumidores runtime migrados para `hypergraph_catalog.get_edital()`. | A remoção completa só foi possível após migrar consumidores de wiki_page (writing_session, compliance, checklist, writing_tools, writing_router) para hypergraph + LLM fallback. |
| **`get_node_neighborhood`** | tool da Sprint 3 (ExploreAgent lê `hypergraphs/{id}.json` direto) | **Implementado** em `core/llm/agent_tools/explore_tools.py` (`resolve_graph_nodes` + `neighborhood`). Testado via `tests/test_get_node_neighborhood.py`. | — |

**Mantido conforme o plano:** schema Hyper-Extract de produção (12 nós / 10 arestas, `Aplicação` central); `find_matching_editais` como tool do ExploreAgent com chamada automática quando há perfil; nós da empresa pelo MESMO extractor/embedder do eco (canvas único); `completude_score` (fórmula da spec); Obsidian só visualização.

---

## Motivação

O sistema atual compara formulários: a empresa preenche campos estruturados (`tipo_entidade`, `trl`, `one_liner`) e o edital é comprimido num schema fixo (`mechanism`, `trl_range`, `key_requirements`). Isso funciona para matches óbvios mas quebra a filosofia original do produto — identificar compatibilidade entre **trajetórias**, não entre formulários.

Consequência concreta: uma empresa que desenvolve IA para triagem industrial nunca aparece em editais de agronegócio, porque `themes: ["agropecuária"]` não tem sobreposição léxica com `descricao: "indústria"`. A compatibilidade real (sensor + IA embarcada + eficiência operacional como princípio domain-agnostic) não é representada em nenhum dos dois lados.

Além disso, para o mesmo corpus de documentos de um edital o sistema executa hoje três processos distintos:
1. Extração LLM para wiki_page (schema fixo)
2. Chunking + embedding para RAG (WritingSession)
3. (proposto) Hyper-Extract para hipergrado

Esta spec elimina o processo 1. As responsabilidades do processo 1 são redistribuídas: RAG cobre WritingSession; propriedades do nó `Edital` no hipergrado cobrem display; elegibilidade é raciocínio do ExploreAgent sobre o subgrafo — sem extração intermediária.

---

## Premissas de negócio adotadas

- **Match via ExploreAgent:** não há serviço de match separado. `find_matching_editais` é uma tool do ExploreAgent. O agente chama automaticamente ao receber uma sessão com perfil disponível.
- **Elegibilidade é raciocínio do ExploreAgent, não pré-filtro determinístico:** constraints de elegibilidade (região, TRL, porte, mecanismo) são nós `Requisito` e `Exclusão` no hipergrado. O subgrafo passado ao agente sempre inclui esses nós — o agente raciocina sobre elegibilidade junto com ressonância, sem estágio separado.
- **Empresa começa com site + documentos (Option A):** o hipergrado da empresa é construído a partir do website e documentos opcionais (pitch, relatórios). O enriquecimento progressivo pelo uso (Option C) é débito planejado.
- **Completude comunicada ao usuário:** o sistema informa quando o corpus da empresa é ralo e incentiva adição de documentos.
- **WritingSession não muda funcionalmente:** já usa RAG como fonte principal; a remoção da wiki_page não afeta o fluxo de redação.
- **Obsidian é só visualização:** o export Obsidian é gerado pelo Hyper-Extract como artefato de visualização. Nenhum componente do sistema lê o vault Obsidian em runtime.

---

## Visão geral da nova arquitetura

```
Corpus edital (silver/*.jsonl — produzido pelo structurer a partir do bronze)
  │
  ├─► Hyper-Extract   →  hypergraphs/{id}.json
  │     └─► embed nós (text-embedding-3-small)  →  grafo unificado (build-time)
  └─► RAG pipeline    →  edital_chunks (inalterado)

Corpus empresa (site + docs)
  │
  └─► Hyper-Extract   →  company_hypergraphs (Supabase, por workspace)
        ├─► embed nós (text-embedding-3-small)  →  grafo unificado (build-time)
        └─► completude score → feedback ao usuário

Grafo unificado (build-time)
  │
  └─► cosine(nó_empresa, nó_edital) > threshold
        → aresta sintética [similar:score]
        → conecta corpora distintos no mesmo espaço

Match (match-time, sem LLM, sem score numérico)
  │
  └─► path search: empresa → arestas sintéticas → edital
        output: lista de caminhos por edital
                cada caminho = justificativa nativa do match

ExploreAgent
  │
  ├─► get_node_neighborhood()    →  perguntas factuais e semânticas (prazo, temas, requisitos)
  ├─► find_matching_editais()    →  match via path search
  └─► company_hypergraph         →  contexto inicial do workspace

WritingSession
  └─► RAG (inalterado)
```

---

## Modelo de dados

### Lado ecossistema

```
data/knowledge_graph/
  hypergraphs/{id}.json     # Hyper-Extract: {nodes: [...], edges: [...]}
                             # nó Edital carrega título, prazo, status, valor, fonte
                             # como propriedades — sem arquivo metadata separado
  graph/synthetic_edges.json # build-time: arestas sintéticas entre nós empresa↔ecossistema
                             # geradas por cosine(text-embedding-3-small) > threshold
                             # reconstruído quando company_hypergraph ou edital muda
```

Não há `metadata/{id}.json` separado. Todas as propriedades de display (título, prazo, status, valor, fonte) são extraídas pelo Hyper-Extract como propriedades do nó `Edital` — acessíveis via `get_node_neighborhood()` pelo ExploreAgent.

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
| `Edital` | A chamada pública como entidade (conecta tudo); carrega título, prazo, status, valor |
| `Programa` | PIPE, PAPPE, FNDCT — conexão transversal entre editais |
| `ICT` | Institutos, universidades — parceria obrigatória ou oportunidade |
| `Investidor` | FIPs, VCs — trilho investidor |
| `Tema` | Domínio de aplicação (saúde, agronegócio, manufatura 4.0) |
| `Tecnologia` | Capacidade técnica (IA, IoT, visão computacional) |
| `Aplicação` | **Caso de uso específico** (triagem, classificação de grãos, diagnóstico) — ponto de cruzamento cross-domain |
| `Mecanismo` | Subvenção, crédito, investimento |
| `Requisito` | Constraints de elegibilidade (TRL, porte, idade, região) e prazos |
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
- Iterar `DOMAIN_PROMPT`: exemplos concretos de `Aplicação`; instrução explícita para `Edital` como nó com propriedades de display (título, prazo, status, valor, fonte); instrução para extrair deadline/status do texto como propriedade do nó `Edital`
- Dedup case-insensitive: `node_key_extractor=lambda x: x.name.lower().strip()`
- Gate antes de Sprint 1: `Outro` < 5% (removido do schema, LLM deve forçar tipo adequado)

---

## Componentes alterados

### 1. Pipeline ETL (`core/ingestion/structurer.py` → `core/retrieval/hyper_extractor.py`)

**Input:** silver `*.jsonl` em `data/silver/structured_docs/{source}/` — produzido pelo `core/ingestion/structurer.py` a partir do bronze. Mesmo insumo que o RAG usa. Sem leitura de bronze diretamente.

**Remove:**
- `pipeline/etl_process.py` — inteiro (síntese LLM de wiki_pages)
- `pipeline/build_knowledge_graph.py` — inteiro (normalização bronze → index.json)
- `core/kg/wiki_schema.py` — inteiro (vocabulários, vigência, MATCH_FIELDS, extraction_prompt)

**Adiciona:**

```python
# core/retrieval/hyper_extractor.py
def run_hyper_extract(silver_jsonl_path: Path, edital_id: str) -> HypergraphResult:
    # lê silver JSONL → texto plano → Hyper-Extract com schema de produção
    # salva em data/knowledge_graph/hypergraphs/{edital_id}.json
    # usa make_client() de core/llm/llm_client.py (não langchain_openai)
    # usa embed_texts() de core/retrieval/embedder.py
```

**Ordenação no `run_daily_etl_task`:**
```
1. scrape → bronze
2. structurer → silver/*.jsonl          (já existe, inalterado)
3. run_hyper_extract → hypergraphs/{id}.json
4. embed_nodes (text-embedding-3-small) → embeddings por nó
5. rebuild_synthetic_edges → graph/synthetic_edges.json
6. chunk_edital_task (RAG — inalterado, já é procrastinate task)
```

### 2. Match pipeline

**Remove inteiramente:**
- `core/services/hybrid_match_service.py`
- `core/services/radar_service.py`
- `core/match_embeddings.py`
- `core/eligibility_producer.py`

**Filosofia:** match é graph path-based, sem LLM no loop, sem score numérico. `find_matching_editais` é uma tool do ExploreAgent.

**Build-time — arestas sintéticas:**
```python
def build_synthetic_edges(
    company_nodes: list[Node],
    ecosystem_nodes: list[Node],  # todos os nós de editais + ICTs + investidores
    threshold: float = 0.80,
) -> list[SyntheticEdge]:
    # cosine(embed(nó_empresa), embed(nó_edital)) > threshold
    # → SyntheticEdge(src=nó_empresa, dst=nó_edital, score=cosine, type="similar")
    # modelo: text-embedding-3-small (canônico do Radar)
    # salva em graph/synthetic_edges.json
```

**Match-time — path search (tool do ExploreAgent):**
```python
def find_matching_editais(
    company_hypergraph: Hypergraph,
    synthetic_edges: list[SyntheticEdge],
    ecosystem_hypergraphs: dict[str, Hypergraph],
) -> list[MatchResult]:
    # para cada edital: busca caminhos empresa → aresta_sintética → edital
    # MatchResult: edital_id + lista de caminhos encontrados
    # cada caminho = [(nó_empresa, aresta_sintética, nó_edital, edge_type_no_edital)]
    # editais sem caminho: excluídos
    # ordenação: por número de caminhos distintos (mais caminhos = mais conexões)
```

**Output:**
- Lista de editais com ao menos 1 caminho, ordenada por riqueza de conexões
- Sem score numérico exposto ao usuário
- Cada caminho disponível para exibição como justificativa

### 3. ExploreAgent (`core/services/explore_agent.py`)

**Remove:**
- wiki_page como fonte de contexto
- Carregamento de `wiki_store` para perguntas factuais
- `GraphService` — Obsidian é só visualização; runtime não lê o vault
- Tool `search_edital_trechos` (RAG) — ExploreAgent não usa RAG
- Referência a `search_edital_trechos` no `EXPLORE_AGENT_SYSTEM` prompt

**Adiciona:**
- Contexto inicial: `company_hypergraph` do workspace (quando disponível)
- Tool `find_matching_editais()` — match via path search (chamada automática com perfil)
- Tool `get_node_neighborhood(node_name, depth=1)` — retorna nós e arestas vizinhas do hipergrado; cobre tanto perguntas factuais (prazo, valor, status no nó `Edital`) quanto semânticas (temas, tecnologias, requisitos via arestas)

**Três rotas do ExploreAgent:**

| Rota | Pergunta exemplo | Fonte |
|---|---|---|
| Factual | "Qual o prazo do FINEP 783?" | `get_node_neighborhood("FINEP 783")` → propriedade do nó Edital |
| Semântica | "Quais tecnologias o FINEP 783 cobre?" | `get_node_neighborhood("FINEP 783")` → nós Tecnologia via aresta `abrange_tema` |
| Descoberta | "Onde minha empresa tem oportunidade real?" | `find_matching_editais` → caminhos → agente narra os matches |

### 4. UX — Match como artefato no chat

O ExploreAgent **é** o chat da homepage. Não há tela de radar separada com lista ranqueada. Os matches emergem dentro da conversa como cards interativos.

**Fluxo:**
```
Homepage (ExploreAgent já ativo)
  → sessão inicia com perfil disponível
  → agente chama find_matching_editais automaticamente
  → retorna cards de editais na conversa

[Card do edital]
  título + conexões identificadas
  ↳ IA embarcada → sensor IoT agrícola
  ↳ eficiência operacional → produtividade
  [Explorar]  [Escrever proposta]

"Explorar" → conversa continua com edital como contexto
"Escrever proposta" → abre WritingSession com edital selecionado
```

**Ordenação dos cards:** número de caminhos distintos (mais conexões = aparece primeiro). Sem score numérico exibido.

**Frontend — componentes removidos:**
- Cards `kind="radar"` no transcript da homepage
- Componente `BriefView` (score X/100 + match_dimensions com percentuais)
- `MatchingWeightsSection`

### 5. WritingSession (`core/services/writing_session.py`)

**Sem mudança funcional.** Já usa RAG como fonte principal. A remoção da wiki_page não afeta o fluxo de redação — `proposal_sections` e `key_facts` não eram consumidos pelo runtime da WritingSession, apenas pelo ExploreAgent (que agora usa o hipergrado).

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
| `pipeline/etl_process.py` | Remover inteiro |
| `pipeline/build_knowledge_graph.py` | Remover inteiro |
| `core/kg/wiki_schema.py` | Remover inteiro |
| `data/knowledge_graph/wiki/` | Deletar após Sprint 1 passar a suíte `matching` |
| `data/knowledge_graph/index.json` | Deletar (substituído pelo hipergrado) |
| `core/services/hybrid_match_service.py` | Remover inteiro |
| `core/services/radar_service.py` | Remover inteiro |
| `core/match_embeddings.py` | Remover |
| `core/eligibility_producer.py` | Remover |
| `core/services/graph_service.py` | Remover (Obsidian = visualização; runtime lê hipergrado diretamente) |
| Tool `search_edital_trechos` no ExploreAgent | Remover tool + referência no EXPLORE_AGENT_SYSTEM |
| Cards `kind="radar"` no frontend + `BriefView` | Remover — matches surfaçados como cards de caminhos no chat |
| `MatchingWeightsSection` | Remover |

---

## Plano de implementação

### Sprint 0 — Edital hypergraph (2-3 dias) · sem mudança user-facing

- [ ] Criar `core/retrieval/hyper_extractor.py`: wrapper Hyper-Extract com schema de produção (12 nós, 10 arestas, Literal Pydantic, dedup case-insensitive); usar `make_client()` de `core/llm/llm_client.py` e `embed_texts()` de `core/retrieval/embedder.py` (não langchain_openai)
- [ ] Iterar `DOMAIN_PROMPT`: exemplos de `Aplicação`; instrução explícita para `Edital` como nó com propriedades de display (título, prazo, status, valor); remover tipos depreciados (Subprograma, Outro, Empresa, Desafio, Região)
- [ ] Input: silver `*.jsonl` em `data/silver/structured_docs/` — mesma função `load_silver()` do experimento
- [ ] Adicionar step ao ETL: Hyper-Extract por edital → `hypergraphs/{id}.json`
- [ ] Gate: rodar Hyper-Extract no corpus completo; verificar `Outro` < 5%, `Edital` > 2% dos nós, prazo/status extraídos corretamente como propriedades do nó `Edital`
- [ ] Rodar hipergrado em paralelo ao sistema existente (não remover nada ainda)

### Sprint 1 — Match com hipergrado (3 dias + eval)

- [ ] Implementar `build_synthetic_edges()`: cosine entre embeddings de nós empresa × ecossistema; calibrar threshold com 10-20 pares anotados
- [ ] Implementar `find_matching_editais()` como tool do ExploreAgent: path search empresa → aresta_sintética → edital
- [ ] Integrar tool ao ExploreAgent: chamada automática quando perfil disponível, output como cards de caminhos
- [ ] Rodar suíte `matching` — gate obrigatório antes de remover componentes antigos
- [ ] Se eval passa: remover `hybrid_match_service.py`, `radar_service.py`, `match_embeddings.py`, `eligibility_producer.py`, `build_knowledge_graph.py`, `wiki_schema.py`

### Sprint 2 — Company corpus (4-5 dias)

- [ ] Migration Supabase: tabela `company_hypergraphs`
- [ ] Endpoint `POST /profile/corpus` + task `build_company_hypergraph_task`
- [ ] Completude score + UI feedback (banner no perfil)
- [ ] Integrar `company_hypergraph` ao `find_matching_editais`
- [ ] `rebuild_synthetic_edges` ao salvar company_hypergraph

### Sprint 3 — ExploreAgent + cleanup (3 dias)

- [x] Adicionar tool `get_node_neighborhood()` ao ExploreAgent (lê `hypergraphs/{id}.json` diretamente)
- [x] Remover tool `search_edital_trechos` + referência no `EXPLORE_AGENT_SYSTEM`
- [x] Remover `GraphService` (Obsidian = visualização apenas)
- [x] Remover wiki_page do contexto do ExploreAgent
- [x] ~Remover `data/knowledge_graph/wiki/` e `index.json`~ **Concluído** — todos os consumidores runtime migrados; arquivos removidos do disco. Dev scripts (export_to_obsidian) tratam ausência graciosamente.
- [x] Remover cards `kind="radar"` + `BriefView` + `MatchingWeightsSection` no frontend
- [x] Remover `core/kg/wiki_schema.py` → substituído por `core/kg/schema.py`
- [x] Avaliar latência do primeiro turno da WritingSession (sem impacto esperado — já usa RAG)

---

## Riscos e decisões em aberto

| Item | Risco | Mitigação |
|---|---|---|
| `Outro` em 22% dos nós no v2 | Poluição no grafo — nós sem tipo não contribuem para paths | Iterar `DOMAIN_PROMPT` antes do Sprint 1; gate: `Outro` < 5% no corpus completo |
| `Aplicação` subpopulado | Novo tipo sem exemplos — LLM classifica incorretamente | Adicionar 5+ exemplos concretos no `DOMAIN_PROMPT`; gate: `Aplicação` > 5% dos nós |
| Extração de prazo/status pelo LLM | Menos confiável que parsing estruturado do bronze | Validar no gate do Sprint 0: amostrar 10+ editais e verificar prazo/status extraídos |
| Threshold cosine das arestas sintéticas | Threshold alto → poucos matches; baixo → ruído | Calibrar empiricamente com 10-20 pares empresa/edital anotados antes de produção |
| Reconstrução de arestas sintéticas | Quando edital ou empresa atualiza, `synthetic_edges.json` fica stale | Invalidar e reconstruir arestas do workspace afetado; armazenar hash do hipergrado como chave de cache |
| Investidor com poucos nós | Corpus ralo → poucas arestas sintéticas → match fraco | Enriquecer corpus (portfólio + tese) antes de incluir no grafo |

---

## Itens para discussão PÓS-sprints (ativar o ativo N-ário)

Levantados em 2026-06-29: após as Sprints 0-1, o hipergrado é consumido só pelo
match e só pelos **nós** — as **hiperarestas** (o diferencial do modelo N-ário)
ainda não têm uso. Estes itens são o destino do hipergrado, mas ficam para
**DISCUSSÃO APÓS as sprints mapeadas** — não desviam o foco de finalizar
Sprint 1/F3 → Sprint 2 → Sprint 3.

1. **Match — viabilidade de percorrer hiperarestas nativas.** Hoje
   `find_matching_editais` casa por cosseno de tema e agrupa por `file_key`
   (proveniência), ignorando as `edges`. Discutir: **viabilidade e dificuldade**
   de percorrer as hiperarestas nativas (`exige`, `aplica_em`, `viabiliza`…) para
   uma justificativa estrutural ("este edital `exige` TRL 6", "Tecnologia X
   `aplica_em` Aplicação Y") em vez de só "casou por tema". Avaliar
   custo/complexidade vs. ganho de explicabilidade.

2. **WritingSession — ensemble hipergrado × RAG.** A spec mantém o Writing no RAG
   (§ fluxo). Discutir um **ensemble**, não substituição: o **hipergrafo garante a
   CONEXÃO** (estrutura — requisitos, exclusões, parcerias, relações
   tema/tecnologia/aplicação) e o **RAG garante a EXATIDÃO** (texto literal do
   edital). Como combinar as duas fontes no contexto do agente de escrita sem
   redundância nem conflito?

Ambos pressupõem Sprints 0-3 concluídas (extração + match + ExploreAgent lendo o
grafo direto). Ver a memória do projeto sobre hiperarestas subaproveitadas.
