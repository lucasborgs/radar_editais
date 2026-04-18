# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

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
LLM_BACKEND=ollama    # or "openai" (default: ollama)
OLLAMA_MODEL=llama3.2
OPENAI_API_KEY=...
OPENAI_MODEL=gpt-4o-mini
```

## Architecture

### Data flow (medallion)
```
Bronze (raw HTML/PDFs/JSON)
  → pipeline/etl_silver.py         (normalize → Parquet, unified schema)
  → pipeline/etl_enrichment.py     (LLM extraction: TRL, temas, search_document)
  → pipeline/etl_gold_vectors.py   (embed → ChromaDB + gold Parquet)
```
All paths are centralized in `config.py` (ROOT, BRONZE_DIR, SILVER_DIR, SILVER_ENRICHED_DIR, GOLD_VECTORS_DIR, CHROMA_DB_DIR).

### Package layout
```
backend/       FastAPI app (api.py) — all REST endpoints, singleton service init
core/          Business logic: MatchingEngine, RAGService, WritingSession, SearchEngine
domain/        CompanyProfile dataclass (user_profile.py) — JSON persistence in profiles/
agents/        LLM agents: AdherenceAnalyzer (analyst_agent.py), ProposalDrafter (writer_agent.py)
pipeline/      ETL stages + extractors/ (scrapers per source)
scripts/       CLI entry points
```

### Core services
- **MatchingEngine** (`core/matching_engine.py`) — deterministic Pandas-based scoring, no LLM, instant
- **SearchEngine** (`core/search_engine.py`) — hybrid semantic (ChromaDB) + lexical (BM25)
- **RAGService** (`core/rag_service.py`) — intent routing across flows: match, explore, search_facts, writing, analyze, proposal, general
- **WritingSession** (`core/writing_session.py`) — collaborative proposal writing with live edital fetching

### API surface (backend/api.py)
```
GET  /stats, /editais, /editais/{id}, /editais/{id}/sections
POST /match, /chat, /analyze, /draft
POST /writing/start, /writing/turn
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
