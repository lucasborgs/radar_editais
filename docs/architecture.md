# Arquitetura — Radar de Editais

Visão de sistema para um Staff AI Engineer. O foco é onde a inteligência mora,
que modelo faz o quê, e como isso é medido — não o CRUD/auth/frontend.

> **Estado de implementação.** Os diagramas retratam a **arquitetura atual em produção**.
> O runtime LangGraph com checkpointer Postgres e Store semântico está **implementado e em produção**
> (migração completa — ver [`docs/components/agents/langgraph-migration.md`](components/agents/langgraph-migration.md) para o histórico).

---

## 1. Data plane — Bronze → Hipergrafo → Chunks

Multi-fonte com adapters por fonte; a descoberta web é uma "torneira" que passa
por gate humano antes de tocar o grafo.

```mermaid
flowchart TB
  subgraph BRONZE["Bronze (raw)"]
    FINEP["FINEP · Liferay API"]
    FAPESP["FAPESP"]
    FAPESC["FAPESC"]
    WEB["Web / Descoberta<br/>core.opportunity_discovery"]
  end

  WEB -->|"torneira"| STAGING["Staging + gate humano<br/>discovery não escreve no KG"]
  STAGING --> NORM
  FINEP --> NORM
  FAPESP --> NORM
  FAPESC --> NORM

  NORM["Normalizers por fonte (adapter pattern)"] --> HEX["hyper_extractor.py<br/>extração N-ária: 12 nós / 10 arestas<br/>2+ chamadas LLM estruturadas por edital<br/>(nós → arestas → merge dedup)"]
  HEX --> HG[("Hipergrados individuais<br/>data/knowledge_graph/hypergraphs/{id}.json")]

  HG --> EMB["embedder.py<br/>text-embedding-3-small (1536d)<br/>embeds nós Edital/Tema/Tecnologia/Aplicação"]
  EMB --> CACHE[("ecosystem_embeddings.npz<br/>cacheados por hash do texto")]

  HG --> CHK["chunk_edital · chunker.py (Art./§)"]
  CHK --> CTX["Contextual Retrieval<br/>core/contextual_retrieval.py"]
  CTX --> CHKEMB["embedder.py<br/>text-embedding-3-large (1536d)"]
  CHKEMB --> VEC[("edital_chunks<br/>pgvector + tsvector/BM25<br/>para RAG na escrita")]
```

---

## 2. AI core (query time) — retrieval + matching + escrita

```mermaid
flowchart TB
  Q["Query / CompanyProfile<br/>(localStorage)"]

  subgraph RET["Retrieval · retriever.py"]
    HYDE["HyDE · gera pseudo-doc via LLM<br/>hyde=True (default)"]
    DENSE["Dense · pgvector"]
    SPARSE["Sparse · BM25 (rank_bm25)"]
    HYDE --> DENSE
    DENSE --> RRF["RRF merge · fts_weight=0.5"]
    SPARSE --> RRF
    RRF --> BOOST["primary_boost 1.5 + metadata_boost"]
    BOOST --> RERANK["rerank · cross-encoder mmarco-mMiniLMv2<br/>fallback gpt-4o-mini / RRF puro"]
    RERANK --> TOPK["top-k chunks"]
  end

  subgraph MATCH["Matching · HypergraphMatch"]
    DIR["Empresa → embed perfil<br/>mesmo embedder do ecossistema"]
    ECO["Ecossistema → ecosystem_embeddings.npz<br/>(nós Tema/Tecnologia/Aplicação<br/>de todos os hipergrados)"]
    DIR --> COS["cosseno numpy on-demand<br/>nós empresa × nós ecossistema"]
    ECO --> COS
    COS --> MARGIN["marginsum por edital<br/>Σ(max(cosseno) − threshold)<br/>threshold 0.55 · piso min_aggregate"]
    MARGIN --> RANK["ranking por afinidade<br/>sem estágio LLM no match core"]
  end

  Q --> RET
  Q --> MATCH

  TOPK -.->|"RAG · retrieve_chunks"| WS

  subgraph EXPLORE["Descoberta · ExploreAgent"]
    EA["ExploreAgent · 3 rotas<br/>factual → reasoning → agent"]
    EA --> TOOLS["tools:<br/>resolve_graph_nodes<br/>neighborhood<br/>(lê hipergrados direto)"]
  end

  subgraph FRONT["⚡ Frontend · estado local"]
    FE["profile_diff → DiffCard<br/>✓ Aceitar → isRadarReady()<br/>acumula perfil no localStorage"]
  end

  EA -.->|"responde + profile_diff"| FRONT
  FRONT -.->|"isRadarReady()"| Q

  MATCH -.->|"edital_id<br/>usuário clica 'Começar proposta'"| WS

  subgraph WRITE["Escrita · runtime agêntico"]
    WS["WritingSession → LangGraph (agent_graph)<br/>RAG via retrieve_chunks<br/>scope = [edital_id]"]
    WS -->|"turn_count=0 + sections vazias"| FT["_first_turn_with_generation()<br/>batch 8 seções + descrição do usuário<br/>retorna draft completo de uma vez"]
    FT -.->|"background"| CKL["ChecklistService · 3 passes paralelos<br/>compliance · qualidade · completude<br/>compliance_flags inline na resposta de /writing/turn"]
    WS -->|"turnos seguintes"| CKL
    WS -->|"save_draft (force=False)"| SCOP["scope_classifier.py · GPT-4o-mini<br/>classifica correção: cosmética vs. conceitual<br/>se conceitual → ripple_suggestion"]
    SCOP -->|"depth≤1 (D9)"| WS
  end
```

