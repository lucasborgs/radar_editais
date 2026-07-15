# System Spec — Radar de Editais (registro histórico)

> Reverse-spec gerada em 2026-06-22 por auditoria automatizada (5 agentes paralelos).
>
> **Registro histórico:** este inventário antecede as migrações gold v3 e
> contém nomes e fluxos removidos. Para o sistema vigente, consulte
> [`docs/architecture.md`](../architecture.md), [`README.md`](../../README.md) e
> [`AGENTS.md`](../../AGENTS.md).

---

## 1. Propósito

Radar de Editais conecta empresas brasileiras a oportunidades de financiamento público (editais FINEP, FAPESP, FAPESC, web) usando um pipeline ETL medallion, busca semântica e agentes LLM. O produto tem dois modos de interação:

- **FrontDoor** (chat conversacional): usuário descreve a empresa, o agente extrai perfil e retorna radar de oportunidades rankeadas.
- **Writing Session** (editor split-pane): usuário escreve proposta assistida por agente com RAG sobre chunks do edital.

---

## 2. Visão Geral da Arquitetura

```
┌─────────────────────────────────────────────────────────────────┐
│  FONTES EXTERNAS                                                 │
│  FINEP (Liferay API) · FAPESP · FAPESC (WordPress) · Web (seed) │
└────────────────────────────┬────────────────────────────────────┘
                             │ cron run_daily_etl (03h UTC)
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│  PIPELINE ETL                                                    │
│                                                                  │
│  Bronze (raw JSON/HTML)                                          │
│    └─ pipeline/extractors/{finep,fapesp,fapesc,web}.py           │
│                                                                  │
│  Silver (blocos estruturados)                                    │
│    └─ core/structurer.py  +  pipeline/adapters/{source}.py       │
│                                                                  │
│  Gold — KG index                                                 │
│    └─ pipeline/build_knowledge_graph.py                          │
│         → data/knowledge_graph/index.json (vigentes)            │
│         → data/knowledge_graph/wiki/{source}/{id}.json          │
│                                                                  │
│  Gold — RAG chunks                                               │
│    └─ procrastinate task chunk_edital                            │
│         → tabela edital_chunks (pgvector + tsvector)            │
└────────────────────────────┬────────────────────────────────────┘
                             │
                    ┌────────┴────────┐
                    │  KG Store       │
                    │  dev:  file     │
                    │  prod: postgres │
                    └────────┬────────┘
                             │
┌────────────────────────────▼────────────────────────────────────┐
│  BACKEND FastAPI  (backend/api.py)                               │
│                                                                  │
│  Matching                    RAG / Writing                       │
│  HybridMatchService          WritingSession                      │
│  KGMatchService              ChecklistService                    │
│  RadarService                ContentLibrary                      │
│                                                                  │
│  Runtime de Agentes                                              │
│  core/llm/agent_graph.py  ← StateGraph LangGraph (3 nós)        │
│  core/llm/agent_runtime.py ← facade de contratos                │
│  core/llm/agent_tools/    ← 7 módulos de tools                  │
└────────────────────────────┬────────────────────────────────────┘
                             │ HTTP + Supabase RLS
┌────────────────────────────▼────────────────────────────────────┐
│  FRONTEND  Next.js 14 (App Router)                               │
│  / (FrontDoor)  ·  /workspace/[id]  ·  /editais  ·  /perfil     │
│  /settings  ·  /library  ·  /pipeline                           │
└─────────────────────────────────────────────────────────────────┘
```

---

## 3. Fontes de Dados

### 3.1 Fontes Ativas

| Fonte    | Extrator                          | Adapter                     | Cadência              | Observações                                      |
|----------|-----------------------------------|-----------------------------|----------------------|--------------------------------------------------|
| `finep`  | `pipeline/extractors/finep.py`    | `pipeline/adapters/finep.py`| cron run_daily_etl   | API Liferay OAuth2; baixa PDFs para pdfplumber   |
| `fapesp` | `pipeline/extractors/fapesp.py`   | `pipeline/adapters/fapesp.py`| cron run_daily_etl  | Snapshot mais recente do bronze                  |
| `fapesc` | `pipeline/extractors/fapesc.py`   | `pipeline/adapters/fapesc.py`| cron run_daily_etl  | WordPress REST; baixa PDF para texto_cru         |
| `web`    | `pipeline/extractors/web.py`      | `pipeline/adapters/web.py`  | cron discover_opportunities (04h UTC) | Bronze aditivo (multi-feeder, dedup por url_hash) |

### 3.2 Fontes Removidas (referências residuais apenas)

- **BNDES**, **CNPq**, **PNCP** — mencionados em comentários/testes/exemplos. Nenhum extrator existe. Sem efeito em runtime.

### 3.3 Entidade Paralela: ICTs

