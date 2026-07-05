# Arquitetura — Radar de Editais

Visão de sistema para um Staff AI Engineer. O foco é onde a inteligência mora,
que modelo faz o quê, e como isso é medido — não o CRUD/auth/frontend.

> **Estado de implementação.** Os diagramas retratam a **arquitetura atual em produção**.
> O runtime LangGraph com checkpointer Postgres e Store semântico está **implementado e em produção**
> (migração completa — ver [`docs/components/agents/langgraph-migration.md`](components/agents/langgraph-migration.md) para o histórico).
> O schema do hipergrado foi consolidado para KG v2 (3 tipos: `Oportunidade`/`Ator`/`Conceito`,
> supersede os 12 tipos originais). O funil de match tem 3 estágios: filtro duro de elegibilidade
> → MaxSim → veredito LLM no top-K. Ver [`docs/specs/kg-redesign.md`](specs/kg-redesign.md) para
> histórico completo das decisões (D1–D16) e registro de divergências PR a PR.

---

## 1. Data plane — Bronze → Hipergrafo v2 → Chunks

Multi-fonte com adapters por fonte; a descoberta web é uma "torneira" que passa
por gate humano antes de tocar o grafo.

```mermaid
flowchart TB
  subgraph BRONZE["Bronze (raw)"]
    FINEP["FINEP · Liferay API"]
    FAPESP["FAPESP"]
    FAPESC["FAPESC"]
    WEB["Web / Descoberta<br/>core.opportunity_discovery"]
    CURADOS["Curados (JSON)<br/>investidores · programas<br/>ict_raw"]
  end

  WEB -->|"torneira"| STAGING["Staging + gate humano<br/>discovery não escreve no KG"]
  STAGING --> NORM
  FINEP --> NORM
  FAPESP --> NORM
  FAPESC --> NORM
  CURADOS -->|"rebuild determinístico<br/>rebuild_curadoria.py"| CBUILD

  NORM["Normalizers por fonte (adapter pattern)"] --> HEX

  subgraph HEX["hyper_extractor.py<br/>extração N-ária: 3 tipos (v2)<br/>Oportunidade / Ator / Conceito<br/>propriedades: mecanismo, constraints,<br/>macro_temas, requisitos_texto<br/>proveniencia via adapter"]
    direction LR
    V2["LLM estruturado<br/>prompts emitem schema v2"]
  end

  HEX --> HG[("Hipergrados individuais<br/>data/knowledge_graph/hypergraphs/{id}.json<br/>formato v2 (format_version: 2)<br/>IDs estáveis prefixados (op:/ator:/con:)")]

  subgraph POS["Pós-processo build-time"]
    CANON["canonicalize_concepts.py<br/>validação + canonicalização LLM<br/>descarta ruído, funde duplicatas<br/>gera macro_temas do vocabulário<br/>controlado (themes_index)"]
    CONS["extract_constraints.py<br/>gpt-4o-mini extrai constraints<br/>estruturadas {tipo,op,valor}<br/>de requisitos/exclusões textuais"]
    PROV["backfill_proveniencia.py<br/>URL oficial do bronze → grafo<br/>(determinístico, zero LLM)"]
  end

  HG --> CANON
  HG --> CONS
  HG --> PROV
  CANON --> HGC["Hipergrados higienizados<br/>(mesmo arquivo, reescrito in-place)"]
  CONS --> HGC
  PROV --> HGC

  CBUILD["rebuild_curadoria.py<br/>rebuild determinístico (zero LLM)<br/>D2: investidor → Ator +<br/>Oportunidade(kind=investimento)<br/>D3: programas → Oportunidade(kind=programa)<br/>100% com URL, tese, estágio, ticket"] --> HGC

  HGC --> EMB["embedder.py<br/>text-embedding-3-small (1536d)<br/>embeds nós Conceito,<br/>descrições de Oportunidade/Ator"]
  EMB --> CACHE[("ecosystem_embeddings.npz<br/>cacheados por hash do texto")]

  HGC --> CHK["chunk_edital · chunker.py (Art./§)"]
  CHK --> CTX["Contextual Retrieval<br/>core/contextual_retrieval.py"]
  CTX --> CHKEMB["embedder.py<br/>text-embedding-3-small (1536d,<br/>default por env desde 2026-06-26)"]
  CHKEMB --> VEC[("edital_chunks<br/>pgvector + tsvector/BM25<br/>para RAG na escrita")]
```

---

## 2. AI core (query time) — funil de match 3 estágios + retrieval + escrita