---

## 3. Runtime agêntico — LangGraph

`StateGraph` ReAct com checkpointer Postgres durável, `interrupt()` nativo para
human-in-the-loop, memória cross-session via Store, e telemetria nativa. O
isolamento multi-tenant usa `thread_id`/namespace por workspace (migrou de RLS) —
o risco #1, ainda gated por um leak test cross-workspace com Postgres real (não rodado).

```mermaid
flowchart TB
  subgraph GRAPH["agent_graph.py — StateGraph ReAct"]
    A["agent"] -->|tool_calls| T["tools (ToolNode)"]
    A -->|no tool_calls| E(["END"])
    T -->|continue| M["manage_memory<br/>🗑️ RemoveMessage GC"]
    T -.->|reflect_pending| R["reflect"]
    T -->|max_steps| E
    R --> M
    M --> A
  end

  subgraph STATE["🔄 Pointer Reference — InjectedState('documents')"]
    DOC["state['documents']<br/>dict[chunk_id → full_text]"]
    SE["search_edital"] -->|writes text| DOC
    RE["read_exact_chunk"] -->|reads text| DOC
  end

  subgraph BYP["🚀 save_draft — Critic bypass + Scope classifier"]
    SD["save_draft(title, content, force)"] -->|"force=True (batch)"| P["set_section_content ✓"]
    SD -->|"force=False"| C["run_critic (subagent)"]
    C -->|approved| SC["scope_classifier (GPT-4o-mini)<br/>cosmético? → segue<br/>conceitual? → ripple_suggestion"]
    C -->|rejected| FIX["issues → model revises"]
    SC --> P
  end

  STATE -.->|carried by all nodes| GRAPH
  BYP -.->|tool| T

  GRAPH --> CKPT["AsyncPostgresSaver<br/>checkpointer durável<br/>interrupt() → request_user_info"]
  GRAPH --> STORE["PostgresStore<br/>memória cross-session<br/>(projeção read-only dos reflection_insights)"]
  GRAPH --> TEL["telemetria<br/>langfuse.langchain.CallbackHandler"]

  CKPT --> ISO["isolamento multi-tenant<br/>RLS → thread_id / namespace por workspace"]:::risk
  STORE --> ISO
  classDef risk fill:#fde,stroke:#c33,stroke-width:1px;
```

---

## 4. Eval + jobs assíncronos — o loop que mede

```mermaid
flowchart LR
  subgraph EVAL["Harness unificado · core/eval"]
    REG["registry.py · 11 SUITES"]
    REG --> S["matching · rag · writing · extraction<br/>profile_extractor · reranker · structurer · compliance_monitor · focus_group"]
    S --> H["Suite.task roda pipeline REAL<br/>evaluators reusam core/*_eval.py"]
    H --> OUT{"LANGFUSE_* setado?"}
    OUT -->|sim| LF["Langfuse Experiment<br/>scores comparáveis entre commits"]
    OUT -->|não| LOCAL["eval_results/*.json"]
    LF --> GATE["GATE de commit / CI"]
    LOCAL --> GATE
  end

  subgraph JOBS["Procrastinate · core/tasks.py"]
    J1["chunk_edital"]
    J2["enrich_content / embed_content"]
    J3["reflect_workspace / synthesize_patterns"]
    J4["run_daily_etl · 03:00 UTC"]
    J5["discover_opportunities · 04:00 UTC"]
  end

  GATE -.->|"protege merges"| JOBS
```

---

## Sinais que um Staff nota (e são reais no código)

| Sinal | Onde |
|---|---|
| Similaridade puramente geométrica no match | HypergraphMatch: marginsum sobre cosseno numpy — sem LLM, sem pesos heurísticos, sem Pandas |
| Modelo por tradeoff explícito | `llm_router` fast/pro/auto; produtores em free-tier (gemini), agregado em gpt-4o-mini |
| RAG não-ingênuo | BM25+dense via RRF com `fts_weight=0.5` justificado pelo corpus; contextual retrieval; dedup por source; HyDE ativo por default com fallback silencioso |
| Mede antes de mergear | 11 suítes no registry, gate de commit, Langfuse Experiments |
| Abstração de troca | `kg_store.py` como seam único; embedder/reranker/LLM parametrizáveis por env |
| Human-in-the-loop durável | LangGraph `interrupt()` + checkpointer Postgres; isolamento via namespace por workspace |
