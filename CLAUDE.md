# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Schema autoritativo

Regras de vocabulários, workflows de ingestão e manutenção vivem em [WIKI.md](WIKI.md) (global) e [wikis/](wikis/)`<fonte>.md` (por fonte). O código lê os blocos YAML via [core/kg/schema.py](core/kg/schema.py). **Mudanças em regras → edite os docs, não o código.**

**Nota:** `wiki_schema.py` foi removido (substituído por `schema.py`). O pipeline legacy `build_knowledge_graph` → `index.json` + `etl_process` → `wiki/*.json` foi removido — todo o catálogo e match vêm dos hipergrados em `data/knowledge_graph/hypergraphs/`.

## Project Overview

**Radar de Editais** matches companies with Brazilian public funding opportunities (editais) using a medallion ETL pipeline, semantic search, and LLM agents.

## Commands

### Setup
```bash
pip install -e .                        # install Python package (required once)
pip install -e ".[dev]"                 # also installs ruff, mypy, pytest
```

### Running the stack
```bash
uvicorn backend.api:app --reload --port 8000   # FastAPI backend
cd frontend && npm run dev                      # Next.js frontend (port 3000)
python -m procrastinate --app=core.tasks.app worker   # background job worker
```

### Tests
```bash
pytest                                          # run all tests
pytest tests/test_hybrid_match_eligibility.py  # single file
pytest -k "test_name"                          # single test by name
pytest -x                                      # stop on first failure
```
Tests use dummy LLM keys by default (`conftest.py`) and disable Langfuse telemetry. Tests that hit real LLM/Supabase are integration tests and require real env vars.

### Lint and type checking
```bash
ruff check .                            # linting (CI enforced)
ruff check . --fix                      # auto-fix what's fixable
cd frontend && npx tsc --noEmit         # TypeScript check (use this, NOT npm run build, when dev server is up)
```
`mypy` has ~575 existing errors and is advisory only — it does NOT block CI.

### Data pipeline
```bash
python -m core.opportunity_discovery       # torneira web (DOU com DISCOVERY_DOU_ENABLED=1)
```
Em prod, scrapers e Descoberta rodam pelos crons do worker (`run_daily_etl`
03:00 UTC, `discover_opportunities` 04:00 UTC — core/tasks.py). O pipeline de build (hyper_extractor + embed) roda em lote via `scripts/run_all.py`.

Discovery web não escreve diretamente no KG — vai para staging com gate humano (`/discovered-opportunities` na UI).

### Avaliação (harness unificado)
```bash
python -m core.eval matching        # roda uma suíte (Langfuse Experiment se configurado; senão eval_results/*.json)
python -m core.eval all              # todas as suítes registradas
python -m core.eval matching --no-push --limit 1   # fallback local, subconjunto (debug)
```
Suítes: `matching`, `rag`, `writing`, `extraction` (e mais 7) — todas em `core/eval/`,
registro em `core/eval/registry.py`. Cada uma é uma `Suite`: `task` roda o pipeline real,
`evaluators` reaproveitam `core/*_eval.py`. Com `LANGFUSE_*` no ambiente vira
Experiment (scores comparáveis entre commits); senão grava `eval_results/*.json`.
NÃO criar harnesses novos paralelos — registrar uma suíte aqui. Prereqs: `rag`
exige SUPABASE+OPENAI+golden; `writing` exige SUPABASE+LLM+`EVAL_WORKSPACE_ID`.

### Frontend
```bash
cd frontend && npm run build   # production build
cd frontend && npm run lint    # ESLint check
```

### Supabase local
```bash
supabase start                  # inicia Postgres local (porta 54322 para Postgres, 54321 para API)
supabase status                 # mostra SUPABASE_URL, ANON_KEY, SERVICE_KEY, JWT_SECRET para .env
supabase db push                # aplica migrations pendentes no remoto
```

