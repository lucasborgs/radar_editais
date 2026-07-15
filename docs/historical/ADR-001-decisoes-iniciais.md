# ADR-001 — Decisões arquiteturais iniciais

> Data: 2026-05-12
> Status: Aceito
> Escopo: Fechamento das 18 decisões pendentes que destravam a implementação das Fases 1–4 do roadmap descrito em [RADAR.md](RADAR.md).

---

## Premissas (não rediscutidas)

- Backend: FastAPI / Frontend: Next.js
- DB relacional: Supabase (Postgres + Auth + Storage)
- Vector DB: pgvector (migrando de ChromaDB)
- PDF parsing: pdfplumber (+ PyMuPDF/Docling como fallback OCR)
- Scraping: requests + Playwright on-demand

---

## Bloco A — Decisões que bloqueiam código

### A1 — Estratégia de LLM por tarefa
**Decisão:** OpenAI only na fase inicial. Evoluir para multi-provider (Sonnet escrita + Haiku matching + Gemini ETL) em fase posterior.
**Justificativa:** Comodidade operacional (1 SDK, 1 chave, 1 conta). Trade-off conhecido: ETL ~30% mais caro e qualidade de escrita longa em PT-BR fica abaixo de Claude. Revisitável quando volume justificar otimização por tarefa.

### A2 — Migração ChromaDB → pgvector
**Decisão:** Cutover seco em janela de manutenção, com backup do ChromaDB.
**Justificativa:** Sem usuários em produção crítica; complexidade de shadow run não se paga sem SLA real.

**Revisão (descoberta na Wave 2 round 2):** Não há código Python referenciando ChromaDB no projeto atual — o matching já foi refatorado para LLM-only sobre o knowledge graph (`core/kg_match_service.py`). Os diretórios `chroma_db/` e `gold_vectors/` em disco são puramente legado e podem ser deletados quando conveniente. **Não há migração de dados a fazer** — a "cutover" virou apenas cleanup de disco + atualização de docs.

### A3 — Embeddings em runtime
**Decisão:** OpenAI `text-embedding-3-large` (1536 dims) + Postgres FTS (`tsvector` com config `portuguese`) para retrieval lexical.
**Justificativa:** Coerente com A1. Hybrid retrieval via `tsvector` nativo do Postgres dispensa adicionar provider ou GPU. Schema sem `sparse_vec` JSONB (RADAR.md §4.5 obsoleto neste ponto).

### A4 — Invocação do ComplianceMonitor
**Decisão:** `asyncio.gather` inline dentro de `/writing/turn`. Endpoint retorna `(draft, compliance_flags)` em uma única resposta.
**Justificativa:** Monitor tem prompt enxuto e latência << LLM principal; latência total ≈ max ≈ LLM principal. Sem infra adicional.

### A5 — Pesos do HybridMatch
**Decisão:** Tabela `matching_weights` no Postgres com `workspace_id` nullable (NULL = global). Cache em memória com TTL curto neutraliza custo da query.
**Justificativa:** Destrava memória procedural editável (RADAR §4.4) e permite ReflectionService alimentar pesos via outcomes na Fase 2.

---

## Bloco B — Decisões que bloqueiam schema

### B1 — Persistência da WritingSession
**Decisão:** Tabela `writing_sessions` (header) + `session_turns` (1 row por turno, com `turn_index, role, content, tokens, created_at`).
**Justificativa:** Estrutura queryable, audit trail natural, base para analytics e ReflectionService.

### B2 — `application_log`: enum e estratégia de escrita
**Decisão:** Schema permissivo (todos os status: `matched, brief_gerado, proposta_iniciada, submetida, em_analise, aprovada, reprovada, desistiu`). Tabela `application_log` update-in-place no status atual + tabela satélite `application_events` append-only via trigger Postgres em todo UPDATE de `status`.
**Justificativa:** Audit trail completo de transições (timing entre estados) + queries simples de status atual.

### B3 — Índice vetorial do `edital_chunks`
**Decisão:** HNSW (`USING hnsw (embedding vector_cosine_ops)`). Coluna `text_search tsvector GENERATED ALWAYS AS (to_tsvector('portuguese', text)) STORED` + índice GIN para FTS.
**Justificativa:** Volume previsto (~10^5 rows) confortável para HNSW puro. Recall importa diretamente na qualidade do retrieval.

