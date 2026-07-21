# AGENTS.md

This file provides guidance to Codex (Codex.ai/code) when working with code in this repository.

Índice e autoridade da documentação: [docs/README.md](docs/README.md).

## Schema autoritativo

Regras de vocabulários, workflows de ingestão e manutenção vivem em [WIKI.md](WIKI.md) (global) e [wikis/](wikis/)`<fonte>.md` (por fonte). O código lê os blocos YAML via [core/kg/schema.py](core/kg/schema.py). **Mudanças em regras → edite os docs, não o código.**

**Nota:** o produtor legado da linhagem hyper-extract (hipergrafos N-ários,
`hyper_extractor.py`, wiki pages, `build_knowledge_graph`) foi removido (v3 PR-C).
Todo o catálogo e match vêm das tabelas **gold** (`entities`/`entity_relationships`/
`match_chunks`, migration 036), populadas por `core.kg.gold.ingest_all()`.

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
pytest tests/unit/test_match_v3.py                  # single file
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
`mypy` is advisory only and does NOT block CI.

### Data pipeline
```bash
python -m core.ingestion.opportunity_discovery       # torneira web (DOU com DISCOVERY_DOU_ENABLED=1)
```
Em prod, scrapers e Descoberta rodam pelos crons do worker (`run_daily_etl`
03:00 UTC, `discover_opportunities` 04:00 UTC — core/tasks.py). O `run_daily_etl`
é: scrapers → bronze → adapter → silver (structurer) → `core.kg.gold.ingest_all()`
incremental (diff por `source_hash`) → embeddings. Para rodar a ingestão gold
manualmente em dev: `DATABASE_URL=…local… OPENAI_API_KEY=… python -m core.kg.gold`
(`--no-skip` reprocessa tudo).

Discovery web não escreve diretamente no KG — vai para staging com gate humano (`/discovered-opportunities` na UI).

### Avaliação (harness unificado)
```bash
python -m core.eval run matching              # diagnóstico local completo
python -m core.eval run matching --limit 1    # subconjunto local (debug)
python -m core.eval run rag --publish         # diagnóstico completo publicado
python -m core.eval gate extraction --publish # decisão bloqueante oficial
```
Suítes: `matching`, `rag`, `writing`, `writing_v2`, `extraction`,
`opportunity_type`, `triage`, `profile_extractor`, `reranker` e `structurer` —
todas em `core/eval/`,
registro em `core/eval/registry.py`. Cada uma é uma `Suite`: `task` roda o pipeline real,
`evaluators` reaproveitam `core/*_eval.py`. `run` nunca bloqueia por qualidade;
`gate` aplica somente critérios aceitos e não permite `--limit`. Runs são locais
por padrão e sempre gravam `eval_results/*.json`; `--publish` envia a rodada
completa ao Langfuse. O manifesto do resultado define se runs são comparáveis.
NÃO criar harnesses novos paralelos — registrar uma suíte aqui. Prereqs: `rag`
exige SUPABASE+OPENAI+golden; `writing` exige SUPABASE+LLM+`EVAL_WORKSPACE_ID`.
Triggers e contratos completos: `docs/specs/evaluation-operations.md`.

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
# Tier 1: Embeddings (padrão: OpenAI text-embedding-3-small 1536d, desde 2026-06-26)
EMBEDDING_MODEL=text-embedding-3-small
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
AGENT_PROFILE_EXTRACTOR_DEFAULT_ENABLED=false  # experimental; não promover sem eval

