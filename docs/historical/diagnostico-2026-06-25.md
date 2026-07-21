# Relatório de Diagnóstico — Radar de Editais

> Gerado em 2026-06-25. Baseado em auditoria completa de código fonte, análise arquitetural e code review do branch `main`.
>
> **Registro histórico:** este diagnóstico antecede as migrações v3 e não
> descreve o runtime atual. Para a arquitetura vigente, consulte
> [`docs/architecture.md`](../architecture.md).

---

## Bugs Confirmados

Verificados diretamente no código fonte.

| # | Arquivo | Descrição | Prioridade |
|---|---|---|---|
| B1 | `core/infra/auth.py:94` | JWKS fetch via `urllib` sem retry, timeout=5s. Um blip no endpoint derruba toda autenticação até o server reiniciar | **Alta** |
| B2 | `core/infra/db.py:49,55,59` | Três `except Exception: pass` aninhados em `get_supabase_user`. JWT quebrado ou SDK com bug passa silenciosamente; queries subsequentes falham de forma imprevisível | **Alta** |
| B3 | `core/ingestion/opportunity_discovery.py:425` | Quando o cliente Supabase está indisponível, findings da discovery são descartados silenciosamente — sem re-queue, sem alarme. Data loss garantido em falhas de rede | **Alta** |
| B4 | `core/services/writing_session.py:1974–1994` | `_call_ollama` usa `requests.post(timeout=300)` sem retry. O resto da classe usa `make_client`. Inconsistência silenciosa em falhas Ollama | **Média** |
| B5 | `backend/routers/discovered.py:61` | `requests.head/get` com timeout=10s fixo, sem retry. Falha transiente de um host de PDF vira HTTP 500 para o frontend | **Média** |
| B6 | `core/ingestion/dou_feeder.py:214` | `requests.Session()` sem retry adapter, apesar do docstring mencionar retry handling | **Média** |
| B7 | `structurer.py:57`, `edital_extractor.py:211`, `entity_matcher.py:45`, `explore_agent.py:227` | URL Ollama hardcoded `http://localhost:11434/v1` em 4 arquivos. `writing_session.py:61` lê `OLLAMA_BASE_URL` corretamente — os outros não | **Baixa** |
| B8 | `core/llm/agent_graph.py:8–9` | Comentário menciona `AGENT_RUNTIME` env var e modo `legacy`. Nenhum dos dois existe no código | **Baixa** |
| B9 | `core/retrieval/retriever.py:357` | Docstring menciona `fts_weight=0.3` mas `DEFAULT_FTS_WEIGHT = 0.5` (linha 44). Stale desde algum ajuste de tuning | **Baixa** |

---

## Bugs Prováveis

Código suspeito sem cobertura de teste para refutar o cenário de falha.

| # | Arquivo | Descrição | Prioridade |
|---|---|---|---|
| P1 | `core/compliance_monitor.py:74,81,99,103,163` | 5 métodos retornam `[]`/`""` ao swallow de exceção, sem log acima de DEBUG. Falhas de compliance somem; o usuário recebe resultado vazio sem aviso. Zero testes | **Alta** |
| P2 | `core/eligibility_producer.py:52,56,111` | 3 métodos retornam `{}`/`[]` como fallback. Produtor silenciosamente entrega elegibilidade vazia ao KG, degradando matching sem nenhum alarme. Zero testes | **Média** |
| P3 | `core/services/writing_session.py` | Classe de 2.400 linhas misturando DB, LLM, HTTP client e lógica de geração. Difícil rastrear efeitos colaterais entre métodos — risco de regressão silenciosa em qualquer mudança | **Média** |

---

## Funcionalidades Incompletas

Código com scaffold mas que não entrega o comportamento documentado.