- `pipeline/extractors/ict_embrapii.py` → `pipeline/build_ict_graph.py` → `data/knowledge_graph/icts.json`
- **Não entra no cron `run_daily_etl`** — executado manualmente. Dados estáticos entre runs.

### 3.4 Dados Curados Manualmente

- `data/knowledge_graph/investidores.json` — fundos de VC para match de investidores
- `data/knowledge_graph/programas.json` — programas recorrentes multi-edital

---

## 4. Pipeline ETL (Detalhe)

### 4.1 build_knowledge_graph.py (L0 → Gold Index)

```
load_bronze(source)
  ↓
_build_editais()       normaliza → entry com id={source}:{native_id}
  ↓
_apply_pme_filter()    descarta não-PME, loga em .filter_rejections.jsonl
  ↓
_split_vigencia()      ABERTAS vs todas
  ↓
_carry_forward_match_fields()   carrega campos de match da wiki store durável
  ↓
kg_store.save()        arquivo local + upsert kg_artifacts (Supabase)
```

Output: `index.json` (vigentes) e `index_historico.json` (todos aceitos).

### 4.2 etl_process.py (L1 → L2 → Wiki Pages)

```
Para cada edital em index.json:
  get_adapter(source)
    ↓ FINEP: pdfplumber · FAPESP/FAPESC/Web: html_to_text
  core/structurer.build_or_load_structured_doc()  → blocos silver (L2)
    ↓
  _call_llm()   extrai wiki page estruturada (gemini-2.5-flash padrão)
    ↓ cache por content_hash (MD5 metadata+silver_meta) → pula se inalterado
  salva KG_WIKI_DIR/{source}/{native_id}.json + blob no Postgres
```

### 4.3 RAG Ingestão (task chunk_edital)

```
Source Adapter → Documento Canônico (L1)
  ↓
core/structurer  → blocos silver (L2)
  ↓
core/retrieval/chunker.py  ~800 tokens/chunk, overlap 150
  flags por chunk: contem_data · contem_valor_financeiro · contem_elegibilidade
                   contem_criterios · contem_tabela
  ↓
core/retrieval/embedder.py  text-embedding-3-large 1536d (padrão)
  ↓
edital_chunks (pgvector + tsvector)
```

### 4.4 RAG Retrieval (core/retrieval/retriever.py)

- **Dense**: pgvector cosine (`embedding_gemma` padrão, coluna configurável via `RETRIEVAL_EMBEDDING_COLUMN`)
- **Sparse**: BM25 Okapi em Python (`rank_bm25`)
- **Fusão**: RRF k=60, `fts_weight=0.3`
- **Boosts**: `primary_boost=1.5` (edital primário), `metadata_boost=1.2` (intent detection regex)
- **Rerank**: `core.reranker.rerank_scores()` sobre top-20 (degradação graciosa)
- **Dedup**: máx 2 chunks por `source_file`
- **HyDE (Hypothetical Document Embeddings)**: gera um pseudo-trecho de edital via
  LLM e embeda *esse trecho* no lugar da query crua (Gao et al., 2022). Atua
  exclusivamente no braço dense (query-side), antes do embedding. **Ativado por
  default** (`hyde=True`). Fallback silencioso: se o LLM falhar/timeout, usa a
  query original. Modelo/config independentes: `HYDE_MODEL` (default `gpt-4o-mini`),
  `HYDE_BASE_URL`, `HYDE_API_KEY`, `HYDE_TIMEOUT_SECONDS`.

RAG é **exclusivo da WritingSession** — matching usa embeddings de summary-level separados.

### 4.5 Estrutura do KG Output

| Arquivo | Origem | Conteúdo |
|---------|--------|----------|
| `index.json` | build_knowledge_graph | Editais vigentes + índices invertidos (tema/público/fonte/subprograma/ano) |
| `index_historico.json` | idem | Todos aceitos pelo filtro PME |
| `wiki/{source}/{id}.json` | etl_process | Wiki page completa por edital |
| `icts.json` | build_ict_graph | Unidades EMBRAPII com themes mapeados |
| `investidores.json` | curado | Fundos de VC |
| `programas.json` | curado | Programas recorrentes |
| `.etl_process_cache.json` | etl_process | Cache MD5 de síntese LLM |
| `.discovery_ledger.json` | opportunity_discovery | URLs já processadas pela Descoberta |
| `.filter_rejections.jsonl` | build_knowledge_graph | Log de rejeições PME (sobrescreve a cada run) |

**KG backends** (`core/kg/kg_store.py`):
- Dev: `KG_STORE_BACKEND=file` → arquivos locais com mtime-cache
- Prod: `KG_STORE_BACKEND=postgres` → tabela `kg_artifacts` (JSONB, TTL 60s), fallback para arquivo

---

## 5. Runtime de Agentes (LangGraph)

### 5.1 Migração