### B4 — `importance_score` em `content_items`
**Decisão:** `importance_score INT (1-10) NOT NULL` + `last_referenced_at TIMESTAMPTZ`. Score base atribuído pelo `enrich_content` no upload. Effective importance computado em query-time via `base * exp(-(now - last_referenced_at) / half_life)`.
**Justificativa:** Adota decay já na Fase 1 (override da recomendação one-shot). Implica que a injeção em WritingSession deve atualizar `last_referenced_at`. Parte do retrieval multi-critério (Fase 2) é antecipada.

### B5 — Multi-tenancy
**Decisão:** Row-Level Security (RLS) no Postgres com policies por `workspace_id` via `auth.uid()` do Supabase Auth. Backend de user-requests usa `anon-key` + JWT do usuário; jobs ETL e workers pg-boss usam `service-role` key.
**Justificativa:** Defense-in-depth real, alinhado com sensibilidade LGPD dos dados de outcomes.

---

## Bloco C — Decisões que bloqueiam produto

### C1 — Definição de "outcome"
**Decisão:** `ReflectionService` consome `aprovada`, `reprovada` e `submetida`.
**Justificativa:** "Submetida" é sinal de comprometimento observável em semanas; terminais (6-12 meses no FINEP) refinam depois. Outros estados intermediários ignorados na Fase 2; reavaliar quando volume permitir.

### C2 — Onboarding do CompanyProfile
**Decisão:** Híbrido — URL gera draft via `ProfileExtractor`, usuário valida e completa formulário estruturado com campos não públicos (TRL típico, mecanismos preferidos, contrapartida disponível, histórico de submissões).
**Justificativa:** Perfil rico sem entrevista de 30min.

### C3 — Escopo das reflexões
**Decisão:** Per-workspace por padrão. Opt-in explícito para contribuir outcomes anonimizados aos pesos globais (`workspaces.contribute_to_global_weights BOOLEAN DEFAULT false`). Anonimização remove `workspace_id` e mantém apenas `match_dimensions + status`.
**Justificativa:** LGPD-compliant; cria caminho legítimo para pesos globais evoluírem com dados.

### C4 — `/review` em 3 passes
**Decisão:** 3 chamadas LLM paralelas via `asyncio.gather` (compliance, qualidade narrativa, completude).
**Justificativa:** Passes informacionalmente independentes; latência cai 3×, custo total em $ inalterado.

---

## Bloco D — Decisões operacionais

### D1 — Hospedagem
**Decisão:** Vercel (frontend Next.js) + Fly.io (backend FastAPI via Dockerfile).
**Justificativa:** Tempo até primeiro deploy mínimo. Migração para Cloud Run/ECS é refactor de horas se necessário.

### D2 — Observabilidade de LLM
**Decisão:** Adiar. Começar com logs estruturados simples. Avaliar Langfuse self-hosted quando debug de regressões justificar.
**Justificativa:** YAGNI no início. Trade-off conhecido: dor para investigar regressões de qualidade até instrumentação ser adicionada.

### D3 — Background jobs
**Decisão:** **procrastinate** já na Fase 1 (revisado — ver addendum). Worker process adicional no Fly.io (mesmo Dockerfile, comando `python -m procrastinate worker`).
**Justificativa:** Preparado para ReflectionService (Fase 2) e automação ETL futura. Investimento de ~2-3h de setup.

### D4 — Secrets management
**Decisão:** Vercel secrets (frontend) + Fly secrets (backend). `.env.example` versionado no git para onboarding de devs.
**Justificativa:** Nativos da hospedagem (D1), zero custo. Migrar para Doppler/Infisical é trivial quando compliance exigir.

---

## Interdependências críticas