### LLM backend env vars
O projeto tem 5 tiers de LLM separados por env var — cada um trocável independentemente:
```bash
# Tier 1: Embeddings (padrão: OpenAI text-embedding-3-large 1536d)
EMBEDDING_MODEL=text-embedding-3-large
# EMBEDDING_BASE_URL=   # endpoint OpenAI-compat (Ollama/vLLM); vazio = canônico

# Tier 2: Contextual Retrieval (padrão: gpt-4o-mini; Gemini Flash-Lite empata no gate)
# CONTEXTUAL_RETRIEVAL_MODEL=gemini-2.0-flash-lite
# CONTEXTUAL_RETRIEVAL_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai/

# Tier 3: Raciocínio/extração determinístico (padrão: gpt-4o-mini)
LLM_BACKEND=openai         # ou "gemini" → aponta match/extração/structurer ao Gemini
OPENAI_API_KEY=...
OPENAI_MODEL=gpt-4o-mini
GEMINI_API_KEY=            # AI Studio (free) — só para editais públicos; nunca para writing

# Tier 4: Agente explore (writing usa sempre OpenAI ou Anthropic)
ANTHROPIC_API_KEY=
ANTHROPIC_MODEL_AGENT=claude-sonnet-4-6
AGENT_PROFILE_EXTRACTOR_DEFAULT_ENABLED=false

# Tier 5: Agente de escrita (base_url sobrescreve endpoint; ZDR/pago apenas)
# AGENT_OPENAI_BASE_URL=https://api.deepseek.com/v1
```
Ver `.env.example` para referência completa com todos os overrides.

## Architecture

Para diagramas Mermaid detalhados do data plane, AI core, runtime agêntico e eval, ver [`docs/architecture.md`](docs/architecture.md).

### Data flow
```
Bronze (FINEP/FAPESP/FAPESC raw via adapters por fonte)
  → pipeline/etl_process.py            (extração + normalização silver)
  → pipeline/build_knowledge_graph.py  (consolida index + wiki/*.json)
    → produtores LLM build-time: eligibility_constraints, mechanism, enrichment

─── LEGADO (removido): o pipeline acima foi substituído pelo hypergrado ───

Bronze → pipeline/hyper_extractor.py  (hipergrafos N-ários: 12 nós/10 arestas)
  → core/retrieval/embedder.py        (embed dos nós por Edital/Tema/Tecnologia/Aplicação)
  → data/knowledge_graph/hypergraphs/{id}.json

Edital chunks (para RAG na WritingSession):
  → procrastinate task `chunk_edital` (core/tasks.py)
  → core/retrieval/chunker.py    chunking estrutural por Art./§
  → core/contextual_retrieval.py  injeta contexto de capítulo via LLM
  → core/retrieval/embedder.py   OpenAI text-embedding-3-large
  → tabela edital_chunks (pgvector + tsvector)
```
Paths em `config.py` (ROOT, BRONZE_DIR, SILVER_DIR, FINEP_PDFS_DIR, KNOWLEDGE_GRAPH_DIR, HYPERGRAPHS_DIR).

### Package layout
```
backend/       api.py (shell: app + middleware + wiring) + routers/ por domínio
               (catalog, graph, matching, applications, brief, writing, files,
               profile) + auth_routes/library_routes + common.py (singletons +
               schema de perfil) + rate_limit.py (limiter)
core/          services/ (writing_session, hybrid/kg/investor/radar match,
               checklist, content_library, explore_agent, graph_service,
               entity_matcher), kg/ (kg_store, wiki_schema, edital_id,
               temporal), retrieval/ (chunker, embedder, retriever, hyde),
               llm/ (llm_client, agent_runtime, agent_graph, agent_tools/),
               eval/ (harness); flat: db, auth, tasks (procrastinate),
               profile_extractor, opportunity_discovery, dou_feeder,
               web_search, contextual_retrieval, reranker, demais serviços
domain/        CompanyProfile dataclass (user_profile.py)
pipeline/      ETL FINEP (extractors/, etl_process, build_knowledge_graph, health_check)
scripts/       CLI: run_all, reindex_edital, dev, deploy
skills/        playbooks de mecanismo (subvencao, credito, equity) — lidos pelo WritingAgent
supabase/      migrations/*.sql + config.toml (local CLI)
data/          bronze/ (raw imutável), silver/ (derivado), knowledge_graph/ (hypergraphs/)
```

### Core services
- **HypergraphMatch** (`core/services/hybrid_match_service.py`) — match por marginsum sobre cosseno entre nós do perfil empresa e nós Tema/Tecnologia/Aplicação dos hipergrados. Threshold 0.55, piso `min_aggregate`. Sem estágio LLM no match core.
- **ExploreAgent** (`core/services/explore_agent.py`) — 3 rotas: factual → reasoning → agent. Lê hipergrados via `resolve_graph_nodes` + `neighborhood`. Retorna string; o `profile_diff` é extraído pelo router (`backend/routers/explore.py`) via `ProfileExtractor`.
- **WritingSession** (`core/services/writing_session.py`) — runtime LangGraph (`agent_graph.py`) com checkpointer Postgres durável. RAG via `retrieve_chunks`. Primeiro turno: batch de 8 seções de uma vez (`_first_turn_with_generation`). `save_draft(force=False)` passa pelo Critic (subagente) + scope_classifier antes de persistir.
- **ChecklistService** (`core/services/checklist_service.py`) — 3 passes paralelos via asyncio.gather: compliance + qualidade + completude.
- **ContentLibrary** (`core/services/content_library.py`) — CRUD + enrich_content via LLM. Soft-delete via `archived_at`.