**A migração do runtime AI-native para LangGraph está 100% completa.** Não existe path legado ativo. O worktree `.claude/worktrees/agent-a731d1f7950269759/` contém o loop hand-rolled antigo (Anthropic SDK direto com `ToolUseBlock`) — é artefato de sprint, não afeta `$PYTHONPATH`.

### 5.2 Arquivos do Runtime

| Arquivo | Papel |
|---------|-------|
| `core/llm/agent_graph.py` | **Implementação real**: `StateGraph` 3 nós (agent → tools → reflect), checkpointer (Postgres via LangGraph), memory store (Etapa 5), geração em lote por seção |
| `core/llm/agent_runtime.py` | **Facade de contratos**: `AgentResult`, `TraceStep`, `StopReason`; shims `run_agent()` / `run_subagent()`; `resolve_agent_provider()`; constantes (`_cap`, `TOOL_RESULT_CHAR_CAP`, `_REFLECT_PROMPT`) |
| `core/llm/llm_client.py` | Factory `make_client`/`make_async_client` (OpenAI SDK direto, para módulos não-agênticos como extratores, reranker, compliance) |

### 5.3 Grafos Ativos

| Grafo | Função | Call sites |
|-------|--------|------------|
| `run_agent_graph_async()` | Ciclo agent→tools→reflect genérico | KGMatchService, ProfileExtractor, deep_research |
| `run_writing_turn()` | Turno conversacional com interrupt/resume | WritingSession._run_agent() |
| `run_generation_turn()` | Geração em lote por seção (2 grafos: orquestrador + agente interno) | WritingSession._run_generation() |

### 5.4 Tools por Domínio

| Arquivo | Factory | Tools | Consumidores |
|---------|---------|-------|--------------|
| `explore_tools.py` | `build_explore_tools(service)` | `list_editais`, `get_edital`, `find_analogues`, `get_graph_neighbors`, `find_ict_partners`, `list_icts`, `list_investidores`, `oportunidades_por_tema`, `search_edital_trechos` | KGMatchService |
| `writing_tools.py` | `build_writing_tools(session)` | `search_edital`, `search_library`, `read_section`, `read_full_proposal`, `save_draft`, `recall_company_learnings`, `request_user_info`, `load_skill` | WritingSession (compõe research + planning) |
| `planning_tools.py` | `build_planning_tools(state)` | `write_todos` | KGMatchService, WritingSession, ProfileExtractor |
| `scratchpad_tools.py` | `build_scratchpad_tools(s)` | `write_note`, `read_note` | ProfileExtractor |
| `research_tools.py` | `build_research_tools()` | `deep_research` (subagente) | KGMatchService (opcional, `DEEP_RESEARCH_ENABLED`), WritingSession |
| `profile_tools.py` | `build_profile_tools(state)` | `fetch_page`, `list_links_matching`, `lookup_cnpj`, `submit_profile` | ProfileExtractor + opportunity_discovery (uso direto de `_fetch_and_parse`) |
| `critic_agent.py` | `build_critic_tools(...)` | `read_target_context`, `read_company_profile`, `read_proposal_sections` | Chamado internamente por `save_draft` via `run_critic()` |

### 5.5 Providers LLM

- **Agentes** (`agent_graph.py`): `ChatAnthropic` ou `ChatOpenAI` (LangChain); resolução automática por API key via `resolve_agent_provider()` com fallback.
- **Módulos não-agênticos** (extratores, reranker, compliance): OpenAI SDK direto via `make_client()` / `make_async_client()`.
- **ETL/extração de wiki**: Gemini (`gemini-2.5-flash` padrão) — `core/llm/llm_client.py` com `LLM_BACKEND` env.

---

## 6. Backend FastAPI

### 6.1 App e Middleware

**Arquivo:** `backend/api.py`

Cadeia ASGI (mais externo → mais interno):
1. `RequestIdMiddleware` — atribui `x-request-id` (UUID12), propaga via contextvar, ecoa no response header
2. `CORSMiddleware` — origens: `localhost:3000` + `127.0.0.1:3000` + `FRONTEND_URL` env
3. `slowapi` rate limiting (`backend/rate_limit.py`)

Exception handler global: devolve 500 com `request_id` no body + header CORS replicado manualmente.

### 6.2 Singletons (backend/common.py)

```python
wiki_matcher = HybridMatchService()   # instanciado no boot, compartilhado por todos os routers
kg_service   = KGMatchService()       # idem
```

Também expõe: `CompanyProfileSchema` (Pydantic), `to_py_profile()`, `load_library_items()`, `profile_from_workspace()`.

### 6.3 Mapa Routers → Services

