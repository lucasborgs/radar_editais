# Radar de Editais

Plataforma de IA que cruza o perfil de startups deep-tech brasileiras com
oportunidades de fomento público — editais, programas e investidores — em ranking
único, com escrita assistida de propostas via RAG.

> IA rascunha, o humano decide.

## Arquitetura

```mermaid
flowchart LR
    %% Input channels → hub
    FONTES[Agências<br/>FINEP/FAPESP/FAPESC] --> SILVER[Silver<br/>extração + normalização]
    DESC[Descoberta Web<br/>crawler + gate admin] -->|promote| SILVER
    SILVER --> GOLD

    GOLD[("Gold — Catálogo<br/>Postgres · entidades<br/>match_chunks")]

    %% Spoke 1: Match
    subgraph MATCH["① Radar — Match"]
        PERFIL[Perfil Empresa] --> S0[Stage 0: Vigência]
        S0 --> S1[Stage 1: Elegibilidade]
        S1 --> S2[Stage 2: MaxSim · zero LLM]
        S2 --> S3[Stage 3: Veredito gpt-4o-mini]
        S3 --> RANK[Ranking + Trilha Investidor]
    end

    GOLD -->|entidades + match_chunks| MATCH

    %% Ingest pipeline (on demand)
    subgraph INGEST["Pré-processamento (sob demanda)"]
        EDITAL_RAW[Edital raw] --> CHUNKER[Chunker estrutural<br/>Art. / §]
        CHUNKER --> CR[Contextual Retrieval<br/>LLM injeta contexto do capítulo]
        CR --> EMBED[Embedder<br/>text-embedding-3-small]
    end

    GOLD -.->|edital raw| INGEST
    EMBED --> EC

    EC[(edital_chunks<br/>pgvector + tsvector)]

    %% Spoke 2: Writing + RAG
    subgraph ESCRITA["② Escrita Assistida"]
        EC --> HYDE[HyDE]
        EC --> BM25
        HYDE --> DENSE[pgvector]
        DENSE --> RRF[RRF]
        BM25 --> RRF
        RRF --> RERANK[Rerank] --> TK[Top-k]
        TK --> WS[WritingSession LangGraph]
        WS --> DRAFT[Rascunho + Citações]
    end

    RANK -->|ranking| ESCRITA

    %% Spoke 3: Explore
    subgraph EXPLORE["③ Explore — Mapa"]
        HG[Hipergrafo N-ário<br/>Oportunidade · Ator · Conceito] --> EA[ExploreAgent ReAct]
        EA --> RESP[Respostas → profile_diff]
    end

    GOLD -->|entidades| HG

    style GOLD fill:#e6f3ff,stroke:#0066cc,stroke-width:2px,color:#000
```

- **Descoberta**: crawler web (DOU + afins) → staging com gate humano. Promovido → silver → gold como qualquer edital de agência.
- **Radar (Match)**: funil determinístico de 4 estágios — vigência (SQL) → elegibilidade → MaxSim (zero LLM) → veredito gpt-4o-mini no top-K. Trilha investidor paralela por cosseno de tese.
- **Explore**: agente ReAct com busca semântica, BFS em hipergrafo, entidades por tags e o motor de match como ferramenta.
- **Escrita**: sessões LangGraph com checkpointer Postgres durável, RAG híbrida (HyDE + pgvector + BM25 + RRF + rerank), ficha da oportunidade via catálogo, checklist paralelo 3-passos.
- **Runtime**: 5 tiers de LLM independentes por env var (embedding, contextual, extração, explore, escrita).

## Stack

**Backend** Python (FastAPI + LangGraph + procrastinate) ·
**Frontend** Next.js 14 (TypeScript + Tailwind + Radix) ·
**Dados** Supabase (Postgres + pgvector + Auth) ·
**LLM** OpenAI + Anthropic ·
**Observabilidade** Langfuse ·
**Deploy** Docker + Cloudflare Tunnel + Vercel

## Rode local

```bash
pip install -e . && supabase start
uvicorn backend.api:app --reload --port 8000
```

Setup completo, testes, lint, eval: [`CLAUDE.md`](CLAUDE.md). Diagramas
detalhados: [`docs/architecture.md`](docs/architecture.md).

## Estrutura

```
backend/     FastAPI: routers por domínio + common.py
core/        services/ · kg/ · retrieval/ · llm/ · eval/
domain/      CompanyProfile (dataclass)
pipeline/    ETL multi-fonte (extractors + adapters)
frontend/    Next.js 14
supabase/    Migrações + config CLI
docs/        architecture.md, ROADMAP, specs, BACKLOG
wikis/       Schema/vocabulários por fonte (doc-as-config)
```

---

Demo: [radar-editais-gold.vercel.app](https://radar-editais-gold.vercel.app)
