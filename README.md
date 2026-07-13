# Radar de Editais

Plataforma de IA que cruza o perfil de startups deep-tech brasileiras com
oportunidades de fomento público — editais, programas e investidores — em ranking
único, com escrita assistida de propostas via RAG.

> IA rascunha, o humano decide.

## Arquitetura

```mermaid
flowchart LR
    subgraph Fontes
        AG[Agências FINEP/FAPESP/FAPESC] --> B[(Bronze)]
        WEB[Descoberta web + gate admin] --> B
        CUR[Curadoria ICTs/investidores] --> B
    end

    B --> HEX[hyper_extractor via LLM]
    B --> CHUNK[chunker + contextual retrieval]

    HEX --> HG[(Hipergrafo N-ário<br/>Oportunidade · Ator · Conceito)]
    HG --> ENT[(Postgres<br/>entidades · match_chunks)]

    CHUNK --> PG[(pgvector + tsvector<br/>edital_chunks)]

    ENT --> E0["E0: Vivo (SQL)"]
    E0 --> E1["E1: Elegibilidade"]
    E1 --> E2["E2: MaxSim (zero LLM)"]
    E2 --> E3["E3: Veredito gpt-4o-mini"]
    E3 --> RANK[Ranking Final]

    ENT --> INV[Investidor · cosseno]
    INV --> RANK

    PG --> HYDE[HyDE] --> DENSE[Dense pgvector]
    PG --> SPARSE[BM25]
    DENSE --> RRF[RRF merge]
    SPARSE --> RRF
    RRF --> BOOST[Boost 1.5x] --> RERANK[Rerank] --> TK[top-k]

    TK --> WS[WritingSession<br/>RAG + LangGraph]
    ENT --> WS
    RANK --> WS

    ENT --> EA[ExploreAgent]
    EA --> TOOLS[Tools: search · tags · BFS · match]
    TOOLS -.-> E2

    EA -.-> FRONT[Frontend · profile_diff]
    FRONT -.-> Q[CompanyProfile]
    Q --> CHUNK
    Q --> E0

    WS --> API[FastAPI]
    EA --> API
    API --> FE[Next.js 14]

    WK[worker procrastinate] -.-> HEX & CHUNK & WEB
```

- **Match**: funil determinístico de 4 estágios — vigência (SQL) → elegibilidade
  → MaxSim (zero LLM) → veredito gpt-4o-mini no top-K. Trilha investidor
  paralela por cosseno de tese.
- **Explore**: agente ReAct com busca semântica, BFS em hipergrafo,
  entidades por tags e o motor de match como ferramenta.
- **Escrita**: sessões LangGraph com checkpointer Postgres durável, RAG híbrida
  (HyDE + pgvector + BM25 + RRF + rerank), ficha da oportunidade via catálogo
  de entidades, checklist paralelo 3-passos.
- **Runtime**: 5 tiers de LLM independentes por env var (embedding, contextual,
  extração, explore, escrita).

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