| Router | Endpoints principais | Service/módulo |
|--------|----------------------|----------------|
| `auth_routes.py` | GET /me · PUT /me/profile · PUT /me/preferences · POST /me/reflect · POST /me/synthesize · GET /me/weights · POST /me/weights/approve | `reflection_service`, `weight_approval`, `profile_drift`, `tasks` |
| `catalog.py` | GET /editais · GET /editais/{id} · GET /stats · GET /commands | `wiki_matcher` (HybridMatch) |
| `graph.py` | GET /graph · POST /kg-explore | `kg_service` (KGMatch) |
| `frontdoor.py` | POST /frontdoor/turn | `kg_service.explore_turn`, `profile_extractor`, `writing_session.persist_frontdoor_turn` |
| `matching.py` | POST /match · POST /match/investidores · POST /match/programas · POST /match/radar | `wiki_matcher`, `investor_match`, `programa_match`, `radar_service` |
| `applications.py` | GET /applications · PUT /applications/{id}/status | `wiki_matcher`, `reflection_service`, `tasks` |
| `brief.py` | POST /opportunity/brief | `opportunity_brief_service` |
| `writing.py` | POST /writing/start · /turn · /generate · GET sessions · checklist | `WritingSession`, `ChecklistService`, `compliance_monitor` |
| `conversations.py` | GET /conversations · GET /conversations/{id} · POST/PATCH entries | `writing_session` (list/get/append/update) |
| `library_routes.py` | GET/POST/PUT/DELETE /library · upload-pdf · archive | `content_library`, `retriever` |
| `files.py` | GET /files · /files/signed-url | `content_library`, Supabase Storage direto |
| `profile.py` | POST /profile/extract · extract-from-document · extract-from-library | `ProfileExtractor`, `profile_inference`, `content_library` |
| `research.py` | GET /research-findings · POST /{id}/promote | `content_library.create_item` |
| `discovered.py` | GET /discovered-opportunities · POST /{id}/promote · reject | Supabase service-role direto |
| `playbooks.py` | GET /playbooks/{mechanism}/layers | `core.skills.resolve_playbook_layers` |

### 6.4 Serviços Core

**`HybridMatchService`** (`core/services/hybrid_match_service.py`)
- Stage 1 determinístico (Pandas): 5 dimensões com pesos configuráveis (elegibilidade 30 + temático 25 + TRL 20 + mecanismo 15 + contrapartida 10 + elegibilidade_dura 10 condicional). Threshold de eliminação = 25. Cache TTL 60s da tabela `matching_weights`.
- Stage 2 LLM: só para editais que passaram no Stage 1.

**`KGMatchService`** (`core/services/kg_match_service.py`)
- LLM lê índice completo + perfil sem embeddings ("Karpathy-style").
- `explore()`: chat livre sobre catálogo (quando `AGENT_EXPLORE_DEFAULT_ENABLED=true`).
- `explore_turn()`: resposta + profile_updates num único JSON 1-shot (padrão atual).
- Motor do FrontDoor e da visualização do grafo.

**`WritingSession`** (`core/services/writing_session.py`)
- DB-backed: headers em `writing_sessions` + turnos em `session_turns`.
- Estado totalmente recuperado do Postgres a cada request (sem cache entre requests).
- Usa runtime LangGraph via `run_agent()` → `agent_graph.py`.
- Janela de contexto: 6 turnos verbatim + compressão após 10 (persiste `summary` no Postgres).

**`ChecklistService`** (`core/services/checklist_service.py`)
- `build_checklist()`: extrai requisitos da `wiki_page.key_requirements` + fatos Tier 1 com verbos de obrigatoriedade.
- `auto_review_checklist()`: 3 passes paralelos via `asyncio.gather` — Compliance + Qualidade + Completude.

**`ContentLibrary`** (`core/services/content_library.py`)
- CRUD de `content_items` por workspace (propostas, PDFs técnicos, findings).
- `create_item` é async — enfileira `enrich_content_task` no procrastinate.

**`RadarService`** (`core/services/radar_service.py`)
- Orquestra HybridMatch + investor_match + programa_match via RRF (k=60).

### 6.5 Auth

**Arquivo:** `core/auth.py`

Fluxo:
1. Frontend → Supabase magic link (OTP email) → JWT.
2. Backend aceita HS256 (`SUPABASE_JWT_SECRET`) e ES256 (JWKS cacheado em processo).
3. Client Supabase por request via `get_supabase_user(jwt)` → todas as queries RLS-gated.
4. `DEMO_MODE`: bypassa JWT, usa service-role e workspace mais recente.

Dependências exportadas: `CurrentUserId`, `DbClient`, `OptionalUserId`, `OptionalDbClient`.

`get_supabase()` → **alias deprecated** para `get_supabase_service()`, mantido por compat de scripts.

### 6.6 Background Jobs (procrastinate)

