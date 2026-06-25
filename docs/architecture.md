# Arquitetura — Radar de Editais

Visão de sistema para um Staff AI Engineer. O foco é onde a inteligência mora,
que modelo faz o quê, e como isso é medido — não o CRUD/auth/frontend.

> **Estado de implementação.** Os diagramas retratam a **arquitetura atual em produção**.
> O runtime LangGraph com checkpointer Postgres e Store semântico está **implementado e em produção**
> (migração completa — ver [`docs/components/agents/langgraph-migration.md`](components/agents/langgraph-migration.md) para o histórico).

---

## 1. Data plane — medallion ETL → Knowledge Graph → chunks

Multi-fonte com adapters por fonte; a descoberta web é uma "torneira" que passa
por gate humano antes de tocar o grafo. Produtores LLM rodam em build-time.

```mermaid
flowchart TB
  subgraph BRONZE["Bronze (raw)"]
    FINEP["FINEP · Liferay API"]
    FAPESP["FAPESP"]
    FAPESC["FAPESC"]
    WEB["Web / Descoberta<br/>core.opportunity_discovery"]
  end

  WEB -->|"torneira"| STAGING["Staging + gate humano<br/>(discovery não escreve no KG)"]
  STAGING --> ADP
  FINEP --> ADP
  FAPESP --> ADP
  FAPESC --> ADP

  ADP["_NORMALIZERS por fonte<br/>build_knowledge_graph.py"] --> SILVER["Silver · etl_process.py"]
  SILVER --> KGB["build_knowledge_graph<br/>consolida index + wiki"]

  KGB --> PROD
  subgraph PROD["Produtores LLM (build-time)"]
    ELIG["eligibility_constraints<br/>gemini-2.5-flash / gpt-4o-mini"]
    MECH["mechanism / trl / objective<br/>infer + síntese"]
    ENR["enrichment (summary/themes)"]
  end

  PROD --> KG[("KG: index.json + wiki/*.json<br/>seam = core/kg/kg_store.py")]

  KG --> CHK["chunk_edital · chunker.py (Art./§)"]
  CHK --> CTX["Contextual Retrieval<br/>core/contextual_retrieval.py"]
  CTX --> EMB["embedder.py<br/>text-embedding-3-large (1536d)"]
  EMB --> VEC[("edital_chunks<br/>pgvector + tsvector/BM25")]
```

---

## 2. AI core (query time) — retrieval + matching dual + escrita

```mermaid
flowchart TB
  Q["Query / CompanyProfile<br/>(localStorage)"]

  subgraph RET["Retrieval · retriever.py"]
    HYDE["HyDE · gera pseudo-doc via LLM<br/>hyde=True (default)"]
    DENSE["Dense · pgvector"]
    SPARSE["Sparse · BM25 (rank_bm25)"]
    HYDE --> DENSE
    DENSE --> RRF["RRF merge · fts_weight=0.3"]
    SPARSE --> RRF
    RRF --> BOOST["primary_boost 1.5 + metadata_boost"]
    BOOST --> RERANK["rerank · cross-encoder mmarco-mMiniLMv2<br/>fallback gpt-4o-mini / RRF puro"]
    RERANK --> TOPK["top-k chunks"]
  end

  subgraph MATCH["Matching"]
    direction TB
    subgraph HM["HybridMatchService"]
      S1["Stage 1 determinístico (Pandas)<br/>elegibilidade·temático·TRL·mecanismo<br/>·contrapartida·elig. dura (região/idade/receita)"]
      S1 --> S2["Stage 2 LLM · gpt-4o-mini<br/>pesos: matching_weights (cache TTL 60s)"]
    end
    subgraph EM["EntityMatcher · Karpathy-style<br/>catálogo inteiro no prompt, 1 LLM call"]
      INV["catalog_investidores<br/>tese/estágio/setor · gpt-4o-mini"]
      PROG["catalog_programas<br/>estágio/elegibilidade/tema · gpt-4o-mini"]
    end
    ICT["ict_match.rank_partners · determinístico"]
  end

  Q --> RET
  Q --> MATCH
  HM --> RADAR
  INV --> RADAR
  PROG --> RADAR
  ICT --> RADAR
  RADAR["radar_service.merge_radar<br/>RRF + entity_floor · multi-quadrante<br/>evento / entidade / programa"]

  TOPK -.->|"RAG · retrieve_chunks"| WS

  subgraph EXPLORE["Descoberta · ExploreAgent"]
    GS["GraphService · leitura do vault Obsidian<br/>sem LLM, cache LRU por mtime"]
    EA["ExploreAgent · 3 rotas<br/>factual → reasoning → agent"]
    GS -->|scope + factual| EA
  end

  subgraph FRONT["⚡ Frontend · estado local"]
    FE["profile_diff → DiffCard<br/>✓ Aceitar → isRadarReady()<br/>acumula perfil no localStorage"]
  end

  EA -.->|"responde + profile_diff"| FRONT
  FRONT -.->|"isRadarReady()"| Q

  RADAR -.->|"RadarItem[].edital_id<br/>usuário clica 'Começar proposta'"| WS

  subgraph WRITE["Escrita · runtime agêntico"]
    WS["WritingSession → LangGraph (agent_graph)<br/>RAG via retrieve_chunks<br/>scope = [edital_id]"]
    WS -->|"turn_count=0 + sections vazias"| FT["_first_turn_with_generation()<br/>batch 8 seções + descrição do usuário<br/>retorna draft completo de uma vez"]
    FT -.->|"background"| CKL["ChecklistService · 3 passes paralelos<br/>compliance · qualidade · completude<br/>polling: GET /{id}/compliance"]
    WS -->|"turnos seguintes"| CKL
    WS -->|"save_draft (force=False)"| SCOP["scope_classifier.py · GPT-4o-mini<br/>classifica correção: cosmética vs. conceitual<br/>se conceitual → ripple_suggestion"]
    SCOP -->|"depth≤1 (D9)"| WS
  end
```

---

## 3. Runtime agêntico (alvo) — LangGraph

`StateGraph` ReAct com checkpointer Postgres durável, `interrupt()` nativo para
human-in-the-loop, memória cross-session via Store, e telemetria nativa. O
isolamento multi-tenant migra de RLS para `thread_id`/namespace por workspace —
o risco #1, gated por um leak test cross-workspace com Postgres real.

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
    REG --> S["matching · rag · writing · extraction<br/>investor_match · opportunity_type · triage<br/>profile_extractor · reranker · structurer · compliance_monitor"]
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
| Fallback determinístico antes do LLM | HybridMatch Stage 1 Pandas → Stage 2; rerank degrada p/ RRF puro |
| Modelo por tradeoff explícito | `llm_router` fast/pro/auto; produtores em free-tier (gemini), agregado em gpt-4o-mini |
| RAG não-ingênuo | BM25+dense via RRF com `fts_weight=0.3` justificado pelo corpus; contextual retrieval; dedup por source; HyDE ativo por default com fallback silencioso |
| Mede antes de mergear | 11 suítes no registry, gate de commit, Langfuse Experiments |
| Abstração de troca | `kg_store.py` como seam único; embedder/reranker/LLM parametrizáveis por env |
| Human-in-the-loop durável (alvo) | LangGraph `interrupt()` + checkpointer Postgres; isolamento via namespace por workspace |