| Decisão | Impacta | Como |
|---|---|---|
| A1 → A3 | OpenAI lock-in | `text-embedding-3-large` é decorrência direta de "OpenAI only" |
| A3 → B3 | Schema `edital_chunks` | `sparse_vec` JSONB sai do schema; FTS via `tsvector` substitui |
| A4 → contrato API | `/writing/turn` retorna tupla `(draft, flags)` em vez de só `draft` |
| A5 → Fase 2 | ReflectionService pode atualizar pesos via INSERT em `matching_weights(workspace_id=...)` |
| B2 → trigger Postgres | UPDATE em `application_log.status` dispara INSERT em `application_events` |
| B4 (override) → Fase 1 task #5 | Inclui `last_referenced_at` + fórmula decay; antecipa parte de Fase 2 task #10 |
| B5 → D1 | Fly app de user-requests usa anon-key + JWT do usuário; workers/ETL usam service-role |
| B5 → ETL | `scripts/run_*.py` rodam com service-role key |
| C3 → schema `workspaces` | Adiciona coluna `contribute_to_global_weights BOOLEAN DEFAULT false` |
| D1 → D4 | Secrets management nativo da plataforma escolhida (Vercel + Fly) |
| D3 (override) → Fly.io infra | Worker process extra (ou app separado) já na Fase 1 |

---

## Mapeamento decisões × fases do plano

### Fase 1 — Decisões que destravam

| Task da Fase 1 | Decisões aplicáveis |
|---|---|
| #1 RAG nos PDFs | A2 (cutover), A3 (3-large + FTS), B3 (HNSW) |
| #2 `application_log` | B2 (híbrido + satélite + trigger), B5 (RLS), C1 (escopo terminais + submetida) |
| #3 `session_log` + persistir WritingSession | B1 (session_turns), B5 (RLS) |
| #4 CompanyProfile no Supabase | B5 (RLS), C2 (onboarding híbrido) |
| #5 `importance_score` | B4 (decay query-time já Fase 1 — escopo expandido) |
| #6 `/review` em 3 passes | C4 (paralelo via asyncio.gather) |
| #7 `@ mentions` | — |
| #8 `/archive` | B5 (RLS), B2 (soft-delete coerente com schema) |
| **(novas tasks)** | A5 (tabela `matching_weights` global), B5 (RLS policies em todas tabelas), D1 (deploy Vercel+Fly), D3 (pg-boss worker), D4 (`.env.example` + secrets) |

### Fase 2 — Decisões que destravam

| Task da Fase 2 | Decisões aplicáveis |
|---|---|
| #9 Embeddings em `content_items` | A3 (3-large) |
| #10 Retrieval multi-critério | B4 (parcialmente antecipado para Fase 1) |
| #11 ReflectionService | C1 (escopo de outcomes), C3 (per-workspace + opt-in), D3 (pg-boss) |
| #12 Insights na WritingSession | — |
| #13 `_WEIGHTS` por workspace | A5 (tabela já criada na Fase 1, agora ReflectionService grava rows) |
| #14 ComplianceMonitor | A4 (`asyncio.gather` inline) |

### Fases 3 e 4

Não há decisões dedicadas neste ADR — as escolhas operacionais (D1–D4) e de schema (B1–B5) já cobrem o necessário para implementação futura.

### Pipeline transversal