| # | Onde | O que falta | Prioridade |
|---|---|---|---|
| I1 | `core/tasks.py` — `run_meta_reflection` | Job para escrever `playbook_overlays` via meta-reflexão cross-tenant não existe. Tabelas criadas (PR #26), mas o job que as popula depende de volume real entre workspaces | **Baixa** |
| I2 | Gate eval `writing` — memória semântica (Et.5 LangGraph) | A/B entre `WRITING_SEMANTIC_MEMORY=1` e `=0` nunca rodado. Bloqueia o corte do bloco fixo de 6 insights. Pré-req: backfill do Store via `scripts/backfill_memory_store.py` | **Média** |
| I3 | Gate eval + telemetria Et.6 (LangGraph) | Suítes de eval real (`writing`/`extraction`/`rag`) e wire do Langfuse Experiment pós-migração nunca validados com N casos. Só rodado com `--limit 1` | **Média** |
| I4 | Discovery — Partes A e B | Scrapers de FAPs (Parte A) e investidores (Parte B) pendentes. Só a Parte C (staging + gate humano) foi entregue | **Baixa** |
| I5 | Migrations 021–024 (`knowledge-evolution`) | Aplicadas e validadas só no Supabase **local**. Ainda não foram ao remoto (`supabase db push`) | **Alta** |

---

## Riscos Arquiteturais

Problemas estruturais que não quebram hoje mas acumulam dívida ou podem quebrar em escala.

| # | Risco | Detalhe | Prioridade |
|---|---|---|---|
| R1 | **Isolamento multi-tenant via `thread_id`/namespace** em vez de RLS (LangGraph checkpointer + Store) | Risco #1 da spec de migração. Leak test cross-workspace com Postgres real ainda não rodado. Um bug aqui expõe dados de um workspace para outro | **Alta** |
| R2 | **57 env vars críticas ausentes do `.env.example`** | `RERANK_BACKEND`, `KG_STORE_BACKEND`, `AGENT_MAX_STEPS`, `WRITING_SEMANTIC_MEMORY`, `CNPJ_LOOKUP_ENABLED`, `DEMO_MODE`, `INLABS_EMAIL/PASSWORD`, `TAVILY_API_KEY`, `WEB_SEARCH_BACKEND`, `ELIGIBILITY_BACKEND`, `HYDE_*`, etc. Deploy cold sem documentação = configuração por adivinhação | **Alta** |
| R3 | **`writing_session.py` — 2.400 linhas** | DB + LLM + HTTP client + geração em uma classe. Qualquer mudança tem superfície de regressão enorme. A ausência de testes unitários granulares agrava | **Média** |
| R4 | **16 scripts com `sys.path.insert(0, ROOT)`** | Viola o gotcha explícito do CLAUDE.md. Se o editable install estiver stale, importam versão diferente de `core/` sem avisar | **Baixa** |
| R5 | **Sem testes de rota nos routers de backend** | Módulos em `backend/routers/` não têm testes diretos — cobertura só por caminhos de integração indireta, o que não captura regressões de contrato de API | **Média** |
| R6 | **Sem cobertura em módulos críticos** | `compliance_monitor`, `eligibility_producer`, `tasks`, `db`, `auth`, `telemetry`, `weight_approval`, `profile_drift`, `opportunity_brief_service` — todos sem arquivo de teste dedicado | **Média** |

---

## Resumo por Prioridade

### Alta — resolver antes de produção com dados reais

- **B1** `core/infra/auth.py:94` — JWKS sem retry → derruba auth em blip
- **B2** `core/infra/db.py:49,55,59` — exception swallow em `get_supabase_user` → auth silenciosamente quebrada
- **B3** `core/ingestion/opportunity_discovery.py:425` — data loss na discovery sem alarme
- **P1** `core/compliance_monitor.py` — swallow silencioso → resultados falsos de compliance
- **I5** Migrations 021–024 não aplicadas no remoto
- **R1** Isolamento multi-tenant não validado → risco de data leak entre workspaces
- **R2** 57 env vars sem documentação → deploy inviável para qualquer pessoa além do autor

### Média — próximo sprint

- **B4** `_call_ollama` sem retry
- **B5** `discovered.py` router sem retry
- **P2** `eligibility_producer` fallback silencioso
- **P3** `writing_session.py` monólito de 2.400 linhas
- **I2/I3** Gates de eval LangGraph pendentes
- **R3** `writing_session.py` risco de regressão por tamanho
- **R5/R6** Lacunas de teste em routers e módulos críticos

### Baixa — backlog técnico

- **B6** `dou_feeder.py` sem retry
- **B7** Ollama URL hardcoded em 4 arquivos
- **B8/B9** Comentários e docstrings stale
- **I1** `run_meta_reflection` só scaffold
- **I4** Scrapers FAPs/investidores (Discovery A e B)
- **R4** `sys.path.insert` nos 16 scripts