| Task | Trigger | Função |
|------|---------|--------|
| `enrich_content` | create/update item na library | LLM → summary/key_facts/themes/importance_score; encadeia `embed_content` |
| `embed_content` | por `enrich_content` | text-embedding-3-large 1536d para content_item |
| `reflect_workspace` | outcome registrado + /me/reflect | Gera reflexão; persiste em `reflection_insights` |
| `synthesize_patterns` | /me/synthesize + cron semanal | Distila padrões L2 + `weight_suggestions` a partir de observações |
| `synthesize_patterns_cron` | Cron `0 5 * * 0` (domingo 05h UTC) | Enfileira `synthesize_patterns` para todos workspaces ativos |
| `chunk_edital` | ETL diário + discover + manual | Adapter L1 → blocos silver → chunker → embedder → upsert `edital_chunks` |
| `run_daily_etl` | Cron `0 3 * * *` (03h UTC) | Scrapers FINEP+FAPESP → chunk_edital → reconstrói índice → wiki pages → Obsidian vault |
| `discover_opportunities` | Cron `0 4 * * *` (04h UTC) | Busca livre (Tavily) → web_raw → chunk_edital → reconstrói índice |

Worker: `python -m procrastinate --app=core.tasks.app worker`. Conector: `PsycopgConnector` (psycopg3 async).

---

## 7. Frontend Next.js 14

### 7.1 Rotas Ativas

| Rota | Função |
|------|--------|
| `/` | FrontDoor — chat conversacional + radar de oportunidades inline |
| `/login` | Magic link Supabase OTP |
| `/auth/redirect` | Callback pós-OTP → redireciona para `/` |
| `/workspace/[sessionId]` | Editor split-pane (doc + chat + checklist) |
| `/editais` | Catálogo tabular com filtros |
| `/editais/[id]` | Detalhe de edital |
| `/perfil` | Gerenciador de perfil (extração URL/PDF + campos manuais) |
| `/settings` | Preferências + pesos de matching + insights |
| `/library` | Gerenciador de arquivos (upload, archive, research findings) |
| `/pipeline` | Kanban de candidaturas |

### 7.2 Rotas Mortas (redirects para `/`)

| Rota | Status |
|------|--------|
| `/dashboard` | `redirect("/")` |
| `/matching` | `redirect("/")` |
| `/sessions` | `redirect("/")` |
| `/onboarding` | `redirect("/")` |
| `/chat` | Resolver legado: `?edital=X` → cria sessão → `/workspace/{id}` |

### 7.3 State Management

Sem React Query, SWR, Redux ou Zustand. Padrão:
- `useState` + `useEffect` + flags `cancelled/alive` para cancelamento
- `useAsync` hook homemade (`frontend/src/lib/hooks.ts`)
- `AuthContext` (`frontend/src/lib/auth.tsx`) — único estado global real
- `sessionStorage` — transcript do FrontDoor
- `localStorage` — `CompanyProfile`, flags de onboarding

### 7.4 Auth Frontend

1. `/login` → `supabase.auth.signInWithOtp({ email })`
2. SDK resolve callback, redireciona via `/auth/redirect/page.tsx`
3. `AuthProvider` monitora `onAuthStateChange`
4. Todas as chamadas em `api.ts` injetam JWT Bearer automaticamente via `getToken()`
5. FrontDoor e /match/radar funcionam sem token (auth opcional)

### 7.5 Endpoints em api.ts Sem Consumidor Frontend

Estes estão definidos em `frontend/src/lib/api.ts` mas **não são chamados em nenhuma página ou componente**:

| Função | Endpoint |
|--------|----------|
| `getGraph` | `GET /graph` |
| `kgExplore` | `POST /kg-explore` |
| `getMatches` | `POST /match` |
| `getInvestorMatches` | `POST /match/investidores` |
| `getProfileDrift` | `GET /me/profile/drift` |
| `listWritingSessions` | `GET /writing/sessions` |
| `extractProfileFromLibraryItem` | `POST /profile/extract-from-library/{id}` |
| `startSectionChat` | `POST /writing/section-start` |

---

## 8. Código Morto e Legados Detectados

### 8.1 Arquivos Mortos Confirmados

| Arquivo | Evidência | Ação sugerida |
|---------|-----------|---------------|
| `pipeline/parsers/docling_blocks.py` | Único caller: `scripts/bench_parsing.py` (benchmark offline). Não está em nenhum caminho de ingestão. | Mover para `scripts/` ou remover |
| `frontend/src/components/KnowledgeGraph.tsx` | Não importado em nenhuma página/componente. Era o grafo do dashboard antigo. | Remover |
| `frontend/src/components/writing/DocumentCanvas.tsx` | Não importado em nenhuma página. Torna `ChecklistPanel.tsx` transitivamente morto. | Remover |
| `frontend/src/components/writing/AttachToLibrary.tsx` | Não importado (apenas em comentário em `/library`). | Remover |

### 8.2 Rotas de Backend Sem Consumidor Frontend

Existem no backend e têm implementação, mas o frontend não as chama:

| Endpoint | Router | Possível motivo |
|----------|--------|-----------------|
| `GET /graph` | `graph.py` | Dashboard de grafo removido em favor do FrontDoor |
| `POST /kg-explore` | `graph.py` | Modo explore desativado no frontend |
| `POST /match` | `matching.py` | Substituído por `/match/radar` |
| `POST /match/investidores` | `matching.py` | Chegam via radar (não separadamente) |
| `GET /me/profile/drift` | `auth_routes.py` | Feature drift nunca exposta no frontend |
| `GET /writing/sessions` | `writing.py` | Substituído por `/conversations` |
| `POST /profile/extract-from-library/{id}` | `profile.py` | Fluxo nunca chegou ao frontend |
| `POST /writing/section-start` | `writing.py` | Specada, não implementada no frontend |

### 8.3 Módulos Core Potencialmente Órfãos

Não confirmados como mortos (podem ter callers via scripts/CLI), mas sem importadores nos routers ou services:

| Módulo | Status aparente |
|--------|-----------------|
| `core/match_embeddings.py` | Não importado em routers/services visíveis — possivelmente usado só em scripts/eval |
| `core/pme_filter.py` | Não importado em routers — possivelmente absorvido no Stage 1 do HybridMatch |
| `core/ict_match.py` | Fase ICT no backlog — não chamado em runtime |

### 8.4 Aliases Deprecated no Código

| Símbolo | Localização | Observação |
|---------|-------------|------------|
| `get_supabase()` | `core/db.py` | Alias deprecated para `get_supabase_service()` |
| `load_finep_bronze` | `pipeline/build_knowledge_graph.py:98` | Marcado explicitamente como "alias depreciado — mantido por compat" |
| Docstring `core/llm/__init__.py` | linha 2 | Diz "runtime Anthropic (agent_runtime)" — desatualizado; runtime hoje é LangGraph |
| Flag `AGENT_RUNTIME=legacy` | Só em changelog histórico (`langgraph-migration.md`) | Não existe mais no código — o path legacy foi removido (docstrings/comentários já limpos) |

---

## 9. Módulos Flat de core/ — Inventário

Além dos services em `core/services/`, existem módulos flat em `core/` com papéis variados:

| Módulo | Papel | Onde é chamado |
|--------|-------|----------------|
| `core/profile_extractor.py` | Extrai `CompanyProfile` de URL/PDF/texto via agente | `profile.py` router, `frontdoor.py` |
| `core/profile_inference.py` | Infere `mecanismo_interesse` e `financiamento_hist` | `profile.py` router |
| `core/opportunity_discovery.py` | Busca livre (Tavily) → oportunidades web | cron `discover_opportunities` |
| `core/dou_feeder.py` | Scraper DOU (desabilitado por padrão, `DISCOVERY_DOU_ENABLED=1`) | Chamado por `opportunity_discovery` |
| `core/deep_research.py` | Subagente de pesquisa web profunda | `research_tools.py` |
| `core/edital_extractor.py` | Extrai campos de edital para enriquecimento | `etl_process.py` |
| `core/eligibility_producer.py` | Produz `eligibility_constraints` via LLM | pipeline/ETL |
| `core/structurer.py` | Converte documento canônico em blocos silver | `etl_process.py`, `tasks.py` |
| `core/reranker.py` | Cross-encoder rerank de chunks RAG | `retriever.py` |
| `core/reflection_service.py` | Gera reflexões sobre outcomes e learning | `auth_routes.py`, `applications.py` |
| `core/weight_approval.py` | Aprovação de sugestões de pesos pelo usuário | `auth_routes.py` |
| `core/compliance_monitor.py` | Monitora compliance de seções da proposta | `writing.py` |
| `core/opportunity_brief_service.py` | Gera brief GO/NO-GO por edital | `brief.py` |
| `core/skills.py` | Resolve playbooks e skills de escrita | `playbooks.py`, `writing_tools.py` |
| `core/web_search.py` | Abstração Tavily para pesquisa web | `deep_research.py`, `opportunity_discovery.py` |
| `core/telemetry.py` | Logging/telemetria | Utilitário interno |
| `core/vocab_lint.py` | Valida vocabulários do schema wiki | `wiki_schema.py` |
| `core/web_identity.py` | Resolve identidade web de empresa | `profile_extractor.py` |

---

## 10. Variáveis de Ambiente Relevantes