Não há decisões dedicadas neste ADR. A taxonomia de falhas ETL (task #22) usa B5 (service-role key) e A2 (sem dependência de ChromaDB).

---

## Fora do escopo planejado

Nenhum item adicional foi levantado durante o fechamento dos 18 itens originais.

---

## Addendum — micro-decisões pós-investigação do estado atual

Após investigação do código, descobriu-se que M1 (Auth), M2 (Workspace ↔ Empresa), M3 (Migration tool) e M5 (JWT flow) já estavam decididas e implementadas. Restavam M4, M6 e uma decisão nova (M7) levantada pelo conflito entre ADR B5 e o código.

### M4 — Local dev story
**Decisão:** Supabase CLI local via Docker (`supabase start`).
**Justificativa:** Migrations rodam idêntico a prod, devs trabalham offline, ambiente reproducível em 1 comando.

### M6 — CI e gates de PR
**Decisão:** GitHub Actions com `ruff + mypy + pytest`. `mypy` strict aplicado gradualmente (`--check-untyped-defs` inicialmente).
**Justificativa:** Setup padrão Python, free para repo privado até 2000min/mês, gates standard.

### M7 — RLS real vs service_role bypass
**Decisão:** Refatorar [core/db.py](../../core/db.py) para criar client per-request com `anon-key` + JWT do usuário (`Authorization: Bearer <jwt>`). RLS torna-se a defesa real. Jobs ETL e workers procrastinate usam `service-role` key separadamente.
**Justificativa:** Conforma com ADR B5 (defense-in-depth real). O código atual usa `service_role` no backend, bypassando RLS — segurança hoje é só app-level por filter manual de `workspace_id`. Refactor de ~1 dia paga-se na primeira tentativa de acessar dado de outro workspace.

### M9 — Escopo de RAG: apenas Writing, nunca Match
**Decisão:** A tabela `edital_chunks` e o retrieval híbrido (cosine + FTS) são consumidos **exclusivamente** por:
  - `WritingSession.turn()` (substituindo o context stuffing atual)
  - `ChecklistService` (Pass 1 compliance e Pass 3 completude, opcional)
  - `OpportunityBriefService` futuro (Fase 3)

`HybridMatchService` e `KGMatchService` **não** consultam `edital_chunks`. Matching continua operando sobre embeddings do summary do edital (1 vetor por edital, migrados de ChromaDB → pgvector pelo cutover A2).

**Justificativa:** Matching é "este edital me serve?" — sinal agregado. RAG é "o que o edital diz sobre X?" — recuperação granular. São propósitos distintos; misturar adiciona complexidade sem ganho.

### M8 — Job queue: pg-boss → procrastinate
**Decisão (revisão de D3):** Substituir **pg-boss** por **procrastinate** como sistema de background jobs. Worker process roda `python -m procrastinate worker`. Schema do procrastinate fica em schema Postgres próprio (não polui tabelas de negócio). Migration aplica via API do procrastinate (geralmente integrada à app de migrations) ou SQL exportado.
**Justificativa:** pg-boss é uma library Node.js sem equivalente em Python. Manter pg-boss exigiria um worker Node.js separado (split-language) por benefício zero. **procrastinate** preserva a intenção original de D3 (Postgres-native, sem broker adicional, mesma infra) e integra com asyncio/FastAPI nativamente. Decisão tomada após investigação do código existente (Python-only).

**Workloads que vão para procrastinate:**
- `enrich_content` ao subir item da library (hoje síncrono — passa a job assíncrono com status `enrichment_status`)
- Chunking + embedding de PDFs de edital novos (RAG da Fase 1)
- `ReflectionService` (Fase 2 — disparado por evento ou periódico)
- ETL FINEP diário (periodic task `@app.periodic(cron="0 3 * * *")`)
- `OpportunityBriefService` (Fase 3 — multi-pass LLM, 30s+)

**Workloads que permanecem inline (não vão para procrastinate):**
- `/review` em 3 passes (decisão C4: `asyncio.gather` inline)
- `ComplianceMonitor` (decisão A4: `asyncio.gather` inline em `/writing/turn`)
- `HybridMatch` / `KGMatch` (interativo, usuário espera resultado)
- Turnos da WritingSession (conversa em tempo real)

### Drifts descobertos a corrigir antes de novas migrations

| Drift | Onde | Ação |
|---|---|---|
| `CompanyProfile` duplicado (dataclass + JSONB) | [domain/user_profile.py](../../domain/user_profile.py) ainda tem `save/load` em disco; [workspaces.profile](../../supabase/migrations/001_init.sql) é a fonte real | Remover `save/load/load_default` do dataclass; ajustar call sites |
| `LLM_BACKEND` default inconsistente | [core/writing_session.py:41](../../core/services/writing_session.py#L41) default `ollama`; [core/content_library.py:53](../../core/services/content_library.py#L53) default `openai`; CLAUDE.md diz ollama; [.env.example](../../.env.example) diz openai | Normalizar tudo para `openai` (conforme A1) |
| `pgvector` extension | Provavelmente não habilitada no Supabase | `CREATE EXTENSION vector` na migration 002 |
| Sessão WritingSession em memória do processo | [backend/api.py](../../backend/api.py) `_writing_sessions: dict = {}` | Resolvido pela task B1 (session_turns) |
