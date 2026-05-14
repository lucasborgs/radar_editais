# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Schema autoritativo

Regras de criação de wiki pages, nós/links do grafo, vocabulários, workflows de ingestão e manutenção vivem em [WIKI.md](WIKI.md) (global) e [wikis/](wikis/)`<fonte>.md` (por fonte). O código lê o schema via [core/wiki_schema.py](core/wiki_schema.py). **Mudanças em regras → edite os docs, não o código.** O validador [tests/test_wiki_schema_consistency.py](tests/test_wiki_schema_consistency.py) garante que doc e código não divergem.

## Project Overview

**Radar de Editais** matches companies with Brazilian public funding opportunities (editais) using a medallion ETL pipeline, semantic search, and LLM agents.

## Commands

### Setup
```bash
pip install -e .                        # install Python package (required once)
```

### Running the stack
```bash
uvicorn backend.api:app --reload --port 8000   # FastAPI backend
cd frontend && npm run dev                      # Next.js frontend (port 3000)
```

### Data pipeline
```bash
python scripts/run_all.py               # scrapers + full ETL (incremental, hash-based)
python scripts/run_finep_pipeline.py    # FINEP only
```

### Fine-tuning pipeline
```bash
python pipeline/etl_generate_pairs.py --backend gemini   # generate training pairs
python pipeline/etl_finetune_tsdae.py                    # TSDAE unsupervised pre-training
```

### Frontend
```bash
cd frontend && npm run build   # production build
cd frontend && npm run lint    # ESLint check
```

### LLM backend env vars
```bash
LLM_BACKEND=openai    # or "ollama" (default: openai)
OLLAMA_MODEL=llama3.2
OPENAI_API_KEY=...
OPENAI_MODEL=gpt-4o-mini
```

## Architecture

### Data flow
```
Bronze (raw FINEP HTML/PDFs/JSON)
  → pipeline/etl_finep_facts.py   (LLM extraction de fatos atômicos)
  → pipeline/etl_finep_cards.py   (LLM síntese de wiki page por edital)
  → pipeline/build_knowledge_graph.py  (consolida index + wiki/*.json)

Edital chunks (para RAG na WritingSession, ADR M9):
  → procrastinate task `chunk_edital` (core/tasks.py)
  → core/chunker.py    chunking estrutural por Art./§
  → core/embedder.py   OpenAI text-embedding-3-large
  → tabela edital_chunks (pgvector + tsvector)
```
Paths em `config.py` (ROOT, BRONZE_DIR, SILVER_DIR, FINEP_PDFS_DIR, KNOWLEDGE_GRAPH_DIR, KG_WIKI_DIR).

Nota: os diretórios `chroma_db/` e `gold_vectors/` em disco são legado de um design anterior já removido do código. Podem ser deletados quando conveniente — nenhum módulo Python os referencia.

### Package layout
```
backend/       FastAPI app (api.py) + auth_routes + library_routes
core/          db (Supabase clients), auth (JWT/DbClient), writing_session,
               hybrid_match_service, kg_match_service, content_library,
               checklist_service, profile_extractor, wiki_schema,
               chunker, embedder, retriever, tasks (procrastinate)
domain/        CompanyProfile dataclass (user_profile.py)
agents/        LLM agents (writer_agent, analyst_agent)
pipeline/      ETL FINEP (extractors/, build_knowledge_graph, health_check)
scripts/       CLI: run_all, run_finep_pipeline, reindex_edital, dev, deploy
supabase/      migrations/*.sql + config.toml (local CLI)
```

### Core services
- **HybridMatchService** (`core/hybrid_match_service.py`) — scoring determinístico Pandas-based + Stage 2 LLM. Lê pesos de `matching_weights` com cache TTL 60s (ADR A5).
- **KGMatchService** (`core/kg_match_service.py`) — LLM raciocina sobre o knowledge graph (index.json + wiki pages). Sem embeddings.
- **WritingSession** (`core/writing_session.py`) — DB-backed (writing_sessions + session_turns). RAG via `retrieve_chunks` substitui context stuffing. Resolve @ mentions de library_items.
- **ChecklistService** (`core/checklist_service.py`) — 3 passes paralelos via asyncio.gather: compliance + qualidade + completude (ADR C4).
- **ContentLibrary** (`core/content_library.py`) — CRUD de items + enrich_content via LLM (summary, key_facts, themes, importance_score 1-10). Soft-delete via archived_at.

### Background jobs (procrastinate, ADR M8)
- `enrich_content_task` — enriquecimento LLM async ao upload de item da library
- `chunk_edital_task` — chunking + embedding de um edital para RAG
- Worker: `python -m procrastinate --app=core.tasks.app worker`

### API surface (backend/api.py)
```
GET  /stats, /editais, /editais/{id}, /editais/{id}/sections
POST /match, /chat, /analyze, /draft
POST /writing/start, /writing/turn, /writing/section-start
GET  /writing/sessions, /writing/sessions/{id}/document
POST /writing/{id}/checklist/auto-review (3 passes paralelos)
GET  /me, PUT /me/profile, PUT /me/preferences
GET/POST/PUT/DELETE /library, POST /library/{id}/archive
```

### Frontend
Next.js 14 + TypeScript + TailwindCSS + Radix UI. API client at `frontend/src/lib/api.ts`. Backend URL configured via `NEXT_PUBLIC_API_URL` in `frontend/.env.local`.

## Key Gotchas

### Parquet + list columns → ndarray
Columns storing Python lists (e.g., `themes`, `keywords`) deserialize from Parquet as `numpy.ndarray`. Always use `_safe_list(val)` when reading them in `core/matching_engine.py` and `core/search_engine.py`.

### Imports
The package is installed via `pip install -e .`. All imports are absolute (`from core.matching_engine import MatchingEngine`). Never add `sys.path` hacks.

### Agents use class, not function
- `agents/analyst_agent.py` → class `AdherenceAnalyzer` (not a standalone function)
- `agents/writer_agent.py` → class `ProposalDrafter`

### LLM enrichment cache
`.enrichment_cache.json` at root prevents re-calling LLM on unchanged editais. Delete to force re-enrichment.