# Tier 5: Agente de escrita (base_url sobrescreve endpoint; ZDR/pago apenas)
# AGENT_OPENAI_BASE_URL=https://api.deepseek.com/v1
```
Ver `envs/.env.example` para referência completa com todos os overrides.

## Architecture

Para diagramas Mermaid detalhados do data plane, AI core, runtime agêntico e eval, ver [`docs/architecture.md`](docs/architecture.md).

### Data flow
```
Catálogo/match (gold — v3, produzido pelo run_daily_etl):
Bronze (FINEP/FAPESP/FAPESC/web raw via adapters por fonte)
  → core/ingestion/structurer.py                 (silver: data/silver/structured_docs/*.jsonl)
  → core/kg/gold.py `ingest_all()`     (incremental, diff por source_hash)
    → mapeadores determinísticos (metadados, agência, programa)
    → tagger LLM (setores/tecnologias) + constraints_producer (elegibilidade)
    → core/retrieval/embedder.py        (embed da entidade + match_chunks)
  → tabelas entities / entity_relationships / match_chunks (migration 036)
Catálogos versionados: data/silver/{investidores,programas}.json + bronze EMBRAPII.

Documento Canônico durável (fronteira comum dos adapters):
  → `core/kg/source_docs.py` persiste em `edital_source_docs` (disco é fallback local)

Edital chunks (para RAG na WritingSession — índice independente do gold):
  → procrastinate task `chunk_edital` (core/tasks.py)
  → core/retrieval/chunker.py    chunking estrutural por Art./§
  → core/contextual_retrieval.py  injeta contexto de capítulo via LLM
  → core/retrieval/embedder.py   OpenAI text-embedding-3-small
  → tabela edital_chunks (pgvector + tsvector)
  → aquecimento diário 05:00 UTC + ensure/prefetch sob demanda (mesmo produtor idempotente)
```
Paths em `core/config.py` (ROOT, BRONZE_DIR, SILVER_DIR, FINEP_PDFS_DIR, KNOWLEDGE_GRAPH_DIR).

### Package layout
```
backend/       api.py (shell: app + middleware + wiring) + routers/ por domínio
               (catalog, graph, matching, applications, brief, writing, files,
               profile) + auth_routes/library_routes + common.py (singletons +
               schema de perfil) + rate_limit.py (limiter)
core/          services/ (writing_session, match_v3, company_chunks,
               match_verdict, checklist, content_library, explore_agent,
               eligibility, opportunity_service), kg/ (kg_store, schema, gold,
               entity_catalog, constraints_producer, edital_id, temporal,
               source_docs, canonicalize), retrieval/
               (chunker, embedder, retriever, hyde),
               llm/ (llm_client, agent_runtime, agent_graph, agent_tools/),
               eval/ (harness); flat: db, auth, tasks (procrastinate),
               profile_extractor, opportunity_discovery, dou_feeder,
               web_search, contextual_retrieval, reranker, demais serviços
domain/        CompanyProfile dataclass (user_profile.py)
pipeline/      ETL multi-fonte (extractors/, adapters/)
scripts/       CLI: reindex_edital, reindex_all, export_to_obsidian, dev.sh, deploy.sh
skills/        playbooks de mecanismo (subvencao, credito, equity) — lidos pelo WritingAgent
supabase/      migrations/*.sql + config.toml (local CLI)
data/          bronze/ (raw imutável), silver/ (derivado + catálogos versionados)
```

### Core services
- **Match v3** (`core/services/match_v3.py`) — funil Stage 0 (vivo, deadline manda) → Stage 1 (`eligibility.py`: unsat elimina, unknown nunca) → Stage 2 (sum-of-max por chunk da empresa sobre `company_chunks` × `match_chunks`, boost de setores) → Stage 3 (rerank opcional + veredito LLM async via `match_verdict.py`). Trilha investidor paralela (cosseno perfil × `entities.embedding`). Lado empresa em `core/services/company_chunks.py` (refresh on-demand, RLS por workspace). Payload: `matched_excerpts[]` (trechos reais) + `setores`. Sem LLM no ranking.
- **ExploreAgent** (`core/services/explore_agent.py`) — 3 rotas: factual → reasoning → agent. Lê o gold via `entity_catalog`/SQL (busca semântica, tags compartilhadas e relações estruturais). Retorna string; o `profile_diff` é extraído pelo router (`backend/routers/explore.py`) via `ProfileExtractor`.
- **WritingSession** (`core/services/writing_session.py`) — runtime LangGraph (`agent_graph.py`) com checkpointer Postgres durável. RAG via `retrieve_chunks`. Primeiro turno: batch de 8 seções de uma vez (`_first_turn_with_generation`). `save_draft(force=False)` passa pelo Critic (subagente) antes de persistir.
- **ChecklistService** (`core/services/checklist_service.py`) — 3 passes paralelos via asyncio.gather: compliance + qualidade + completude.
- **ContentLibrary** (`core/services/content_library.py`) — CRUD + enrich_content via LLM. Soft-delete via `archived_at`.

### LLM runtime (core/llm/)
- **agent_runtime.py** — contrato `AgentResult`/`TraceStep`, `run_agent`/`run_agent_async`, `run_subagent`. Delega inteiramente ao grafo LangGraph; o loop hand-rolled foi removido.
- **agent_graph.py** — `StateGraph` ReAct (agent → tools → manage_memory → reflect → agent). Checkpointer Postgres. `interrupt()` para human-in-the-loop. Memória cross-session via PostgresStore.
- **agent_tools/** — tools LangChain nativas por domínio: `writing_tools`, `explore_tools`, `profile_tools`, `research_tools`, `planning_tools`, `scratchpad_tools`, `critic_agent`.
- **llm_client.py** — factory `make_client`/`make_async_client` com timeout/retry. **Todos os módulos usam esta factory** em vez de instanciar `OpenAI()` diretamente.

### Retrieval pipeline (core/retrieval/)
BM25 + dense via RRF (`fts_weight=0.5`, `DEFAULT_FTS_WEIGHT` em retriever.py). HyDE ativo por default (gera pseudo-doc antes de embedar). Contextual Retrieval injeta contexto de capítulo em cada chunk no ingest. Reranker: cross-encoder `mmarco-mMiniLMv2` (opcional, `pip install -e ".[rerank]"`); fallback `llm` (gpt-4o-mini) ou RRF puro.

### Background jobs (procrastinate — core/tasks.py)
- `chunk_edital_task` — chunking + contextual retrieval + embedding de um edital
- `warm_edital_chunks_task` (05:00 UTC) — aquece idempotentemente o corpus de escrita do catálogo
- `enrich_content_task` / `embed_content` — enriquecimento LLM ao upload da library
- `reflect_workspace` / `synthesize_patterns` — memória cross-session (Store)
- `run_daily_etl` (03:00 UTC) / `discover_opportunities` (04:00 UTC)

### API surface (backend/routers/ — wiring em backend/api.py)
```
GET  /stats, /editais, /editais/{id}, /opportunities, /oportunidades/{id}
POST /frontdoor/turn, /explore, /radar/matches, /match/verdicts
POST /writing/start, /writing/turn, /writing/section-start
GET  /writing/sessions, /writing/{id}/document
POST /writing/{id}/checklist/auto-review
GET  /discovered-opportunities, POST /discovered-opportunities/{id}/promote|reject
GET  /me, PUT /me/profile, PUT /me/preferences
GET/POST/PUT/DELETE /library, POST /library/{id}/archive
```

### Frontend
Next.js 14 + TypeScript + TailwindCSS + Radix UI. API client at `frontend/src/lib/api.ts`. Backend URL via `NEXT_PUBLIC_API_URL` in `frontend/.env.local`. Design tokens em `frontend/DESIGN_SYSTEM.md`. Toast canônico = **sonner**. Perfil da empresa acumulado no `localStorage` (não no backend) até `isRadarReady()`.

Jornadas primárias: Explorar `/`, Radar `/radar` e Projetos `/projects`.
Ecossistema permanece em `/oportunidades`; o ambiente interno de um projeto usa
`/workspace/{sessionId}`.

## Key Gotchas

### Imports
The package is installed via `pip install -e .`. All imports are absolute (`from core.services.match_v3 import find_matching_opportunities`). Never add `sys.path` hacks.

### Ingestão incremental gold
`core.kg.gold.ingest_all()` compara `source_hash` no Postgres e não reprocessa
entidades inalteradas. Use `python -m core.kg.gold --no-skip` para uma
reingestão deliberada.

### Trocar modelo LLM
Cada tier tem sua própria env var (ver seção LLM backend acima). Trocar um tier não afeta os outros. Modelos com dimensão de embedding diferente de 1536 exigem migração da coluna `vector(1536)` — não trocar sem eval.

### Reranker opcional
`sentence-transformers` não está nas deps padrão (evita torch em prod). Para usar `RERANK_BACKEND=cross-encoder`, instalar `pip install -e ".[rerank]"`. Em prod, usar `RERANK_BACKEND=llm` (gpt-4o-mini) ou deixar sem rerank (RRF puro).

### Ciclo de vida de capacidades
Flags default off não são automaticamente código morto. A classificação entre
ativa, opcional, experimental e dormente, incluindo gates de memória,
ProfileExtractor, CNPJ e embeddings locais, vive em
[`docs/reference/capability-lifecycle.md`](docs/reference/capability-lifecycle.md).
Não ative capacidades experimentais ou dormentes apenas porque o wiring existe.

### Discovery staging
`core/ingestion/opportunity_discovery.py` escreve em staging (tabela `discovered_opportunities`), não no KG. O gate humano em `/discovered-opportunities` promove/rejeita antes de tocar o pipeline de build.

### KG = tabelas gold
O KG ativo é relacional: `entities` + `entity_relationships` + `match_chunks`
(migration 036), lido por `core/kg/entity_catalog.py`. Match =
`core/services/match_v3.py`; catálogo/explore = `entity_catalog` + tools de
exploração. A vizinhança estrutural percorre `entity_relationships`; não há mais
hipergrafo JSON nem resolução cross-source por `(type, name)`.

`core/kg/kg_store.py` permanece por compatibilidade operacional do ledger de
Descoberta e das ferramentas de vocabulário. Não é o backend do catálogo ou do
match v3.