### LLM runtime (core/llm/)
- **agent_runtime.py** — contrato `AgentResult`/`TraceStep`, `run_agent`/`run_agent_async`, `run_subagent`. Delega inteiramente ao grafo LangGraph; o loop hand-rolled foi removido.
- **agent_graph.py** — `StateGraph` ReAct (agent → tools → manage_memory → reflect → agent). Checkpointer Postgres. `interrupt()` para human-in-the-loop. Memória cross-session via PostgresStore.
- **agent_tools/** — tools LangChain nativas por domínio: `writing_tools`, `explore_tools`, `profile_tools`, `research_tools`, `planning_tools`, `scratchpad_tools`, `critic_agent`, `scope_classifier`.
- **llm_client.py** — factory `make_client`/`make_async_client` com timeout/retry. **Todos os módulos usam esta factory** em vez de instanciar `OpenAI()` diretamente.

### Retrieval pipeline (core/retrieval/)
BM25 + dense via RRF (`fts_weight=0.5`, `DEFAULT_FTS_WEIGHT` em retriever.py). HyDE ativo por default (gera pseudo-doc antes de embedar). Contextual Retrieval injeta contexto de capítulo em cada chunk no ingest. Reranker: cross-encoder `mmarco-mMiniLMv2` (opcional, `pip install -e ".[rerank]"`); fallback `llm` (gpt-4o-mini) ou RRF puro.

### Background jobs (procrastinate — core/tasks.py)
- `chunk_edital_task` — chunking + contextual retrieval + embedding de um edital
- `enrich_content_task` / `embed_content` — enriquecimento LLM ao upload da library
- `reflect_workspace` / `synthesize_patterns` — memória cross-session (Store)
- `run_daily_etl` (03:00 UTC) / `discover_opportunities` (04:00 UTC)

### API surface (backend/routers/ — wiring em backend/api.py)
```
GET  /stats, /editais, /editais/{id}, /editais/{id}/sections
POST /match, /chat, /analyze, /draft
POST /writing/start, /writing/turn, /writing/section-start
GET  /writing/sessions, /writing/sessions/{id}/document
POST /writing/{id}/checklist/auto-review
GET  /discovered-opportunities, POST /discovered-opportunities/{id}/promote, POST /discovered-opportunities/{id}/reject
GET  /me, PUT /me/profile, PUT /me/preferences
GET/POST/PUT/DELETE /library, POST /library/{id}/archive
```

### Frontend
Next.js 14 + TypeScript + TailwindCSS + Radix UI. API client at `frontend/src/lib/api.ts`. Backend URL via `NEXT_PUBLIC_API_URL` in `frontend/.env.local`. Design tokens em `frontend/DESIGN_SYSTEM.md`. Toast canônico = **sonner**. Perfil da empresa acumulado no `localStorage` (não no backend) até `isRadarReady()`.

## Key Gotchas

### Imports
The package is installed via `pip install -e .`. All imports are absolute (`from core.services.hybrid_match_service import HybridMatchService`). Never add `sys.path` hacks.

### LLM enrichment cache
`.enrichment_cache.json` at root prevents re-calling LLM on unchanged editais. Delete to force re-enrichment.

### Trocar modelo LLM
Cada tier tem sua própria env var (ver seção LLM backend acima). Trocar um tier não afeta os outros. Modelos com dimensão de embedding diferente de 1536 exigem migração da coluna `vector(1536)` — não trocar sem eval.

### Reranker opcional
`sentence-transformers` não está nas deps padrão (evita torch em prod). Para usar `RERANK_BACKEND=cross-encoder`, instalar `pip install -e ".[rerank]"`. Em prod, usar `RERANK_BACKEND=llm` (gpt-4o-mini) ou deixar sem rerank (RRF puro).

### Discovery staging
`core/opportunity_discovery.py` escreve em staging (tabela `discovered_opportunities`), não no KG. O gate humano em `/discovered-opportunities` promove/rejeita antes de tocar o pipeline de build.