| Variável | Efeito |
|----------|--------|
| `LLM_BACKEND` | `openai` (padrão) ou `ollama` para módulos não-agênticos |
| `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` | Providers dos agentes LangGraph |
| `OPENAI_MODEL` | Modelo para módulos não-agênticos (`gpt-4o-mini` padrão) |
| `OPENAI_MODEL_AGENT` / `ANTHROPIC_MODEL_AGENT` | Modelo dos agentes LangGraph |
| `OLLAMA_MODEL` | Modelo local quando `LLM_BACKEND=ollama` |
| `KG_STORE_BACKEND` | `file` (dev) ou `postgres` (prod) |
| `RETRIEVAL_EMBEDDING_COLUMN` | Coluna pgvector usada no retrieval (`embedding_gemma` padrão) |
| `HYDE_MODEL` | Modelo de chat para HyDE (`gpt-4o-mini` padrão) |
| `HYDE_BASE_URL` | Endpoint OpenAI-compat para HyDE (ex.: Ollama local) |
| `HYDE_API_KEY` | Key do provider HyDE (fallback: `OPENAI_API_KEY`) |
| `HYDE_TIMEOUT_SECONDS` | Timeout curto para HyDE (`10` padrão — está no caminho crítico do turno) |
| `AGENT_EXPLORE_DEFAULT_ENABLED` | Liga modo explore multi-turn no KGMatchService |
| `DEEP_RESEARCH_ENABLED` | Liga subagente de pesquisa profunda no KGMatch |
| `DISCOVERY_DOU_ENABLED` | Liga scraper do Diário Oficial |
| `DEMO_MODE` | Bypassa JWT completamente (service-role) |
| `SUPABASE_URL` / `SUPABASE_ANON_KEY` / `SUPABASE_JWT_SECRET` / `SUPABASE_SERVICE_KEY` | Conexão Supabase |
| `DATABASE_URL` | psycopg3 para procrastinate |
| `FRONTEND_URL` | Origens CORS adicionais além de localhost:3000 |

---

## 11. Estado de Maturidade por Área

| Área | Maturidade | Observações |
|------|------------|-------------|
| Pipeline ETL (FINEP + FAPESP) | ✅ Produção | Multi-fonte end-to-end; incremental com cache MD5 |
| Pipeline ETL (FAPESC) | ✅ Ativo | WordPress REST; menos testado que FINEP |
| Pipeline ETL (Web/Descoberta) | ✅ Ativo | Gated em queue humano (`discovered_opportunities`) |
| Knowledge Graph Store | ✅ Produção | Dual-backend file/postgres com fallback |
| HybridMatch Stage 1 | ✅ Produção | Pesos configuráveis, eval-gated |
| HybridMatch Stage 2 (LLM) | ✅ Produção | |
| KGMatch / FrontDoor | ✅ Produção | Motor principal do chat |
| Runtime LangGraph | ✅ Migração completa | 3 nós + checkpointer + memory store |
| WritingSession + RAG | ✅ Produção | Contextual Retrieval + rerank |
| ChecklistService | ✅ Produção | 3 passes paralelos |
| ContentLibrary | ✅ Produção | Enrich LLM async |
| RadarService (RRF) | ✅ Produção | Unifica 3 quadrantes |
| ICT / EMBRAPII | 🟡 Parcial | Extrator ativo, não entra no cron; match ICT no backlog |
| Investidores / VC | 🟡 Parcial | Match ativo, sem UI dedicada |
| Reflection / Pesos adaptativos | 🟡 Em progresso | Backend pronto, UX em settings |
| Frontend Dashboard (grafo KG) | ❌ Removido | Componente morto, endpoints sem consumidor |
| Testes / Eval | ℹ️ Ver BACKLOG | Suítes: matching, rag, writing, extraction |

---

---

## 12. Testes e Suítes de Eval

### 12.1 Cobertura de Testes (72 arquivos)

| Frente | Arquivos de teste | Cobertura |
|--------|-------------------|-----------|
| Matching | `test_hybrid_match_*.py`, `test_filter_*.py`, `test_pme_filter.py`, `test_match_card_fields.py`, `test_match_embeddings.py`, `test_radar_service.py` | Stage 1 + Stage 2 + filtros + elegibilidade dura + radar RRF |
| Investidores/Programas | `test_investor_match.py`, `test_investor_eval.py`, `test_programa_match.py` | Match por tese + avaliação |
| ICT | `test_ict_match.py` | Match ICT (feature em backlog) |
| RAG | `test_retriever.py`¹, `test_chunker.py`, `test_contextual_retrieval.py`, `test_reranker.py`, `test_golden_comparison_cases.py` | Chunking + retrieval + rerank + goldens |
| Pipeline | `test_fapesp_extractor.py`, `test_fapesp_adapter.py`, `test_fapesc_extractor.py`, `test_pipeline_health.py`, `test_edital_extraction.py`, `test_eval_extraction.py`, `test_parsing_*.py`, `test_chunk_edital_gate.py` | Extratores + adapters + health + extração |
| Escrita | `test_writing_eval.py`, `test_writing_session_agent.py`, `test_writer_prompts.py`, `test_checklist_service.py` | Agente de escrita + prompts + checklist |
| Agentes/Runtime | `test_agent_runtime.py`, `test_agent_graph_*.py`, `test_agent_tools_registry.py`, `test_scratchpad_tools.py`, `test_planning_tools.py`, `test_subagent.py`, `test_deep_research.py`, `test_explore_*.py`, `test_load_skill_tool.py`, `test_context_budget.py`, `test_reflection_trigger.py`, `test_critic_coherence.py` | LangGraph + tools + subagentes + explore + checkpointer + memory |
| KG / Schema | `test_kg_store.py`, `test_kg_store_wiki.py`, `test_wiki_schema_consistency.py`, `test_vocab_lint.py`, `test_temporal.py`, `test_edital_id.py`, `test_resolve_scope.py` | Store + schema + vocabulário + temporal |
| Perfil / FrontDoor | `test_profile_extractor_*.py`, `test_profile_inference.py`, `test_frontdoor_turn.py`, `test_conversations.py` | Extração de perfil + frontdoor |
| Infra | `test_checkpointer_postgres.py`, `test_memory_store*.py`, `test_telemetry_*.py`, `test_web_search.py`, `test_web_fetch.py`, `test_applications_pipeline.py`, `test_feedback_endpoint.py`, `test_document_extraction.py`, `test_export_investidor.py`, `test_dou_feeder.py`, `test_opportunity_discovery*.py` | Postgres checkpointer + memoria + telemetria + web + candidaturas |