```mermaid
flowchart TB
  Q["Query / CompanyProfile<br/>(localStorage)"]

  subgraph FUNIL["Funil de match (3 estágios)"]
    direction TB
    E0["Estágio 0 — Filtro duro<br/>eligibility.py<br/>constraints × perfil<br/>sat / unsat / unknown<br/>determinístico, zero LLM<br/>unknown NUNCA elimina"] --> E1
    E1["Estágio 1 — Afinidade MaxSim<br/>hypergraph_match.py<br/>cosseno Conceito-empresa ×<br/>Conceito-oportunidade (threshold 0.55)<br/>agregação MaxSim (Σ dos máximos<br/>por nó-empresa)<br/>piso MIN_AGGREGATE_SCORE=1.35<br/>expansão via catálogo (damping 0.30)<br/>sem LLM no ranking"] --> E2
    E2["Estágio 2 — Veredito LLM top-K<br/>match_verdict.py<br/>gpt-4o-mini (tier 3)<br/>serializa subgrafo + perfil em LN<br/>output estruturado: racional,<br/>red_flags, fit_mecanismo, recomendação<br/>async + cache (match_verdicts table)<br/>card renderiza sem e recebe pronto"]
  end

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

  Q --> E0
  Q --> E1
  E1 --> E2
  E2 --> RANK["Ranking final com veredito<br/>reordena só dentro do top-K"]

  TOPK -.->|"RAG · retrieve_chunks"| WS

  subgraph EXPLORE["Descoberta · ExploreAgent"]
    EA["ExploreAgent · 3 rotas<br/>factual → reasoning → agent"]
    EA --> TOOLS["tools:<br/>resolve_graph_nodes (por id)<br/>neighborhood (BFS cap 20/nó)<br/>serializa propriedades/constraints<br/>(lê hipergrados v2)"]
  end

  subgraph FRONT["⚡ Frontend · estado local"]
    FE["profile_diff → DiffCard<br/>✓ Aceitar → isRadarReady()<br/>acumula perfil no localStorage"]
  end

  EA -.->|"responde + profile_diff"| FRONT
  FRONT -.->|"isRadarReady()"| Q
  Q -.->|"profile threading<br/>para ExploreAgent tools"| EA

  RANK -.->|"edital_id<br/>usuário clica 'Começar proposta'"| WS

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
o risco #1, coberto por leak test cross-workspace com Postgres real
(`tests/test_tenant_isolation.py`, rodado 2026-07-03).

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

  subgraph TOOLS["ToolNode — tools por domínio"]
    WT["writing_tools"]
    ET["explore_tools<br/>resolve por id canônico<br/>neighborhood com BFS cap<br/>serializa constraints/propriedades"]
    PT["profile_tools<br/>find_matching_editais com profile<br/>threading p/ Estágio 0"]
    RT["research_tools"]
    PLT["planning_tools"]
    SCT["scratchpad_tools"]
    CA["critic_agent + scope_classifier"]
  end

  T --> TOOLS

  subgraph STATE["🔄 Pointer Reference — InjectedState('documents')"]
    DOC["state['documents']<br/>dict[chunk_id → full_text]"]
    SE["search_edital"] -->|writes text| DOC
    RE["read_exact_chunk"] -->|reads text| DOC
  end

  STATE -.->|carried by all nodes| GRAPH

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
    J6["compute_match_verdicts<br/>LLM verdicts no top-K<br/>cache + queueing_lock"]
  end

  subgraph SCRIPTS["Scripts build-time"]
    S1["migrate_hypergraphs_v2.py<br/>PR1: ids estáveis + migração"]
    S2["canonicalize_concepts.py<br/>PR3: higiene + canon + macro_temas"]
    S3["backfill_proveniencia.py<br/>PR4: URL do bronze → grafo"]
    S4["extract_constraints.py<br/>PR5: constraints de requisitos textuais"]
    S5["rebuild_curadoria.py<br/>PR4.1: rebuild determinístico curados"]
  end

  GATE -.->|"protege merges"| JOBS
  GATE -.-> SCRIPTS
```

---

## 5. Sinais que um Staff nota (e são reais no código)

| Sinal | Onde |
|---|---|
| Funil de match 3 estágios — cada estágio com motor diferente (determinístico → geométrico → LLM) e custo crescente | `hypergraph_match.py` (Estágio 0/1) + `match_verdict.py` (Estágio 2, K≪N) — não há LLM no ranking, só no veredito do top-K |
| Agregação MaxSim substituiu marginsum: evita inflação por nós redundantes, ganho forward-looking para late-interaction | `hypergraph_match.py:_maxsim()` — recalibrado empiricamente no golden (1.35 / 0.60), sweep revelou platô fino antes do precipício |
| Modelo por tradeoff explícito | `llm_router` fast/pro/auto; produtores em free-tier (gemini), agregado em gpt-4o-mini; embedding small para nós, large para chunks |
| RAG não-ingênuo | BM25+dense via RRF com `fts_weight=0.5` justificado pelo corpus; contextual retrieval; dedup por source; HyDE ativo por default com fallback silencioso |
| Elegibilidade é estágio determinístico, não raciocínio do agente | `eligibility.py` — constraints tipadas (porte/UF/faturamento/TRL/forma_jurídica) × perfil → sat/unsat/unknown; unknown nunca elimina, gera flag no card |
| Veredito LLM assíncrono com cache | `match_verdict.py`: task procrastinate + tabela `match_verdicts` com input_hash; card renderiza sem veredito, recebe pronto via poll |
| Mede antes de mergear | 11 suítes no registry, gate de commit, Langfuse Experiments |
| Abstração de troca | `kg_store.py` como seam único; embedder/reranker/LLM parametrizáveis por env; adapters com `provenance()` por fonte |
| Schema v2 reduziu 12 tipos de nó para 3 + propriedades | Oportunidade/Ator/Conceito; Mecanismo/Requisito/Exclusão/Fonte viram propriedades ou são removidos; IDs estáveis prefixados resolvem travessia cross-fonte |
| Human-in-the-loop durável | LangGraph `interrupt()` + checkpointer Postgres; isolamento via namespace por workspace |