¹ `test_retriever.py`: integração com pgvector real exige fixture com DB ativo — TODO aberto, sem cobertura de integração.

### 12.2 Suítes Eval Registradas (core/eval/registry.py)

| Suíte | O que mede |
|-------|------------|
| `matching` | Precisão@K do HybridMatch via rúbrica LLM + vigência/elegibilidade |
| `rag` | Recall/Hit@K + reciprocal rank + faithfulness do retriever de chunks |
| `writing` | Qualidade do agente de escrita |
| `extraction` | Presença/abstenção + correção de value + faithfulness da extração vs golden |
| `investor_match` | Precisão@K do match por tese de investidor via rúbrica LLM + expected_hit |
| `opportunity_type` | Acurácia da classificação edital/desafio/programa |
| `triage` | Triagem da Descoberta: acurácia + guarda de falso negativo |
| `profile_extractor` | Acerto de campos do CompanyProfile vs golden |
| `reranker` | top1_accuracy + NDCG@3 vs golden |
| `structurer` | Classificação de blocos (kind) + presença de headings vs golden |
| `compliance_monitor` | Recall + precision das flags vs golden (offline, sem banco) |

### 12.3 TODOs e DEPRECATEDs Relevantes

O codebase principal tem apenas 3 ocorrências relevantes:

| Arquivo:linha | Tipo | Conteúdo |
|---------------|------|----------|
| `core/db.py:68` | DEPRECATED | `get_supabase()` alias retrocompat — código novo deve usar `DbClient`. Ainda referenciado por pipelines/scripts legados. |
| `core/skills.py:198` | TODO | Acesso ao banco de overlays de skills faz fallback silencioso para `[]` — sem regressão mas sem cobertura de erro. |
| `tests/test_retriever.py:7` | TODO | Integração com pgvector real exige fixture com DB ativo — não existe. |

---

## 13. Documentação Relacionada

### Visão Geral
- [Arquitetura AI (diagramas Mermaid)](../architecture.md) — data plane, matching dual, runtime agêntico, eval loop

### Por Componente Técnico (`docs/components/`)
- **Agentes:** [LangGraph migration](langgraph-migration.md) · [Deep research](deep-research-design.md) · [Memory architecture](memory-architecture-2026-06.md) · [Auditoria agêntica (00-09)](agent-audit-2026-06-13.md)
- **Matching:** [Embedding bake-off](embedding-bakeoff.md)
- **Knowledge/Playbooks:** [Knowledge evolution](knowledge-evolution.md) · [Skills by mechanism](skills-by-mechanism.md) · [Playbook authoring guide](../reference/playbook-authoring.md)
- **Frontend:** [Chat-first architecture](chat-first-architecture.md)
- **Infra:** [Demo cost profile](demo-cost-profile.md)

### Features Ativas / WIP (`docs/features/`)
- [FrontDoor UX](frontdoor-ux.md) · [Workspace UX](workspace-ux.md) · [ICT Fase C](ict-phase-c.md)
- [Robustez match+escrita](robustez-match-escrita.md) · [Eligibility constraints](eligibility-constraints.md)
- [Discovery Parte C (FAPs)](discovery-opportunities.md) · [Explore grounded](explore-grounded-comparison.md)

### Histórico / ADRs (`docs/historical/`)
- [Multi-quadrante (ADR)](spec-multi-quadrante.md) · [Agent patterns](spec-agent-patterns.md) · [Mechanism scope decisions (ADR)](mechanism-scope-decisions.md)
- [Extraction schema v2](extraction-schema.md) · [ICT mapping base](ict-mapping.md) · [KG entity wiki pages](kg-entity-wiki-pages.md)
- [Backend reorg](refactor-backend.md) · [DOU feeder](dou-feeder.md) · [Onboarding input UX](onboarding-input-ux.md)

*Gerado por auditoria de 6 agentes paralelos em 2026-06-22. Para atualizar: re-rodar os agentes e mergear findings.*
